# Configuration

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
| Audio streaming codec | AAC | Codec used when library music is streamed to another Home Assistant player through the media source. |

## Data updates

Sessions are polled at the configured interval (default 5 s): one
`GET /Sessions?controllableByUserId=...` per poll. Every command triggers an
immediate refresh, so the state follows a button press without waiting for the
next poll. A failed poll makes the client entities unavailable and is logged
once; a refused token (HTTP 401) starts the re-authentication flow.
