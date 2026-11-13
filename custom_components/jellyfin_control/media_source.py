"""Media source: stream the Jellyfin library to any Home Assistant player."""

from __future__ import annotations

import mimetypes
from typing import override

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .api import AUDIO_FORMATS, JellyfinError, JsonObject
from .const import CONF_AUDIO_CODEC, DEFAULT_AUDIO_CODEC, DOMAIN
from .coordinator import JellyfinConfigEntry
from .library import Library, LibraryNode
from .views import signed_image_path, stream_path


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Set up the Jellyfin media source."""
    return JellyfinMediaSource(hass)


def mime_type(item: JsonObject, audio_codec: str = DEFAULT_AUDIO_CODEC) -> str | None:
    """Return the MIME type of what the stream relay will send.

    Audio is served in the configured codec (as is or transcoded), so its type
    follows the codec, not the file. Video is served as stored.
    """
    media_type = item.get("MediaType")
    if media_type == "Audio":
        return AUDIO_FORMATS.get(audio_codec, AUDIO_FORMATS["mp3"])[1]
    sources = item.get("MediaSources") or []
    containers = [s.get("Container") for s in sources] + [item.get("Container")]
    names = [s.get("Path") for s in sources]
    names += [f"file.{str(c).split(',')[0]}" for c in containers if c]
    for name in names:
        if name:
            guessed, _ = mimetypes.guess_type(str(name))
            if guessed:
                return guessed
    if media_type == "Video":
        return "video/mp4"
    return None


class JellyfinMediaSource(MediaSource):
    """Browse and resolve Jellyfin items for other players."""

    name = "Jellyfin Control"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    def _entries(self) -> list[JellyfinConfigEntry]:
        return [
            e
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
        ]

    def _split(self, identifier: str) -> tuple[JellyfinConfigEntry, str]:
        """Return the entry and node id of an `<entry_id>/<node_id>` identifier."""
        entries = self._entries()
        if not entries:
            raise Unresolvable(translation_domain=DOMAIN, translation_key="no_server")
        entry_id, _, node_id = identifier.partition("/")
        for entry in entries:
            if entry.entry_id == entry_id:
                return entry, node_id
        if len(entries) == 1 and "/" not in identifier:
            return entries[0], identifier
        raise Unresolvable(translation_domain=DOMAIN, translation_key="no_server")

    @override
    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Return a stream URL for an item."""
        entry, item_id = self._split(item.identifier)
        client = entry.runtime_data.client
        try:
            media = await client.item(item_id)
        except JellyfinError as err:
            raise Unresolvable(
                translation_domain=DOMAIN, translation_key="not_found"
            ) from err
        codec = entry.options.get(CONF_AUDIO_CODEC, DEFAULT_AUDIO_CODEC)
        mime = mime_type(media, codec)
        if mime is None:
            raise Unresolvable(
                translation_domain=DOMAIN, translation_key="nothing_to_play"
            )
        # A relay path, not a Jellyfin URL: the token stays between Home
        # Assistant and the server instead of landing in the player's state,
        # the recorder and the player's own logs.
        return PlayMedia(stream_path(entry.entry_id, item_id), mime)

    @override
    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse servers, then their libraries."""
        entries = self._entries()
        if not item.identifier:
            if len(entries) == 1:
                return await self._browse(entries[0], "")
            return BrowseMediaSource(
                domain=DOMAIN,
                identifier=None,
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.CHANNELS,
                title=self.name,
                can_play=False,
                can_expand=True,
                children_media_class=MediaClass.DIRECTORY,
                children=[
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=f"{e.entry_id}/",
                        media_class=MediaClass.DIRECTORY,
                        media_content_type=MediaType.CHANNELS,
                        title=e.title,
                        can_play=False,
                        can_expand=True,
                    )
                    for e in entries
                ],
            )
        entry, node_id = self._split(item.identifier)
        return await self._browse(entry, node_id)

    async def _browse(
        self, entry: JellyfinConfigEntry, node_id: str
    ) -> BrowseMediaSource:
        library = Library(entry.runtime_data.client)
        try:
            node = (
                await library.browse(node_id)
                if node_id
                else await library.root(entry.title)
            )
        except JellyfinError as err:
            raise Unresolvable(
                translation_domain=DOMAIN, translation_key="not_found"
            ) from err
        return self._to_source(entry, node, with_children=True)

    def _to_source(
        self, entry: JellyfinConfigEntry, node: LibraryNode, *, with_children: bool
    ) -> BrowseMediaSource:
        thumbnail = None
        if node.image is not None:
            thumbnail = signed_image_path(self.hass, entry.entry_id, node.image)
        codec = entry.options.get(CONF_AUDIO_CODEC, DEFAULT_AUDIO_CODEC)
        # Only single items stream; folders are expanded by the browser.
        playable = node.can_play and node.item is not None and not node.can_expand
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry.entry_id}/{node.node_id}",
            media_class=node.media_class,
            media_content_type=(
                (mime_type(node.item, codec) or node.content_type)
                if playable and node.item is not None
                else node.content_type
            ),
            title=node.title,
            can_play=playable,
            can_expand=node.can_expand,
            thumbnail=thumbnail,
            not_shown=node.not_shown,
            children_media_class=node.children_media_class,
            children=(
                [self._to_source(entry, c, with_children=False) for c in node.children]
                if with_children
                else None
            ),
        )
