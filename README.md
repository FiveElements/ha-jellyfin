# Jellyfin Control for Home Assistant

Browse your Jellyfin library and drive every Jellyfin app from Home Assistant:
playback, queue, tracks, navigation, messages and search, through the Jellyfin
REST API only.

[![Version](https://img.shields.io/github/v/release/FiveElements/ha-jellyfin?label=version)](https://github.com/FiveElements/ha-jellyfin/releases/latest)
[![Home Assistant](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FFiveElements%2Fha-jellyfin%2Fmain%2Fhacs.json&query=%24.homeassistant&prefix=%E2%89%A5%20&label=Home%20Assistant&logo=homeassistant&color=41BDF5)](https://www.home-assistant.io/)
[![Validate](https://github.com/FiveElements/ha-jellyfin/actions/workflows/validate.yml/badge.svg)](https://github.com/FiveElements/ha-jellyfin/actions/workflows/validate.yml)
[![CodeQL](https://github.com/FiveElements/ha-jellyfin/actions/workflows/codeql.yml/badge.svg)](https://github.com/FiveElements/ha-jellyfin/actions/workflows/codeql.yml)
[![CI](https://github.com/FiveElements/ha-jellyfin/actions/workflows/ci.yml/badge.svg)](https://github.com/FiveElements/ha-jellyfin/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-Material_for_MkDocs-526CFE?logo=materialformkdocs)](https://fiveelements.github.io/ha-jellyfin/)
[![Coverage](https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen?logo=pytest)](https://github.com/FiveElements/ha-jellyfin/actions/workflows/ci.yml)
[![Dependabot](https://img.shields.io/badge/dependabot-enabled-025E8C?logo=dependabot)](https://github.com/FiveElements/ha-jellyfin/blob/main/.github/dependabot.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/FiveElements/ha-jellyfin/badge)](https://scorecard.dev/viewer/?uri=github.com/FiveElements/ha-jellyfin)
[![License](https://img.shields.io/github/license/FiveElements/ha-jellyfin)](LICENSE)

The integration is self-contained: it ships its own asynchronous REST client
(no `jellyfin-apiclient-python`), uses Home Assistant's HTTP session, and runs
next to the official `jellyfin` integration without conflict (its domain is
`jellyfin_control`).

## What it adds over the official integration

| Feature | Official `jellyfin` | Jellyfin Control |
| --- | --- | --- |
| Play, pause, stop, seek | yes | yes |
| Next / previous track | no | yes |
| Volume set, mute | yes | yes |
| Volume up / down | no | yes |
| Shuffle and repeat modes | no | yes |
| Enqueue (play now, next, last) | yes (single item) | yes; folders, series, albums, artists, genres and playlists are expanded into a queue |
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
| Stream to other players (media source) | yes (URL carries the token) | yes, relayed by Home Assistant: the token never leaves it |

## Install

**HACS**: add `https://github.com/FiveElements/ha-jellyfin` as a custom
repository of type *Integration*, install **Jellyfin Control**, restart Home
Assistant.

**Manually**: copy `custom_components/jellyfin_control` into your
`config/custom_components/` folder and restart.

Then **Settings → Devices & services → Add integration → Jellyfin Control**.

Requires Home Assistant 2026.11 or newer and Jellyfin 10.9 or newer.

## Setup

1. **Server**: the address, for example `http://192.168.1.10:8096`. A
   `/jellyfin` base path behind a reverse proxy is found automatically. Turn
   *Verify SSL certificate* off only for a self-signed certificate.
2. **Sign in**, either:
   - **Username and password**: exchanged once for an access token, then
     discarded;
   - **Quick Connect**: enter the displayed code in a Jellyfin app already
     signed in (*Settings → Quick Connect*), then submit.

Use an administrator account to see and control the clients of every user and
to scan libraries. A regular user controls only their own clients.

One entry per server. **Reconfigure** changes the address; a revoked token
starts a **re-authentication**.

### Options

| Option | Default | Meaning |
| --- | --- | --- |
| Polling interval | 5 s | How often sessions are read (2-300 s). |
| Audio streaming codec | MP3 | Codec (MP3, AAC, Opus, FLAC) used when library music is streamed to another Home Assistant player through the media source. |

## Entities

For each Jellyfin app (device) seen by the server:

| Entity | Purpose |
| --- | --- |
| `media_player.<device>` | State, artwork, metadata and every playback control the app advertises. `off` while the app is disconnected. |
| `remote.<device>_remote` | Navigation and playback commands (only for apps that accept remote control). It cannot power the app on or off: `turn_on`/`turn_off` are refused. |
| `select.<device>_audio_track` | Audio track of the playing item. |
| `select.<device>_subtitles` | Subtitles of the playing item, with `off`. |

On the server device:

| Entity | Purpose |
| --- | --- |
| `sensor.<server>_active_clients` | Apps playing something. |
| `sensor.<server>_connected_clients` | Apps connected (disabled by default). |
| `button.<server>_scan_libraries` | Scan every library (enabled by default for administrators only). |

## Actions

All take a `jellyfin_control` media player as target.

| Action | Does |
| --- | --- |
| `jellyfin_control.play_items` | Play or queue item ids (`enqueue`: play, next, add, replace), with `shuffle`, `start_index`, `start_position`. |
| `jellyfin_control.play_media_shuffle` | Play an item or folder shuffled. |
| `jellyfin_control.play_instant_mix` | Play a mix built from a song, album, artist or genre. |
| `jellyfin_control.seek_relative` | Move by ± seconds. |
| `jellyfin_control.send_message` | Show a message on the TV. |
| `jellyfin_control.send_text` | Type into the focused field (search box). |
| `jellyfin_control.send_command` | Any general command, with arguments. |
| `jellyfin_control.display_content` | Open an item's page in the app. |
| `jellyfin_control.set_audio_stream` / `set_subtitle_stream` | Switch tracks by index (`-1` turns subtitles off). |
| `jellyfin_control.set_user_data` | Mark played / unplayed, favorite / not. |
| `jellyfin_control.get_queue` | **Returns** the play queue. |
| `jellyfin_control.browse_library` | **Returns** the children of a folder (root, `special:resume`, `special:next_up`, `special:latest`, `special:favorites`, any item id). |
| `jellyfin_control.search_library` | **Returns** matching items. |

Remote commands accept these aliases: `up`, `down`, `left`, `right`, `select`
(`ok`), `back`, `home`, `menu`, `info` (`osd`), `stats`, `fullscreen`,
`settings`, `search`, `context_menu`, `page_up`, `page_down`, `channel_up`, `channel_down`,
`guide`, `volume_up`, `volume_down`, `mute`, `play_pause`, `play`, `pause`,
`stop`, `next`, `previous`, `rewind`, `fast_forward`. Any other name is sent
as a Jellyfin general command.

### Example

```yaml
actions:
  - action: jellyfin_control.search_library
    target:
      entity_id: media_player.living_room
    data:
      query: "The Matrix"
      media_types: [movie]
      limit: 1
    response_variable: found
  - action: jellyfin_control.play_items
    target:
      entity_id: media_player.living_room
    data:
      item_ids: "{{ found['media_player.living_room'].items[0].id }}"
```

More in the [documentation](https://fiveelements.github.io/ha-jellyfin/).

## Data updates and limits

- Sessions are **polled** (default every 5 s). A command triggers an immediate
  refresh.
- What a client can do is what it **advertises**: the media player features,
  track selectors and actions follow its `SupportedCommands`. A web browser
  tab accepts fewer commands than the Android TV app; an action the client
  does not support is refused with a clear error.
- Jellyfin clients only play items of **their own library**: media sources of
  other integrations (TTS, local files) cannot be sent to them.
- A folder is expanded into at most **200 items** (URL length), the media
  browser lists at most **1000 children** per folder, *Recently added* the
  **100** newest items.
- `search_library` returns **50** items by default, at most **500**.
- `send_command` and raw remote commands are sent as they are: the client
  ignores a command it does not know.
- Scanning libraries needs an **administrator** account.
- Jellyfin has no discovery protocol Home Assistant can use: the address is
  entered by hand.

## Troubleshooting

- *Cannot connect*: check the URL from the Home Assistant host
  (`curl http://host:8096/System/Info/Public` must return JSON).
- *A client is missing*: only clients that the signed-in user may control are
  listed; sign in as an administrator to see everyone's.
- *A command does nothing*: the client may not advertise it; download the
  diagnostics of the entry, they list each client's supported commands.
- Enable debug logs:

  ```yaml
  logger:
    logs:
      custom_components.jellyfin_control: debug
  ```

## Remove

**Settings → Devices & services → Jellyfin Control → ⋮ → Delete**. The
access token is revoked on the server. Remove the files (or uninstall from
HACS) and restart.

## Privacy

The integration talks only to your Jellyfin server. The access token never
appears in logs, diagnostics or URLs:

- artwork is fetched by Home Assistant and served from its own address;
- media-source streams and thumbnails are relayed by Home Assistant through
  short-lived signed `/api/jellyfin_control/...` paths, so a Cast device or a
  browser never sees the token, and only reaches Home Assistant.

Diagnostics hide the token, the user id and client addresses. The media
player exposes the Jellyfin user name of each session as an attribute; client
IP addresses are not exposed. To tell apart clients that share a name (every
browser is "Chrome"), the address of a client on the **local network** is
looked up in the local DNS (reverse lookup, cached an hour) and only the
machine name is shown; public addresses are never looked up.
