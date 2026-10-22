import pytest

from app import youtube as yt

VALID_ID = "dQw4y9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4y9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4y9WgXcQ&t=42s&si=abc123",
        "http://youtube.com/watch?v=dQw4y9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4y9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4y9WgXcQ",
        "https://youtu.be/dQw4y9WgXcQ",
        "https://youtu.be/dQw4y9WgXcQ?t=10&si=xyz",
        "https://www.youtube.com/shorts/dQw4y9WgXcQ",
        "https://www.youtube.com/shorts/dQw4y9WgXcQ?feature=share",
        "https://www.youtube.com/live/dQw4y9WgXcQ",
        "https://www.youtube.com/embed/dQw4y9WgXcQ",
        "https://www.youtube.com/v/dQw4y9WgXcQ",
        "  https://www.youtube.com/watch?v=dQw4y9WgXcQ  ",
    ],
)
def test_valid_urls(url):
    assert yt.parse_youtube_url(url) == VALID_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=tooshort",          # ID too short
        "https://www.youtube.com/watch?v=dQw4y9WgXcQZ",      # ID too long
        "https://www.youtube.com/watch?v=dQw4y9WgXc!",       # invalid character
        "https://youtu.be/dQw4y9WgXc!",                      # invalid character
        "https://www.youtube.com/shorts/dQw4y9WgX",          # too short
    ],
)
def test_invalid_video_ids(url):
    with pytest.raises(yt.InvalidURLError) as exc:
        yt.parse_youtube_url(url)
    assert exc.value.code == "INVALID_URL"


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/12345678901",
        "https://www.dailymotion.com/video/x1234",
        "https://example.com/watch?v=dQw4y9WgXcQ",
        "https://169.254.169.254/watch?v=dQw4y9WgXcQ",  # cloud metadata IP
        "https://127.0.0.1/watch?v=dQw4y9WgXcQ",        # loopback
        "https://192.168.1.1/watch?v=dQw4y9WgXcQ",      # private LAN
    ],
)
def test_unsupported_domains(url):
    with pytest.raises(yt.UnsupportedHostError) as exc:
        yt.parse_youtube_url(url)
    assert exc.value.code == "UNSUPPORTED_HOST"


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.evil.com/watch?v=dQw4y9WgXcQ",
        "https://www.youtube.com.evil.com/watch?v=dQw4y9WgXcQ",
        "https://notyoutube.com/watch?v=dQw4y9WgXcQ",
        "https://fakeyoutube.com/watch?v=dQw4y9WgXcQ",
        "https://youtube-com.example.org/watch?v=dQw4y9WgXcQ",
    ],
)
def test_lookalike_hosts_rejected(url):
    with pytest.raises(yt.UnsupportedHostError):
        yt.parse_youtube_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://www.youtube.com/watch?v=dQw4y9WgXcQ",
        "javascript://www.youtube.com/watch?v=dQw4y9WgXcQ",
        "www.youtube.com/watch?v=dQw4y9WgXcQ",  # missing scheme
        "",
        "   ",
        "not a url at all",
    ],
)
def test_malformed_urls(url):
    with pytest.raises(yt.MalformedURLError) as exc:
        yt.parse_youtube_url(url)
    assert exc.value.code == "MALFORMED_URL"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@www.youtube.com/watch?v=dQw4y9WgXcQ",
        "https://user@youtube.com/watch?v=dQw4y9WgXcQ",
        "https://www.youtube.com@evil.com/watch?v=dQw4y9WgXcQ",
    ],
)
def test_credential_urls_rejected(url):
    with pytest.raises(yt.YouTubeError):
        yt.parse_youtube_url(url)


def test_watch_without_v_param():
    with pytest.raises(yt.InvalidURLError):
        yt.parse_youtube_url("https://www.youtube.com/watch?t=30")


def test_playlist_only_url_rejected():
    with pytest.raises(yt.InvalidURLError):
        yt.parse_youtube_url("https://www.youtube.com/playlist?list=PL1234567890")


def test_parse_iso8601_duration():
    assert yt.parse_iso8601_duration("PT2M30S") == 150
    assert yt.parse_iso8601_duration("PT1H2M3S") == 3723
    assert yt.parse_iso8601_duration("PT45S") == 45
    assert yt.parse_iso8601_duration("PT1H") == 3600
    assert yt.parse_iso8601_duration("P1DT1S") == 86401
    assert yt.parse_iso8601_duration("") is None
    assert yt.parse_iso8601_duration(None) is None
    assert yt.parse_iso8601_duration("garbage") is None
