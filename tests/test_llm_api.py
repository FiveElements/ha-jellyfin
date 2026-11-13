"""Tests of the LLM API (Assist agents and the MCP server)."""

from __future__ import annotations

from collections.abc import AsyncIterator
import copy
from typing import Any

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.jellyfin_control.llm_api import (
    LLM_API_ID,
    Player,
    ToolError,
    normalize,
    resolve_player,
    track_index,
)
from homeassistant.components.homeassistant.exposed_entities import (
    async_expose_entity,
)
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.helpers import entity_registry as er, llm
from homeassistant.setup import async_setup_component

from .conftest import ITEMS, FakeJellyfin, make_session, refresh

PLAYER = "media_player.living_room"
SEEK = "/Sessions/sess1/Playing/Seek"
COMMAND = "/Sessions/sess1/Command"
TICKS = 10_000_000


@pytest.fixture
async def assist(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> AsyncIterator[MockConfigEntry]:
    """Set up exposure (as Assist does), then the integration."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry


def context() -> llm.LLMContext:
    """Return the context of an Assist request."""
    return llm.LLMContext(
        platform="test",
        context=Context(),
        language="en",
        assistant="conversation",
        device_id=None,
    )


async def tool(hass: HomeAssistant, name: str, **args: Any) -> llm.ToolResult:
    """Call a tool of the Jellyfin Control API."""
    # APIInstance.async_call_tool records a conversation trace, which needs
    # hassil (not in the test harness): call the tool itself.
    llm_context = context()
    api = await llm.async_get_api(hass, LLM_API_ID, llm_context)
    (found,) = [t for t in api.tools if t.name == name]
    tool_input = llm.ToolInput(tool_name=name, tool_args=args)
    result = await found.async_call(hass, tool_input, llm_context)
    assert isinstance(result, llm.ToolResult)
    return result


async def ok(hass: HomeAssistant, name: str, **args: Any) -> Any:
    """Call a tool that must succeed."""
    result = await tool(hass, name, **args)
    assert not result.error, result.data
    return result.data


async def error(hass: HomeAssistant, name: str, **args: Any) -> str:
    """Call a tool that must fail, and return its message."""
    result = await tool(hass, name, **args)
    assert result.error, result.data
    return str(result.data["error"])


def with_chapters() -> dict[str, Any]:
    """Return the playing episode with four chapters."""
    item = copy.deepcopy(ITEMS["ep1"])
    item["Chapters"] = [
        {"StartPositionTicks": 0, "Name": "Intro"},
        {"StartPositionTicks": 30 * TICKS, "Name": "Part 1"},
        {"StartPositionTicks": 120 * TICKS, "Name": "Part 2"},
        {"StartPositionTicks": 300 * TICKS, "Name": "Credits"},
        {"Name": "No start"},
    ]
    return item


async def test_api_registered(hass: HomeAssistant, assist: MockConfigEntry) -> None:
    api = await llm.async_get_api(hass, LLM_API_ID, context())
    names = {t.name for t in api.tools}
    assert {
        "jellyfin_get_players",
        "jellyfin_set_subtitles",
        "jellyfin_seek",
        "jellyfin_seek_chapter",
        "jellyfin_get_chapters",
        "jellyfin_scan_library",
    } <= names
    assert all(t.integration == "jellyfin_control" for t in api.tools)
    assert "Living room (media_player.living_room)" in api.api_prompt
    read_only = {t.name for t in api.tools if t.annotations.read_only}
    assert read_only == {
        "jellyfin_get_players",
        "jellyfin_search",
        "jellyfin_browse",
        "jellyfin_get_queue",
        "jellyfin_get_chapters",
    }


async def test_get_players(hass: HomeAssistant, assist: MockConfigEntry) -> None:
    data = await ok(hass, "jellyfin_get_players")
    (player,) = data["players"]
    assert player["entity_id"] == PLAYER
    assert player["state"] == "playing"
    assert player["media_title"] == "Pilot"
    assert "seek" in player["features"]
    assert player["subtitle_tracks"] == {
        "current": "off",
        "options": ["off", "3: French SRT"],
    }
    assert player["audio_tracks"]["current"] == "1: English"
    assert player["last_played"]["title"] == "Show - S01E02 - Pilot"
    assert "friendly_name" not in player["last_played"]


async def test_subtitles(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    for track, index in (("off", "-1"), ("français", "3"), ("3", "3"), ("aucun", "-1")):
        await ok(hass, "jellyfin_set_subtitles", track=track)
        assert fake.last("POST", COMMAND).body == {
            "Name": "SetSubtitleStreamIndex",
            "Arguments": {"Index": index},
        }
    assert "Tracks" in await error(hass, "jellyfin_set_subtitles", track="klingon")
    assert "Tracks" in await error(hass, "jellyfin_set_subtitles", track="9")

    fake.sessions = [make_session(NowPlayingItem=None)]
    await refresh(hass, assist)
    message = await error(hass, "jellyfin_set_subtitles", track="off")
    assert "Nothing with selectable tracks" in message


async def test_audio_track(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    await ok(hass, "jellyfin_set_audio_track", track="Francais")
    assert fake.last("POST", COMMAND).body == {
        "Name": "SetAudioStreamIndex",
        "Arguments": {"Index": "2"},
    }
    await ok(hass, "jellyfin_set_audio_track", track="english")
    assert fake.last("POST", COMMAND).body["Arguments"] == {"Index": "1"}
    await ok(hass, "jellyfin_set_audio_track", track="2")
    assert fake.last("POST", COMMAND).body["Arguments"] == {"Index": "2"}
    # Audio has no "off".
    assert "Tracks" in await error(hass, "jellyfin_set_audio_track", track="off")


async def test_seek(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    await ok(hass, "jellyfin_seek", position=90)
    assert fake.last("POST", SEEK).params == {"seekPositionTicks": str(90 * TICKS)}
    await ok(hass, "jellyfin_seek", offset=-120)
    assert fake.last("POST", SEEK).params == {"seekPositionTicks": "0"}
    await ok(hass, "jellyfin_seek", offset=30)
    ticks = int(fake.last("POST", SEEK).params["seekPositionTicks"])
    assert 90 * TICKS <= ticks < 92 * TICKS
    assert "position or an offset" in await error(hass, "jellyfin_seek")


async def test_chapters(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    fake.set("GET", "/Items/ep1", (200, with_chapters()))
    data = await ok(hass, "jellyfin_get_chapters")
    assert data["current"] == 1
    assert data["position"] == 60
    assert data["chapters"][2] == {"index": 2, "name": "Part 2", "start": 120}
    assert len(data["chapters"]) == 4

    await ok(hass, "jellyfin_seek_chapter", chapter="next")
    assert fake.last("POST", SEEK).params == {"seekPositionTicks": str(120 * TICKS)}
    await ok(hass, "jellyfin_seek_chapter", chapter="Previous")
    assert fake.last("POST", SEEK).params == {"seekPositionTicks": "0"}
    await ok(hass, "jellyfin_seek_chapter", chapter="3")
    assert fake.last("POST", SEEK).params == {"seekPositionTicks": str(300 * TICKS)}
    for chapter in ("4", "-1", "middle"):
        await error(hass, "jellyfin_seek_chapter", chapter=chapter)

    fake.set("GET", "/Items/ep1", (200, ITEMS["ep1"]))
    assert (await ok(hass, "jellyfin_get_chapters"))["current"] is None
    await error(hass, "jellyfin_seek_chapter", chapter="next")

    fake.sessions = [make_session(NowPlayingItem=None)]
    await refresh(hass, assist)
    await error(hass, "jellyfin_get_chapters")


async def test_last_chapter_has_no_next(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    session = make_session()
    session["PlayState"]["PositionTicks"] = 400 * TICKS
    fake.sessions = [session]
    await refresh(hass, assist)
    fake.set("GET", "/Items/ep1", (200, with_chapters()))
    assert (await ok(hass, "jellyfin_get_chapters"))["current"] == 3
    await error(hass, "jellyfin_seek_chapter", chapter="next")


async def test_playback_volume_shuffle(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    await ok(hass, "jellyfin_playback", action="pause")
    assert fake.calls_to("POST", "/Sessions/sess1/Playing/Pause")
    await ok(hass, "jellyfin_playback", action="next")
    assert fake.calls_to("POST", "/Sessions/sess1/Playing/NextTrack")

    await ok(hass, "jellyfin_volume", level=30, mute=False)
    assert fake.last("POST", COMMAND).body == {
        "Name": "SetVolume",
        "Arguments": {"Volume": "30"},
    }
    await ok(hass, "jellyfin_volume", step="up")
    assert fake.last("POST", COMMAND).body["Name"] == "VolumeUp"
    await ok(hass, "jellyfin_volume", step="down")
    assert fake.last("POST", COMMAND).body["Name"] == "VolumeDown"
    await error(hass, "jellyfin_volume")

    await ok(hass, "jellyfin_shuffle_repeat", shuffle=False, repeat="one")
    assert fake.last("POST", COMMAND).body == {
        "Name": "SetRepeatMode",
        "Arguments": {"RepeatMode": "RepeatOne"},
    }
    await error(hass, "jellyfin_shuffle_repeat")


async def test_library_tools(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    data = await ok(hass, "jellyfin_search", query="matrix", media_types=["Movie"])
    assert data["items"]
    assert fake.last("GET", "/Items").params["IncludeItemTypes"] == "Movie"
    assert (await ok(hass, "jellyfin_browse"))["items"]
    data = await ok(hass, "jellyfin_browse", parent_id="lib_movies", limit=5)
    assert data["id"] == "lib_movies"
    assert len((await ok(hass, "jellyfin_get_queue"))["items"]) == 2

    await ok(hass, "jellyfin_play", item_ids=["mov1"], start_position=10)
    params = fake.last("POST", "/Sessions/sess1/Playing").params
    assert params["itemIds"] == "mov1"
    assert params["startPositionTicks"] == str(10 * TICKS)
    await ok(hass, "jellyfin_instant_mix", item_id="song1")

    await ok(hass, "jellyfin_set_user_data", item_id="mov1", played=True)
    assert fake.calls_to("POST", "/UserPlayedItems/mov1")
    await ok(hass, "jellyfin_set_user_data", item_id="mov1", favorite=False)
    assert fake.calls_to("DELETE", "/UserFavoriteItems/mov1")
    await error(hass, "jellyfin_set_user_data", item_id="mov1")

    await ok(hass, "jellyfin_scan_library")
    assert fake.calls_to("POST", "/Library/Refresh")


async def test_client_ui_tools(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    await ok(hass, "jellyfin_show_message", message="Dinner", title="Hi")
    assert fake.last("POST", "/Sessions/sess1/Message").body["Text"] == "Dinner"
    await ok(hass, "jellyfin_navigate", commands=["up", "select"], repeats=2)
    names = [c.body["Name"] for c in fake.calls_to("POST", COMMAND)]
    assert names == ["MoveUp", "Select", "MoveUp", "Select"]
    await ok(hass, "jellyfin_send_text", text="matrix")
    assert fake.last("POST", COMMAND).body["Name"] == "SendString"
    await ok(hass, "jellyfin_display_item", item_id="mov1")
    assert fake.last("POST", COMMAND).body["Name"] == "DisplayContent"


async def test_service_errors_reach_the_model(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    session = make_session()
    session["Capabilities"]["SupportedCommands"] = []
    fake.sessions = [session]
    await refresh(hass, assist)
    # The client does not advertise DisplayMessage.
    await error(hass, "jellyfin_show_message", message="Dinner")
    # No remote control: no remote entity.
    fake.sessions = [make_session(SupportsRemoteControl=False)]
    await refresh(hass, assist)
    entity_registry = er.async_get(hass)
    entity_registry.async_remove("remote.living_room_remote")
    await hass.async_block_till_done()
    assert "has no remote" in await error(hass, "jellyfin_navigate", commands=["up"])


async def test_scan_disabled(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    er.async_get(hass).async_update_entity(
        "button.media_scan_libraries", disabled_by=er.RegistryEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert "administrator" in await error(hass, "jellyfin_scan_library")


async def test_player_selection(
    hass: HomeAssistant, fake: FakeJellyfin, assist: MockConfigEntry
) -> None:
    er.async_get(hass).async_update_entity(PLAYER, aliases=["Jellyfin TV", None])
    fake.sessions.append(
        make_session(
            Id="sess2", DeviceId="dev2", DeviceName="Kitchen", NowPlayingItem=None
        )
    )
    await refresh(hass, assist)

    await ok(hass, "jellyfin_playback", player="jellyfin tv", action="pause")
    assert fake.calls_to("POST", "/Sessions/sess1/Playing/Pause")
    await ok(hass, "jellyfin_playback", player="Kitchen", action="stop")
    assert fake.calls_to("POST", "/Sessions/sess2/Playing/Stop")
    # Both connected: the player must be named.
    assert "Several players" in await error(hass, "jellyfin_playback", action="stop")
    assert "No player matches" in await error(
        hass, "jellyfin_playback", player="bedroom", action="stop"
    )

    # Not exposed to Assist: out of reach.
    async_expose_entity(hass, "conversation", "media_player.kitchen", False)
    await ok(hass, "jellyfin_playback", action="stop")
    assert len((await ok(hass, "jellyfin_get_players"))["players"]) == 1
    async_expose_entity(hass, "conversation", PLAYER, False)
    assert "No Jellyfin player" in await error(hass, "jellyfin_playback", action="stop")
    assert "No Jellyfin player" in await error(hass, "jellyfin_search", query="x")


def player(entity_id: str, state: str, *, area: str | None = None) -> Player:
    """Return a player for the resolution tests."""
    name = entity_id.split(".")[1].replace("_", " ").title()
    return Player(entity_id, name, (), area, None, State(entity_id, state))


def test_resolve_player() -> None:
    salon = player("media_player.salon_tv", "playing", area="Salon")
    old = player("media_player.salon_chrome", "off", area="Salon")
    bureau = player("media_player.bureau", "idle", area="Bureau")
    assert resolve_player([salon, old, bureau], "salon") is salon
    assert resolve_player([salon, bureau], "BUREAU") is bureau
    assert resolve_player([old], None) is old
    with pytest.raises(ToolError, match="several players"):
        resolve_player(
            [salon, bureau, player("media_player.b2", "idle", area="Bureau")], "bureau"
        )
    with pytest.raises(ToolError):
        resolve_player([], "salon")


def test_track_index() -> None:
    options = ["off", "3: French SRT", "4: English - Default"]
    assert track_index(options, "Désactivé", subtitles=True) == -1
    assert track_index(options, "anglais", subtitles=True) == 4
    assert normalize(" Français ") == "francais"
