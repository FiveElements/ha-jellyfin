"""Base entities for the Jellyfin Control integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import override

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import (
    JellyfinAuthError,
    JellyfinError,
    JellyfinForbiddenError,
    JellyfinNotFoundError,
    JsonObject,
)
from .const import DOMAIN
from .coordinator import JellyfinCoordinator, supported_commands


def server_device_info(coordinator: JellyfinCoordinator) -> DeviceInfo:
    """Return the service device shared by the server entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.server_id)},
        entry_type=dr.DeviceEntryType.SERVICE,
        manufacturer="Jellyfin",
        name=coordinator.server_name,
        sw_version=coordinator.server_version,
        configuration_url=coordinator.client.url,
    )


def client_device_identifier(coordinator: JellyfinCoordinator, device_key: str) -> str:
    """Return the registry identifier of a client device."""
    return f"{coordinator.server_id}-{device_key}"


async def call_api[T](call: Callable[[], Awaitable[T]]) -> T:
    """Run an API call, translating client errors for the user."""
    try:
        return await call()
    except JellyfinAuthError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except JellyfinForbiddenError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="forbidden"
        ) from err
    except JellyfinNotFoundError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_found"
        ) from err
    except JellyfinError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err


class JellyfinEntity(CoordinatorEntity[JellyfinCoordinator]):
    """Base of every Jellyfin Control entity."""

    _attr_has_entity_name = True


class JellyfinServerEntity(JellyfinEntity):
    """Entity attached to the server device."""

    def __init__(self, coordinator: JellyfinCoordinator, key: str) -> None:
        """Attach the entity to the server device."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.server_id}-{key}"
        self._attr_device_info = server_device_info(coordinator)


class JellyfinClientEntity(JellyfinEntity):
    """Entity attached to one client device (a Jellyfin app)."""

    def __init__(
        self, coordinator: JellyfinCoordinator, device_key: str, key: str | None
    ) -> None:
        """Attach the entity to the client device."""
        super().__init__(coordinator)
        self.device_key = device_key
        identifier = client_device_identifier(coordinator, device_key)
        self._attr_unique_id = identifier if key is None else f"{identifier}-{key}"
        session = coordinator.session(device_key) or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer="Jellyfin",
            model=session.get("Client"),
            name=session.get("DeviceName") or device_key,
            sw_version=session.get("ApplicationVersion"),
            via_device_id=dr.async_get_device_id_by_identifier(
                coordinator.hass,
                (DOMAIN, coordinator.server_id),
                config_entry_id=coordinator.config_entry.entry_id,
            ),
        )

    @property
    def session(self) -> JsonObject | None:
        """Return the live session of the device, if connected."""
        return self.coordinator.session(self.device_key)

    @property
    def session_id(self) -> str:
        """Return the live session id or raise when the device is gone."""
        session = self.session
        if session is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="session_unavailable"
            )
        return str(session["Id"])

    @property
    def now_playing(self) -> JsonObject | None:
        """Return the item being played, if any."""
        session = self.session
        item = session.get("NowPlayingItem") if session else None
        return item if isinstance(item, dict) else None

    @property
    def play_state(self) -> JsonObject:
        """Return the play state of the session (empty when absent)."""
        session = self.session
        state = session.get("PlayState") if session else None
        return state if isinstance(state, dict) else {}

    def supports(self, command: str) -> bool:
        """Return whether the session advertises a general command."""
        session = self.session
        return session is not None and command in supported_commands(session)

    def require_command(self, command: str) -> None:
        """Refuse a command the client does not advertise."""
        if not self.supports(command):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="command_unsupported",
                translation_placeholders={"command": command},
            )

    async def async_general_command(
        self, name: str, arguments: dict[str, str | int] | None = None
    ) -> None:
        """Send a general command to the session and refresh."""
        session_id = self.session_id
        await call_api(
            lambda: self.coordinator.client.general_command(session_id, name, arguments)
        )
        await self.coordinator.async_request_refresh()

    async def async_playstate(
        self, command: str, seek_ticks: int | None = None
    ) -> None:
        """Send a playstate command to the session and refresh."""
        session_id = self.session_id
        await call_api(
            lambda: self.coordinator.client.playstate(session_id, command, seek_ticks)
        )
        await self.coordinator.async_request_refresh()

    @property
    @override
    def available(self) -> bool:
        """Client entities need the device to be connected."""
        return super().available and self.session is not None
