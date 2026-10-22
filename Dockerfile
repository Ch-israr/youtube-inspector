FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# FFmpeg/ffprobe from the Debian package manager — media-processing and
# output-validation infrastructure only (see README "Media tooling").
# FFmpeg is not a YouTube extractor and never fetches anything.
# Licensing: Debian's ffmpeg package is a GPL-enabled build. The binary
# ships only inside this service image and is not redistributed outside
# it; the corresponding Debian source is available from the Debian
# archive, which satisfies the build's distribution obligations for this
# use. We do not vendor FFmpeg source into this repository.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Fail the build if the media tools are absent or broken.
RUN ffmpeg -version && ffprobe -version

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
