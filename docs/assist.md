# Assist and MCP

Assist offers intents only. None of them changes a select entity or calls an
integration action, so a voice assistant could read the subtitles of a film
but not turn them off. The integration therefore registers its own LLM API,
**Jellyfin Control**, with one tool per control.

## Enable it

- **MCP clients** (Claude, other agents): in *Settings > Devices & services >
  Model Context Protocol Server > Configure*, select **Jellyfin Control** next
  to **Assist**. The client sees the tools after it reconnects.
- **Conversation agents** (OpenAI, Anthropic, Ollama...): select
  **Jellyfin Control** in the agent's *Control Home Assistant* options.

Only media players **exposed to Assist** (*Settings > Voice assistants >
Expose*) can be targeted, as with the Assist API. The tools run the
integration's actions as the requesting user, with the same checks: a
command the client does not advertise is refused with the reason.

## Players

Tools take an optional `player`: its name, an alias, its area or its entity
id. It can be left out when a single player is connected. The prompt given to
the model lists the players with their aliases, area and state.

## Tools

| Tool | Does |
| --- | --- |
| `jellyfin_get_players` | State, playing item, position, volume, audio and subtitle tracks, last played item, supported features. |
| `jellyfin_search` | Search the library (`query`, `media_types`, `limit`). |
| `jellyfin_browse` | Browse: libraries, `special:resume`, `special:next_up`, `special:latest`, `latest:LIBRARY_ID`, any folder. |
| `jellyfin_get_queue` | The play queue. |
| `jellyfin_play` | Play item ids (`enqueue`, `shuffle`, `start_position`). |
| `jellyfin_playback` | `play`, `pause`, `play_pause`, `stop`, `next`, `previous`. |
| `jellyfin_seek` | `offset` in seconds (forward, negative goes back) or `position` from the start. |
| `jellyfin_get_chapters` | Chapters of the playing item and the current one. |
| `jellyfin_seek_chapter` | `next`, `previous` or a chapter index. |
| `jellyfin_volume` | `level` (0-100), `mute`, `step` (`up`/`down`). |
| `jellyfin_shuffle_repeat` | `shuffle`, `repeat` (`off`, `all`, `one`). |
| `jellyfin_set_subtitles` | `track`: `off`, an index, or a language or title (`french`, `français`). |
| `jellyfin_set_audio_track` | `track`: an index, or a language or title. |
| `jellyfin_instant_mix` | Play similar music from an item. |
| `jellyfin_show_message` | Show a message on screen. |
| `jellyfin_navigate` | Remote keys (`up`, `down`, `select`, `back`, `home`...), with `repeats`. |
| `jellyfin_send_text` | Type into the focused field. |
| `jellyfin_display_item` | Open an item's page. |
| `jellyfin_set_user_data` | Mark played or unplayed, favorite or not. |
| `jellyfin_scan_library` | Scan the libraries (administrators). |

Read-only tools (`jellyfin_get_players`, `jellyfin_search`, `jellyfin_browse`,
`jellyfin_get_queue`, `jellyfin_get_chapters`) stay available when the MCP
server is set to read-only.

Track names come from Jellyfin, in English: French language names
(`anglais`, `allemand`, `japonais`...) are translated before matching.
Jellyfin applies the default subtitle track again to each new item, so
subtitles turned off come back on the next episode unless the Jellyfin user
settings say otherwise.
