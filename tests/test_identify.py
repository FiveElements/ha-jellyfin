"""Telling apart clients that report the same name (every browser is "Chrome")."""

from __future__ import annotations

import base64
import time
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.jellyfin_control.const import DOMAIN
from custom_components.jellyfin_control.coordinator import (
    _lookup_host_name,
    local_address,
)
from custom_components.jellyfin_control.useragent import (
    browser_info,
    client_browser,
    user_agent_from_device_id,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import SERVER_ID, FakeJellyfin, make_session, refresh

WINDOWS_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)
COORDINATOR = "custom_components.jellyfin_control.coordinator"


def web_device_id(user_agent: str, stamp: str = "1793398697178") -> str:
    """Encode a device id the way jellyfin-web does ("=" becomes "1")."""
    raw = f"{user_agent}|{stamp}".encode()
    return base64.b64encode(raw).decode().replace("=", "1")


def web_session(device_id: str, **overrides: Any) -> dict[str, Any]:
    session = make_session(
        Id="web1",
        DeviceId=device_id,
        DeviceName="Chrome",
        Client="Jellyfin Web",
        ApplicationVersion="10.11.2",
        RemoteEndPoint="192.168.1.30",
    )
    session.update(overrides)
    return session


@pytest.mark.parametrize("stamp", ["1", "12", "123", "1707867140504"])
def test_device_id_decodes_whatever_the_padding(stamp: str) -> None:
    device_id = web_device_id(WINDOWS_CHROME, stamp)
    assert user_agent_from_device_id(device_id) == WINDOWS_CHROME


@pytest.mark.parametrize("device_id", ["dev1", "", "28cc424b21fc0f91754", "!!!"])
def test_other_device_ids_are_not_browsers(device_id: str) -> None:
    assert client_browser(device_id) is None


@pytest.mark.parametrize(
    ("user_agent", "system", "label"),
    [
        (WINDOWS_CHROME, "Windows", "Chrome 156"),
        ("Mozilla/5.0 (X11; Linux x86_64) Chrome/121.0", "Linux", "Chrome 121"),
        ("Mozilla/5.0 (Linux; Android 14) Chrome/130.0", "Android", "Chrome 130"),
        ("Mozilla/5.0 (Windows NT 10.0) Chrome/140 Edg/140.0", "Windows", "Edge 140"),
        ("Mozilla/5.0 (X11; Linux; rv:140.0) Firefox/140.0", "Linux", "Firefox 140"),
        (
            "Mozilla/5.0 (Intel Mac OS X 10_15) Version/18.1 Safari",
            "macOS",
            "Safari 18",
        ),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 18) Version/18.1", "iOS", "Safari 18"),
        ("Mozilla/5.0 (Unknown)", None, None),
    ],
)
def test_browser_info(user_agent: str, system: str | None, label: str | None) -> None:
    info = browser_info(user_agent)
    assert (info.system, info.label) == (system, label)


def test_local_address() -> None:
    assert local_address("192.168.1.30") == "192.168.1.30"
    assert local_address("::ffff:10.0.0.5") == "10.0.0.5"
    assert local_address("fe80::1") == "fe80::1"
    # A public address is never sent to a DNS server.
    assert local_address("8.8.8.8") is None
    assert local_address("not an ip") is None
    assert local_address(None) is None


def test_lookup_host_name() -> None:
    target = f"{COORDINATOR}.socket.gethostbyaddr"
    with patch(target, return_value=("pc-bureau.home.loc", [], [])):
        assert _lookup_host_name("192.168.1.30") == "pc-bureau.home.loc"
    with patch(target, return_value=("192.168.1.30", [], [])):
        assert _lookup_host_name("192.168.1.30") is None
    with patch(target, side_effect=OSError("no PTR")):
        assert _lookup_host_name("192.168.1.30") is None


async def test_browsers_are_named_after_their_machine(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    entry: MockConfigEntry,
    fake_reverse_dns: dict[str, str],
) -> None:
    device_id = web_device_id(WINDOWS_CHROME)
    fake.sessions = [web_session(device_id)]
    fake_reverse_dns["192.168.1.30"] = "pc-bureau.home.loc"
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, f"{SERVER_ID}-{device_id}")}
    )
    assert device is not None
    assert device.name == "Chrome · pc-bureau"
    assert device.model == "Jellyfin Web · Chrome 156 · Windows"
    state = hass.states.get("media_player.chrome_pc_bureau")
    assert state is not None
    assert state.attributes["host_name"] == "pc-bureau.home.loc"
    assert "remote_address" not in state.attributes
    assert state.attributes["operating_system"] == "Windows"
    assert state.attributes["browser"] == "Chrome 156"


async def test_registry_follows_the_machine_and_keeps_user_names(
    hass: HomeAssistant,
    fake: FakeJellyfin,
    entry: MockConfigEntry,
    fake_reverse_dns: dict[str, str],
) -> None:
    device_id = web_device_id(WINDOWS_CHROME)
    fake.sessions = [web_session(device_id)]
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    identifiers = {(DOMAIN, f"{SERVER_ID}-{device_id}")}
    device = registry.async_get_device(identifiers=identifiers)
    # No DNS name: the operating system tells it apart.
    assert device is not None and device.name == "Chrome · Windows"

    # The name appears later, once the cached miss has expired.
    fake_reverse_dns["192.168.1.30"] = "salon.home.loc"
    entry.runtime_data.coordinator._host_names.clear()
    await refresh(hass, entry)
    device = registry.async_get_device(identifiers=identifiers)
    assert device is not None and device.name == "Chrome · salon"

    # A name chosen by the user is kept; the version follows the app.
    registry.async_update_device(device.id, name_by_user="Bureau")
    fake.sessions = [web_session(device_id, ApplicationVersion="10.11.3")]
    await refresh(hass, entry)
    device = registry.async_get_device(identifiers=identifiers)
    assert device is not None
    assert device.name_by_user == "Bureau"
    assert device.sw_version == "10.11.3"


async def test_public_address_is_not_looked_up(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    fake.sessions = [make_session(RemoteEndPoint="203.0.113.7")]
    with patch(f"{COORDINATOR}._lookup_host_name") as lookup:
        await refresh(hass, loaded)
    lookup.assert_not_called()


async def test_slow_reverse_dns_does_not_hold_the_poll(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    def slow(address: str) -> str:
        time.sleep(0.2)
        return "late"

    coordinator = loaded.runtime_data.coordinator
    fake.sessions = [make_session(RemoteEndPoint="192.168.1.99")]
    with (
        patch(f"{COORDINATOR}.HOST_NAME_TIMEOUT", 0.01),
        patch(f"{COORDINATOR}._lookup_host_name", side_effect=slow),
    ):
        await refresh(hass, loaded)
    assert coordinator.host_name(fake.sessions[0]) is None
