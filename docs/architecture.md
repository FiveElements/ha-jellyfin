# Architecture

| Module | Role |
| --- | --- |
| `api.py` | `JellyfinClient`: async REST client on Home Assistant's aiohttp session. No Home Assistant import; raises its own errors. Encodes every path segment, follows no redirect. |
| `coordinator.py` | Polls `/Sessions`, keys them by device id, drops Home Assistant's own session and the server's. |
| `library.py` | The library tree as plain `LibraryNode` values, search, folder-to-queue expansion and the play queue. Shared by the media player, the media source and the actions. |
| `entity.py` | Base entities, device info, `call_api()` that turns client errors into translated Home Assistant errors. |
| `media_player.py` | One player per device: state, controls, play media, browse and search, artwork proxy, action handlers. |
| `remote.py`, `select.py`, `sensor.py`, `button.py` | The other platforms. |
| `media_source.py` | The library as a media source; resolves to relayed stream paths. |
| `views.py` | Home Assistant views relaying artwork and streams with the token in a header (signed paths, Range pass-through). |
| `services.py` | Registration and schemas of the entity actions. |
| `config_flow.py` | Server, password or Quick Connect, reauth, reconfigure, options. |
| `diagnostics.py` | Entry dump without token, user id or client addresses. |

The config entry stores the URL, the SSL flag, Home Assistant's own Jellyfin
device id, the access token, the user id and the server id; never a password.
