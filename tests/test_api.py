import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "30")
    return TestClient(create_app())


def test_health(client):
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "youtube-inspector"
    assert body["version"] == "1.0.0"
    # Media-tool versions are reported (validation depends on ffprobe).
    # The exact key sets are asserted so that no environment variable,
    # secret or other configuration can leak through this endpoint.
    assert set(body) == {"status", "service", "version", "tools"}
    assert set(body["tools"]) == {"ffmpeg", "ffprobe"}
    for value in body["tools"].values():
        assert value is None or isinstance(value, str)
    assert "YOUTUBE_API_KEY" not in resp.text


def test_frontend_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="url-input"' in html
    assert "YouTube Inspector" in html
    # The explanatory "About downloads" note is expected and allowed...
    assert "About downloads" in html
    # No embedded player anywhere: no iframe, no nocookie embed host.
    assert "<iframe" not in html.lower()
    assert "youtube-nocookie" not in html
    # A thumbnail download control exists...
    assert "Download thumbnail" in html
    # ...but no button may promise a video download. The thumbnail button
    # ("Download thumbnail") is the only download control allowed.
    for button_html in re.findall(r"<button.*?</button>", html, flags=re.S):
        text = re.sub(r"<[^>]+>", "", button_html).strip().lower()
        assert text != "download"
        assert "download video" not in text
        assert "download the video" not in text


def test_frontend_js_has_no_embed(client):
    resp = client.get("/app.js")
    assert resp.status_code == 200
    js = resp.text
    assert "youtube-nocookie" not in js
    assert "iframe" not in js.lower()
    # The thumbnail download goes through the backend endpoint.
    assert "/api/v1/youtube/thumbnail/" in js


def test_css_hidden_rule_overrides_display(client):
    # Regression (2026-10-22 live browser test): .loading sets display:flex,
    # which silently overrode the hidden attribute and left the spinner
    # visible after results rendered. The stylesheet must force hidden.
    resp = client.get("/styles.css")
    assert resp.status_code == 200
    assert "[hidden]" in resp.text
    assert "display: none !important" in resp.text


def test_security_headers(client):
    resp = client.get("/api/v1/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    csp = resp.headers["Content-Security-Policy"]
    # The embed was removed: no frames may be loaded at all.
    assert "frame-src 'none'" in csp
    assert "youtube-nocookie" not in csp
    # Thumbnails still display via <img> from YouTube's image host.
    assert "https://i.ytimg.com" in csp
