# Entities

For each Jellyfin app (device) seen by the server:

| Entity | Purpose |
| --- | --- |
| `media_player.<device>` | State, artwork, metadata and every playback control the app advertises. `off` while the app is disconnected. |
| `remote.<device>_remote` | Navigation and playback commands (only for apps that accept remote control). |
| `select.<device>_audio_track` | Audio track of the playing item. |
| `select.<device>_subtitles` | Subtitles of the playing item, with `off`. |

On the server device:

| Entity | Purpose |
| --- | --- |
| `sensor.<server>_active_clients` | Apps playing something. |
| `sensor.<server>_connected_clients` | Apps connected. |
| `button.<server>_scan_libraries` | Scan every library (enabled by default for administrators only). |

## Devices

Each Jellyfin app is one device, identified by its Jellyfin **device id** (not
its session id, which changes on every reconnection), linked to the server
device. New apps appear as soon as they connect. A device that is no longer
connected can be deleted from its device page.

## Media player features

Features follow the commands the app advertises:

| Feature | Needs |
| --- | --- |
| Play, pause, stop, seek, next, previous, play media, enqueue | media control support |
| Volume set | `SetVolume` |
| Volume step | `VolumeUp` and `VolumeDown` |
| Mute | `Mute` and `Unmute`, or `ToggleMute` |
| Shuffle | `SetShuffleQueue` |
| Repeat | `SetRepeatMode` |
| Browse, search | always |

Extra attributes: `session_id`, `user_name`, `client`, `remote_end_point`,
`queue_size`, `play_method`, `audio_stream_index`, `subtitle_stream_index`,
`media_type`.
