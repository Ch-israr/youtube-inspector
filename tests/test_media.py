"""Tests for app.media (ffprobe tooling + download-engine boundary) and
for the POST /api/v1/youtube/download endpoint.

The engine registry ships intentionally empty — no permitted engine exists
for arbitrary YouTube video downloads — and these tests pin that contract:
the endpoint validates and rate-limits like the others, then answers an
honest 422 DOWNLOAD_NOT_PERMITTED.
"""
import base64
import shutil

import pytest
from fastapi.testclient import TestClient

from app import media
from app.main import create_app

DOWNLOAD_URL = "/api/v1/youtube/download"
WATCH_URL = "https://www.youtube.com/watch?v=dQw4y9WgXcQ"
URL_INFO = {
    "platform": "youtube",
    "video_id": "dQw4y9WgXcQ",
    "canonical_url": WATCH_URL,
    "url": WATCH_URL,
}

# A real 1x1 JPEG, generated with ffmpeg's lavfi color source
# (ffmpeg -f lavfi -i color=white:s=1x1 -frames:v 1) — ffprobe parses
# it as mjpeg, 1x1.
REAL_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjI4LjEwMgD/2wBDAAgEBAQEBAUFBQUFBQYGBgYGBgYGBgYGBgYHBwcICAgHBwcGBgcHCAgICAkJCQgICAgJCQoKCgwMCwsODg4RERT/xABLAAEBAAAAAAAAAAAAAAAAAAAABwEBAAAAAAAAAAAAAAAAAAAAABABAAAAAAAAAAAAAAAAAAAAABEBAAAAAAAAAAAAAAAAAAAAAP/AABEIAAEAAQMBIgACEQADEQD/2gAMAwEAAhEDEQA/AL+AD//Z"
)
GARBAGE = b"this is definitely not a media file, just text bytes. " * 8

requires_ffprobe = pytest.mark.skipif(
    shutil.which("ffprobe") is None, reason="ffprobe is not installed"
)


@pytest.fixture()
def clean_env(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "30")


@pytest.fixture()
def registry_snapshot():
    """Restore ENGINE_REGISTRY after a test that registers an engine."""
    saved = list(media.ENGINE_REGISTRY)
    yield media.ENGINE_REGISTRY
    media.ENGINE_REGISTRY[:] = saved


# ---------------------------------------------------------------------------
# Tool versions + probing
# ---------------------------------------------------------------------------


def test_tool_versions_shape():
    versions = media.tool_versions()
    assert set(versions) == {"ffmpeg", "ffprobe"}
    for value in versions.values():
        assert value is None or isinstance(value, str)
    # Consistency with actual availability on this machine.
    assert (versions["ffprobe"] is not None) == media.ffprobe_available()


@requires_ffprobe
def test_probe_media_real_jpeg():
    versions = media.tool_versions()
    assert versions["ffprobe"] is not None
    assert versions["ffprobe"].startswith("ffprobe version")

    info = media.probe_media(REAL_JPEG)
    assert info is not None
    assert info["video_codec"] == "mjpeg"
    assert info["width"] == 1
    assert info["height"] == 1
    assert info["format"]
    assert media.is_image_media(info)


@requires_ffprobe
def test_probe_media_garbage_returns_none():
    assert media.probe_media(GARBAGE) is None


def test_probe_media_empty_input_returns_none():
    assert media.probe_media(b"") is None


def test_is_image_media_classification():
    assert media.is_image_media(
        {"format": "image2", "video_codec": "mjpeg", "width": 2, "height": 2}
    )
    assert media.is_image_media(
        {"format": "jpeg_pipe", "video_codec": "mjpeg", "width": 2, "height": 2}
    )
    assert not media.is_image_media(
        {"format": "mp3", "video_codec": "mp3", "width": None, "height": None}
    )
    assert not media.is_image_media(
        {"format": "mov,mp4", "video_codec": "h264", "width": 2, "height": 2}
    )
    assert not media.is_image_media(None)
    assert not media.is_image_media({})


# ---------------------------------------------------------------------------
# Engine registry
# ---------------------------------------------------------------------------


class _DummyEngine:
    name = "dummy"
    permitted_basis = "Test-only engine; handles content the operator owns."

    def supports(self, url_info):
        return url_info.get("platform") == "youtube"


def test_registry_is_empty_by_default():
    assert media.ENGINE_REGISTRY == []
    assert media.select_engine(URL_INFO) is None


def test_select_engine_selects_registered_engine(registry_snapshot):
    engine = _DummyEngine()
    registry_snapshot.append(engine)
    assert media.select_engine(URL_INFO) is engine


def test_engine_without_permitted_basis_is_never_selected(registry_snapshot):
    class NoBasisEngine:
        name = "no-basis"
        permitted_basis = "   "

        def supports(self, url_info):
            return True

    registry_snapshot.append(NoBasisEngine())
    assert media.select_engine(URL_INFO) is None


def test_non_supporting_engine_is_skipped(registry_snapshot):
    class OtherPlatformEngine:
        name = "other"
        permitted_basis = "Test-only engine for a different platform."

        def supports(self, url_info):
            return url_info.get("platform") == "vimeo"

    registry_snapshot.append(OtherPlatformEngine())
    assert media.select_engine(URL_INFO) is None


# ---------------------------------------------------------------------------
# Download endpoint
# ---------------------------------------------------------------------------


def test_download_not_permitted(clean_env):
    client = TestClient(create_app())
    resp = client.post(DOWNLOAD_URL, json={"url": WATCH_URL})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "DOWNLOAD_NOT_PERMITTED"
    assert body["platform"] == "youtube"
    assert "No permitted download workflow exists" in body["message"]


def test_download_invalid_url(clean_env):
    client = TestClient(create_app())
    resp = client.post(DOWNLOAD_URL, json={"url": "not a url"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "MALFORMED_URL"


def test_download_missing_url_field(clean_env):
    client = TestClient(create_app())
    resp = client.post(DOWNLOAD_URL, json={})
    assert resp.status_code == 400
    assert resp.json()["code"] == "MALFORMED_URL"


def test_download_unsupported_host(clean_env):
    client = TestClient(create_app())
    resp = client.post(DOWNLOAD_URL, json={"url": "https://vimeo.com/123456789"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "UNSUPPORTED_HOST"
    assert body["platform"] == "youtube"


def test_download_rate_limited(clean_env, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    client = TestClient(create_app())
    statuses = [
        client.post(DOWNLOAD_URL, json={"url": WATCH_URL}).status_code
        for _ in range(3)
    ]
    assert statuses == [422, 422, 429]
    resp = client.post(DOWNLOAD_URL, json={"url": WATCH_URL})
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMITED"
