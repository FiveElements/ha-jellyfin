# Security policy

## Supported versions

Only the latest release receives fixes. HACS offers it as an update as soon as
it is tagged.

## Reporting a vulnerability

Please report privately through
[GitHub private vulnerability reporting](https://github.com/FiveElements/ha-jellyfin/security/advisories/new),
not in a public issue.

Expect an acknowledgement within a week. Once a fix is released the advisory is
published with credit, unless you ask otherwise.

## Scope

The integration stores one Jellyfin access token per server and talks only to
that server. Reports about Jellyfin itself belong to the Jellyfin project.
In scope here: anything that leaks the access token (logs, diagnostics,
exceptions, URLs other than the media-source stream URL), sends it to a host
other than the configured server, lets an item id or device name reach another
endpoint, or lets an action control a client the signed-in user may not.
