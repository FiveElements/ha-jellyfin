# Install

**HACS**: add `https://github.com/FiveElements/ha-jellyfin` as a custom
repository of type *Integration*, install **Jellyfin Control**, restart Home
Assistant.

**Manually**: copy `custom_components/jellyfin_control` into your
`config/custom_components/` folder and restart.

Then **Settings → Devices & services → Add integration → Jellyfin Control**.

Requires Home Assistant 2026.11 or newer and Jellyfin 10.9 or newer.

## Remove

**Settings → Devices & services → Jellyfin Control → ⋮ → Delete**. The
access token is revoked on the server. Remove the files (or uninstall from
HACS) and restart.
