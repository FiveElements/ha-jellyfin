"""Server button: scan all libraries."""

from __future__ import annotations

from typing import override

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import JellyfinConfigEntry, JellyfinCoordinator
from .entity import JellyfinServerEntity, call_api

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the library scan button."""
    async_add_entities([JellyfinRefreshLibraryButton(entry.runtime_data.coordinator)])


class JellyfinRefreshLibraryButton(JellyfinServerEntity, ButtonEntity):
    """Ask the server to scan every library."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "refresh_library"

    def __init__(self, coordinator: JellyfinCoordinator) -> None:
        """Initialize the button; only administrators may scan."""
        super().__init__(coordinator, "refresh_library")
        self._attr_entity_registry_enabled_default = coordinator.is_admin

    @override
    async def async_press(self) -> None:
        """Start the scan."""
        await call_api(self.coordinator.client.refresh_library)
