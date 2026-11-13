"""Tests of the media player: state, controls, playback and library."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.typing import ClientSessionGenerator, WebSocketGenerator

from custom_components.jellyfin_control.const import DOMAIN
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_ENQUEUE,
    ATTR_MEDIA_REPEAT,
    ATTR_MEDIA_SEEK_POSITION,
    ATTR_MEDIA_SHUFFLE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MP_DOMAIN,
    MediaPlayerEntityFeature,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import FakeJellyfin, connection_error, make_session, refresh

PLAYER = "media_player.living_room"


async def mp(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(
        MP_DOMAIN, service, {ATTR_ENTITY_ID: PLAYER, **data}, blocking=True
    )


async def jf(hass: HomeAssistant, service: str, **data: Any) -> Any:
    return await hass.services.async_call(
        DOMAIN, service, {ATTR_ENTITY_ID: PLAYER, **data}, blocking=True
    )


async def jf_response(hass: HomeAssistant, service: str, **data: Any) -> Any:
    result = await hass.services.async_call(
        DOMAIN,
        service,
        {ATTR_ENTITY_ID: PLAYER, **data},
        blocking=True,
        return_response=True,
    )
    return result[PLAYER]


async def test_playing_state(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    state = hass.states.get(PLAYER)
    assert state.state == "playing"
    attrs = state.attributes
    assert attrs["media_title"] == "Pilot"
    assert attrs["media_series_title"] == "Show"
    assert attrs["media_season"] == "1"
    assert attrs["media_episode"] == "2"
    assert attrs["media_content_type"] == "tvshow"
    assert attrs["media_content_id"] == "ep1"
    assert attrs["media_duration"] == 3600
    assert attrs["media_position"] == 60
    assert attrs["volume_level"] == 0.5
    assert attrs["is_volume_muted"] is False
    assert attrs["repeat"] == "all"
    assert attrs["shuffle"] is True
    assert attrs["app_name"] == "Jellyfin Android TV"
    assert attrs["queue_size"] == 2
    assert attrs["media_type"] == "Episode"
    assert "entity_picture" in attrs
    features = MediaPlayerEntityFeature(attrs[ATTR_SUPPORTED_FEATURES])
    for feature in (
        MediaPlayerEntityFeature.PAUSE,
        MediaPlayerEntityFeature.SEEK,
        MediaPlayerEntityFeature.VOLUME_SET,
        MediaPlayerEntityFeature.VOLUME_STEP,
        MediaPlayerEntityFeature.VOLUME_MUTE,
        MediaPlayerEntityFeature.SHUFFLE_SET,
        MediaPlayerEntityFeature.REPEAT_SET,
        MediaPlayerEntityFeature.BROWSE_MEDIA,
        MediaPlayerEntityFeature.SEARCH_MEDIA,
        MediaPlayerEntityFeature.MEDIA_ENQUEUE,
    ):
        assert feature in features


async def test_other_states(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    session = make_session()
    session["PlayState"].update(IsPaused=True, VolumeLevel=0, PositionTicks=0)
    session["NowPlayingItem"] = {
        "Id": "song1",
        "Type": "Audio",
        "Name": "Song",
        "Album": "Album",
        "AlbumArtist": "Band",
        "Artists": ["A", "B"],
        "IndexNumber": 4,
        "AlbumId": "alb1",
        "AlbumPrimaryImageTag": "atag",
    }
    fake.sessions = [session]
    await refresh(hass, loaded)
    attrs = hass.states.get(PLAYER).attributes
    assert hass.states.get(PLAYER).state == "paused"
    assert attrs["volume_level"] == 0.0
    assert attrs["media_artist"] == "A, B"
    assert attrs["media_album_artist"] == "Band"
    assert attrs["media_track"] == 4
    assert attrs["media_content_type"] == "music"

    fake.sessions = [make_session(NowPlayingItem=None)]
    await refresh(hass, loaded)
    assert hass.states.get(PLAYER).state == "idle"
    assert "media_title" not in hass.states.get(PLAYER).attributes

    fake.sessions = []
    await refresh(hass, loaded)
    state = hass.states.get(PLAYER)
    assert state.state == "off"
    features = MediaPlayerEntityFeature(state.attributes[ATTR_SUPPORTED_FEATURES])
    assert MediaPlayerEntityFeature.PAUSE not in features
    assert MediaPlayerEntityFeature.BROWSE_MEDIA in features


async def test_limited_client_features(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    session = make_session(SupportsRemoteControl=False)
    session["Capabilities"] = {"SupportedCommands": ["ToggleMute"]}
    session["SupportedCommands"] = None
    fake.sessions = [session]
    await refresh(hass, loaded)
    features = MediaPlayerEntityFeature(
        hass.states.get(PLAYER).attributes[ATTR_SUPPORTED_FEATURES]
    )
    assert MediaPlayerEntityFeature.VOLUME_MUTE in features
    assert MediaPlayerEntityFeature.PAUSE not in features
    assert MediaPlayerEntityFeature.VOLUME_SET not in features
    fake.calls.clear()
    await mp(hass, "volume_mute", **{ATTR_MEDIA_VOLUME_MUTED: True})
    assert fake.last("POST", "/Sessions/sess1/Command").body["Name"] == "ToggleMute"
    fake.calls.clear()
    await mp(hass, "volume_mute", **{ATTR_MEDIA_VOLUME_MUTED: False})
    assert not fake.calls_to("POST", "/Sessions/sess1/Command")


@pytest.mark.parametrize(
    ("service", "data", "path"),
    [
        ("media_play", {}, "Unpause"),
        ("media_pause", {}, "Pause"),
        ("media_play_pause", {}, "PlayPause"),
        ("media_stop", {}, "Stop"),
        ("media_next_track", {}, "NextTrack"),
        ("media_previous_track", {}, "PreviousTrack"),
        ("media_seek", {ATTR_MEDIA_SEEK_POSITION: 90}, "Seek"),
    ],
)
async def test_playstate_commands(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
    service: str,
    data: dict[str, Any],
    path: str,
) -> None:
    await mp(hass, service, **data)
    call = fake.last("POST", f"/Sessions/sess1/Playing/{path}")
    if path == "Seek":
        assert call.params["seekPositionTicks"] == "900000000"


@pytest.mark.parametrize(
    ("service", "data", "name", "arguments"),
    [
        ("volume_set", {ATTR_MEDIA_VOLUME_LEVEL: 0.3}, "SetVolume", {"Volume": "30"}),
        ("volume_up", {}, "VolumeUp", {}),
        ("volume_down", {}, "VolumeDown", {}),
        ("volume_mute", {ATTR_MEDIA_VOLUME_MUTED: True}, "Mute", {}),
        ("volume_mute", {ATTR_MEDIA_VOLUME_MUTED: False}, "Unmute", {}),
        (
            "shuffle_set",
            {ATTR_MEDIA_SHUFFLE: False},
            "SetShuffleQueue",
            {"ShuffleMode": "Sorted"},
        ),
        (
            "repeat_set",
            {ATTR_MEDIA_REPEAT: "one"},
            "SetRepeatMode",
            {"RepeatMode": "RepeatOne"},
        ),
    ],
)
async def test_general_commands(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
    service: str,
    data: dict[str, Any],
    name: str,
    arguments: dict[str, str],
) -> None:
    await mp(hass, service, **data)
    body = fake.last("POST", "/Sessions/sess1/Command").body
    assert body == {"Name": name, "Arguments": arguments}


async def test_play_media(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    await mp(
        hass,
        "play_media",
        **{ATTR_MEDIA_CONTENT_ID: "mov1", ATTR_MEDIA_CONTENT_TYPE: "movie"},
    )
    params = fake.last("POST", "/Sessions/sess1/Playing").params
    assert params == {"playCommand": "PlayNow", "itemIds": "mov1"}

    await mp(
        hass,
        "play_media",
        **{
            ATTR_MEDIA_CONTENT_ID: f"media-source://jellyfin_control/{loaded.entry_id}/alb1",
            ATTR_MEDIA_CONTENT_TYPE: "album",
            ATTR_MEDIA_ENQUEUE: "next",
        },
    )
    params = fake.last("POST", "/Sessions/sess1/Playing").params
    assert params == {"playCommand": "PlayNext", "itemIds": "song1"}
    query = fake.last("GET", "/Items").params
    assert query["ParentId"] == "alb1"

    with pytest.raises(ServiceValidationError):
        await mp(
            hass,
            "play_media",
            **{
                ATTR_MEDIA_CONTENT_ID: "media-source://media_source/local/a.mp3",
                ATTR_MEDIA_CONTENT_TYPE: "music",
            },
        )
    # An item of another Jellyfin server is refused, not looked up here.
    with pytest.raises(ServiceValidationError, match="own library"):
        await mp(
            hass,
            "play_media",
            **{
                ATTR_MEDIA_CONTENT_ID: "media-source://jellyfin_control/other/alb1",
                ATTR_MEDIA_CONTENT_TYPE: "album",
            },
        )
    with pytest.raises(ServiceValidationError):
        await mp(
            hass,
            "play_media",
            **{
                ATTR_MEDIA_CONTENT_ID: "media-source://jellyfin_control",
                ATTR_MEDIA_CONTENT_TYPE: "music",
            },
        )


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (
            "art1",
            {
                "ArtistIds": "art1",
                "IncludeItemTypes": "Audio",
                "SortBy": "ProductionYear,Album,ParentIndexNumber,IndexNumber,SortName",
            },
        ),
        (
            "gen1",
            {"GenreIds": "gen1", "IncludeItemTypes": "Audio", "MediaTypes": "Audio"},
        ),
        ("pl1", {"ParentId": "pl1", "IsFolder": "false"}),
        (
            "ser1",
            {"ParentId": "ser1", "SortBy": "ParentIndexNumber,IndexNumber,SortName"},
        ),
        (
            "lib_other",
            {
                "ParentId": "lib_other",
                "Recursive": "true",
                "IsFolder": "false",
                "SortBy": "Album,ParentIndexNumber,IndexNumber,SortName",
            },
        ),
    ],
)
async def test_containers_are_expanded(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
    item: str,
    expected: dict[str, str],
) -> None:
    await jf(hass, "play_items", item_ids=[item], enqueue="add")
    query = fake.last("GET", "/Items").params
    for key, value in expected.items():
        assert query[key] == value
    assert fake.last("POST", "/Sessions/sess1/Playing").params["playCommand"] == (
        "PlayLast"
    )


async def test_play_items_options(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    await jf(
        hass,
        "play_items",
        item_ids=["mov1", "ep1", "mov1"],
        start_index=1,
        start_position=30,
    )
    params = fake.last("POST", "/Sessions/sess1/Playing").params
    assert params["itemIds"] == "mov1,ep1"
    assert params["startIndex"] == "1"
    assert params["startPositionTicks"] == "300000000"

    await jf(hass, "play_items", item_ids=["songs:lib_music"], shuffle=True)
    assert fake.last("GET", "/Items").params["SortBy"] == "Random"
    await jf(hass, "play_items", item_ids=["songs:lib_music"])
    assert fake.last("GET", "/Items").params["SortBy"] != "Random"

    fake.set("GET", "/Items", (200, {"Items": []}))
    with pytest.raises(ServiceValidationError):
        await jf(hass, "play_items", item_ids=["alb1"])


async def test_play_items_shuffle_mixes_single_items(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    with patch(
        "custom_components.jellyfin_control.media_player.random.shuffle",
        side_effect=lambda ids: ids.reverse(),
    ):
        await jf(hass, "play_items", item_ids=["mov1", "ep1"], shuffle=True)
    assert fake.last("POST", "/Sessions/sess1/Playing").params["itemIds"] == "ep1,mov1"


async def test_play_items_rejects_empty_ids(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.calls.clear()
    with pytest.raises(ServiceValidationError):
        await jf(hass, "play_items", item_ids=[""])
    assert not fake.calls_to("GET", "/Items/")
    assert not fake.calls_to("POST", "/Sessions/sess1/Playing")


async def test_volume_cleared_when_not_reported(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    assert hass.states.get(PLAYER).attributes["volume_level"] == 0.5
    session = make_session()
    del session["PlayState"]["VolumeLevel"]
    fake.sessions = [session]
    await refresh(hass, loaded)
    assert "volume_level" not in hass.states.get(PLAYER).attributes


async def test_play_media_shuffle(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    await jf(hass, "play_media_shuffle", media={"media_content_id": "alb1"})
    assert fake.last("GET", "/Items").params["SortBy"] == "Random"
    assert fake.last("POST", "/Sessions/sess1/Playing").params["itemIds"] == "song1"


async def test_instant_mix(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    await jf(hass, "play_instant_mix", item_id="song1")
    assert fake.last("POST", "/Sessions/sess1/Playing").params["itemIds"] == "song1"
    fake.set("GET", "/Items/song1/InstantMix", (200, {"Items": []}))
    with pytest.raises(ServiceValidationError):
        await jf(hass, "play_instant_mix", item_id="song1")


async def test_seek_relative(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    await jf(hass, "seek_relative", seconds=-120)
    assert fake.last("POST", "/Sessions/sess1/Playing/Seek").params == {
        "seekPositionTicks": "0"
    }
    await jf(hass, "seek_relative", seconds=99999)
    assert fake.last("POST", "/Sessions/sess1/Playing/Seek").params == {
        "seekPositionTicks": "36000000000"
    }


async def test_seek_relative_counts_time_since_poll(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The polled position is stale while playing; 10 s later it is 10 s on."""
    freezer.tick(timedelta(seconds=10))
    await jf(hass, "seek_relative", seconds=5)
    ticks = int(
        fake.last("POST", "/Sessions/sess1/Playing/Seek").params["seekPositionTicks"]
    )
    # Setup ran before the clock froze: allow a second of real time.
    expected = 600_000_000 + 15 * 10_000_000
    assert expected <= ticks < expected + 10_000_000


async def test_client_ui_services(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    await jf(hass, "send_message", message="Dinner", title="HA", timeout=3)
    assert fake.last("POST", "/Sessions/sess1/Message").body == {
        "Header": "HA",
        "Text": "Dinner",
        "TimeoutMs": 3000,
    }
    await jf(hass, "send_text", text="Matrix")
    assert fake.last("POST", "/Sessions/sess1/Command").body == {
        "Name": "SendString",
        "Arguments": {"String": "Matrix"},
    }
    await jf(hass, "display_content", item_id="mov1")
    assert fake.last("POST", "/Sessions/sess1/Command").body["Arguments"] == {
        "ItemId": "mov1",
        "ItemType": "Movie",
        "ItemName": "Matrix",
    }
    await jf(hass, "send_command", command="SetVolume", arguments={"Volume": 10})
    assert fake.last("POST", "/Sessions/sess1/Command").body == {
        "Name": "SetVolume",
        "Arguments": {"Volume": "10"},
    }
    await jf(hass, "set_audio_stream", index=2)
    await jf(hass, "set_subtitle_stream", index=-1)
    assert fake.last("POST", "/Sessions/sess1/Command").body == {
        "Name": "SetSubtitleStreamIndex",
        "Arguments": {"Index": "-1"},
    }


async def test_unsupported_command_and_missing_session(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    session = make_session()
    session["Capabilities"]["SupportedCommands"] = []
    fake.sessions = [session]
    await refresh(hass, loaded)
    with pytest.raises(ServiceValidationError, match="SendString"):
        await jf(hass, "send_text", text="x")
    fake.sessions = []
    await refresh(hass, loaded)
    with pytest.raises(HomeAssistantError):
        await jf(hass, "send_command", command="GoHome")
    assert await jf_response(hass, "get_queue") == {"items": []}


@pytest.mark.parametrize(
    ("error", "exception"),
    [
        ((401, None), HomeAssistantError),
        ((403, None), HomeAssistantError),
        ((404, None), ServiceValidationError),
        (connection_error(), HomeAssistantError),
    ],
)
async def test_errors_are_translated(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
    error: Any,
    exception: type[Exception],
) -> None:
    fake.set("POST", "/Sessions/sess1/Playing/Stop", error)
    with pytest.raises(exception) as info:
        await mp(hass, "media_stop")
    assert info.value.translation_domain == DOMAIN


async def test_user_data(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    await jf(hass, "set_user_data", item_id="mov1", played=True, favorite=False)
    assert fake.calls_to("POST", "/UserPlayedItems/mov1")
    assert fake.calls_to("DELETE", "/UserFavoriteItems/mov1")


async def test_queue(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    result = await jf_response(hass, "get_queue")
    assert result["items"][0]["current"] is True
    assert result["items"][0]["title"] == "Show - S01E02 - Pilot"
    assert result["items"][1]["current"] is False
    session = make_session(PlaylistItemId=None)
    session["NowPlayingQueue"].append({"Id": "unknown"})
    fake.sessions = [session]
    await refresh(hass, loaded)
    result = await jf_response(hass, "get_queue")
    assert result["items"][0]["current"] is True
    assert result["items"][2]["title"] == "unknown"
    fake.sessions = [make_session(NowPlayingQueue=[])]
    await refresh(hass, loaded)
    assert await jf_response(hass, "get_queue") == {"items": []}


@pytest.mark.parametrize(
    ("parent", "endpoint", "count"),
    [
        (None, "/UserViews", 8),
        ("special:resume", "/UserItems/Resume", 1),
        ("special:next_up", "/Shows/NextUp", 1),
        ("special:latest", "/Items/Latest", 1),
        ("special:favorites", "/Artists", 3),
        ("ser1", "/Shows/ser1/Seasons", 1),
        ("sea1", "/Shows/ser1/Episodes", 2),
        ("art1", "/Items", 2),
        ("alb1", "/Items", 1),
        ("pl1", "/Items", 2),
        ("lib_music", "/Items/lib_music", 3),
        ("artists:lib_music", "/Artists/AlbumArtists", 1),
        ("albums:lib_music", "/Items", 2),
        ("songs:lib_music", "/Items", 1),
        ("lib_tv", "/LiveTv/Channels", 1),
        ("lib_movies", "/Items", 1),
        ("lib_shows", "/Items", 2),
        ("lib_other", "/Items", 2),
    ],
)
async def test_browse_library(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
    parent: str | None,
    endpoint: str,
    count: int,
) -> None:
    data = {"parent_id": parent} if parent else {}
    result = await jf_response(hass, "browse_library", **data)
    assert fake.calls_to("GET", endpoint)
    assert len(result["items"]) == count


async def test_browse_summaries(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    result = await jf_response(hass, "browse_library", parent_id="lib_movies")
    assert result["not_shown"] == 2
    movie = result["items"][0]
    assert movie["title"] == "Matrix (1999)"
    assert movie["duration"] == 8160
    assert movie["favorite"] is True
    assert movie["can_play"] is True
    result = await jf_response(hass, "browse_library", parent_id="alb1")
    assert result["items"][0]["artists"] == ["Artist"]
    assert result["items"][0]["title"] == "1. Song"


async def test_browse_library_limit(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    """Answers meant for agents stay small; what is cut is counted."""
    result = await jf_response(hass, "browse_library", limit=2)
    assert len(result["items"]) == 2
    full = await jf_response(hass, "browse_library", limit=1000)
    assert result["not_shown"] == len(full["items"]) - 2


async def test_resume_lists_videos_only(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    """Without MediaTypes the server also listed series and seasons."""
    await jf_response(hass, "browse_library", parent_id="special:resume")
    assert fake.last("GET", "/UserItems/Resume").params["MediaTypes"] == "Video"


async def test_search_library(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    result = await jf_response(hass, "search_library", query="ma")
    assert [i["id"] for i in result["items"]] == ["art1", "mov1", "song1"]
    result = await jf_response(
        hass, "search_library", query="ma", media_types=["movie"], limit=1
    )
    assert fake.last("GET", "/Items").params["IncludeItemTypes"] == "Movie"
    assert len(result["items"]) == 1
    fake.calls.clear()
    result = await jf_response(
        hass, "search_library", query="ma", media_types=["MusicArtist"]
    )
    assert fake.calls_to("GET", "/Artists") and not fake.calls_to("GET", "/Items")
    assert await jf_response(hass, "search_library", query="  ") == {"items": []}


async def test_media_browser(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "media_player/browse_media", "entity_id": PLAYER}
    )
    response = await client.receive_json()
    assert response["success"]
    root = response["result"]
    assert root["title"] == "Media"
    assert root["can_search"] is True
    titles = [c["title"] for c in root["children"]]
    assert titles[:4] == ["Continue watching", "Next up", "Recently added", "Favorites"]

    await client.send_json_auto_id(
        {
            "type": "media_player/browse_media",
            "entity_id": PLAYER,
            "media_content_id": "lib_movies",
            "media_content_type": "library",
        }
    )
    response = await client.receive_json()
    movie = response["result"]["children"][0]
    assert movie["can_play"] is True
    assert movie["thumbnail"].startswith("/api/media_player_proxy/")
    assert response["result"]["not_shown"] == 2

    await client.send_json_auto_id(
        {
            "type": "media_player/browse_media",
            "entity_id": PLAYER,
            "media_content_id": "media-source://jellyfin_control",
            "media_content_type": "library",
        }
    )
    assert (await client.receive_json())["result"]["title"] == "Media"

    await client.send_json_auto_id(
        {
            "type": "media_player/search_media",
            "entity_id": PLAYER,
            "search_query": "ma",
            "media_filter_classes": ["movie"],
        }
    )
    response = await client.receive_json()
    assert response["success"], response
    assert [r["title"] for r in response["result"]["result"]] == [
        "Matrix (1999)",
        "1. Song",
    ]
    assert fake.last("GET", "/Items").params["IncludeItemTypes"] == "Movie"


def test_search_types() -> None:
    from custom_components.jellyfin_control.library import search_types

    assert search_types(None)[1] is True
    assert search_types(["artist", "bogus", "track", "music"]) == (["Audio"], True)


async def test_images(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
) -> None:
    client = await hass_client()
    picture = hass.states.get(PLAYER).attributes["entity_picture"]
    response = await client.get(picture)
    assert response.status == 200
    assert await response.read() == fake.image
    image_call = fake.last("GET", "/Items/ep1/Images/Primary")
    assert image_call.params["tag"] == "tag1"

    entity = hass.data["entity_components"]["media_player"].get_entity(PLAYER)
    assert entity.media_image_url.endswith(
        "/Items/ep1/Images/Primary?maxWidth=600&tag=tag1"
    )
    assert await entity.async_get_browse_image(
        "movie", "mov1", "mov1/Backdrop/bd1"
    ) == (
        fake.image,
        "image/png",
    )
    assert await entity.async_get_browse_image("movie", "mov1", "bad") == (None, None)
    assert await entity.async_get_browse_image("movie", "mov1") == (None, None)
    fake.set("GET", "/Items/mov1/Images/Backdrop", (404, None))
    assert await entity.async_get_browse_image(
        "movie", "mov1", "mov1/Backdrop/bd1"
    ) == (None, None)

    fake.sessions = [make_session(NowPlayingItem={"Id": "x", "Type": "Movie"})]
    await refresh(hass, loaded)
    assert "entity_picture" not in hass.states.get(PLAYER).attributes
    assert entity.media_image_url is None
    assert await entity.async_get_media_image() == (None, None)


def test_image_fallbacks() -> None:
    from custom_components.jellyfin_control.library import ImageRef, image_ref

    episode = {"Id": "e", "SeriesId": "s", "SeriesPrimaryImageTag": "st"}
    assert image_ref(episode) == ImageRef("s", "Primary", "st")
    clip = {"Id": "c", "ParentBackdropItemId": "p", "ParentBackdropImageTags": ["b"]}
    assert image_ref(clip) == ImageRef("p", "Backdrop", "b")
    assert image_ref({"Id": "x"}) is None
    assert ImageRef.parse("a/Primary/") == ImageRef("a", "Primary", None)
