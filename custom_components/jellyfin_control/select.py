"""Select entities: audio and subtitle tracks of the playing item."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
    SelectEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import JsonObject
from .const import DOMAIN
from .coordinator import JellyfinConfigEntry, JellyfinCoordinator
from .entity import JellyfinClientEntity, async_add_client_entities

PARALLEL_UPDATES = 0

SUBTITLES_OFF = "off"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add track selectors for each client device."""
    coordinator = entry.runtime_data.coordinator
    async_add_client_entities(
        entry,
        SELECT_DOMAIN,
        async_add_entities,
        lambda key: [
            JellyfinTrackSelect(coordinator, key, "Audio"),
            JellyfinTrackSelect(coordinator, key, "Subtitle"),
        ],
    )


def stream_label(stream: JsonObject) -> str:
    """Return a unique, readable label for a media stream."""
    title = (
        stream.get("DisplayTitle")
        or stream.get("Title")
        or stream.get("Language")
        or stream.get("Codec")
        or ""
    )
    return f"{stream.get('Index')}: {title}".rstrip(": ")


class JellyfinTrackSelect(JellyfinClientEntity, SelectEntity):
    """Choose the audio or subtitle track of the playing item."""

    def __init__(
        self, coordinator: JellyfinCoordinator, device_key: str, stream_type: str
    ) -> None:
        """Initialize the selector."""
        key = f"{stream_type.lower()}_track"
        super().__init__(coordinator, device_key, key)
        self._stream_type = stream_type
        self._attr_translation_key = key
        self._command = (
            "SetAudioStreamIndex"
            if stream_type == "Audio"
            else "SetSubtitleStreamIndex"
        )

    def _streams(self) -> list[JsonObject]:
        item = self.now_playing
        streams: list[Any] = (item or {}).get("MediaStreams") or []
        return [
            s
            for s in streams
            if isinstance(s, dict)
            and s.get("Type") == self._stream_type
            and s.get("Index") is not None
        ]

    @property
    @override
    def available(self) -> bool:
        """Only while an item with such tracks plays on a capable client."""
        return (
            super().available and self.supports(self._command) and bool(self._streams())
        )

    @property
    @override
    def options(self) -> list[str]:
        """Return the track labels."""
        labels = [stream_label(s) for s in self._streams()]
        if self._stream_type == "Subtitle":
            labels.insert(0, SUBTITLES_OFF)
        return labels

    @property
    @override
    def current_option(self) -> str | None:
        """Return the active track."""
        key = f"{self._stream_type}StreamIndex"
        index = self.play_state.get(key)
        if self._stream_type == "Subtitle" and (index is None or index < 0):
            return SUBTITLES_OFF
        for stream in self._streams():
            if stream.get("Index") == index:
                return stream_label(stream)
        return None

    @override
    async def async_select_option(self, option: str) -> None:
        """Switch to the chosen track."""
        if option == SUBTITLES_OFF and self._stream_type == "Subtitle":
            index = -1
        else:
            match = [s for s in self._streams() if stream_label(s) == option]
            if not match:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="invalid_option",
                    translation_placeholders={"option": option},
                )
            index = int(match[0]["Index"])
        await self.async_general_command(self._command, {"Index": index})
