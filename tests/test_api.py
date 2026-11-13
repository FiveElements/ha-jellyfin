"""Tests of the REST client, without Home Assistant entities."""

from __future__ import annotations

import pytest

from custom_components.jellyfin_control.api import (
    AuthResult,
    JellyfinApiError,
    JellyfinAuthError,
    JellyfinClient,
    JellyfinConnectionError,
    JellyfinForbiddenError,
    JellyfinNotFoundError,
    QuickConnectRequest,
    normalize_url,
)

from .conftest import (
    BASE_URL,
    TOKEN,
    USER_ID,
    FakeJellyfin,
    FakeSession,
    connection_error,
    timeout_error,
)


def client(server: FakeJellyfin, url: str = BASE_URL, **kwargs: str) -> JellyfinClient:
    return JellyfinClient(
        FakeSession(server),  # type: ignore[arg-type]
        url,
        device_id="dev",
        device_name='Home "Assistant", main',
        client_name="Home Assistant",
        client_version="2026.11.0",
        **kwargs,
    )


def test_normalize_url() -> None:
    assert normalize_url(" jellyfin.local:8096/ ") == "http://jellyfin.local:8096"
    assert normalize_url("https://x/jf/") == "https://x/jf"


def test_authorization_header_is_sanitized() -> None:
    c = client(FakeJellyfin(), access_token='to"ken,')
    header = c.authorization()
    assert 'Device="Home Assistant main"' in header
    assert header.endswith('Token="token"')
    assert 'to"ken' not in repr(c) and "token" not in repr(c)


def test_secrets_are_not_in_repr() -> None:
    assert TOKEN not in repr(AuthResult(TOKEN, USER_ID, "u"))
    assert "s3cret" not in repr(QuickConnectRequest("s3cret", "1", False))


def test_path_segments_cannot_escape() -> None:
    url = client(FakeJellyfin()).build_url("Items", "../System/Restart")
    assert url.raw_path == "/Items/..%2FSystem%2FRestart"
    url = client(FakeJellyfin()).build_url("Items", "..", ".")
    assert url.raw_path == "/Items/%2E%2E/%2E"
    url = client(FakeJellyfin()).build_url("Items", params={"a": True, "b": 2})
    assert url.query_string == "a=true&b=2"


async def test_locate_server_finds_base_path() -> None:
    server = FakeJellyfin(base_path="/jellyfin")
    c = client(server)
    info = await c.locate_server()
    assert info.name == "Media"
    assert c.url == BASE_URL + "/jellyfin"


async def test_locate_server_restores_url_on_failure() -> None:
    server = FakeJellyfin(base_path="/elsewhere")
    c = client(server)
    with pytest.raises(JellyfinNotFoundError):
        await c.locate_server()
    assert c.url == BASE_URL


async def test_locate_server_skips_present_base_path() -> None:
    server = FakeJellyfin()
    c = client(server, url=BASE_URL + "/jellyfin")
    with pytest.raises(JellyfinNotFoundError):
        await c.locate_server()
    # /jellyfin/jellyfin is never tried.
    assert len(server.calls) == 1


async def test_public_info_without_id() -> None:
    server = FakeJellyfin()
    server.set("GET", "/System/Info/Public", (200, {"ServerName": "no id"}))
    with pytest.raises(JellyfinConnectionError):
        await client(server).public_info()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, JellyfinAuthError),
        (403, JellyfinForbiddenError),
        (404, JellyfinNotFoundError),
        (302, JellyfinConnectionError),
        (500, JellyfinApiError),
    ],
)
async def test_status_mapping(status: int, error: type[Exception]) -> None:
    server = FakeJellyfin()
    server.set("GET", "/Users/Me", (status, None))
    with pytest.raises(error):
        await client(server, access_token=TOKEN).current_user()


@pytest.mark.parametrize("exc", [connection_error(), timeout_error()])
async def test_transport_errors(exc: Exception) -> None:
    server = FakeJellyfin()
    server.set("GET", "/Users/Me", exc)
    with pytest.raises(JellyfinConnectionError):
        await client(server).current_user()


async def test_non_json_and_wrong_shapes() -> None:
    server = FakeJellyfin()
    c = client(server, user_id=USER_ID)
    server.set("GET", "/Users/Me", (200, ["not", "an", "object"]))
    with pytest.raises(JellyfinConnectionError):
        await c.current_user()
    server.set("GET", "/Sessions", (200, {"not": "a list"}))
    with pytest.raises(JellyfinConnectionError):
        await c.sessions()
    server.set("GET", "/Items/Latest", (200, {}))
    with pytest.raises(JellyfinConnectionError):
        await c.latest_items({})
    server.set("POST", "/Users/AuthenticateByName", (200, {"AccessToken": ""}))
    with pytest.raises(JellyfinConnectionError):
        await c.authenticate("u", "p")
    server.set("POST", "/Users/AuthenticateByName", (200, None))
    with pytest.raises(JellyfinConnectionError):
        await c.authenticate("u", "p")
    server.set("POST", "/QuickConnect/Initiate", (200, {"Code": "1"}))
    with pytest.raises(JellyfinConnectionError):
        await c.quick_connect_initiate()


async def test_invalid_json_text() -> None:
    server = FakeJellyfin()
    c = client(server)

    class Raw(FakeSession):
        def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            response = super().request(*args, **kwargs)
            response._raw = b"<html>proxy</html>"
            return response

    c._session = Raw(server)  # type: ignore[assignment]
    with pytest.raises(JellyfinConnectionError):
        await c.public_info()


async def test_user_required() -> None:
    with pytest.raises(JellyfinAuthError):
        await client(FakeJellyfin()).sessions()


async def test_sessions_and_items_filter_garbage() -> None:
    server = FakeJellyfin()
    server.sessions = [{"Id": "a"}, {"no": "id"}, "junk"]  # type: ignore[list-item]
    server.set("GET", "/UserViews", (200, {"Items": [{"Id": "x"}, 3]}))
    c = client(server, access_token=TOKEN, user_id=USER_ID)
    assert await c.sessions(active_within=60) == [{"Id": "a"}]
    assert server.last("GET", "/Sessions").params["activeWithinSeconds"] == "60"
    assert await c.user_views() == [{"Id": "x"}]
    server.set("GET", "/UserViews", (200, {}))
    assert await c.user_views() == []


async def test_quick_connect_falls_back_to_get_on_405() -> None:
    server = FakeJellyfin()
    server.set("POST", "/QuickConnect/Initiate", (405, None))
    request = await client(server).quick_connect_initiate()
    assert request.code == "123456"
    assert server.calls_to("GET", "/QuickConnect/Initiate")
    server.set("POST", "/QuickConnect/Initiate", (500, None))
    with pytest.raises(JellyfinApiError):
        await client(server).quick_connect_initiate()


async def test_images() -> None:
    server = FakeJellyfin()
    c = client(server, access_token=TOKEN)
    assert await c.image("i", "Primary", "t", 100, 100) == (server.image, "image/png")
    assert await c.image("i", "Primary", None, 100, 3) is None
    server.set("GET", "/Items/i/Images/Primary", (404, None))
    assert await c.image("i", "Primary", None, 100, 100) is None
    server.set("GET", "/Items/i/Images/Primary", connection_error())
    with pytest.raises(JellyfinConnectionError):
        await c.image("i", "Primary", None, 100, 100)


async def test_image_larger_than_announced() -> None:
    server = FakeJellyfin(image=b"x" * 10)
    c = client(server)

    class Lying(FakeSession):
        def request(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            response = super().request(*args, **kwargs)
            response.content_length = 0
            return response

    c._session = Lying(server)  # type: ignore[assignment]
    assert await c.image("i", "Primary", None, 100, 5) is None


def test_stream_urls_carry_token() -> None:
    c = client(FakeJellyfin(), access_token=TOKEN, user_id=USER_ID)
    audio = c.audio_stream_url("song1", "mp3")
    assert "/Audio/song1/universal" in audio and f"api_key={TOKEN}" in audio
    assert "AudioCodec=mp3" in audio
    video = c.video_stream_url("mov1", "ms2")
    assert "static=true" in video and "MediaSourceId=ms2" in video
    assert "MediaSourceId" not in c.video_stream_url("mov1", None)


async def test_user_data_endpoints() -> None:
    server = FakeJellyfin()
    c = client(server, access_token=TOKEN, user_id=USER_ID)
    await c.set_played("mov1", True)
    await c.set_played("mov1", False)
    await c.set_favorite("mov1", True)
    await c.set_favorite("mov1", False)
    assert [x.method for x in server.calls_to("POST", "/UserPlayedItems/mov1")]
    assert server.calls_to("DELETE", "/UserFavoriteItems/mov1")
    await c.logout()
    assert server.calls_to("POST", "/Sessions/Logout")
