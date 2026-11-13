# AGENTS.md

This file provides guidance to Agentic Code when working with code in this repository.

## What this is

A Home Assistant custom integration (`custom_components/jellyfin_control`,
HACS-installable) that browses a Jellyfin library and fully controls Jellyfin
clients through the REST API. It is a self-contained replacement for the
official `jellyfin` integration (which depends on `jellyfin-apiclient-python`
and exposes few controls) and runs next to it: the domain is
`jellyfin_control`. Requires Python 3.14.2+, Home Assistant 2026.11.0+ and
Jellyfin 10.9+. Declared quality scale is **platinum** (`manifest.json`).

The REST surface was taken from the Kodifin Android remote
(`protocol/mediaserver` in that project), restricted to Jellyfin.

## Commands

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-test.txt

.venv/bin/pytest tests -q                              # full suite (Linux/macOS only)
.venv/bin/pytest tests/test_media_player.py -q         # one file
.venv/bin/ruff check custom_components tests fuzz scripts
.venv/bin/ruff format --check custom_components tests fuzz scripts
.venv/bin/mypy custom_components/jellyfin_control
pip install -r requirements-docs.txt && mkdocs serve   # http://127.0.0.1:8000
```

CI uses **Python 3.14** and pins `pytest-homeassistant` (Home Assistant's own
test package, versioned like Home Assistant) in `requirements-test.txt`; it must
match the oldest row of `.github/workflows/ci.yml` and `hacs.json`'s
`homeassistant` floor. Do not install `pytest-homeassistant-custom-component`
alongside it (it ends at 2026.10 and its fixtures overlap). `pyproject.toml`
disables the respx pytest plugin: Home Assistant aliases `httpx` on import and
refuses once `httpx` is already loaded.

Lint, docs and fuzz install with `--require-hashes` from
`requirements-{lint,docs,fuzz}.txt`, compiled from their `.in` by
`scripts/lock.sh` (in Docker). The test job is deliberately **not**
hash-locked: such a lock must carry Home Assistant's exact pins, so every CVE
in one of them would raise alerts only an HA release could fix.

`fuzz/fuzz_api.py` is an Atheris harness for the client's injection guards
(path segments cannot escape, the Authorization header keeps its shape), run
for a minute by the CI `Fuzz` job. It loads `api.py` by path, so it needs
aiohttp and yarl but no Home Assistant. It already found that `quote()` keeps
`.`/`..` segments, which a proxy resolves to the parent.

`.github/workflows/validate.yml` runs `hassfest` and HACS validation (also
weekly). Pushes to `main` publish the MkDocs site (`docs.yml`); pull requests
build it with `mkdocs build --strict`. `scorecard.yml` keeps
`permissions: read-all` at the top because `publish_results` refuses any
top-level write scope; every other workflow is `contents: read` at the top and
grants write scopes per job. Actions are pinned by commit SHA with the version
in a trailing comment; Dependabot updates them and its PRs squash-merge
themselves once every check is green (`dependabot-automerge.yml`).

`main` is protected: changes go through a pull request and need the checks
`Ruff`, `Tests`, `Fuzz`, `hassfest`, `HACS`, `Analyze (python)`,
`Analyze (actions)` and `Build`. Admins bypass it. `Tests` aggregates the
matrix; require it, never a `Tests (HA ...)` row. Renaming a job means updating
the required list (`gh api -X PUT repos/FiveElements/ha-jellyfin/branches/main/protection`)
in the same change.

### Windows

Home Assistant imports `fcntl`, so the suite cannot run natively. Docker
Desktop is the local stand-in for CI; do not claim a change is tested until
this (or Ubuntu CI) has run:

```bash
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "$PWD:/src" -v ha-jellyfin-pip:/root/.cache/pip -w /src \
  python:3.14-bookworm bash -lc '
    pip install -q pytest-homeassistant==2026.11.0b6 pytest-cov==7.1.0 mypy==1.18.2 ruff==0.17.3
    pytest tests -q --cov=custom_components.jellyfin_control --cov-fail-under=95
    mypy custom_components/jellyfin_control
  '
```

### Releasing

`git tag vX.Y.Z && git push origin vX.Y.Z`. The tag is the only version: the
release workflow stamps it into the `manifest.json` inside
`jellyfin_control.zip`, which HACS installs (`zip_release`). The repository's
`manifest.json` and `pyproject.toml` stay at `0.0.0` on purpose; do not bump
them.

## Architecture

| Module | Role |
| ------ | ---- |
| `api.py` | `JellyfinClient`. No Home Assistant import; takes the injected aiohttp session; raises `JellyfinAuthError` (401), `JellyfinForbiddenError` (403), `JellyfinNotFoundError` (404), `JellyfinConnectionError` (transport, redirect, non-JSON), `JellyfinApiError`. |
| `coordinator.py` | Polls `/Sessions?controllableByUserId=` and keys sessions by **device id**; fetches `/Items/{id}` once per playing video that lacks `MediaStreams` (cached, pruned when it stops); reverse-DNS names of **local** client addresses (cached an hour, never blocks the poll) name client devices. |
| `library.py` | Library tree as `LibraryNode` values, search, folder expansion (`playable_ids`), queue. Shared by media player, media source and actions. |
| `entity.py` | Base entities, device info, `call_api()` (client error -> translated HA error), `async_add_client_entities()`: every client platform adds entities through it (offline devices restored from the registries, live sessions on each update, ids forgotten on removal so a deleted device comes back). |
| `useragent.py` | No Home Assistant import. Reads the OS and browser from a Jellyfin Web device id (base64 User-Agent, `=` written `1`), so clients all named "Chrome" can be told apart. |
| `media_player.py` | One per device; all controls; entity action handlers (`async_*` methods named in `services.py`). |
| `remote.py` / `select.py` / `sensor.py` / `button.py` | Navigation, track selection, session counts, library scan. |
| `media_source.py` | Library as a media source; identifiers are `<entry_id>/<node_id>`; resolves to a `views.py` stream path. |
| `views.py` | Authenticated HA views relaying artwork and streams (Range pass-through, 404/416 passed, other errors 502); `signed_image_path()` and `stream_path()`. |
| `services.py` | Entity action registration on the media player domain. |
| `config_flow.py` | Server, then password or Quick Connect; reauth (another account aborts with `wrong_account` after revoking the new token); reconfigure; options (reload on change). |

`entry.runtime_data` is `JellyfinData(client, coordinator)`.

## Invariants worth knowing

- **Only the access token is stored, never the password.** Quick Connect and
  password sign-in both end in `_async_finish`. `async_remove_entry` revokes the
  token (`POST /Sessions/Logout`) and must not fail when the server is gone.
- **Entities are keyed on the device id, not the session id.** Session ids
  change on every reconnection; the official integration created a new entity
  each time. When several sessions share a device id, the most recently active
  wins. Home Assistant's own session and sessions reporting the server id as
  device (Jellystat) are dropped.
- **A disconnected client is `off`, not unavailable** (media player, remote).
  Client entities are unavailable only when the poll fails; selects are
  unavailable unless the playing item has such tracks and the client
  advertises the command.
- **Features follow `SupportedCommands`.** Never advertise a feature the client
  did not; actions needing an unadvertised command raise
  `command_unsupported` before calling the server. `send_command` and raw
  remote commands are the deliberate exception: they are sent unfiltered.
- **Redirects are never followed** and every path segment is percent-encoded
  (`.`/`..` included): the token must not leave for another host or endpoint.
- **Artwork goes through the Home Assistant proxy** with the integration's
  session (`async_get_media_image`, `async_get_browse_image`); browse
  thumbnails carry an `ImageRef` key, never a token.
- **The token never appears in a URL.** Media-source streams and thumbnails
  point to `views.py` (`/api/jellyfin_control/...`, signed by Home Assistant);
  the view calls Jellyfin with the token in the `Authorization` header.
  `api.py` stream builders return URLs without `api_key`. A stream that fails
  after its headers were sent closes the connection (`force_close`) so the
  player does not wait for missing bytes.
- **Default audio codec is MP3** (`AUDIO_FORMATS` maps codec to container and
  MIME type, which `mime_type()` must agree with). `ITEM_FIELDS` deliberately
  omits `MediaSources`: browse lists stay small; streams use the item id.
- **Folders are expanded into at most `MAX_QUEUE_ITEMS` (200) ids**: they travel
  in the query string of `POST /Sessions/{id}/Playing`.
- **Virtual browse ids contain a colon** (`special:resume`, `artists:<lib>`,
  `albums:<lib>`, `songs:<lib>`, `latest:<lib>`); Jellyfin ids are 32 hex
  digits and cannot collide.
- **The server device is created in `async_setup_entry` before the platforms**:
  client devices link to it with `via_device_id` (`via_device` is deprecated).

## Quality scale

`custom_components/jellyfin_control/quality_scale.yaml` is the checklist.
Silver is held by `--cov-fail-under=95`; Platinum by `mypy --strict`. Change the
code *or* the comment, not the status alone.

## Conventions

- Ruff with `D` (pydocstyle, pep257): every module, class and function needs a
  docstring; line length 88; target `py314`. Tests are exempt from
  `D100/D103/D104`.
- Comments explain *why*, usually naming the failure mode the code prevents.
- New user-facing strings go in both `translations/en.json` and
  `translations/fr.json`. Config-flow fields need `data_description`.
- `README.md` is the user documentation; keep its claims (limits, privacy)
  honest and update `docs/` alongside behaviour changes.
- Tests use `FakeJellyfin` (`tests/conftest.py`): the real client runs against
  an in-memory server through an injected session. Override an endpoint with
  `fake.set(method, path, (status, body) | handler | exception)`.
