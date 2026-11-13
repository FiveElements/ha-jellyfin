"""Tests of the remote, select, sensor and button entities."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, mock_restore_cache

from custom_components.jellyfin_control.remote import resolve_command
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.remote import (
    ATTR_COMMAND,
    ATTR_DELAY_SECS,
    ATTR_NUM_REPEATS,
    SERVICE_SEND_COMMAND,
)
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import FakeJellyfin, make_session, refresh

REMOTE = "remote.living_room_remote"
AUDIO = "select.living_room_audio_track"
SUBTITLES = "select.living_room_subtitles"
LAST_PLAYED = "sensor.living_room_last_played"


def test_resolve_command() -> None:
    assert resolve_command(" Up ") == "MoveUp"
    assert resolve_command("ToggleStats") == "ToggleStats"


async def test_remote(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    assert hass.states.get(REMOTE).state == "on"
    await hass.services.async_call(
        "remote",
        SERVICE_SEND_COMMAND,
        {
            ATTR_ENTITY_ID: REMOTE,
            ATTR_COMMAND: ["up", "select", "play_pause"],
            ATTR_NUM_REPEATS: 2,
            ATTR_DELAY_SECS: 0.001,
        },
        blocking=True,
    )
    names = [c.body["Name"] for c in fake.calls_to("POST", "/Sessions/sess1/Command")]
    assert names == ["MoveUp", "Select", "MoveUp", "Select"]
    assert len(fake.calls_to("POST", "/Sessions/sess1/Playing/PlayPause")) == 2

    fake.sessions = []
    await refresh(hass, loaded)
    assert hass.states.get(REMOTE).state == "off"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "remote",
            SERVICE_SEND_COMMAND,
            {ATTR_ENTITY_ID: REMOTE, ATTR_COMMAND: ["up"]},
            blocking=True,
        )


async def test_no_remote_without_remote_control(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.sessions.append(
        make_session(
            Id="s2", DeviceId="dev2", DeviceName="Browser", SupportsRemoteControl=False
        )
    )
    await refresh(hass, loaded)
    assert hass.states.get("media_player.browser") is not None
    assert hass.states.get("remote.browser_remote") is None


async def test_track_selects(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    audio = hass.states.get(AUDIO)
    assert audio.state == "1: English"
    assert audio.attributes["options"] == ["1: English", "2: French"]
    subtitles = hass.states.get(SUBTITLES)
    assert subtitles.state == "off"
    assert subtitles.attributes["options"] == ["off", "3: French SRT"]

    await hass.services.async_call(
        "select",
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: AUDIO, ATTR_OPTION: "2: French"},
        blocking=True,
    )
    assert fake.last("POST", "/Sessions/sess1/Command").body == {
        "Name": "SetAudioStreamIndex",
        "Arguments": {"Index": "2"},
    }
    await hass.services.async_call(
        "select",
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SUBTITLES, ATTR_OPTION: "off"},
        blocking=True,
    )
    assert fake.last("POST", "/Sessions/sess1/Command").body["Arguments"] == {
        "Index": "-1"
    }
    await hass.services.async_call(
        "select",
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SUBTITLES, ATTR_OPTION: "3: French SRT"},
        blocking=True,
    )
    assert fake.last("POST", "/Sessions/sess1/Command").body["Arguments"] == {
        "Index": "3"
    }

    entity = hass.data["entity_components"]["select"].get_entity(AUDIO)
    with pytest.raises(ServiceValidationError):
        await entity.async_select_option("9: Klingon")


async def test_track_select_states(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    session = make_session()
    session["PlayState"].update(AudioStreamIndex=7, SubtitleStreamIndex=3)
    fake.sessions = [session]
    await refresh(hass, loaded)
    assert hass.states.get(AUDIO).state == "unknown"
    assert hass.states.get(SUBTITLES).state == "3: French SRT"

    fake.sessions = [make_session(NowPlayingItem=None)]
    await refresh(hass, loaded)
    assert hass.states.get(AUDIO).state == "unavailable"


async def test_sensors(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    assert hass.states.get("sensor.media_active_clients").state == "1"
    fake.sessions.append(
        make_session(
            Id="s2", DeviceId="dev2", DeviceName="Kitchen", NowPlayingItem=None
        )
    )
    await refresh(hass, loaded)
    assert hass.states.get("sensor.media_active_clients").state == "1"


async def test_refresh_button(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    await hass.services.async_call(
        "button",
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.media_scan_libraries"},
        blocking=True,
    )
    assert fake.calls_to("POST", "/Library/Refresh")
    fake.set("POST", "/Library/Refresh", (403, None))
    with pytest.raises(HomeAssistantError, match="administrator"):
        await hass.services.async_call(
            "button",
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: "button.media_scan_libraries"},
            blocking=True,
        )


async def test_refresh_button_disabled_for_non_admin(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    fake.is_admin = False
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity = entity_registry.async_get("button.media_scan_libraries")
    assert entity is not None and entity.disabled_by is not None


async def test_last_played(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    state = hass.states.get(LAST_PLAYED)
    assert state.state == "Show - S01E02 - Pilot"
    assert state.attributes["media_content_id"] == "ep1"
    assert state.attributes["media_content_type"] == "Episode"
    assert state.attributes["series"] == "Show"
    assert state.attributes["season"] == 1
    assert state.attributes["episode"] == 2
    started = state.attributes["played_at"]

    # Still playing: the same play, played_at does not move.
    freezer.tick(timedelta(minutes=5))
    await refresh(hass, loaded)
    assert hass.states.get(LAST_PLAYED).attributes["played_at"] == started

    # Stopped, then disconnected: the item stays.
    fake.sessions = [make_session(NowPlayingItem=None)]
    await refresh(hass, loaded)
    fake.sessions = []
    await refresh(hass, loaded)
    state = hass.states.get(LAST_PLAYED)
    assert state.state == "Show - S01E02 - Pilot"
    assert state.attributes["played_at"] == started

    # Played again after a stop: a new play.
    freezer.tick(timedelta(minutes=5))
    fake.sessions = [make_session()]
    await refresh(hass, loaded)
    assert hass.states.get(LAST_PLAYED).attributes["played_at"] != started

    movie = {"Id": "mov1", "Type": "Movie", "Name": "Matrix", "ProductionYear": 1999}
    fake.sessions = [make_session(NowPlayingItem=movie)]
    await refresh(hass, loaded)
    state = hass.states.get(LAST_PLAYED)
    assert state.state == "Matrix (1999)"
    assert state.attributes["year"] == 1999
    assert "series" not in state.attributes


async def test_last_played_long_title(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    item = {"Id": "long", "Type": "Video", "Name": "x" * 400}
    fake.sessions = [make_session(NowPlayingItem=item)]
    await refresh(hass, loaded)
    assert len(hass.states.get(LAST_PLAYED).state) == 255


async def test_last_played_restored(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    mock_restore_cache(
        hass,
        [
            State(
                LAST_PLAYED,
                "Matrix (1999)",
                {
                    "media_content_id": "mov1",
                    "media_content_type": "Movie",
                    "played_at": "2026-11-12T20:00:00+00:00",
                    "year": 1999,
                    "friendly_name": "ignored",
                },
            )
        ],
    )
    # Still playing the restored item after the restart: not a new play.
    movie = {"Id": "mov1", "Type": "Movie", "Name": "Matrix", "ProductionYear": 1999}
    fake.sessions = [make_session(NowPlayingItem=movie)]
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(LAST_PLAYED)
    assert state.state == "Matrix (1999)"
    assert state.attributes["media_content_id"] == "mov1"
    assert state.attributes["played_at"] == "2026-11-12T20:00:00+00:00"
    assert "friendly_name" in state.attributes  # its own, not the restored one
    assert state.attributes["friendly_name"] != "ignored"

    fake.sessions = []
    await refresh(hass, entry)
    assert hass.states.get(LAST_PLAYED).state == "Matrix (1999)"


async def test_last_played_unavailable_when_poll_fails(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.set("GET", "/Sessions", (500, None))
    await refresh(hass, loaded)
    assert hass.states.get(LAST_PLAYED).state == "unavailable"
