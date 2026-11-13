"""Session polling for the Jellyfin Control integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    JellyfinAuthError,
    JellyfinClient,
    JellyfinError,
    JsonObject,
    PublicInfo,
)
from .const import (
    CONF_CLIENT_DEVICE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    OPTION_SCAN_INTERVAL,
)

type JellyfinConfigEntry = ConfigEntry[JellyfinData]


@dataclass(slots=True)
class JellyfinData:
    """Runtime data of a config entry."""

    client: JellyfinClient
    coordinator: JellyfinCoordinator


def session_key(session: JsonObject) -> str:
    """Return the stable identity of a session's device.

    Session ids change each time a client reconnects; the device id does not,
    so entities keyed on it survive an app restart instead of piling up.
    """
    return str(session.get("DeviceId") or session["Id"])


def supported_commands(session: JsonObject) -> list[str]:
    """Return the general commands a session advertises."""
    caps = session.get("Capabilities")
    commands = caps.get("SupportedCommands") if isinstance(caps, dict) else None
    if not commands:
        commands = session.get("SupportedCommands")
    return [str(c) for c in commands or []]


def supports_media_control(session: JsonObject) -> bool:
    """Return whether the session accepts playstate and play commands."""
    caps = session.get("Capabilities")
    if isinstance(caps, dict) and caps.get("SupportsMediaControl"):
        return True
    return bool(session.get("SupportsMediaControl")) or bool(
        session.get("SupportsRemoteControl")
    )


class JellyfinCoordinator(DataUpdateCoordinator[dict[str, JsonObject]]):
    """Poll the sessions the configured user may control."""

    config_entry: JellyfinConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: JellyfinConfigEntry,
        client: JellyfinClient,
        server: PublicInfo,
        user: JsonObject,
    ) -> None:
        """Initialize the coordinator."""
        interval = config_entry.options.get(OPTION_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self.server_id = server.server_id
        self.server_name = server.name
        self.server_version = server.version
        self.user_id = str(user.get("Id") or client.user_id)
        self.user_name = str(user.get("Name") or "")
        policy = user.get("Policy")
        self.is_admin = bool(isinstance(policy, dict) and policy.get("IsAdministrator"))
        self.own_device_id: str = config_entry.data[CONF_CLIENT_DEVICE_ID]
        # Devices seen since setup: entities are created once per device.
        self._streams: dict[str, list[JsonObject]] = {}

    @override
    async def _async_update_data(self) -> dict[str, JsonObject]:
        """Fetch sessions, keyed by device id."""
        try:
            sessions = await self.client.sessions()
        except JellyfinAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except JellyfinError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        result: dict[str, JsonObject] = {}
        for session in sessions:
            device_id = session.get("DeviceId")
            # Our own API session and tools reporting as the server itself
            # (Jellystat) are not players.
            if device_id in (self.own_device_id, self.server_id):
                continue
            key = session_key(session)
            current = result.get(key)
            if current is None or _activity(session) >= _activity(current):
                result[key] = session
        await self._add_missing_streams(result)
        return result

    async def _add_missing_streams(self, sessions: dict[str, JsonObject]) -> None:
        """Fill in the tracks of items the server reported without them.

        The server only lists MediaStreams when it knows the media source; the
        track selectors used to stay unavailable then. One lookup per item,
        cached while it plays.
        """
        playing: set[str] = set()
        for session in sessions.values():
            item = session.get("NowPlayingItem")
            if not isinstance(item, dict) or not item.get("Id"):
                continue
            item_id = str(item["Id"])
            playing.add(item_id)
            if item.get("MediaStreams") or item.get("MediaType") != "Video":
                continue
            if item_id not in self._streams:
                try:
                    details = await self.client.item(item_id)
                except JellyfinError as err:
                    # Not cached: a timeout at start used to leave the track
                    # selectors unavailable for the whole film.
                    LOGGER.debug("No tracks for %s yet: %s", item_id, err)
                    item["MediaStreams"] = []
                    continue
                self._streams[item_id] = details.get("MediaStreams") or []
            item["MediaStreams"] = self._streams[item_id]
        for item_id in set(self._streams) - playing:
            del self._streams[item_id]

    def session(self, device_key: str) -> JsonObject | None:
        """Return the live session of a device, if connected."""
        return (self.data or {}).get(device_key)


def _activity(session: dict[str, Any]) -> str:
    """Sort key: ISO timestamps compare as strings."""
    return str(session.get("LastActivityDate") or "")
