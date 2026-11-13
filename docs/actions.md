# Actions

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
`settings`, `search`, `page_up`, `page_down`, `channel_up`, `channel_down`,
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

## Responses

`browse_library` and `search_library` return items shaped like:

```yaml
items:
  - id: 9f2c0d7e5b8a4c1f9e3d2b6a7c8d9e0f
    title: The Matrix (1999)
    name: The Matrix
    type: Movie
    media_class: movie
    can_play: true
    can_expand: false
    year: 1999
    duration: 8160
    played: false
    favorite: true
```

`get_queue` returns `position`, `id`, `title`, `type` and `current` for each
queue entry.
