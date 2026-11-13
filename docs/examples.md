# Examples

## Resume the next episode when you sit down

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.sofa_occupied
    to: "on"
actions:
  - action: jellyfin_control.browse_library
    target:
      entity_id: media_player.living_room
    data:
      parent_id: special:next_up
    response_variable: next_up
  - if: "{{ next_up['media_player.living_room'].items | count > 0 }}"
    then:
      - action: jellyfin_control.play_items
        target:
          entity_id: media_player.living_room
        data:
          item_ids: "{{ next_up['media_player.living_room'].items[0].id }}"
```

## Pause when the doorbell rings, and say why

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.doorbell
    to: "on"
actions:
  - action: media_player.media_pause
    target:
      entity_id: media_player.living_room
  - action: jellyfin_control.send_message
    target:
      entity_id: media_player.living_room
    data:
      title: Doorbell
      message: Someone is at the door
      timeout: 10
```

## Subtitles off for movies

```yaml
triggers:
  - trigger: state
    entity_id: media_player.living_room
    attribute: media_content_id
conditions:
  - condition: state
    entity_id: media_player.living_room
    attribute: media_content_type
    state: movie
actions:
  - action: select.select_option
    target:
      entity_id: select.living_room_subtitles
    data:
      option: "off"
```

## Navigate with a physical remote

```yaml
actions:
  - action: remote.send_command
    target:
      entity_id: remote.living_room_remote
    data:
      command: [down, select]
      delay_secs: 0.3
```

## Search and play (script with a field)

```yaml
fields:
  title:
    selector:
      text:
sequence:
  - action: jellyfin_control.search_library
    target:
      entity_id: media_player.living_room
    data:
      query: "{{ title }}"
      limit: 1
    response_variable: found
  - action: jellyfin_control.play_items
    target:
      entity_id: media_player.living_room
    data:
      item_ids: "{{ found['media_player.living_room'].items[0].id }}"
```
