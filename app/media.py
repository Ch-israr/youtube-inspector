"""Media tooling and the download-engine boundary.

Two responsibilities:

1. **FFmpeg/ffprobe integration** for *media processing and validation
   only*. FFmpeg is NOT a YouTube extractor and is never used to fetch
   anything. Today ffprobe is load-bearing in exactly one place: fetched
   thumbnail bytes are probed before they are served, so an upstream that
   returns an HTML error page or other non-image bytes can never be
   delivered to a user as a "successful" image download. If a permitted
   download engine ever exists, ffprobe verification of its output (and
   ffmpeg merging/remuxing of legitimately obtained streams) plugs in
   here too.

2. **The download-engine registry.** An engine may only be registered when
   it implements a workflow that is *permitted* for the content it handles,
   and it must say why, in its ``permitted_basis``. As of this build no
   permitted engine exists for arbitrary YouTube video downloads — YouTube's
   Terms of Service and Developer Policies (§III.E.1) prohibit third-party
   services from downloading YouTube videos, and every known extractor
   works through access YouTube does not grant. The registry is therefore
   intentionally empty and the download endpoint honestly reports
   ``DOWNLOAD_NOT_PERMITTED``. If a permitted workflow ever becomes
   available, it registers here and the endpoint starts serving real jobs
   with no frontend or API-shape change.

All subprocess calls use argument lists (never ``shell=True``), fixed
argument vectors, and bounded timeouts. Media probing runs on a temp file
inside a per-call ``TemporaryDirectory``; cleanup is guaranteed by the
context manager and no user-controlled string ever reaches the command
line — the only path passed is one we created.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

_VERSION_TIMEOUT_SECONDS = 5
_PROBE_TIMEOUT_SECONDS = 10

_versions_cache: dict[str, str | None] | None = None


def _first_version_line(binary: str) -> str | None:
    """First line of ``<binary> -version``, or None if unavailable."""
    try:
        proc = subprocess.run(
            [binary, "-version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    lines = (proc.stdout or "").splitlines()
    return lines[0].strip() if lines else None


def tool_versions() -> dict[str, str | None]:
    """{"ffmpeg": <version line or None>, "ffprobe": <version line or None>}.

    Cached after the first call — the binaries in a deployed image do not
    change at runtime.
    """
    global _versions_cache
    if _versions_cache is None:
        _versions_cache = {
            "ffmpeg": _first_version_line("ffmpeg"),
            "ffprobe": _first_version_line("ffprobe"),
        }
    return dict(_versions_cache)


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def probe_media(data: bytes) -> dict | None:
    """Probe raw media bytes with ffprobe.

    Returns {"format": ..., "video_codec": ..., "width": ..., "height": ...}
    for the first video stream, or None on any failure: empty input,
    ffprobe missing, timeout, non-zero exit, or unparseable output.
    """
    if not data:
        return None
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="media-probe-") as tmpdir:
            path = Path(tmpdir) / "media.bin"
            path.write_bytes(data)
            proc = subprocess.run(
                [
                    ffprobe,
                    "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=codec_name,width,height",
                    "-show_entries", "format=format_name",
                    "-of", "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SECONDS,
            )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout or "")
    except ValueError:
        return None
    streams = payload.get("streams") or []
    format_name = (payload.get("format") or {}).get("format_name")
    if not streams and not format_name:
        return None
    stream = streams[0] if streams else {}
    return {
        "format": format_name,
        "video_codec": stream.get("codec_name"),
        "width": stream.get("width"),
        "height": stream.get("height"),
    }


def is_image_media(info: dict | None) -> bool:
    """True when a probe_media result indicates a still-image format."""
    if not info:
        return False
    fmt = (info.get("format") or "").lower()
    codec = (info.get("video_codec") or "").lower()
    return "image" in fmt or "jpeg" in fmt or codec in ("mjpeg", "jpeg")


# --------------------------------------------------------------------------
# Download-engine boundary
# --------------------------------------------------------------------------


class DownloadEngine(Protocol):
    """Contract for a download engine.

    ``permitted_basis`` is REQUIRED and must be a non-empty human-readable
    statement of why this engine is permitted for the content it handles
    (e.g. "serves only media the account holder uploaded, via the
    platform's official owner-download mechanism"). An engine without a
    stated permitted basis is never selected.
    """

    name: str
    permitted_basis: str

    def supports(self, url_info: dict) -> bool:
        """True when this engine can handle the given parsed URL info."""
        ...


# Engines may only be registered when a permitted workflow exists. As of
# this build none exists for YouTube video downloads, so this list is
# intentionally empty. Do not register extractors (yt-dlp, Cobalt, ...)
# here: they access YouTube through channels YouTube does not permit,
# which the service's compliance basis forbids outright.
ENGINE_REGISTRY: list[DownloadEngine] = []


def select_engine(url_info: dict) -> DownloadEngine | None:
    """First registered engine that (a) states a permitted basis and
    (b) supports this URL — or None when no permitted engine applies."""
    for engine in ENGINE_REGISTRY:
        basis = (getattr(engine, "permitted_basis", "") or "").strip()
        if not basis:
            continue  # No stated permitted basis: never selected, ever.
        try:
            if engine.supports(url_info):
                return engine
        except Exception:
            continue  # A broken engine must not take selection down.
    return None
