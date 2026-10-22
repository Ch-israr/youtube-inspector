import httpx
import pytest
from fastapi.testclient import TestClient

from app import media
from app import youtube as yt
from app.main import create_app

VIDEO_ID = "dQw4y9WgXcQ"
THUMB_URL = f"/api/v1/youtube/thumbnail/{VIDEO_ID}"
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x10" * 2048 + b"\xff\xd9"


@pytest.fixture()
def clean_env(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "30")


@pytest.fixture(autouse=True)
def _valid_probe(monkeypatch):
    """Stand in for a passing ffprobe validation of the thumbnail bytes.

    The mocked upstream serves synthetic JPEG bytes (JPEG_BYTES is a
    header/trailer stub, not a strictly valid image), so the route's
    ffprobe output-validation is stubbed to a passing image result. This
    keeps the thumbnail tests deterministic whether or not the machine
    running them has ffprobe installed. The real probe is covered in
    tests/test_media.py; the rejection path is covered by
    test_thumbnail_invalid_media_rejected below.
    """
    monkeypatch.setattr(
        media,
        "probe_media",
        lambda data: {
            "format": "image2",
            "video_codec": "mjpeg",
            "width": 1280,
            "height": 720,
        },
    )


def use_transport(monkeypatch, handler):
    """Force every httpx.AsyncClient created by the app to use a MockTransport."""
    real_client = httpx.AsyncClient

    class PatchedClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs.pop("proxy", None)
            kwargs.pop("mounts", None)
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)


def jpeg_ok(request):
    assert request.url.host == "i.ytimg.com"
    return httpx.Response(200, content=JPEG_BYTES, headers={"Content-Type": "image/jpeg"})


def test_thumbnail_success(clean_env, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return jpeg_ok(request)

    use_transport(monkeypatch, handler)
    client = TestClient(create_app())
    resp = client.get(THUMB_URL)
    assert resp.status_code == 200
    assert resp.content == JPEG_BYTES
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="youtube-{VIDEO_ID}-thumbnail.jpg"'
    )
    assert int(resp.headers["content-length"]) == len(JPEG_BYTES)
    # Max-resolution variant is tried first and suffices.
    assert seen == [f"/vi/{VIDEO_ID}/maxresdefault.jpg"]


def test_thumbnail_fallback_to_hq(clean_env, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if "maxresdefault" in request.url.path:
            return httpx.Response(404)
        return jpeg_ok(request)

    use_transport(monkeypatch, handler)
    client = TestClient(create_app())
    resp = client.get(THUMB_URL)
    assert resp.status_code == 200
    assert resp.content == JPEG_BYTES
    assert seen == [
        f"/vi/{VIDEO_ID}/maxresdefault.jpg",
        f"/vi/{VIDEO_ID}/hqdefault.jpg",
    ]


def test_thumbnail_unavailable_both_404(clean_env, monkeypatch):
    use_transport(monkeypatch, lambda request: httpx.Response(404))
    client = TestClient(create_app())
    resp = client.get(THUMB_URL)
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "THUMBNAIL_UNAVAILABLE"
    assert body["platform"] == "youtube"
    assert body["message"]


@pytest.mark.parametrize(
    "bad_id",
    [
        "short",          # too short
        "dQw4y9WgXc",     # 10 chars
        "dQw4y9WgXcQQ",   # 12 chars
        "abc!defghij",    # 11 chars but '!' is not allowed
        "abc defghij",    # space is not allowed
    ],
)
def test_thumbnail_invalid_video_id(clean_env, bad_id):
    client = TestClient(create_app())
    resp = client.get(f"/api/v1/youtube/thumbnail/{bad_id}")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "INVALID_VIDEO_ID"
    assert body["platform"] == "youtube"


def test_thumbnail_provider_timeout(clean_env, monkeypatch):
    def timeout_handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    use_transport(monkeypatch, timeout_handler)
    client = TestClient(create_app())
    resp = client.get(THUMB_URL)
    assert resp.status_code == 502
    assert resp.json()["code"] == "PROVIDER_TIMEOUT"


def test_thumbnail_oversized_body_rejected(clean_env, monkeypatch):
    oversized = b"\xff\xd8" + b"\x00" * (yt.MAX_THUMBNAIL_BYTES + 1)
    use_transport(
        monkeypatch,
        lambda request: httpx.Response(200, content=oversized),
    )
    client = TestClient(create_app())
    resp = client.get(THUMB_URL)
    assert resp.status_code == 502
    assert resp.json()["code"] == "PROVIDER_ERROR"
    assert len(resp.content) < len(oversized)  # nothing partial is served


def test_thumbnail_upstream_error_does_not_leak(clean_env, monkeypatch):
    use_transport(
        monkeypatch,
        lambda request: httpx.Response(500, text="secret upstream detail"),
    )
    client = TestClient(create_app())
    resp = client.get(THUMB_URL)
    assert resp.status_code == 502
    assert resp.json()["code"] == "PROVIDER_ERROR"
    assert "secret upstream detail" not in resp.text


def test_thumbnail_rate_limited(clean_env, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    use_transport(monkeypatch, jpeg_ok)
    client = TestClient(create_app())
    statuses = [client.get(THUMB_URL).status_code for _ in range(3)]
    assert statuses[:2] == [200, 200]
    assert statuses[2] == 429
    resp = client.get(THUMB_URL)
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMITED"


def test_thumbnail_invalid_media_rejected(clean_env, monkeypatch):
    # The upstream returns bytes with a 200, but ffprobe validation must
    # refuse to serve anything that is not really an image — an HTML error
    # page from upstream can never become a "successful" download.
    use_transport(monkeypatch, jpeg_ok)
    monkeypatch.setattr(media, "ffprobe_available", lambda: True)
    client = TestClient(create_app())

    # Case 1: ffprobe cannot parse the bytes at all.
    monkeypatch.setattr(media, "probe_media", lambda data: None)
    resp = client.get(THUMB_URL)
    assert resp.status_code == 502
    assert resp.json()["code"] == "PROVIDER_ERROR"

    # Case 2: ffprobe parses the bytes, but they are not an image.
    monkeypatch.setattr(
        media,
        "probe_media",
        lambda data: {
            "format": "mp3",
            "video_codec": "mp3",
            "width": None,
            "height": None,
        },
    )
    resp = client.get(THUMB_URL)
    assert resp.status_code == 502
    assert resp.json()["code"] == "PROVIDER_ERROR"
