"""Tests of the config, reauth, reconfigure and options flows."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.jellyfin_control.const import (
    CONF_ACCESS_TOKEN,
    CONF_AUDIO_CODEC,
    CONF_SERVER_ID,
    CONF_USER_ID,
    DOMAIN,
    OPTION_SCAN_INTERVAL,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import (
    BASE_URL,
    SERVER_ID,
    TOKEN,
    USER_ID,
    FakeJellyfin,
    connection_error,
)


@pytest.fixture(autouse=True)
def no_setup():
    """Do not set the entry up after the flow creates it."""
    with patch(
        "custom_components.jellyfin_control.async_setup_entry", return_value=None
    ):
        yield


async def _start(hass: HomeAssistant, url: str = "jellyfin.local:8096"):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: url, CONF_VERIFY_SSL: True}
    )


async def test_password_flow(hass: HomeAssistant, fake: FakeJellyfin) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "password"}
    )
    assert result["step_id"] == "password"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "jerome", CONF_PASSWORD: "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "jerome", CONF_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Media"
    data = result["data"]
    assert data[CONF_URL] == BASE_URL
    assert data[CONF_ACCESS_TOKEN] == TOKEN
    assert data[CONF_USER_ID] == USER_ID
    assert data[CONF_SERVER_ID] == SERVER_ID
    assert CONF_PASSWORD not in data
    assert result["result"].unique_id == SERVER_ID


async def test_password_errors(hass: HomeAssistant, fake: FakeJellyfin) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "password"}
    )
    fake.set("POST", "/Users/AuthenticateByName", connection_error())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "u", CONF_PASSWORD: "pw"}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    fake.set("POST", "/Users/AuthenticateByName", RuntimeError("boom"))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "u", CONF_PASSWORD: "pw"}
    )
    assert result["errors"] == {"base": "unknown"}


async def test_server_errors(hass: HomeAssistant, fake: FakeJellyfin) -> None:
    fake.set("GET", "/System/Info/Public", connection_error())
    result = await _start(hass)
    assert result["errors"] == {"base": "cannot_connect"}
    fake.set("GET", "/System/Info/Public", RuntimeError("boom"))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: BASE_URL}
    )
    assert result["errors"] == {"base": "unknown"}


async def test_base_path_is_kept(hass: HomeAssistant, fake: FakeJellyfin) -> None:
    fake.base_path = "/jellyfin"
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "password"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "u", CONF_PASSWORD: "pw"}
    )
    assert result["data"][CONF_URL] == BASE_URL + "/jellyfin"


async def test_already_configured_updates_url(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_URL: "old"})
    result = await _start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_URL] == BASE_URL


async def test_quick_connect_flow(hass: HomeAssistant, fake: FakeJellyfin) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_connect"}
    )
    assert result["step_id"] == "quick_connect"
    assert result["description_placeholders"]["code"] == "123456"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "quick_connect_pending"}
    fake.set("GET", "/QuickConnect/Connect", (404, None))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "quick_connect_expired"}
    fake.overrides.clear()
    fake.quick_connect_authenticated = True
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACCESS_TOKEN] == TOKEN


async def test_quick_connect_disabled(hass: HomeAssistant, fake: FakeJellyfin) -> None:
    fake.set("GET", "/QuickConnect/Enabled", (200, False))
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_connect"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "quick_connect_disabled"


async def test_quick_connect_errors(hass: HomeAssistant, fake: FakeJellyfin) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "quick_connect"}
    )
    fake.set("GET", "/QuickConnect/Connect", (401, None))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "invalid_auth"}
    fake.set("GET", "/QuickConnect/Enabled", connection_error())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_reauth(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_ACCESS_TOKEN: "revoked"}
    )
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "password"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "jerome", CONF_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_ACCESS_TOKEN] == TOKEN


async def test_reauth_other_server(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_SERVER_ID: "other"}
    )
    result = await entry.start_reauth_flow(hass)
    assert result["reason"] == "unique_id_mismatch"


async def test_reauth_unreachable(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    fake.set("GET", "/System/Info/Public", connection_error())
    result = await entry.start_reauth_flow(hass)
    assert result["reason"] == "cannot_connect"


async def test_reconfigure(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    fake.set("GET", "/System/Info/Public", connection_error())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: "http://jellyfin.local:8096", CONF_VERIFY_SSL: False},
    )
    assert result["errors"] == {"base": "cannot_connect"}
    fake.overrides.clear()
    fake.base_path = "/jellyfin"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: "http://jellyfin.local:8096", CONF_VERIFY_SSL: False},
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_URL] == BASE_URL + "/jellyfin"
    assert entry.data[CONF_VERIFY_SSL] is False


async def test_reconfigure_other_server(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    fake.set("GET", "/System/Info/Public", (200, {"Id": "other", "ServerName": "x"}))
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: BASE_URL}
    )
    assert result["reason"] == "unique_id_mismatch"


async def test_options(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPTION_SCAN_INTERVAL: 10.0, CONF_AUDIO_CODEC: "mp3"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {OPTION_SCAN_INTERVAL: 10, CONF_AUDIO_CODEC: "mp3"}
