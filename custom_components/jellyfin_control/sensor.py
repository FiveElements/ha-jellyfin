"""Server sensors: connected and playing clients."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .api import JsonObject
from .coordinator import JellyfinConfigEntry, JellyfinCoordinator
from .entity import JellyfinServerEntity

PARALLEL_UPDATES = 0


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
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=len,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the server sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(JellyfinSensor(coordinator, d) for d in SENSORS)


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
