"""YouTube provider module.

Isolated behind a small interface so additional platform providers can be
added later without changing the generic API infrastructure.

Compliance: metadata comes ONLY from official endpoints:
- YouTube Data API v3 (when YOUTUBE_API_KEY is configured)
- YouTube's official oEmbed endpoint (otherwise)

No scraping, no extraction, no downloading. Ever.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlparse

import httpx

ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Paths of the form /<prefix>/<VIDEO_ID>
_ID_PATH_PREFIXES = ("shorts", "live", "embed", "v")

UPSTREAM_TIMEOUT = httpx.Timeout(connect=8.0, read=8.0, write=8.0, pool=8.0)

DOWNLOAD_NOTE = (
    "YouTube does not provide a permitted way for third-party services to "
    "download videos; downloading is not offered."
)


class YouTubeError(Exception):
    """Base class for provider errors. Carries an API error code."""

    code = "PROVIDER_ERROR"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class MalformedURLError(YouTubeError):
    code = "MALFORMED_URL"


class UnsupportedHostError(YouTubeError):
    code = "UNSUPPORTED_HOST"


class InvalidURLError(YouTubeError):
    code = "INVALID_URL"


class ProviderTimeoutError(YouTubeError):
    code = "PROVIDER_TIMEOUT"


class ProviderError(YouTubeError):
    code = "PROVIDER_ERROR"


def parse_youtube_url(raw: str) -> str:
    """Validate a user-supplied URL and return the 11-char video ID.

    Raises MalformedURLError / UnsupportedHostError / InvalidURLError.
    """
    if raw is None or not isinstance(raw, str) or not raw.strip():
        raise MalformedURLError("A URL is required.")
    url = raw.strip()

    try:
        parsed = urlparse(url)
    except ValueError:
        raise MalformedURLError("That URL could not be parsed.")

    if parsed.scheme not in ("http", "https"):
        if not parsed.scheme:
            raise MalformedURLError("The URL must start with http:// or https://.")
        raise MalformedURLError(f"Unsupported URL scheme: {parsed.scheme}.")

    # Reject embedded credentials (user:pass@host) — a classic deception trick.
    if parsed.username or parsed.password:
        raise MalformedURLError("URLs containing credentials are not accepted.")
    if "@" in parsed.netloc:
        raise MalformedURLError("URLs containing '@' in the host part are not accepted.")

    host = (parsed.hostname or "").lower()
    if not host:
        raise MalformedURLError("The URL has no host.")
    if host not in ALLOWED_HOSTS:
        raise UnsupportedHostError(f"'{host}' is not a YouTube host.")

    path = parsed.path or ""

    if host.endswith("youtu.be"):
        candidate = path.lstrip("/").split("/")[0]
        return _checked_id(candidate)

    if path == "/watch":
        values = parse_qs(parsed.query).get("v")
        if not values:
            raise InvalidURLError("The watch URL is missing its 'v' video parameter.")
        return _checked_id(values[0])

    for prefix in _ID_PATH_PREFIXES:
        marker = f"/{prefix}/"
        if path.startswith(marker):
            candidate = path[len(marker):].split("/")[0]
            return _checked_id(candidate)

    raise InvalidURLError(
        "That YouTube URL format is not supported. Use a watch, Shorts, live, "
        "embed or youtu.be link."
    )


def _checked_id(candidate: str) -> str:
    if not candidate or not VIDEO_ID_RE.match(candidate):
        raise InvalidURLError("That does not look like a valid YouTube video ID.")
    return candidate


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def parse_iso8601_duration(value: str | None) -> int | None:
    """Parse an ISO-8601 duration like PT1H2M3S into seconds."""
    if not value:
        return None
    match = _DURATION_RE.match(value)
    if not match:
        return None
    parts = {k: float(v) if v else 0.0 for k, v in match.groupdict().items()}
    total = (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )
    return int(total)


def _empty_metadata(video_id: str) -> dict:
    return {
        "id": video_id,
        "canonical_url": canonical_url(video_id),
        "title": None,
        "channel": None,
        "thumbnail_url": None,
        "duration_seconds": None,
    }


def _pick_thumbnail(thumbnails: dict) -> str | None:
    for size in ("maxres", "high", "medium", "default"):
        thumb = thumbnails.get(size)
        if thumb and thumb.get("url"):
            return thumb["url"]
    return None


async def _inspect_via_data_api(client: httpx.AsyncClient, video_id: str, api_key: str) -> dict | None:
    """Return metadata dict, or None when the video is unavailable."""
    try:
        resp = await client.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,contentDetails,status",
                "id": video_id,
                "key": api_key,
            },
        )
    except httpx.TimeoutException:
        raise ProviderTimeoutError("YouTube did not respond in time. Please try again.")
    except httpx.HTTPError:
        raise ProviderError("Could not reach YouTube. Please try again.")

    if resp.status_code in (401, 403, 404):
        # 404 -> gone. 401/403 with a key usually means quota/key problems;
        # do not leak the upstream body (it can echo the key). Treat a 404 as
        # unavailable; auth/quota failures are provider errors.
        if resp.status_code == 404:
            return None
        raise ProviderError("YouTube metadata service rejected the request.")
    if resp.status_code != 200:
        raise ProviderError("YouTube metadata service returned an error.")

    try:
        payload = resp.json()
    except ValueError:
        raise ProviderError("YouTube metadata service returned an unreadable response.")

    items = payload.get("items") or []
    if not items:
        return None

    item = items[0]
    snippet = item.get("snippet") or {}
    content = item.get("contentDetails") or {}
    status = item.get("status") or {}

    availability = _availability_from_status(status)
    if availability == "unavailable":
        return None

    metadata = _empty_metadata(video_id)
    metadata.update(
        {
            "title": snippet.get("title"),
            "channel": snippet.get("channelTitle"),
            "thumbnail_url": _pick_thumbnail(snippet.get("thumbnails") or {}),
            "duration_seconds": parse_iso8601_duration(content.get("duration")),
        }
    )
    return metadata


def _availability_from_status(status: dict) -> str:
    privacy = status.get("privacyStatus")
    if privacy in ("private", "privacyStatusUnspecified"):
        return "unavailable"
    if status.get("uploadStatus") in ("deleted", "failed", "rejected"):
        return "unavailable"
    return "ok"


async def _inspect_via_oembed(client: httpx.AsyncClient, video_id: str) -> dict | None:
    """oEmbed fallback (no key). Return metadata dict or None if unavailable."""
    try:
        resp = await client.get(
            "https://www.youtube.com/oembed",
            params={"url": canonical_url(video_id), "format": "json"},
        )
    except httpx.TimeoutException:
        raise ProviderTimeoutError("YouTube did not respond in time. Please try again.")
    except httpx.HTTPError:
        raise ProviderError("Could not reach YouTube. Please try again.")

    if resp.status_code in (401, 404):
        return None
    if resp.status_code != 200:
        raise ProviderError("YouTube metadata service returned an error.")

    try:
        payload = resp.json()
    except ValueError:
        raise ProviderError("YouTube metadata service returned an unreadable response.")

    metadata = _empty_metadata(video_id)
    metadata.update(
        {
            "title": payload.get("title"),
            "channel": payload.get("author_name"),
            "thumbnail_url": payload.get("thumbnail_url"),
            "duration_seconds": None,  # oEmbed does not provide duration.
        }
    )
    return metadata


def _make_default_client() -> httpx.AsyncClient:
    """Build the upstream HTTP client.

    Honours a standard HTTPS_PROXY environment variable when present, and
    falls back to ignoring environment proxy settings if the environment's
    proxy configuration cannot be parsed — a broken proxy env var must
    never take the service down.
    """
    kwargs = {"timeout": UPSTREAM_TIMEOUT, "follow_redirects": False}
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        try:
            return httpx.AsyncClient(proxy=proxy, **kwargs)
        except Exception:
            pass
    try:
        return httpx.AsyncClient(**kwargs)
    except Exception:
        return httpx.AsyncClient(trust_env=False, **kwargs)


async def inspect_video(video_id: str, client: httpx.AsyncClient | None = None) -> dict:
    """Inspect a video by ID using official endpoints only.

    Returns {"status": "ok" | "unavailable", "video": {...}}.
    Raises ProviderTimeoutError / ProviderError on upstream failures.
    """
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    own_client = client is None
    if own_client:
        try:
            client = _make_default_client()
        except Exception:
            raise ProviderError("Could not initialise the YouTube connection.")
    try:
        if api_key:
            metadata = await _inspect_via_data_api(client, video_id, api_key)
        else:
            metadata = await _inspect_via_oembed(client, video_id)
    finally:
        if own_client:
            await client.aclose()

    if metadata is None:
        return {"status": "unavailable", "video": _empty_metadata(video_id)}
    return {"status": "ok", "video": metadata}
