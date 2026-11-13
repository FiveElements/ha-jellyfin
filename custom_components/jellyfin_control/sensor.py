"""Sensors: session counts on the server, last played item on each client."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .api import JsonObject
from .coordinator import JellyfinConfigEntry, JellyfinCoordinator
from .entity import (
    JellyfinClientEntity,
    JellyfinServerEntity,
    async_add_client_entities,
)
from .library import item_title

PARALLEL_UPDATES = 0

# A state longer than this is refused by Home Assistant.
MAX_STATE_LENGTH = 255

ATTR_MEDIA_ID = "media_content_id"
ATTR_MEDIA_TYPE = "media_content_type"
ATTR_PLAYED_AT = "played_at"
# Attribute name -> item field, kept only when the item has it.
ITEM_ATTRIBUTES: dict[str, str] = {
    "name": "Name",
    "series": "SeriesName",
    "series_id": "SeriesId",
    "season": "ParentIndexNumber",
    "episode": "IndexNumber",
    "album": "Album",
    "artist": "AlbumArtist",
    "year": "ProductionYear",
}
RESTORED_ATTRIBUTES = (
    ATTR_MEDIA_ID,
    ATTR_MEDIA_TYPE,
    ATTR_PLAYED_AT,
    *ITEM_ATTRIBUTES,
)


@dataclass(frozen=True, kw_only=True)
class JellyfinSensorEntityDescription(SensorEntityDescription):
    """Describe a server sensor."""

    value_fn: Callable[[dict[str, JsonObject]], StateType]


SENSORS: tuple[JellyfinSensorEntityDescription, ...] = (
    JellyfinSensorEntityDescription(
        key="watching",
        translation_key="watching",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: sum(1 for s in data.values() if s.get("NowPlayingItem")),
    ),
    JellyfinSensorEntityDescription(
        key="sessions",
        translation_key="sessions",
        # Secondary to "active clients"; automations rarely need it.
        entity_registry_enabled_default=False,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=len,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the server sensors and a last played sensor per client."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(JellyfinSensor(coordinator, d) for d in SENSORS)
    async_add_client_entities(
        entry,
        SENSOR_DOMAIN,
        async_add_entities,
        lambda key: [JellyfinLastPlayedSensor(coordinator, key)],
    )


class JellyfinSensor(JellyfinServerEntity, SensorEntity):
    """A count derived from the sessions."""

    entity_description: JellyfinSensorEntityDescription

    def __init__(
        self,
        coordinator: JellyfinCoordinator,
        description: JellyfinSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> StateType:
        """Return the count."""
        return self.entity_description.value_fn(self.coordinator.data)


def played_attributes(item: JsonObject) -> dict[str, Any]:
    """Return the attributes describing a played item."""
    attributes: dict[str, Any] = {
        ATTR_MEDIA_ID: str(item.get("Id") or ""),
        ATTR_MEDIA_TYPE: item.get("Type"),
        ATTR_PLAYED_AT: dt_util.utcnow().isoformat(),
    }
    for name, field in ITEM_ATTRIBUTES.items():
        if (value := item.get(field)) is not None:
            attributes[name] = value
    return attributes


class JellyfinLastPlayedSensor(JellyfinClientEntity, RestoreEntity, SensorEntity):
    """The last item a client played, kept after it stops or disconnects.

    The media player forgets the item as soon as playback stops, so "what was
    I watching in the living room?" had no answer. The value survives a
    restart (restored state): Home Assistant only sees what it polled.
    """

    _attr_translation_key = "last_played"

    def __init__(self, coordinator: JellyfinCoordinator, device_key: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_key, "last_played")
        self._title: str | None = None
        self._attributes: dict[str, Any] = {}
        # Item seen playing on the previous poll: a new play (another item,
        # or the same one started again after a stop) moves played_at.
        self._playing_id: str | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last item, then read the current session."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self._title = last.state
            self._attributes = {
                key: last.attributes[key]
                for key in RESTORED_ATTRIBUTES
                if key in last.attributes
            }
            # Still playing the restored item: the restart is not a new play.
            self._playing_id = self._attributes.get(ATTR_MEDIA_ID)
        self._record()

    def _record(self) -> None:
        item = self.now_playing
        item_id = str(item.get("Id") or "") if item else ""
        if item is None or not item_id:
            self._playing_id = None
            return
        if item_id != self._playing_id:
            self._title = item_title(item)[:MAX_STATE_LENGTH]
            self._attributes = played_attributes(item)
        self._playing_id = item_id

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Record a newly playing item."""
        self._record()
        super()._handle_coordinator_update()

    @property
    @override
    def available(self) -> bool:
        """Stay available while the client is offline: that is the point."""
        return self.coordinator.last_update_success

    @property
    @override
    def native_value(self) -> str | None:
        """Return the title of the last played item."""
        return self._title

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the item id, type and details."""
        return self._attributes
