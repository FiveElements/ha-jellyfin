"""The Jellyfin Control integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL, __version__ as ha_version
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import JellyfinAuthError, JellyfinClient, JellyfinError
from .const import (
    CLIENT_NAME,
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_DEVICE_ID,
    CONF_SERVER_ID,
    CONF_USER_ID,
    DOMAIN,
    LOGGER,
    PLATFORMS,
)
from .coordinator import JellyfinConfigEntry, JellyfinCoordinator, JellyfinData
from .entity import (
    async_sync_client_devices,
    client_device_identifier,
    server_device_info,
)
from .services import async_setup_services
from .views import async_register_views

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def create_client(
    hass: HomeAssistant,
    url: str,
    device_id: str,
    *,
    verify_ssl: bool = True,
    access_token: str | None = None,
    user_id: str | None = None,
) -> JellyfinClient:
    """Create a client sharing Home Assistant's HTTP session."""
    return JellyfinClient(
        async_get_clientsession(hass, verify_ssl=verify_ssl),
        url,
        device_id=device_id,
        device_name=hass.config.location_name or CLIENT_NAME,
        client_name=CLIENT_NAME,
        client_version=ha_version,
        access_token=access_token,
        user_id=user_id,
    )


def entry_client(hass: HomeAssistant, entry: JellyfinConfigEntry) -> JellyfinClient:
    """Create the client of a config entry."""
    return create_client(
        hass,
        entry.data[CONF_URL],
        entry.data[CONF_CLIENT_DEVICE_ID],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, True),
        access_token=entry.data[CONF_ACCESS_TOKEN],
        user_id=entry.data[CONF_USER_ID],
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services and relay views once for every entry."""
    async_setup_services(hass)
    async_register_views(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: JellyfinConfigEntry) -> None:
    """Connect to the server and start polling sessions."""
    client = entry_client(hass, entry)
    try:
        server = await client.public_info()
        user = await client.current_user()
    except JellyfinAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except JellyfinError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    coordinator = JellyfinCoordinator(hass, entry, client, server, user)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = JellyfinData(client=client, coordinator=coordinator)
    # Client devices link to the server through its registry id, so the
    # server device must exist before any platform creates a client entity.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **server_device_info(coordinator)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_sync_client_devices(coordinator)
    entry.async_on_unload(
        coordinator.async_add_listener(lambda: async_sync_client_devices(coordinator))
    )


async def async_unload_entry(hass: HomeAssistant, entry: JellyfinConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: JellyfinConfigEntry) -> None:
    """Revoke the access token when the entry is deleted."""
    try:
        await entry_client(hass, entry).logout()
    except JellyfinError as err:
        # The server may be gone for good; the entry must still go.
        LOGGER.debug("Could not revoke the Jellyfin token: %s", err)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    device_entry: dr.AnyDeviceEntry,
) -> bool:
    """Allow removing a client device that is no longer connected."""
    if entry.state is not ConfigEntryState.LOADED:
        # No live sessions to compare with (server down, reauth pending):
        # every client may go, the server device stays.
        live = {entry.data[CONF_SERVER_ID]}
    else:
        coordinator = entry.runtime_data.coordinator
        live = {coordinator.server_id} | {
            client_device_identifier(coordinator, key) for key in coordinator.data
        }
    return not any(
        domain == DOMAIN and identifier in live
        for domain, identifier in device_entry.identifiers
    )
