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
    assert body == {"status": "ok", "service": "youtube-inspector", "version": "1.0.0"}


def test_frontend_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="url-input"' in html
    assert "YouTube Inspector" in html
    # The explanatory "About downloads" note is expected and allowed...
    assert "About downloads" in html
    # ...but no button may offer a download action.
    for button_html in re.findall(r"<button.*?</button>", html, flags=re.S):
        assert "download" not in button_html.lower()


def test_security_headers(client):
    resp = client.get("/api/v1/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "youtube-nocookie.com" in resp.headers["Content-Security-Policy"]
