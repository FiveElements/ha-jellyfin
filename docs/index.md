# Jellyfin Control

Browse your Jellyfin library and drive every Jellyfin app from Home Assistant:
playback, queue, tracks, navigation, messages and search, through the Jellyfin
REST API only.

It runs next to the official `jellyfin` integration (domain `jellyfin_control`) and ships its own asynchronous REST client.

## What it adds over the official integration

| Feature | Official `jellyfin` | Jellyfin Control |
| --- | --- | --- |
| Play, pause, stop, seek | yes | yes |
| Next / previous track | no | yes |
| Volume set, mute | yes | yes |
| Volume up / down | no | yes |
| Shuffle and repeat modes | no | yes |
| Enqueue (play now, next, last) | partial | yes; folders, series, albums, artists, genres and playlists are expanded into a queue |
| Audio and subtitle track selection | no | `select` entities and actions |
| Remote navigation | blocking, raw names | async, with aliases (`up`, `home`, `info`...) and every general command |
| Messages, text input, open an item page on the TV | no | yes |
| Play queue as data | no | `get_queue` action |
| Library browsing | movies, shows, music | every library type, Continue watching, Next up, Recently added, Favorites, artists/albums/songs, Live TV |
| Search | yes | yes, plus a `search_library` action that returns data |
| Mark played / favorite | no | yes |
| Instant mix | no | yes |
| Scan libraries | no | button (administrators) |
| Entities survive a client restart | no (keyed on session id) | yes (keyed on device id); a disconnected client reads `off` |
| Credentials stored | username and password | an access token only; sign in by password or Quick Connect; the token is revoked when the entry is deleted |
| Stream to other players (media source) | yes | yes |

Start with [Install](install.md), then [Configuration](configuration.md).
