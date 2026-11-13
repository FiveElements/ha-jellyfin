"""HTTP views relaying Jellyfin artwork and streams through Home Assistant.

Players and browsers fetch these paths, signed by Home Assistant, instead of
Jellyfin URLs: the access token stays in the Authorization header between
Home Assistant and the server, and the server address never reaches a client
that may not be able to resolve it (Nabu Casa, HTTPS front end).
"""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from urllib.parse import quote

import aiohttp
from aiohttp import hdrs, web

from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .api import AUDIO_FORMATS, JellyfinError, JellyfinNotFoundError
from .const import (
    CONF_AUDIO_CODEC,
    DEFAULT_AUDIO_CODEC,
    DOMAIN,
    LOGGER,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_WIDTH,
)
from .coordinator import JellyfinConfigEntry
from .library import ImageRef

IMAGE_URL = "/api/jellyfin_control/{entry_id}/image/{item_id}/{image_type}/{tag}"
STREAM_URL = "/api/jellyfin_control/{entry_id}/stream/{item_id}"
# Long enough to browse a library, short enough that a leaked link expires.
THUMBNAIL_SIGNATURE_TTL = timedelta(hours=24)
CHUNK_SIZE = 64 * 1024
FORWARDED_HEADERS = (
    hdrs.CONTENT_TYPE,
    hdrs.CONTENT_LENGTH,
    hdrs.CONTENT_RANGE,
    hdrs.ACCEPT_RANGES,
)


@callback
def async_register_views(hass: HomeAssistant) -> None:
    """Register the relay views once."""
    hass.http.register_view(JellyfinImageView(hass))
    hass.http.register_view(JellyfinStreamView(hass))


def stream_path(entry_id: str, item_id: str) -> str:
    """Return the unsigned relay path of a stream.

    It carries no query string, so Home Assistant signs it for the player that
    receives it (async_process_play_media_url).
    """
    return STREAM_URL.format(entry_id=entry_id, item_id=quote(item_id, safe=""))


def signed_image_path(hass: HomeAssistant, entry_id: str, image: ImageRef) -> str:
    """Return a signed relay path for a thumbnail."""
    path = IMAGE_URL.format(
        entry_id=entry_id,
        item_id=quote(image.item_id, safe=""),
        image_type=quote(image.image_type, safe=""),
        tag=quote(image.tag or "-", safe=""),
    )
    signed: str = async_sign_path(hass, path, THUMBNAIL_SIGNATURE_TTL)
    return signed


def _entry(hass: HomeAssistant, entry_id: str) -> JellyfinConfigEntry | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    if entry.state is not ConfigEntryState.LOADED:
        return None
    return entry


class JellyfinImageView(HomeAssistantView):
    """Relay an item image."""

    url = IMAGE_URL
    name = "api:jellyfin_control:image"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Keep the Home Assistant instance."""
        self.hass = hass

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        item_id: str,
        image_type: str,
        tag: str,
    ) -> web.Response:
        """Return the image bytes."""
        entry = _entry(self.hass, entry_id)
        if entry is None or not image_type.isalpha():
            return web.Response(status=HTTPStatus.NOT_FOUND)
        try:
            result = await entry.runtime_data.client.image(
                item_id,
                image_type,
                None if tag == "-" else tag,
                MAX_IMAGE_WIDTH,
                MAX_IMAGE_BYTES,
            )
        except JellyfinError as err:
            LOGGER.debug("Image %s/%s unavailable: %s", item_id, image_type, err)
            return web.Response(status=HTTPStatus.BAD_GATEWAY)
        if result is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        content, content_type = result
        return web.Response(
            body=content,
            content_type=content_type,
            headers={hdrs.CACHE_CONTROL: "private, max-age=86400"},
        )


class JellyfinStreamView(HomeAssistantView):
    """Relay a media stream, Range requests included (seeking)."""

    url = STREAM_URL
    name = "api:jellyfin_control:stream"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Keep the Home Assistant instance."""
        self.hass = hass

    async def get(
        self, request: web.Request, entry_id: str, item_id: str
    ) -> web.StreamResponse:
        """Stream the item from Jellyfin to the caller."""
        entry = _entry(self.hass, entry_id)
        if entry is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        client = entry.runtime_data.client
        try:
            item = await client.item(item_id)
        except JellyfinNotFoundError:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        except JellyfinError as err:
            LOGGER.debug("Stream %s unavailable: %s", item_id, err)
            return web.Response(status=HTTPStatus.BAD_GATEWAY)
        fallback_type = None
        if item.get("MediaType") == "Audio":
            codec = entry.options.get(CONF_AUDIO_CODEC, DEFAULT_AUDIO_CODEC)
            url = client.audio_stream_url(item_id, codec)
            fallback_type = AUDIO_FORMATS.get(codec, AUDIO_FORMATS["mp3"])[1]
        else:
            sources = item.get("MediaSources") or [{}]
            url = client.video_stream_url(item_id, sources[0].get("Id"))

        response: web.StreamResponse | None = None
        try:
            async with client.open_stream(
                url, request.headers.get(hdrs.RANGE)
            ) as upstream:
                if upstream.status not in (HTTPStatus.OK, HTTPStatus.PARTIAL_CONTENT):
                    return web.Response(status=_relay_status(upstream.status))
                response = web.StreamResponse(status=upstream.status)
                for header in FORWARDED_HEADERS:
                    if value := upstream.headers.get(header):
                        response.headers[header] = value
                if fallback_type and hdrs.CONTENT_TYPE not in response.headers:
                    response.headers[hdrs.CONTENT_TYPE] = fallback_type
                await response.prepare(request)
                async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except (aiohttp.ClientError, ConnectionError, TimeoutError) as err:
            # A player closing the connection mid-stream lands here too: that
            # is how seeking and stopping look, not worth a warning.
            LOGGER.debug("Stream %s interrupted: %s", item_id, err)
            if response is not None and response.prepared:
                # Headers are gone: close the connection so the player sees a
                # short body instead of waiting for bytes that never come.
                response.force_close()
                return response
            return web.Response(status=HTTPStatus.BAD_GATEWAY)


def _relay_status(status: int) -> int:
    """Pass through statuses the player can act on; anything else is a 502."""
    if status in (HTTPStatus.NOT_FOUND, HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE):
        return status
    return HTTPStatus.BAD_GATEWAY
