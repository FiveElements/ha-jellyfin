"""Remote entities: navigate a Jellyfin client like with its remote."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from functools import partial
from typing import Any, override

from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    DEFAULT_NUM_REPEATS,
    DOMAIN as REMOTE_DOMAIN,
    RemoteEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, PLAYSTATE_COMMANDS, REMOTE_ALIASES
from .coordinator import JellyfinConfigEntry, JellyfinCoordinator
from .entity import (
    JellyfinClientEntity,
    async_add_client_entities,
    call_api,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a remote for each remotely controllable client."""
    coordinator = entry.runtime_data.coordinator
    async_add_client_entities(
        entry,
        REMOTE_DOMAIN,
        async_add_entities,
        lambda key: [JellyfinRemote(coordinator, key)],
        include=lambda session: bool(session.get("SupportsRemoteControl")),
    )


def resolve_command(command: str) -> str:
    """Map a short alias (``up``, ``home``...) to its Jellyfin command."""
    return REMOTE_ALIASES.get(command.strip().lower(), command.strip())


class JellyfinRemote(JellyfinClientEntity, RemoteEntity):
    """Send navigation and playback commands to a client."""

    _attr_translation_key = "remote"

    def __init__(self, coordinator: JellyfinCoordinator, device_key: str) -> None:
        """Initialize the remote."""
        super().__init__(coordinator, device_key, "remote")

    @property
    @override
    def is_on(self) -> bool:
        """The remote is on while the client is connected."""
        return self.session is not None

    @property
    @override
    def available(self) -> bool:
        """Stay available so the state reads off rather than unavailable."""
        return self.coordinator.last_update_success

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Jellyfin cannot power a client on or off."""
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="power_unsupported"
        )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Jellyfin cannot power a client on or off."""
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="power_unsupported"
        )

    @override
    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        """Send commands, honouring repeats and delay."""
        num_repeats: int = kwargs.get(ATTR_NUM_REPEATS, DEFAULT_NUM_REPEATS)
        delay: float = kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS)
        session_id = self.session_id
        client = self.coordinator.client
        commands = [resolve_command(c) for c in command]
        first = True
        for _ in range(num_repeats):
            for name in commands:
                if not first and delay:
                    await asyncio.sleep(delay)
                first = False
                if name in PLAYSTATE_COMMANDS:
                    await call_api(partial(client.playstate, session_id, name))
                else:
                    await call_api(partial(client.general_command, session_id, name))
        await self.coordinator.async_request_refresh()
