"""Diagnostics of the Jellyfin Control integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_ACCESS_TOKEN, CONF_USER_ID
from .coordinator import JellyfinConfigEntry, supported_commands

TO_REDACT = {CONF_ACCESS_TOKEN, CONF_USER_ID, "RemoteEndPoint", "UserId"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: JellyfinConfigEntry
) -> dict[str, Any]:
    """Return a dump without the token, user ids or client addresses."""
    coordinator = entry.runtime_data.coordinator
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "server": {
            "id": coordinator.server_id,
            "name": coordinator.server_name,
            "version": coordinator.server_version,
            "user_is_admin": coordinator.is_admin,
        },
        "sessions": [
            {
                "device_key": key,
                "device_name": session.get("DeviceName"),
                "client": session.get("Client"),
                "application_version": session.get("ApplicationVersion"),
                "supports_remote_control": session.get("SupportsRemoteControl"),
                "supported_commands": supported_commands(session),
                "play_state": async_redact_data(
                    session.get("PlayState") or {}, TO_REDACT
                ),
                "now_playing": {
                    k: (session.get("NowPlayingItem") or {}).get(k)
                    for k in ("Id", "Type", "MediaType", "Name", "RunTimeTicks")
                },
            }
            for key, session in (coordinator.data or {}).items()
        ],
    }
