"""Entity services of the Jellyfin Control integration."""

from __future__ import annotations

from typing import Any

import probatio

from homeassistant.components.media_player import (
    ATTR_MEDIA,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_ENQUEUE,
    DOMAIN as MP_DOMAIN,
    MediaPlayerEnqueue,
    MediaPlayerEntityFeature,
)
from homeassistant.core import HomeAssistant, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv, service

from .const import DEFAULT_MESSAGE_TIMEOUT, DOMAIN, MAX_SEARCH_RESULTS

PLAY = MediaPlayerEntityFeature.PLAY_MEDIA


def _promote_media_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Accept the media selector's object as well as a bare content id."""
    media = data.pop(ATTR_MEDIA, None)
    if (
        isinstance(media, dict)
        and ATTR_MEDIA_CONTENT_ID not in data
        and (content_id := media.get(ATTR_MEDIA_CONTENT_ID))
    ):
        data[ATTR_MEDIA_CONTENT_ID] = content_id
    return data


def _schema(fields: dict[Any, Any]) -> Any:
    return cv.make_entity_service_schema(fields)


SERVICES: tuple[tuple[str, str, Any, int | None, SupportsResponse], ...] = (
    (
        "play_media_shuffle",
        "async_play_media_shuffle",
        probatio.All(
            _promote_media_fields,
            _schema({probatio.Required(ATTR_MEDIA_CONTENT_ID): cv.string}),
        ),
        PLAY,
        SupportsResponse.NONE,
    ),
    (
        "play_items",
        "async_play_items",
        _schema(
            {
                probatio.Required("item_ids"): probatio.All(
                    cv.ensure_list, [cv.string], probatio.Length(min=1)
                ),
                probatio.Optional(
                    ATTR_MEDIA_ENQUEUE, default=MediaPlayerEnqueue.PLAY
                ): probatio.Coerce(MediaPlayerEnqueue),
                probatio.Optional("shuffle", default=False): cv.boolean,
                probatio.Optional("start_index"): cv.positive_int,
                probatio.Optional("start_position"): probatio.All(
                    probatio.Coerce(float), probatio.Range(min=0)
                ),
            }
        ),
        PLAY,
        SupportsResponse.NONE,
    ),
    (
        "play_instant_mix",
        "async_play_instant_mix",
        _schema({probatio.Required("item_id"): cv.string}),
        PLAY,
        SupportsResponse.NONE,
    ),
    (
        "seek_relative",
        "async_seek_relative",
        _schema({probatio.Required("seconds"): probatio.Coerce(float)}),
        MediaPlayerEntityFeature.SEEK,
        SupportsResponse.NONE,
    ),
    (
        "send_message",
        "async_send_message",
        _schema(
            {
                probatio.Required("message"): cv.string,
                probatio.Optional("title", default=""): cv.string,
                probatio.Optional(
                    "timeout", default=DEFAULT_MESSAGE_TIMEOUT
                ): probatio.All(
                    probatio.Coerce(float), probatio.Range(min=1, max=3600)
                ),
            }
        ),
        None,
        SupportsResponse.NONE,
    ),
    (
        "send_text",
        "async_send_text",
        _schema({probatio.Required("text"): cv.string}),
        None,
        SupportsResponse.NONE,
    ),
    (
        "send_command",
        "async_send_command",
        _schema(
            {
                probatio.Required("command"): cv.string,
                probatio.Optional("arguments"): probatio.Schema(
                    {cv.string: probatio.Any(cv.string, int, float, bool)}
                ),
            }
        ),
        None,
        SupportsResponse.NONE,
    ),
    (
        "display_content",
        "async_display_content",
        _schema({probatio.Required("item_id"): cv.string}),
        None,
        SupportsResponse.NONE,
    ),
    (
        "set_audio_stream",
        "async_set_audio_stream",
        _schema({probatio.Required("index"): cv.positive_int}),
        None,
        SupportsResponse.NONE,
    ),
    (
        "set_subtitle_stream",
        "async_set_subtitle_stream",
        _schema(
            {
                probatio.Required("index"): probatio.All(
                    probatio.Coerce(int), probatio.Range(min=-1)
                )
            }
        ),
        None,
        SupportsResponse.NONE,
    ),
    (
        "set_user_data",
        "async_set_user_data",
        _schema(
            {
                probatio.Required("item_id"): cv.string,
                probatio.Optional("played"): cv.boolean,
                probatio.Optional("favorite"): cv.boolean,
            }
        ),
        None,
        SupportsResponse.NONE,
    ),
    (
        "get_queue",
        "async_get_queue",
        _schema({}),
        None,
        SupportsResponse.ONLY,
    ),
    (
        "browse_library",
        "async_browse_library",
        _schema({probatio.Optional("parent_id"): cv.string}),
        None,
        SupportsResponse.ONLY,
    ),
    (
        "search_library",
        "async_search_library",
        _schema(
            {
                probatio.Required("query"): cv.string,
                probatio.Optional("media_types"): probatio.All(
                    cv.ensure_list, [cv.string]
                ),
                probatio.Optional("limit", default=MAX_SEARCH_RESULTS): probatio.All(
                    probatio.Coerce(int), probatio.Range(min=1, max=500)
                ),
            }
        ),
        None,
        SupportsResponse.ONLY,
    ),
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the media player services of the integration."""
    for name, method, schema, feature, response in SERVICES:
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain=MP_DOMAIN,
            schema=schema,
            func=method,
            required_features=[feature] if feature is not None else None,
            supports_response=response,
        )
