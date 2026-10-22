"""YouTube Inspector — FastAPI application.

A compliant YouTube URL inspector: metadata and official embedded previews
only, served from official YouTube endpoints. No downloading, no extraction,
no scraping.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import youtube as yt

SERVICE_NAME = "youtube-inspector"
SERVICE_VERSION = "1.0.0"
PLATFORM = "youtube"

MAX_BODY_BYTES = 8 * 1024  # 8 KB cap on API request bodies

STATIC_DIR = Path(__file__).parent / "static"


class InspectRequest(BaseModel):
    url: str


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "platform": PLATFORM},
    )


class TokenBucketLimiter:
    """Simple in-memory per-IP token bucket (refills continuously)."""

    def __init__(self, limit_per_minute: int):
        self.capacity = max(1, limit_per_minute)
        self.rate = self.capacity / 60.0  # tokens per second
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(float(self.capacity), tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return True
            self._buckets[key] = (tokens, now)
            return False


def _allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS", "").strip()
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app() -> FastAPI:
    app = FastAPI(title="YouTube Inspector", version=SERVICE_VERSION)

    origins = _allowed_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )

    limiter = TokenBucketLimiter(int(os.environ.get("RATE_LIMIT_PER_MINUTE", "30")))

    @app.middleware("http")
    async def security_and_limits(request: Request, call_next):
        # Request-body size cap for API routes.
        if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            if length is not None:
                try:
                    if int(length) > MAX_BODY_BYTES:
                        return _error(413, "REQUEST_TOO_LARGE", "Request body is too large.")
                except ValueError:
                    return _error(400, "MALFORMED_REQUEST", "Invalid Content-Length header.")
            else:
                body = await request.body()
                if len(body) > MAX_BODY_BYTES:
                    return _error(413, "REQUEST_TOO_LARGE", "Request body is too large.")

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' https://i.ytimg.com data:; "
            "frame-src https://www.youtube-nocookie.com; "
            "style-src 'self'; script-src 'self'; "
            "base-uri 'self'; form-action 'self'"
        )
        return response

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}

    @app.post("/api/v1/youtube/inspect")
    async def inspect(request: Request):
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.allow(client_ip):
            return _error(429, "RATE_LIMITED", "Too many requests. Please wait a moment and try again.")

        try:
            payload = await request.json()
        except Exception:
            return _error(400, "MALFORMED_URL", "Request body must be JSON with a 'url' field.")
        if not isinstance(payload, dict) or "url" not in payload:
            return _error(400, "MALFORMED_URL", "Request body must be JSON with a 'url' field.")

        try:
            video_id = yt.parse_youtube_url(payload.get("url"))
        except yt.YouTubeError as exc:
            return _error(400, exc.code, exc.message)

        try:
            result = await yt.inspect_video(video_id)
        except yt.ProviderTimeoutError as exc:
            return _error(502, exc.code, exc.message)
        except yt.ProviderError as exc:
            return _error(502, exc.code, exc.message)
        except Exception:
            # Absolute safety net: an unexpected provider-side failure must
            # surface as a structured 502, never a 500 or a stack trace.
            return _error(502, "PROVIDER_ERROR", "YouTube's service couldn't answer just now.")

        return {
            "platform": PLATFORM,
            "status": result["status"],
            "video": result["video"],
            "capabilities": {"metadata": True, "preview": True, "download": False},
            "actions": ["inspect", "preview"],
            "download_note": yt.DOWNLOAD_NOTE,
        }

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()
