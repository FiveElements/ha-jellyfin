"""Asynchronous client for the Jellyfin REST API.

No Home Assistant import here: the client takes an injected aiohttp session
and raises its own exceptions, which the integration translates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import quote

import aiohttp
from yarl import URL

type JsonObject = dict[str, Any]

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
# Streams last as long as the media; only connecting is bounded.
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=15)
# A browse page of 1000 items is a few MB; anything far larger is not an answer.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024

# Audio codec offered to other players -> (container, MIME type). The server
# either serves the file as is when it already matches, or transcodes to it,
# so the MIME type always describes what is sent.
AUDIO_FORMATS: dict[str, tuple[str, str]] = {
    "mp3": ("mp3", "audio/mpeg"),
    "aac": ("aac", "audio/aac"),
    "opus": ("ogg", "audio/ogg"),
    "flac": ("flac", "audio/flac"),
}
BASE_PATH_CANDIDATES = ("/jellyfin",)


class JellyfinError(Exception):
    """Base error of the Jellyfin client."""


class JellyfinConnectionError(JellyfinError):
    """The server could not be reached or answered something unreadable."""


class JellyfinAuthError(JellyfinError):
    """The server refused the credentials or the access token (401)."""


class JellyfinForbiddenError(JellyfinError):
    """The user is authenticated but lacks the right (403)."""


class JellyfinNotFoundError(JellyfinError):
    """The resource does not exist (404)."""


class JellyfinApiError(JellyfinError):
    """Any other HTTP error."""

    def __init__(self, status: int) -> None:
        """Keep the status for the log; never the response body."""
        super().__init__(f"HTTP {status}")
        self.status = status


@dataclass(frozen=True, slots=True)
class PublicInfo:
    """Unauthenticated identity of a server (GET /System/Info/Public)."""

    server_id: str
    name: str
    version: str | None


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Outcome of a login: the token is the only credential kept."""

    access_token: str
    user_id: str
    user_name: str

    def __repr__(self) -> str:
        """Keep the token out of logs and tracebacks."""
        return f"AuthResult(user_id={self.user_id!r}, access_token=***)"


@dataclass(frozen=True, slots=True)
class QuickConnectRequest:
    """A pending Quick Connect request; the secret is worth a token."""

    secret: str
    code: str
    authenticated: bool

    def __repr__(self) -> str:
        """Keep the secret out of logs and tracebacks."""
        return f"QuickConnectRequest(code={self.code!r}, secret=***)"


def normalize_url(url: str) -> str:
    """Return the server URL without trailing slash, scheme defaulting to http."""
    url = url.strip()
    if "://" not in url:
        url = f"http://{url}"
    return url.rstrip("/")


def _header_param(value: str) -> str:
    """Percent-encode a value of the auth header.

    Jellyfin URL-decodes each value, so encoding keeps quotes, commas and
    control characters from breaking the `Key="value"` syntax while a `+` or
    `%41` in a location name still arrives unchanged.
    """
    return quote(value, safe=" ")


async def _read_body(response: aiohttp.ClientResponse, limit: int) -> bytes | None:
    """Read the whole body; None as soon as it is known to exceed `limit`.

    `StreamReader.read(n)` returns what is buffered, not n bytes: a body
    spread over several TCP segments came back truncated and failed as
    "Response is not JSON" as soon as a client started playing.
    """
    if (response.content_length or 0) > limit:
        return None
    body = bytearray()
    async for chunk in response.content.iter_chunked(READ_CHUNK_SIZE):
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


def _segment(value: str) -> str:
    """Encode one path segment so an identifier cannot escape it."""
    if value in (".", ".."):
        # quote() keeps dots, and a proxy would resolve them to the parent.
        return value.replace(".", "%2E")
    return quote(value, safe="")


class JellyfinClient:
    """Thin asynchronous wrapper over the endpoints the integration uses."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        device_id: str,
        device_name: str,
        client_name: str,
        client_version: str,
        access_token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Bind the client to one server and one device identity."""
        self._session = session
        self.url = normalize_url(url)
        self._device_id = device_id
        self._device_name = device_name
        self._client_name = client_name
        self._client_version = client_version
        self.access_token = access_token
        self.user_id = user_id

    def __repr__(self) -> str:
        """Keep the token out of logs and tracebacks."""
        return f"JellyfinClient(url={self.url!r}, user_id={self.user_id!r})"

    # -- plumbing -------------------------------------------------------

    def authorization(self) -> str:
        """Return the `Authorization` header value Jellyfin expects."""
        value = (
            f'MediaBrowser Client="{_header_param(self._client_name)}", '
            f'Device="{_header_param(self._device_name)}", '
            f'DeviceId="{_header_param(self._device_id)}", '
            f'Version="{_header_param(self._client_version)}"'
        )
        if self.access_token:
            value += f', Token="{_header_param(self.access_token)}"'
        return value

    def build_url(
        self, *segments: str, params: Mapping[str, str | int | bool] | None = None
    ) -> URL:
        """Build an API URL, encoding each segment."""
        path = "".join(f"/{_segment(s)}" for s in segments)
        url = URL(self.url + path, encoded=True)
        if params:
            url = url.update_query(
                {
                    k: (str(v).lower() if isinstance(v, bool) else str(v))
                    for k, v in params.items()
                }
            )
        return url

    def _require_user(self) -> str:
        if not self.user_id:
            raise JellyfinAuthError("No user bound to the client")
        return self.user_id

    async def _request(
        self,
        method: str,
        *segments: str,
        params: Mapping[str, str | int | bool] | None = None,
        body: Any = None,
        authenticated: bool = True,
    ) -> Any:
        """Send one request and decode the JSON answer (None when empty)."""
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = self.authorization()
        url = self.build_url(*segments, params=params)
        try:
            async with self._session.request(
                method,
                url,
                headers=headers,
                json=body,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as response:
                _raise_for_status(response.status)
                raw = await _read_body(response, MAX_RESPONSE_BYTES)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise JellyfinConnectionError(str(err) or type(err).__name__) from err
        if raw is None:
            raise JellyfinConnectionError("Response too large")
        text = raw.decode("utf-8", errors="replace")
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError as err:
            # A reverse proxy answering its own HTML page used to surface as
            # a decode error deep inside the integration.
            raise JellyfinConnectionError("Response is not JSON") from err

    async def _get_object(
        self, *segments: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        result = await self._request("GET", *segments, params=params)
        if not isinstance(result, dict):
            raise JellyfinConnectionError("Expected a JSON object")
        return result

    async def _get_items(
        self, *segments: str, params: Mapping[str, str | int | bool] | None = None
    ) -> JsonObject:
        """GET an endpoint answering a BaseItemDtoQueryResult."""
        result = await self._get_object(*segments, params=params)
        items = result.get("Items")
        result["Items"] = [i for i in items if isinstance(i, dict)] if items else []
        result.setdefault("TotalRecordCount", len(result["Items"]))
        return result

    # -- server and authentication ------------------------------------

    async def public_info(self) -> PublicInfo:
        """Return the server identity without authentication."""
        result = await self._request(
            "GET", "System", "Info", "Public", authenticated=False
        )
        if not isinstance(result, dict) or not result.get("Id"):
            raise JellyfinConnectionError("Not a Jellyfin server")
        return PublicInfo(
            server_id=str(result["Id"]),
            name=str(result.get("ServerName") or "Jellyfin"),
            version=result.get("Version"),
        )

    async def locate_server(self) -> PublicInfo:
        """Find the API root, trying the usual base paths behind a proxy."""
        try:
            return await self.public_info()
        except JellyfinError as first:
            root = self.url
            for path in BASE_PATH_CANDIDATES:
                if root.endswith(path):
                    continue
                self.url = root + path
                try:
                    return await self.public_info()
                except JellyfinError:
                    continue
            self.url = root
            raise first

    async def authenticate(self, username: str, password: str) -> AuthResult:
        """Exchange a username and password for an access token."""
        result = await self._request(
            "POST",
            "Users",
            "AuthenticateByName",
            body={"Username": username, "Pw": password},
        )
        return self._apply_auth(result)

    async def quick_connect_enabled(self) -> bool:
        """Return whether Quick Connect is enabled on the server."""
        return bool(
            await self._request("GET", "QuickConnect", "Enabled", authenticated=True)
        )

    async def quick_connect_initiate(self) -> QuickConnectRequest:
        """Open a Quick Connect request, falling back to GET on a 405."""
        try:
            result = await self._request("POST", "QuickConnect", "Initiate")
        except JellyfinApiError as err:
            if err.status != 405:
                raise
            result = await self._request("GET", "QuickConnect", "Initiate")
        return _quick_connect(result)

    async def quick_connect_state(self, secret: str) -> QuickConnectRequest:
        """Return the state of a Quick Connect request (404 once expired)."""
        result = await self._request(
            "GET", "QuickConnect", "Connect", params={"secret": secret}
        )
        return _quick_connect(result)

    async def authenticate_quick_connect(self, secret: str) -> AuthResult:
        """Exchange an approved Quick Connect secret for an access token."""
        result = await self._request(
            "POST",
            "Users",
            "AuthenticateWithQuickConnect",
            body={"Secret": secret},
        )
        return self._apply_auth(result)

    def _apply_auth(self, result: Any) -> AuthResult:
        if not isinstance(result, dict):
            raise JellyfinConnectionError("Unexpected authentication answer")
        token = result.get("AccessToken")
        user = result.get("User")
        if not token or not isinstance(user, dict) or not user.get("Id"):
            raise JellyfinConnectionError("Unexpected authentication answer")
        self.access_token = str(token)
        self.user_id = str(user["Id"])
        return AuthResult(
            access_token=self.access_token,
            user_id=self.user_id,
            user_name=str(user.get("Name") or ""),
        )

    async def current_user(self) -> JsonObject:
        """Return the user the token belongs to (validates the token)."""
        return await self._get_object("Users", "Me")

    async def logout(self) -> None:
        """Revoke the access token."""
        await self._request("POST", "Sessions", "Logout")

    # -- sessions and remote control ----------------------------------

    async def sessions(self, active_within: int | None = None) -> list[JsonObject]:
        """Return the sessions the user may control."""
        params: dict[str, str | int | bool] = {
            "controllableByUserId": self._require_user()
        }
        if active_within is not None:
            params["activeWithinSeconds"] = active_within
        result = await self._request("GET", "Sessions", params=params)
        if not isinstance(result, list):
            raise JellyfinConnectionError("Expected a list of sessions")
        return [s for s in result if isinstance(s, dict) and s.get("Id")]

    async def playstate(
        self, session_id: str, command: str, seek_ticks: int | None = None
    ) -> None:
        """Send a playstate command (PlayPause, Stop, Seek...)."""
        params: dict[str, str | int | bool] = {}
        if seek_ticks is not None:
            params["seekPositionTicks"] = max(0, seek_ticks)
        await self._request(
            "POST", "Sessions", session_id, "Playing", command, params=params
        )

    async def general_command(
        self,
        session_id: str,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> None:
        """Send a general command (MoveUp, SetVolume, DisplayContent...)."""
        body = {
            "Name": name,
            "Arguments": {k: str(v) for k, v in (arguments or {}).items()},
        }
        await self._request("POST", "Sessions", session_id, "Command", body=body)

    async def play(
        self,
        session_id: str,
        play_command: str,
        item_ids: Sequence[str],
        *,
        start_index: int | None = None,
        start_position_ticks: int | None = None,
    ) -> None:
        """Start or queue items on a session (PlayNow, PlayNext, PlayLast)."""
        params: dict[str, str | int | bool] = {
            "playCommand": play_command,
            "itemIds": ",".join(item_ids),
        }
        if start_index is not None:
            params["startIndex"] = start_index
        if start_position_ticks is not None:
            params["startPositionTicks"] = start_position_ticks
        await self._request("POST", "Sessions", session_id, "Playing", params=params)

    async def send_message(
        self, session_id: str, header: str, text: str, timeout_ms: int
    ) -> None:
        """Display a message on the session screen."""
        await self._request(
            "POST",
            "Sessions",
            session_id,
            "Message",
            body={"Header": header, "Text": text, "TimeoutMs": timeout_ms},
        )

    async def refresh_library(self) -> None:
        """Scan every library (administrators only)."""
        await self._request("POST", "Library", "Refresh")

    # -- library ------------------------------------------------------

    async def user_views(self) -> list[JsonObject]:
        """Return the user's root libraries."""
        result = await self._get_items(
            "UserViews", params={"userId": self._require_user()}
        )
        items: list[JsonObject] = result["Items"]
        return items

    async def items(self, params: Mapping[str, str | int | bool]) -> JsonObject:
        """Query items (GET /Items) for the user."""
        return await self._get_items(
            "Items", params={"userId": self._require_user(), **params}
        )

    async def item(self, item_id: str) -> JsonObject:
        """Return one item with its streams and media sources."""
        return await self._get_object(
            "Items", item_id, params={"userId": self._require_user()}
        )

    async def resume_items(self, params: Mapping[str, str | int | bool]) -> JsonObject:
        """Return the items the user can resume."""
        return await self._get_items(
            "UserItems", "Resume", params={"userId": self._require_user(), **params}
        )

    async def next_up(self, params: Mapping[str, str | int | bool]) -> JsonObject:
        """Return the next episodes to watch."""
        return await self._get_items(
            "Shows", "NextUp", params={"userId": self._require_user(), **params}
        )

    async def latest_items(
        self, params: Mapping[str, str | int | bool]
    ) -> list[JsonObject]:
        """Return the latest items added (a bare list, not a query result)."""
        result = await self._request(
            "GET",
            "Items",
            "Latest",
            params={"userId": self._require_user(), **params},
        )
        if not isinstance(result, list):
            raise JellyfinConnectionError("Expected a list of items")
        return [i for i in result if isinstance(i, dict)]

    async def seasons(
        self, series_id: str, params: Mapping[str, str | int | bool]
    ) -> JsonObject:
        """Return the seasons of a series."""
        return await self._get_items(
            "Shows",
            series_id,
            "Seasons",
            params={"userId": self._require_user(), **params},
        )

    async def episodes(
        self, series_id: str, params: Mapping[str, str | int | bool]
    ) -> JsonObject:
        """Return episodes of a series, in airing order."""
        return await self._get_items(
            "Shows",
            series_id,
            "Episodes",
            params={"userId": self._require_user(), **params},
        )

    async def album_artists(self, params: Mapping[str, str | int | bool]) -> JsonObject:
        """Return album artists (GET /Artists/AlbumArtists)."""
        return await self._get_items(
            "Artists",
            "AlbumArtists",
            params={"userId": self._require_user(), **params},
        )

    async def artists(self, params: Mapping[str, str | int | bool]) -> JsonObject:
        """Return artists (GET /Artists), used by search."""
        return await self._get_items(
            "Artists", params={"userId": self._require_user(), **params}
        )

    async def instant_mix(self, item_id: str, limit: int) -> JsonObject:
        """Return an instant mix built from an item."""
        return await self._get_items(
            "Items",
            item_id,
            "InstantMix",
            params={"userId": self._require_user(), "limit": limit},
        )

    async def live_tv_channels(
        self, params: Mapping[str, str | int | bool]
    ) -> JsonObject:
        """Return live TV channels."""
        return await self._get_items(
            "LiveTv", "Channels", params={"userId": self._require_user(), **params}
        )

    async def set_played(self, item_id: str, played: bool) -> None:
        """Mark an item played or unplayed."""
        await self._request(
            "POST" if played else "DELETE",
            "UserPlayedItems",
            item_id,
            params={"userId": self._require_user()},
        )

    async def set_favorite(self, item_id: str, favorite: bool) -> None:
        """Add or remove an item from the favorites."""
        await self._request(
            "POST" if favorite else "DELETE",
            "UserFavoriteItems",
            item_id,
            params={"userId": self._require_user()},
        )

    # -- images and streams -------------------------------------------

    def image_url(
        self, item_id: str, image_type: str, tag: str | None, max_width: int
    ) -> str:
        """Return the URL of an image; Jellyfin serves images anonymously."""
        params: dict[str, str | int | bool] = {"maxWidth": max_width}
        if tag:
            params["tag"] = tag
        return str(
            self.build_url("Items", item_id, "Images", image_type, params=params)
        )

    async def image(
        self,
        item_id: str,
        image_type: str,
        tag: str | None,
        max_width: int,
        max_bytes: int,
    ) -> tuple[bytes, str] | None:
        """Fetch an image from the API host only; None when absent."""
        url = URL(self.image_url(item_id, image_type, tag, max_width), encoded=True)
        headers = {"Accept": "image/*", "Authorization": self.authorization()}
        try:
            async with self._session.get(
                url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=False
            ) as response:
                if response.status == 404:
                    return None
                _raise_for_status(response.status)
                content = await _read_body(response, max_bytes)
                if content is None:
                    return None
                return content, response.content_type or "image/jpeg"
        except (aiohttp.ClientError, TimeoutError) as err:
            raise JellyfinConnectionError(str(err) or type(err).__name__) from err

    def audio_stream_url(self, item_id: str, audio_codec: str) -> URL:
        """Return a universal audio stream URL; the token goes in a header."""
        container, _ = AUDIO_FORMATS.get(audio_codec, AUDIO_FORMATS["mp3"])
        return self.build_url(
            "Audio",
            item_id,
            "universal",
            params={
                "UserId": self._require_user(),
                "DeviceId": self._device_id,
                "MaxStreamingBitrate": "140000000",
                "Container": container,
                "TranscodingContainer": container,
                "TranscodingProtocol": "http",
                "AudioCodec": audio_codec,
            },
        )

    def video_stream_url(self, item_id: str, media_source_id: str | None) -> URL:
        """Return a static video stream URL; the token goes in a header."""
        params: dict[str, str | int | bool] = {
            "static": True,
            "DeviceId": self._device_id,
        }
        if media_source_id:
            params["MediaSourceId"] = media_source_id
        return self.build_url("Videos", item_id, "stream", params=params)

    def open_stream(
        self, url: URL, range_header: str | None
    ) -> AbstractAsyncContextManager[aiohttp.ClientResponse]:
        """Open a media stream with the token in the Authorization header."""
        headers = {"Authorization": self.authorization()}
        if range_header:
            headers["Range"] = range_header
        return self._session.get(
            url, headers=headers, timeout=STREAM_TIMEOUT, allow_redirects=False
        )


def _quick_connect(result: Any) -> QuickConnectRequest:
    if not isinstance(result, dict) or not result.get("Secret"):
        raise JellyfinConnectionError("Unexpected Quick Connect answer")
    return QuickConnectRequest(
        secret=str(result["Secret"]),
        code=str(result.get("Code") or ""),
        authenticated=bool(result.get("Authenticated")),
    )


def _raise_for_status(status: int) -> None:
    if status < 300:
        return
    if status == 401:
        raise JellyfinAuthError("Unauthorized")
    if status == 403:
        raise JellyfinForbiddenError("Forbidden")
    if status == 404:
        raise JellyfinNotFoundError("Not found")
    if 300 <= status < 400:
        # Redirects are not followed: the token must not leave for another host.
        raise JellyfinConnectionError(f"Unexpected redirect ({status})")
    raise JellyfinApiError(status)
