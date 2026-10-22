# YouTube Inspector

A compliant YouTube URL inspector: paste a YouTube link and get its title,
channel, thumbnail and duration — plus a standalone, versioned API that any
website can integrate with.

**Video downloading is deliberately not offered.** YouTube provides no
permitted way for third-party services to download videos, and YouTube's
Developer Policies prohibit API clients from downloading, storing or
enabling offline playback of YouTube audiovisual content without prior
written approval. `capabilities.download` is always `false`, and the UI
never offers a video download.

The one file the service *does* deliver is the video's **thumbnail image**
(a JPG still image, not the video) via
`GET /api/v1/youtube/thumbnail/{video_id}` — see below. Inspect reports this
as `capabilities.thumbnail_download: true`.

## Compliance basis

- Metadata comes **only** from official endpoints:
  - **YouTube Data API v3** when `YOUTUBE_API_KEY` is configured (title,
    channel, best thumbnail, duration, availability).
  - **YouTube's official oEmbed endpoint** when no key is configured (title,
    channel, thumbnail; duration is not provided by oEmbed and is returned
    as `null`).
- There is **no embedded player**: the result page shows the video's
  thumbnail image only — nothing is framed, played back or copied.
- Thumbnail files are fetched only from YouTube's fixed image host
  (`i.ytimg.com`) and served straight from memory (details below).
- No scraping of youtube.com pages, no yt-dlp or any extractor, no cookies,
  no passwords, no session tokens — ever.
- Unavailable/private/deleted videos are reported honestly as
  `"status": "unavailable"` instead of producing fake results.

## Quick start (local)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

With Docker:

```bash
docker build -t youtube-inspector .
docker run --rm -p 8000:8000 youtube-inspector
```

## API

### `GET /api/v1/health`

```bash
curl https://<your-service>/api/v1/health
```

```json
{"status": "ok", "service": "youtube-inspector", "version": "1.0.0"}
```

### `POST /api/v1/youtube/inspect`

```bash
curl -X POST https://<your-service>/api/v1/youtube/inspect \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.youtube.com/watch?v=dQw4y9WgXcQ"}'
```

Response (200):

```json
{
  "platform": "youtube",
  "status": "ok",
  "video": {
    "id": "dQw4y9WgXcQ",
    "canonical_url": "https://www.youtube.com/watch?v=dQw4y9WgXcQ",
    "title": "…",
    "channel": "…",
    "thumbnail_url": "https://i.ytimg.com/vi/dQw4y9WgXcQ/maxresdefault.jpg",
    "duration_seconds": 213
  },
  "capabilities": {"metadata": true, "preview": true, "download": false, "thumbnail_download": true},
  "actions": ["inspect", "preview"],
  "download_note": "YouTube does not provide a permitted way for third-party services to download videos; downloading is not offered."
}
```

For an unavailable video the response is still HTTP 200 with
`"status": "unavailable"` and `null` metadata fields — the inspection itself
succeeded.

Validation errors (HTTP 400) and other failures use a consistent shape:

```json
{"code": "UNSUPPORTED_HOST", "message": "…", "platform": "youtube"}
```

Error codes: `MALFORMED_URL`, `INVALID_URL`, `UNSUPPORTED_HOST`,
`INVALID_VIDEO_ID` (400), `THUMBNAIL_UNAVAILABLE` (404), `RATE_LIMITED`
(429), `REQUEST_TOO_LARGE` (413), `PROVIDER_TIMEOUT` (502),
`PROVIDER_ERROR` (502). Upstream error bodies are never forwarded — they can
contain sensitive details — and API keys never appear in responses or logs.

Interactive documentation is served at `/api/docs` (OpenAPI).

### `GET /api/v1/youtube/thumbnail/{video_id}`

Downloads the video's **thumbnail image** (a JPG still image — not the
video).

```bash
curl -OJ https://<your-service>/api/v1/youtube/thumbnail/dQw4y9WgXcQ
# saves youtube-dQw4y9WgXcQ-thumbnail.jpg
```

- `{video_id}` must match `^[A-Za-z0-9_-]{11}$`, otherwise HTTP 400
  `INVALID_VIDEO_ID`.
- The server fetches `https://i.ytimg.com/vi/{id}/maxresdefault.jpg` from
  YouTube's fixed image host only, falling back to `hqdefault.jpg` on a
  404. If both are missing: HTTP 404 `THUMBNAIL_UNAVAILABLE`.
- Success: HTTP 200, `Content-Type: image/jpeg`,
  `Content-Disposition: attachment; filename="youtube-{id}-thumbnail.jpg"`,
  and a correct `Content-Length`.
- Upstream bodies are capped at **2 MB** (an oversized body aborts with
  502 `PROVIDER_ERROR`; nothing partial is served). Upstream timeout (8 s)
  → 502 `PROVIDER_TIMEOUT`; other upstream failures → 502 `PROVIDER_ERROR`.
  Upstream error bodies are never forwarded.
- The same per-IP rate limit as `/inspect` applies (429 `RATE_LIMITED`).

**No-storage design:** the image is streamed through memory and never
written to disk. There is no temp file to expire, clean up, or leak between
users — the no-storage approach exceeds the usual short-lived-file
requirement by never creating a file at all.

### Accepted URL formats

`youtube.com` / `www.youtube.com` / `m.youtube.com` / `music.youtube.com` /
`youtu.be` with `/watch?v=ID`, `/shorts/ID`, `/live/ID`, `/embed/ID`,
`/v/ID` or `youtu.be/ID`. Hosts are matched against an exact allow-list;
lookalike hosts, credential-embedded URLs and non-HTTP(S) schemes are
rejected. The server only ever calls the two fixed official endpoints above
with a validated video ID, which structurally prevents SSRF through this API.

## Integrating from another website

- **Same-origin (default):** serve the API and your frontend from one
  origin (as this repo does — FastAPI serves the static frontend). No CORS
  configuration is needed and no secrets exist in browser code.
- **Cross-origin:** set `ALLOWED_ORIGINS` to a comma-separated allow-list of
  your frontend origins. The API then emits CORS headers for exactly those
  origins. Credentials mode is never enabled, and `"*"` is never used.
- **Future shared frontend:** the API is platform-modular — YouTube logic
  lives in `app/youtube.py` behind `parse_*` / `inspect_*` functions, so
  additional platform APIs can be added under `/api/v1/<platform>/…` with
  the same response conventions (`platform`, `status`, `video`,
  `capabilities`, structured errors) without touching the YouTube service.

## Configuration

| Variable               | Default | Purpose                                                                 |
|------------------------|---------|-------------------------------------------------------------------------|
| `YOUTUBE_API_KEY`      | unset   | Enables the Data API v3 metadata path. Unset → oEmbed fallback.        |
| `ALLOWED_ORIGINS`      | unset   | Comma-separated CORS allow-list. Unset → same-origin only.              |
| `RATE_LIMIT_PER_MINUTE`| `30`    | Per-IP token-bucket limit on the inspect and thumbnail endpoints.       |
| `PORT`                 | `8000`  | Listen port (set automatically by Render).                              |

Copy `.env.example` to `.env` for local configuration; never commit `.env`.

Getting a key (optional): Google Cloud Console → enable *YouTube Data API
v3* → create an API key. The default 10,000 units/day quota costs 1 unit per
inspection (`videos.list`).

## Deployment (Render)

`render.yaml` defines a free Docker web service with health checks on
`/api/v1/health`. Import the repo in Render (Blueprint) or create a Docker
web service pointing at the `Dockerfile`, then set `YOUTUBE_API_KEY` and
`ALLOWED_ORIGINS` in the dashboard (both are declared `sync: false`, so no
secret is ever stored in the repo).

### Why no NGINX?

Render's edge already terminates TLS and reverse-proxies to the service.
Adding an NGINX process inside the container would consume part of the free
tier's 512 MB, add a second failure mode, and provide nothing the platform
doesn't already provide. Header hardening, body-size limits and rate
limiting are implemented in the application instead (see `app/main.py`).

### Why a static frontend with no build step?

The frontend is plain HTML/CSS/vanilla JS served by FastAPI. That keeps the
deployment a single small Python service: no Node toolchain, no build
minutes, no extra memory on the free tier, and the frontend stays trivially
replaceable because all logic lives behind the API contract.

## Security

- Exact host allow-list + strict video-ID validation; userinfo/`@` tricks
  and lookalike hosts rejected.
- The server never fetches user-supplied URLs — outbound calls go only to
  `www.googleapis.com` / `www.youtube.com/oembed` for metadata and
  `i.ytimg.com` for thumbnails, always with a validated ID.
- 8 KB request-body cap, 8 s upstream timeouts, 2 MB thumbnail cap,
  per-IP rate limiting.
- Security headers on every response (`nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, and a CSP with `frame-src 'none'` that only allows
  `i.ytimg.com` images beyond the site's own assets).
- No secrets in code, logs, or error responses; non-root Docker user.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

The suite covers URL parsing (valid formats, lookalike hosts, schemes,
credentials), the API contract with **mocked** upstream responses (success,
unavailable, timeout, upstream error, rate limiting, API-key non-leakage),
the thumbnail endpoint with a mocked image host (success, maxres→hq
fallback, unavailable, invalid ID, timeout, oversized body, rate limiting),
health, CORS and the frontend (including asserting there is no embedded
player and no button offers a *video* download — the only download control
is "Download thumbnail"). Unit tests make no live network calls.

## Maintenance

- Dependencies are pinned in `requirements.txt`. To update: bump versions,
  run the full test suite, and verify `/api/v1/health` plus one inspect call
  on a staging deploy before promoting.
- The free Render tier sleeps after inactivity; the first request after a
  nap can take ~30–60 s (cold start). The health check keeps configuration
  honest, not the service awake.
