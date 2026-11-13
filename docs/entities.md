# Entities

For each Jellyfin app (device) seen by the server:

| Entity | Purpose |
| --- | --- |
| `media_player.<device>` | State, artwork, metadata and every playback control the app advertises. `off` while the app is disconnected. |
| `remote.<device>_remote` | Navigation and playback commands (only for apps that accept remote control). It cannot power the app on or off: `turn_on`/`turn_off` are refused. |
| `select.<device>_audio_track` | Audio track of the playing item. |
| `select.<device>_subtitles` | Subtitles of the playing item, with `off`. |
| `sensor.<device>_last_played` | Title of the last item the app played, kept after it stops, disconnects or Home Assistant restarts. Attributes: `media_content_id` (to play it again), `media_content_type`, `played_at` (when the play started), and when known `name`, `series`, `series_id`, `season`, `episode`, `album`, `artist`, `year`. Only plays seen by a poll count. |

On the server device:

| Entity | Purpose |
| --- | --- |
| `sensor.<server>_active_clients` | Apps playing something. |
| `sensor.<server>_connected_clients` | Apps connected (disabled by default). |
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

Extra attributes: `session_id`, `user_name`, `client`, `client_version`,
`host_name`, `last_activity`, `queue_size`, `play_method`,
`audio_stream_index`, `subtitle_stream_index`, `media_type`, and for web
browsers `operating_system` and `browser`. Client IP addresses are not
exposed.

### Telling clients apart

Jellyfin Web names every tab after its browser, so three computers all show
up as "Chrome". The device name gets the **machine name**: the local DNS name
of the client's address (reverse lookup, local addresses only, cached an
hour). Without one, it gets the operating system instead. The model reads
the browser and version from the User-Agent encoded in the web client's
device id, e.g. `Jellyfin Web · Chrome 156 · Windows`. The registry follows
these values on each poll; a name you set on the device is kept.

If every client shows the same machine name, Jellyfin sees the address of
your reverse proxy: add the proxy to *Known proxies* in the Jellyfin
networking settings so it records the real client address.
