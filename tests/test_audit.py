"""Regression tests for the defects found by the code audit."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.jellyfin_control import async_remove_config_entry_device
from custom_components.jellyfin_control.api import READ_CHUNK_SIZE as CHUNK_SIZE
from custom_components.jellyfin_control.const import (
    CONF_USER_ID,
    DOMAIN,
    OPTION_SCAN_INTERVAL,
)
from custom_components.jellyfin_control.library import ImageRef
from custom_components.jellyfin_control.media_source import mime_type
from custom_components.jellyfin_control.views import signed_image_path, stream_path
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import (
    ITEMS,
    SERVER_ID,
    TOKEN,
    USER_ID,
    FakeJellyfin,
    connection_error,
    make_session,
    refresh,
)

PLAYER = "media_player.living_room"


async def jf(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(
        DOMAIN, service, {ATTR_ENTITY_ID: PLAYER, **data}, blocking=True
    )


# -- devices and entities -------------------------------------------------


async def test_offline_client_reads_off_after_reload(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.sessions = []
    await hass.config_entries.async_reload(loaded.entry_id)
    await hass.async_block_till_done()
    # Rebuilt from the registry: off, not a restored "unavailable".
    assert hass.states.get(PLAYER).state == "off"
    assert hass.states.get("remote.living_room_remote").state == "off"
    assert hass.states.get("select.living_room_audio_track").state == "unavailable"
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier(
        (DOMAIN, f"{SERVER_ID}-dev1"), loaded.entry_id
    )
    # The offline entity did not rename the device to its key.
    assert device is not None and device.name == "Living room"

    fake.sessions = [make_session()]
    await refresh(hass, loaded)
    assert hass.states.get(PLAYER).state == "playing"


async def test_deleted_device_comes_back(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.sessions = []
    await refresh(hass, loaded)
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier(
        (DOMAIN, f"{SERVER_ID}-dev1"), loaded.entry_id
    )
    assert device is not None
    assert await async_remove_config_entry_device(hass, loaded, device)
    devices.async_remove_device(device.id)
    await hass.async_block_till_done()
    assert hass.states.get(PLAYER) is None

    fake.sessions = [make_session()]
    await refresh(hass, loaded)
    assert hass.states.get(PLAYER).state == "playing"
    assert hass.states.get("select.living_room_subtitles") is not None


async def test_remove_device_while_not_loaded(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    devices = dr.async_get(hass)
    live = devices.async_get_device_by_identifier(
        (DOMAIN, f"{SERVER_ID}-dev1"), loaded.entry_id
    )
    server = devices.async_get_device_by_identifier(
        (DOMAIN, SERVER_ID), loaded.entry_id
    )
    await hass.config_entries.async_unload(loaded.entry_id)
    assert loaded.state is ConfigEntryState.NOT_LOADED
    assert await async_remove_config_entry_device(hass, loaded, live)
    assert not await async_remove_config_entry_device(hass, loaded, server)


async def test_sessions_sensor_disabled_by_default(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    entity = er.async_get(hass).async_get("sensor.media_connected_clients")
    assert (
        entity is not None
        and entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    )


async def test_verify_ssl_reaches_the_session(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "verify_ssl": False}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert fake.sessions_requested == [False]
    assert fake.last("GET", "/Sessions").params["controllableByUserId"] == USER_ID


async def test_scan_interval_option(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={OPTION_SCAN_INTERVAL: 42})
    assert await hass.config_entries.async_setup(entry.entry_id)
    coordinator = entry.runtime_data.coordinator
    assert coordinator.update_interval == timedelta(seconds=42)


# -- media player -----------------------------------------------------------


async def test_position_timestamp_follows_resume(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    session = make_session()
    session["PlayState"]["IsPaused"] = True
    fake.sessions = [session]
    await refresh(hass, loaded)
    paused_at = hass.states.get(PLAYER).attributes["media_position_updated_at"]

    resumed = make_session()
    fake.sessions = [resumed]
    with patch(
        "custom_components.jellyfin_control.media_player.dt_util.utcnow",
        return_value=paused_at + timedelta(minutes=10),
    ):
        await refresh(hass, loaded)
    attrs = hass.states.get(PLAYER).attributes
    # Same position, but playing again: the timestamp moved with the resume,
    # so the frontend does not add the whole pause to the position.
    assert attrs["media_position"] == 60
    assert attrs["media_position_updated_at"] == paused_at + timedelta(minutes=10)


async def test_no_client_address_in_attributes(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    attrs = hass.states.get(PLAYER).attributes
    assert "remote_end_point" not in attrs
    assert "192.168.1.20" not in str(attrs)


async def test_artwork_failure_is_a_clean_miss(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.set("GET", "/Items/ep1/Images/Primary", connection_error())
    entity = hass.data["entity_components"]["media_player"].get_entity(PLAYER)
    assert await entity.async_get_media_image() == (None, None)


async def test_queue_cap(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    ids = [f"x{i}" for i in range(250)]

    async def single(self: Any, item_id: str, *, shuffle: bool = False) -> list[str]:
        return [item_id]

    with patch(
        "custom_components.jellyfin_control.library.Library.playable_ids", single
    ):
        await jf(hass, "play_items", item_ids=ids)
    sent = fake.last("POST", "/Sessions/sess1/Playing").params["itemIds"].split(",")
    assert sent == ids[:200]


async def test_long_queue_is_looked_up_in_chunks(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.sessions = [
        make_session(NowPlayingQueue=[{"Id": f"q{i}"} for i in range(250)])
    ]
    await refresh(hass, loaded)
    fake.calls.clear()
    result = await hass.services.async_call(
        DOMAIN,
        "get_queue",
        {ATTR_ENTITY_ID: PLAYER},
        blocking=True,
        return_response=True,
    )
    lookups = fake.calls_to("GET", "/Items")
    assert [len(c.params["Ids"].split(",")) for c in lookups] == [100, 100, 50]
    assert len(result[PLAYER]["items"]) == 250


@pytest.mark.parametrize(
    ("service", "data", "command"),
    [
        ("send_message", {"message": "x"}, "DisplayMessage"),
        ("display_content", {"item_id": "mov1"}, "DisplayContent"),
        ("set_audio_stream", {"index": 1}, "SetAudioStreamIndex"),
        ("set_subtitle_stream", {"index": 1}, "SetSubtitleStreamIndex"),
    ],
)
async def test_unadvertised_commands_are_refused(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
    service: str,
    data: dict[str, Any],
    command: str,
) -> None:
    session = make_session()
    session["Capabilities"]["SupportedCommands"].remove(command)
    fake.sessions = [session]
    await refresh(hass, loaded)
    fake.calls.clear()
    with pytest.raises(ServiceValidationError, match=command):
        await jf(hass, service, **data)
    assert not [c for c in fake.calls if c.method == "POST"]


async def test_set_user_data_directions(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.calls.clear()
    await jf(hass, "set_user_data", item_id="mov1", played=False, favorite=True)
    assert [(c.method, c.path) for c in fake.calls if "User" in c.path] == [
        ("DELETE", "/UserPlayedItems/mov1"),
        ("POST", "/UserFavoriteItems/mov1"),
    ]


# -- remote and select ----------------------------------------------------


@pytest.mark.parametrize("service", ["turn_on", "turn_off", "toggle"])
async def test_remote_power_is_refused(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry, service: str
) -> None:
    with pytest.raises(ServiceValidationError) as info:
        await hass.services.async_call(
            "remote",
            service,
            {ATTR_ENTITY_ID: "remote.living_room_remote"},
            blocking=True,
        )
    assert info.value.translation_key == "power_unsupported"


async def test_select_needs_the_command(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    session = make_session()
    session["Capabilities"]["SupportedCommands"].remove("SetAudioStreamIndex")
    fake.sessions = [session]
    await refresh(hass, loaded)
    assert hass.states.get("select.living_room_audio_track").state == "unavailable"
    assert hass.states.get("select.living_room_subtitles").state == "off"


async def test_missing_tracks_are_fetched_once(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    ITEMS["ep1"]["MediaStreams"] = [
        {"Type": "Audio", "Index": 1, "DisplayTitle": "English"},
        {"Type": "Subtitle", "Index": 2, "DisplayTitle": "French"},
    ]
    try:
        session = make_session()
        del session["NowPlayingItem"]["MediaStreams"]
        fake.sessions = [session]
        fake.calls.clear()
        await refresh(hass, loaded)
        await refresh(hass, loaded)
        assert len(fake.calls_to("GET", "/Items/ep1")) == 1
        assert hass.states.get("select.living_room_subtitles").attributes[
            "options"
        ] == ["off", "2: French"]

        # A failed lookup leaves the selects without options, not broken.
        fake.set("GET", "/Items/ep2", connection_error())
        other = make_session()
        other["NowPlayingItem"] = {"Id": "ep2", "Type": "Episode", "MediaType": "Video"}
        fake.sessions = [other]
        await refresh(hass, loaded)
        assert hass.states.get("select.living_room_audio_track").state == "unavailable"
        coordinator = loaded.runtime_data.coordinator
        assert set(coordinator._streams) == {"ep2"}
    finally:
        del ITEMS["ep1"]["MediaStreams"]


# -- config flow ------------------------------------------------------------


async def test_reauth_as_another_user_is_refused(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_USER_ID: "someone-else"}
    )
    with patch("custom_components.jellyfin_control.async_setup_entry"):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "password"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "jerome", CONF_PASSWORD: "pw"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_USER_ID] == "someone-else"
    # The token minted for the wrong account is revoked at once.
    assert TOKEN in fake.last("POST", "/Sessions/Logout").headers["Authorization"]


async def test_reauth_revoke_tolerates_errors(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_USER_ID: "someone-else"}
    )
    fake.set("POST", "/Sessions/Logout", connection_error())
    with patch("custom_components.jellyfin_control.async_setup_entry"):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "password"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: "jerome", CONF_PASSWORD: "pw"}
        )
    assert result["reason"] == "wrong_account"


async def test_diagnostics_hide_user_id(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
) -> None:
    result = await get_diagnostics_for_config_entry(hass, hass_client, loaded)
    assert USER_ID not in str(result)


# -- relay views ------------------------------------------------------------


def test_mime_types() -> None:
    assert mime_type({"MediaType": "Audio"}, "flac") == "audio/flac"
    assert mime_type({"MediaType": "Audio"}, "bogus") == "audio/mpeg"
    assert mime_type({"MediaType": "Video", "Container": "mkv"}) == "video/x-matroska"


async def test_image_view(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
) -> None:
    client = await hass_client()
    path = signed_image_path(hass, loaded.entry_id, ImageRef("mov1", "Primary", "t1"))
    response = await client.get(path)
    assert response.status == 200
    assert await response.read() == fake.image
    assert fake.last("GET", "/Items/mov1/Images/Primary").params["tag"] == "t1"

    untagged = signed_image_path(hass, loaded.entry_id, ImageRef("mov1", "Thumb", None))
    assert (await client.get(untagged)).status == 200
    assert "tag" not in fake.last("GET", "/Items/mov1/Images/Thumb").params

    fake.set("GET", "/Items/mov1/Images/Primary", (404, None))
    assert (await client.get(path)).status == 404
    fake.set("GET", "/Items/mov1/Images/Primary", connection_error())
    assert (await client.get(path)).status == 502
    bad_type = f"/api/jellyfin_control/{loaded.entry_id}/image/mov1/../x"
    assert (await client.get(bad_type)).status == 404
    other = "/api/jellyfin_control/nope/image/mov1/Primary/-"
    assert (await client.get(other)).status == 404


async def test_views_require_auth(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
) -> None:
    client = await hass_client_no_auth()
    path = stream_path(loaded.entry_id, "mov1")
    assert (await client.get(path)).status == 401


async def test_stream_view(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
) -> None:
    client = await hass_client()
    video = stream_path(loaded.entry_id, "mov1")
    response = await client.get(video)
    assert response.status == 200
    assert await response.read() == fake.stream
    assert response.headers["Content-Type"] == "video/x-matroska"
    upstream = fake.last("GET", "/Videos/mov1/stream")
    assert upstream.params["MediaSourceId"] == "ms2"
    assert TOKEN in upstream.headers["Authorization"]
    assert "api_key" not in upstream.params

    response = await client.get(video, headers={"Range": "bytes=4-"})
    assert response.status == 206
    assert await response.read() == fake.stream[4:]
    assert response.headers["Content-Range"] == "bytes 4-9/10"

    response = await client.get(video, headers={"Range": "bytes=99-"})
    assert response.status == 416

    audio = await client.get(stream_path(loaded.entry_id, "song1"))
    assert audio.status == 200
    assert audio.headers["Content-Type"] == "audio/mpeg"
    assert fake.last("GET", "/Audio/song1/universal").params["AudioCodec"] == "mp3"


async def test_stream_view_errors(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
) -> None:
    client = await hass_client()
    assert (await client.get(stream_path(loaded.entry_id, "missing"))).status == 404
    assert (await client.get(stream_path("nope", "mov1"))).status == 404
    fake.set("GET", "/Items/mov1", connection_error())
    assert (await client.get(stream_path(loaded.entry_id, "mov1"))).status == 502
    fake.overrides.clear()
    fake.set("GET", "/Videos/mov1/stream", (500, None))
    assert (await client.get(stream_path(loaded.entry_id, "mov1"))).status == 502
    fake.set("GET", "/Videos/mov1/stream", connection_error())
    assert (await client.get(stream_path(loaded.entry_id, "mov1"))).status == 502


async def test_stream_interrupted_mid_way(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
) -> None:
    # Larger than one relayed chunk, so the cut leaves bytes missing.
    fake.stream = b"x" * (CHUNK_SIZE + 10)
    fake.stream_error = aiohttp.ClientPayloadError("cut")
    client = await hass_client()
    response = await client.get(stream_path(loaded.entry_id, "mov1"))
    # Headers were already sent: the player gets a short body and a closed
    # connection instead of waiting forever for the missing bytes.
    assert response.status == 200
    with pytest.raises(aiohttp.ClientPayloadError):
        await response.read()
