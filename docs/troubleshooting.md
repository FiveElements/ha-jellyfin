# Troubleshooting

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

## Known limitations

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
