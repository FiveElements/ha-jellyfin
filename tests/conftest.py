"""Shared fixtures: a fake Jellyfin server behind an injected HTTP session."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Generator
import copy
from dataclasses import dataclass, field
import json
import re
from typing import Any
from unittest.mock import patch
from urllib.parse import unquote

import aiohttp
import pytest
from pytest_homeassistant.common import MockConfigEntry
from yarl import URL

from custom_components.jellyfin_control.const import (
    CONF_ACCESS_TOKEN,
    CONF_CLIENT_DEVICE_ID,
    CONF_SERVER_ID,
    CONF_USER_ID,
    DOMAIN,
)
from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant

BASE_URL = "http://jellyfin.local:8096"
SERVER_ID = "f00dfeedf00dfeedf00dfeedf00dfeed"
USER_ID = "aaaabbbbccccddddeeeeffff00001111"
TOKEN = "secret-token"
HA_DEVICE_ID = "ha-device"

ALL_COMMANDS = [
    "MoveUp",
    "MoveDown",
    "MoveLeft",
    "MoveRight",
    "Select",
    "Back",
    "GoHome",
    "ToggleOsdMenu",
    "ToggleContextMenu",
    "SetVolume",
    "VolumeUp",
    "VolumeDown",
    "Mute",
    "Unmute",
    "ToggleMute",
    "SetShuffleQueue",
    "SetRepeatMode",
    "SetAudioStreamIndex",
    "SetSubtitleStreamIndex",
    "DisplayMessage",
    "SendString",
    "DisplayContent",
]


def make_session(**overrides: Any) -> dict[str, Any]:
    """Return a playing Android TV session."""
    session: dict[str, Any] = {
        "Id": "sess1",
        "DeviceId": "dev1",
        "DeviceName": "Living room",
        "Client": "Jellyfin Android TV",
        "ApplicationVersion": "0.17.0",
        "UserName": "jerome",
        "UserId": USER_ID,
        "RemoteEndPoint": "192.168.1.20",
        "SupportsRemoteControl": True,
        "LastActivityDate": "2026-11-13T10:00:00Z",
        "Capabilities": {
            "SupportsMediaControl": True,
            "SupportedCommands": list(ALL_COMMANDS),
        },
        "PlayState": {
            "PositionTicks": 600_000_000,
            "IsPaused": False,
            "IsMuted": False,
            "VolumeLevel": 50,
            "AudioStreamIndex": 1,
            "SubtitleStreamIndex": -1,
            "RepeatMode": "RepeatAll",
            "PlaybackOrder": "Shuffle",
            "PlayMethod": "DirectPlay",
        },
        "NowPlayingItem": {
            "Id": "ep1",
            "Type": "Episode",
            "MediaType": "Video",
            "Name": "Pilot",
            "SeriesName": "Show",
            "SeriesId": "ser1",
            "ParentIndexNumber": 1,
            "IndexNumber": 2,
            "RunTimeTicks": 36_000_000_000,
            "ImageTags": {"Primary": "tag1"},
            "MediaStreams": [
                {"Type": "Video", "Index": 0},
                {"Type": "Audio", "Index": 1, "DisplayTitle": "English"},
                {"Type": "Audio", "Index": 2, "DisplayTitle": "French"},
                {"Type": "Subtitle", "Index": 3, "DisplayTitle": "French SRT"},
            ],
        },
        "NowPlayingQueue": [
            {"Id": "ep1", "PlaylistItemId": "p1"},
            {"Id": "ep2", "PlaylistItemId": "p2"},
        ],
        "PlaylistItemId": "p1",
    }
    session.update(overrides)
    return session


ITEMS: dict[str, dict[str, Any]] = {
    "ep1": {
        "Id": "ep1",
        "Type": "Episode",
        "MediaType": "Video",
        "Name": "Pilot",
        "SeriesName": "Show",
        "SeriesId": "ser1",
        "ParentIndexNumber": 1,
        "IndexNumber": 2,
        "MediaSources": [{"Id": "ms1", "Path": "/media/show/s01e02.mkv"}],
        "ImageTags": {"Primary": "tag1"},
    },
    "ep2": {"Id": "ep2", "Type": "Episode", "MediaType": "Video", "Name": "Second"},
    "mov1": {
        "Id": "mov1",
        "Type": "Movie",
        "MediaType": "Video",
        "Name": "Matrix",
        "ProductionYear": 1999,
        "RunTimeTicks": 81_600_000_000,
        "UserData": {"Played": False, "IsFavorite": True},
        "MediaSources": [{"Id": "ms2", "Path": "/media/matrix.mp4"}],
        "BackdropImageTags": ["bd1"],
    },
    "song1": {
        "Id": "song1",
        "Type": "Audio",
        "MediaType": "Audio",
        "Name": "Song",
        "IndexNumber": 1,
        "Album": "Album",
        "AlbumId": "alb1",
        "AlbumPrimaryImageTag": "atag",
        "Artists": ["Artist"],
        "MediaSources": [{"Container": "flac"}],
    },
    "ser1": {
        "Id": "ser1",
        "Type": "Series",
        "Name": "Show",
        "IsFolder": True,
        "ProductionYear": 2020,
    },
    "sea1": {
        "Id": "sea1",
        "Type": "Season",
        "Name": "Season 1",
        "IsFolder": True,
        "SeriesId": "ser1",
        "SeriesPrimaryImageTag": "stag",
    },
    "alb1": {"Id": "alb1", "Type": "MusicAlbum", "Name": "Album", "IsFolder": True},
    "art1": {"Id": "art1", "Type": "MusicArtist", "Name": "Artist", "IsFolder": True},
    "pl1": {"Id": "pl1", "Type": "Playlist", "Name": "Mix", "IsFolder": True},
    "gen1": {"Id": "gen1", "Type": "MusicGenre", "Name": "Jazz", "IsFolder": True},
    "lib_movies": {
        "Id": "lib_movies",
        "Type": "CollectionFolder",
        "Name": "Movies",
        "CollectionType": "movies",
        "IsFolder": True,
    },
    "lib_shows": {
        "Id": "lib_shows",
        "Type": "CollectionFolder",
        "Name": "Shows",
        "CollectionType": "tvshows",
        "IsFolder": True,
    },
    "lib_music": {
        "Id": "lib_music",
        "Type": "CollectionFolder",
        "Name": "Music",
        "CollectionType": "music",
        "IsFolder": True,
    },
    "lib_tv": {
        "Id": "lib_tv",
        "Type": "UserView",
        "Name": "Live TV",
        "CollectionType": "livetv",
        "IsFolder": True,
    },
    "lib_other": {
        "Id": "lib_other",
        "Type": "CollectionFolder",
        "Name": "Home videos",
        "CollectionType": "homevideos",
        "IsFolder": True,
    },
    "ch1": {"Id": "ch1", "Type": "TvChannel", "Name": "News"},
    "photo1": {"Id": "photo1", "Type": "Photo", "MediaType": "Photo", "Name": "Pic"},
}


def page(*ids: str, total: int | None = None) -> dict[str, Any]:
    """Return a query result made of known items."""
    items = [copy.deepcopy(ITEMS[i]) for i in ids]
    return {"Items": items, "TotalRecordCount": len(items) if total is None else total}


@dataclass
class Call:
    """A request received by the fake server."""

    method: str
    path: str
    params: dict[str, str]
    body: Any
    headers: dict[str, str]


type Handler = Callable[["Call"], tuple[int, Any]]


@dataclass
class FakeJellyfin:
    """In-memory Jellyfin server."""

    sessions: list[dict[str, Any]] = field(default_factory=lambda: [make_session()])
    calls: list[Call] = field(default_factory=list)
    overrides: dict[tuple[str, str], tuple[int, Any] | Handler | Exception] = field(
        default_factory=dict
    )
    password: str = "pw"
    is_admin: bool = True
    quick_connect_authenticated: bool = False
    image: bytes = b"\x89PNG-image"
    base_path: str = ""

    def set(self, method: str, path: str, result: Any) -> None:
        """Override the answer of an endpoint (status/body, handler or error)."""
        self.overrides[(method, path)] = result

    def calls_to(self, method: str, path: str) -> list[Call]:
        """Return the calls made to an endpoint."""
        return [c for c in self.calls if c.method == method and c.path == path]

    def last(self, method: str, path: str) -> Call:
        """Return the last call made to an endpoint."""
        matching = self.calls_to(method, path)
        assert matching, f"no call to {method} {path}"
        return matching[-1]

    def handle(self, call: Call) -> tuple[int, Any]:
        """Answer one request."""
        override = self.overrides.get((call.method, call.path))
        if isinstance(override, Exception):
            raise override
        if callable(override):
            return override(call)
        if override is not None:
            return override
        return self._default(call)

    def _default(self, call: Call) -> tuple[int, Any]:
        path, params = call.path, call.params
        if path == "/System/Info/Public":
            return 200, {"Id": SERVER_ID, "ServerName": "Media", "Version": "10.11.2"}
        if path == "/Users/AuthenticateByName":
            if call.body.get("Pw") != self.password:
                return 401, None
            return 200, self._auth()
        if path == "/Users/AuthenticateWithQuickConnect":
            return 200, self._auth()
        if path == "/QuickConnect/Enabled":
            return 200, True
        if path == "/QuickConnect/Initiate":
            return 200, {"Secret": "qc-secret", "Code": "123456"}
        if path == "/QuickConnect/Connect":
            return 200, {
                "Secret": params.get("secret"),
                "Code": "123456",
                "Authenticated": self.quick_connect_authenticated,
            }
        if path == "/Users/Me":
            return 200, {
                "Id": USER_ID,
                "Name": "jerome",
                "Policy": {"IsAdministrator": self.is_admin},
            }
        if path == "/Sessions":
            return 200, copy.deepcopy(self.sessions)
        if path.startswith("/Sessions/") or path in (
            "/Library/Refresh",
            "/Sessions/Logout",
        ):
            return 204, None
        if path == "/UserViews":
            return 200, page("lib_movies", "lib_shows", "lib_music", "lib_tv")
        if path == "/Items/Latest":
            return 200, [copy.deepcopy(ITEMS["mov1"])]
        if m := re.fullmatch(r"/Items/([^/]+)/Images/(\w+)", path):
            return 200, self.image
        if m := re.fullmatch(r"/Items/([^/]+)/InstantMix", path):
            return 200, page("song1")
        if m := re.fullmatch(r"/Items/([^/]+)", path):
            item = ITEMS.get(unquote(m.group(1)))
            return (200, copy.deepcopy(item)) if item else (404, None)
        if path == "/Items":
            if ids := params.get("Ids"):
                return 200, page(*[i for i in ids.split(",") if i in ITEMS])
            if params.get("searchTerm"):
                return 200, page("mov1", "song1")
            parent = params.get("ParentId", "")
            if params.get("IncludeItemTypes") == "Audio" or parent == "alb1":
                return 200, page("song1")
            if parent == "pl1":
                return 200, page("song1", "mov1")
            if parent == "lib_movies":
                return 200, page("mov1", total=3)
            return 200, page("mov1", "ser1")
        if path in ("/UserItems/Resume", "/Shows/NextUp"):
            return 200, page("ep1")
        if re.fullmatch(r"/Shows/[^/]+/Seasons", path):
            return 200, page("sea1")
        if re.fullmatch(r"/Shows/[^/]+/Episodes", path):
            return 200, page("ep1", "ep2")
        if path in ("/Artists", "/Artists/AlbumArtists"):
            return 200, page("art1")
        if path == "/LiveTv/Channels":
            return 200, page("ch1")
        if path.startswith(("/UserPlayedItems/", "/UserFavoriteItems/")):
            return 200, {}
        return 404, None

    def _auth(self) -> dict[str, Any]:
        return {"AccessToken": TOKEN, "User": {"Id": USER_ID, "Name": "jerome"}}


class FakeContent:
    """Stand-in for aiohttp's StreamReader."""

    def __init__(self, data: bytes) -> None:
        """Keep the body."""
        self._data = data

    async def read(self, n: int = -1) -> bytes:
        """Return up to n bytes."""
        return self._data if n < 0 else self._data[:n]


class FakeResponse:
    """Stand-in for aiohttp's ClientResponse."""

    def __init__(self, status: int, body: Any) -> None:
        """Encode the body like the server would."""
        self.status = status
        if isinstance(body, bytes):
            self._raw = body
            self.content_type = "image/png"
        else:
            self._raw = b"" if body is None else json.dumps(body).encode()
            self.content_type = "application/json"
        self.content_length = len(self._raw)
        self.content = FakeContent(self._raw)

    async def text(self) -> str:
        """Return the body as text."""
        return self._raw.decode()

    async def __aenter__(self) -> FakeResponse:
        """Enter the context."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Leave the context."""


class FakeSession:
    """Route aiohttp requests to the fake server."""

    def __init__(self, server: FakeJellyfin) -> None:
        """Bind to a server."""
        self.server = server

    def request(
        self,
        method: str,
        url: URL,
        *,
        headers: dict[str, str] | None = None,
        json: Any = None,
        **_: Any,
    ) -> FakeResponse:
        """Answer one request."""
        url = URL(str(url))
        assert url.host == "jellyfin.local"
        path = url.path
        if self.server.base_path:
            if not path.startswith(self.server.base_path):
                return FakeResponse(404, None)
            path = path[len(self.server.base_path) :]
        call = Call(method, path, dict(url.query), json, dict(headers or {}))
        self.server.calls.append(call)
        status, body = self.server.handle(call)
        return FakeResponse(status, body)

    def get(self, url: URL, **kwargs: Any) -> FakeResponse:
        """Answer a GET."""
        return self.request("GET", url, **kwargs)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Make the custom component loadable in every test."""


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Give each test its own writable config directory."""
    return hass_tmp_config_dir


@pytest.fixture
def fake() -> Generator[FakeJellyfin]:
    """Serve the fake Jellyfin to every client the integration creates."""
    server = FakeJellyfin()
    with patch(
        "custom_components.jellyfin_control.async_get_clientsession",
        return_value=FakeSession(server),
    ):
        yield server


@pytest.fixture
def entry() -> MockConfigEntry:
    """Return a configured entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Media",
        unique_id=SERVER_ID,
        data={
            CONF_URL: BASE_URL,
            CONF_VERIFY_SSL: True,
            CONF_CLIENT_DEVICE_ID: HA_DEVICE_ID,
            CONF_ACCESS_TOKEN: TOKEN,
            CONF_USER_ID: USER_ID,
            CONF_SERVER_ID: SERVER_ID,
        },
    )


@pytest.fixture
async def loaded(
    hass: HomeAssistant, fake: FakeJellyfin, entry: MockConfigEntry
) -> AsyncIterator[MockConfigEntry]:
    """Set up the integration with the fake server."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry


async def refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Poll the fake server now."""
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


def connection_error() -> Exception:
    """Return an error the client sees as an unreachable server."""
    return aiohttp.ClientConnectionError("refused")


def timeout_error() -> Exception:
    """Return a timeout."""
    return TimeoutError()
