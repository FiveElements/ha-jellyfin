"""Tests of setup, unload, removal, devices and diagnostics."""

from __future__ import annotations

from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.jellyfin_control import async_remove_config_entry_device
from custom_components.jellyfin_control.const import DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import (
    HA_DEVICE_ID,
    SERVER_ID,
    TOKEN,
    FakeJellyfin,
    connection_error,
    make_session,
    refresh,
)


async def test_setup_and_unload(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    assert loaded.state is ConfigEntryState.LOADED
    assert hass.states.get("media_player.living_room") is not None
    assert await hass.config_entries.async_unload(loaded.entry_id)
    assert loaded.state is ConfigEntryState.NOT_LOADED


async def test_setup_auth_failure_starts_reauth(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    fake.set("GET", "/Users/Me", (401, None))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_setup_not_ready(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    fake.set("GET", "/System/Info/Public", connection_error())
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_poll_failures(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.set("GET", "/Sessions", connection_error())
    await refresh(hass, loaded)
    assert hass.states.get("media_player.living_room").state == "unavailable"
    assert hass.states.get("remote.living_room_remote").state == "unavailable"
    fake.set("GET", "/Sessions", (401, None))
    await refresh(hass, loaded)
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_sessions_are_filtered_and_merged(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.sessions = [
        make_session(Id="own", DeviceId=HA_DEVICE_ID),
        make_session(Id="server", DeviceId=SERVER_ID),
        make_session(Id="old", LastActivityDate="2026-11-13T09:00:00Z"),
        make_session(Id="new", LastActivityDate="2026-11-13T11:00:00Z"),
        make_session(Id="older", LastActivityDate="2026-11-13T08:00:00Z"),
    ]
    await refresh(hass, loaded)
    data = loaded.runtime_data.coordinator.data
    assert list(data) == ["dev1"]
    assert data["dev1"]["Id"] == "new"


async def test_new_device_is_added(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.sessions.append(
        make_session(
            Id="sess2", DeviceId="dev2", DeviceName="Kitchen", NowPlayingItem=None
        )
    )
    await refresh(hass, loaded)
    assert hass.states.get("media_player.kitchen").state == "idle"
    assert hass.states.get("remote.kitchen_remote").state == "on"


async def test_remove_entry_revokes_token(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    await hass.config_entries.async_remove(loaded.entry_id)
    logout = fake.last("POST", "/Sessions/Logout")
    assert TOKEN in logout.headers["Authorization"]


async def test_remove_entry_tolerates_unreachable_server(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.set("POST", "/Sessions/Logout", connection_error())
    await hass.config_entries.async_remove(loaded.entry_id)
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_remove_stale_device(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    live = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{SERVER_ID}-dev1"), loaded.entry_id
    )
    server = device_registry.async_get_device_by_identifier(
        (DOMAIN, SERVER_ID), loaded.entry_id
    )
    assert live is not None and server is not None
    assert live.via_device_id == server.id
    stale = device_registry.async_get_or_create(
        config_entry_id=loaded.entry_id, identifiers={(DOMAIN, f"{SERVER_ID}-gone")}
    )
    assert not await async_remove_config_entry_device(hass, loaded, live)
    assert not await async_remove_config_entry_device(hass, loaded, server)
    assert await async_remove_config_entry_device(hass, loaded, stale)


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    fake: FakeJellyfin,
    loaded: MockConfigEntry,
) -> None:
    result = await get_diagnostics_for_config_entry(hass, hass_client, loaded)
    text = str(result)
    assert TOKEN not in text
    assert "192.168.1.20" not in text
    assert result["server"]["id"] == SERVER_ID
    assert result["sessions"][0]["now_playing"]["Type"] == "Episode"
