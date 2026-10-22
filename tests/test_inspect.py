import httpx
import pytest
from fastapi.testclient import TestClient

from app import youtube as yt
from app.main import create_app

WATCH_URL = "https://www.youtube.com/watch?v=dQw4y9WgXcQ"


@pytest.fixture()
def clean_env(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "30")


def use_transport(monkeypatch, handler):
    """Force every httpx.AsyncClient created by the app to use a MockTransport."""
    real_client = httpx.AsyncClient

    class PatchedClient(real_client):
        def __init__(self, *args, **kwargs):
            # Tests must be hermetic: ignore any proxy configuration the app
            # picks up from the environment and always use the mock.
            kwargs.pop("proxy", None)
            kwargs.pop("mounts", None)
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", PatchedClient)


def oembed_ok(request):
    return httpx.Response(
        200,
        json={
            "title": "Mocked Video Title",
            "author_name": "Mock Channel",
            "thumbnail_url": "https://i.ytimg.com/vi/dQw4y9WgXcQ/hqdefault.jpg",
        },
    )


def test_inspect_success_oembed(clean_env, monkeypatch):
    use_transport(monkeypatch, oembed_ok)
    client = TestClient(create_app())
    resp = client.post("/api/v1/youtube/inspect", json={"url": WATCH_URL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform"] == "youtube"
    assert body["status"] == "ok"
    video = body["video"]
    assert video["id"] == "dQw4y9WgXcQ"
    assert video["canonical_url"] == WATCH_URL
    assert video["title"] == "Mocked Video Title"
    assert video["channel"] == "Mock Channel"
    assert video["thumbnail_url"].startswith("https://i.ytimg.com/")
    assert video["duration_seconds"] is None  # oEmbed provides no duration
    assert body["capabilities"] == {"metadata": True, "preview": True, "download": False}
    assert body["actions"] == ["inspect", "preview"]
    assert "download" in body["download_note"].lower()


def test_inspect_unavailable_oembed_404(clean_env, monkeypatch):
    use_transport(monkeypatch, lambda request: httpx.Response(404))
    client = TestClient(create_app())
    resp = client.post("/api/v1/youtube/inspect", json={"url": WATCH_URL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["video"]["id"] == "dQw4y9WgXcQ"
    assert body["video"]["title"] is None
    assert body["video"]["thumbnail_url"] is None
    assert body["capabilities"]["download"] is False


def test_inspect_provider_timeout(clean_env, monkeypatch):
    def timeout_handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    use_transport(monkeypatch, timeout_handler)
    client = TestClient(create_app())
    resp = client.post("/api/v1/youtube/inspect", json={"url": WATCH_URL})
    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == "PROVIDER_TIMEOUT"
    assert body["platform"] == "youtube"


def test_inspect_provider_error_upstream_500(clean_env, monkeypatch):
    use_transport(monkeypatch, lambda request: httpx.Response(500, text="upstream exploded"))
    client = TestClient(create_app())
    resp = client.post("/api/v1/youtube/inspect", json={"url": WATCH_URL})
    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == "PROVIDER_ERROR"
    assert "upstream exploded" not in resp.text  # upstream bodies never leak


@pytest.mark.parametrize(
    "payload, code",
    [
        ({"url": "https://vimeo.com/123"}, "UNSUPPORTED_HOST"),
        ({"url": "https://youtube.com.evil.com/watch?v=dQw4y9WgXcQ"}, "UNSUPPORTED_HOST"),
        ({"url": "https://www.youtube.com/watch?v=short"}, "INVALID_URL"),
        ({"url": "ftp://www.youtube.com/watch?v=dQw4y9WgXcQ"}, "MALFORMED_URL"),
        ({"url": ""}, "MALFORMED_URL"),
        ({"url": "https://user:pass@www.youtube.com/watch?v=dQw4y9WgXcQ"}, "MALFORMED_URL"),
        ({}, "MALFORMED_URL"),
    ],
)
def test_inspect_validation_errors(clean_env, payload, code):
    client = TestClient(create_app())
    resp = client.post("/api/v1/youtube/inspect", json=payload)
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == code
    assert body["platform"] == "youtube"
    assert body["message"]


def test_inspect_empty_body(clean_env):
    client = TestClient(create_app())
    resp = client.post("/api/v1/youtube/inspect")
    assert resp.status_code == 400
    assert resp.json()["code"] == "MALFORMED_URL"


def test_inspect_non_json_body(clean_env):
    client = TestClient(create_app())
    resp = client.post(
        "/api/v1/youtube/inspect",
        content="this is not json",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "MALFORMED_URL"


def test_rate_limit(clean_env, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    use_transport(monkeypatch, oembed_ok)
    client = TestClient(create_app())
    statuses = [
        client.post("/api/v1/youtube/inspect", json={"url": WATCH_URL}).status_code
        for _ in range(5)
    ]
    assert statuses[:3] == [200, 200, 200]
    assert 429 in statuses[3:]
    resp = client.post("/api/v1/youtube/inspect", json={"url": WATCH_URL})
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMITED"


FAKE_KEY = "FAKE-SECRET-KEY-12345"


def data_api_ok(request):
    assert request.url.host == "www.googleapis.com"
    return httpx.Response(
        200,
        json={
            "items": [
                {
                    "snippet": {
                        "title": "Data API Title",
                        "channelTitle": "Data API Channel",
                        "thumbnails": {
                            "medium": {"url": "https://i.ytimg.com/vi/dQw4y9WgXcQ/mqdefault.jpg"},
                            "maxres": {"url": "https://i.ytimg.com/vi/dQw4y9WgXcQ/maxresdefault.jpg"},
                        },
                    },
                    "contentDetails": {"duration": "PT3M33S"},
                    "status": {"privacyStatus": "public", "embeddable": True},
                }
            ]
        },
    )


def test_inspect_success_data_api(clean_env, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", FAKE_KEY)
    use_transport(monkeypatch, data_api_ok)
    client = TestClient(create_app())
    resp = client.post("/api/v1/youtube/inspect", json={"url": WATCH_URL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["video"]["title"] == "Data API Title"
    assert body["video"]["channel"] == "Data API Channel"
    assert body["video"]["duration_seconds"] == 213
    assert body["video"]["thumbnail_url"].endswith("maxresdefault.jpg")
    assert FAKE_KEY not in resp.text


def test_inspect_data_api_empty_items_unavailable(clean_env, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", FAKE_KEY)
    use_transport(monkeypatch, lambda request: httpx.Response(200, json={"items": []}))
    client = TestClient(create_app())
    resp = client.post("/api/v1/youtube/inspect", json={"url": WATCH_URL})
    assert resp.status_code == 200
    assert resp.json()["status"] == "unavailable"


def test_api_key_never_leaks_on_upstream_error(clean_env, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", FAKE_KEY)

    def leaky_handler(request):
        # Simulate an upstream error body that echoes the key back.
        return httpx.Response(
            403,
            json={"error": {"message": f"API key {FAKE_KEY} is invalid", "status": "PERMISSION_DENIED"}},
        )

    use_transport(monkeypatch, leaky_handler)
    client = TestClient(create_app())
    resp = client.post("/api/v1/youtube/inspect", json={"url": WATCH_URL})
    assert resp.status_code == 502
    assert resp.json()["code"] == "PROVIDER_ERROR"
    assert FAKE_KEY not in resp.text
    assert "PERMISSION_DENIED" not in resp.text


def test_cors_disabled_by_default(clean_env):
    client = TestClient(create_app())
    resp = client.get("/api/v1/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in resp.headers


def test_cors_allowlist(clean_env, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://example.com")
    client = TestClient(create_app())
    resp = client.get("/api/v1/health", headers={"Origin": "https://example.com"})
    assert resp.headers.get("access-control-allow-origin") == "https://example.com"
    resp2 = client.get("/api/v1/health", headers={"Origin": "https://other.example"})
    assert "access-control-allow-origin" not in resp2.headers


def test_inspect_client_construction_failure_is_502(clean_env, monkeypatch):
    """A broken proxy environment must never produce a 500."""

    class ExplodingClient:
        def __init__(self, *args, **kwargs):
            raise httpx.InvalidURL("Invalid port: ':1]'")

    monkeypatch.setattr(httpx, "AsyncClient", ExplodingClient)
    client = TestClient(create_app(), raise_server_exceptions=False)
    resp = client.post("/api/v1/youtube/inspect", json={"url": WATCH_URL})
    assert resp.status_code == 502
    assert resp.json()["code"] == "PROVIDER_ERROR"


def test_oversized_body_rejected(clean_env):
    client = TestClient(create_app())
    resp = client.post(
        "/api/v1/youtube/inspect",
        content=b'{"url": "' + b"x" * 9000 + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "REQUEST_TOO_LARGE"
