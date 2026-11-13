"""Media player entities: one per Jellyfin client device."""

from __future__ import annotations

from functools import partial
import random
from typing import Any, cast, override

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_MEDIA_ENQUEUE,
    DOMAIN as MP_DOMAIN,
    BrowseMedia,
    MediaPlayerEnqueue,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.core import HomeAssistant, ServiceResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import JellyfinError
from .const import (
    CONTENT_TYPE_MAP,
    DOMAIN,
    LOGGER,
    MAX_BROWSE_RESULTS,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_WIDTH,
    MAX_QUEUE_ITEMS,
    MAX_SEARCH_RESULTS,
    MEDIA_TYPE_OPTIONS,
    TICKS_PER_SECOND,
)
from .coordinator import (
    JellyfinConfigEntry,
    JellyfinCoordinator,
    supports_media_control,
)
from .entity import (
    JellyfinClientEntity,
    async_add_client_entities,
    call_api,
)
from .library import (
    ImageRef,
    Library,
    LibraryNode,
    image_ref,
    search_types,
    summarize,
)
from .useragent import client_browser

PARALLEL_UPDATES = 0

REPEAT_TO_JELLYFIN = {
    RepeatMode.OFF: "RepeatNone",
    RepeatMode.ONE: "RepeatOne",
    RepeatMode.ALL: "RepeatAll",
}
REPEAT_FROM_JELLYFIN = {v: k for k, v in REPEAT_TO_JELLYFIN.items()}

ENQUEUE_TO_COMMAND = {
    MediaPlayerEnqueue.PLAY: "PlayNow",
    MediaPlayerEnqueue.REPLACE: "PlayNow",
    MediaPlayerEnqueue.NEXT: "PlayNext",
    MediaPlayerEnqueue.ADD: "PlayLast",
}

MEDIA_CONTROL_FEATURES = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.MEDIA_ENQUEUE
)
LIBRARY_FEATURES = (
    MediaPlayerEntityFeature.BROWSE_MEDIA | MediaPlayerEntityFeature.SEARCH_MEDIA
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JellyfinConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a media player for each client device, now and when one appears."""
    coordinator = entry.runtime_data.coordinator
    async_add_client_entities(
        entry,
        MP_DOMAIN,
        async_add_entities,
        lambda key: [JellyfinMediaPlayer(coordinator, key)],
    )


def _ticks_to_seconds(ticks: Any) -> int | None:
    if ticks is None:
        return None
    return int(ticks) // TICKS_PER_SECOND


class JellyfinMediaPlayer(JellyfinClientEntity, MediaPlayerEntity):
    """A Jellyfin client seen as a media player."""

    _attr_name = None
    _attr_media_image_remotely_accessible = False

    def __init__(self, coordinator: JellyfinCoordinator, device_key: str) -> None:
        """Initialize the player."""
        super().__init__(coordinator, device_key, None)
        self._library = Library(coordinator.client)
        self._last_position: tuple[Any, ...] | None = None
        self._update_attributes()

    # -- state ----------------------------------------------------------

    @property
    @override
    def available(self) -> bool:
        """A disconnected client is off, not unavailable."""
        return self.coordinator.last_update_success

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        self._update_attributes()
        super()._handle_coordinator_update()

    @callback
    def _update_attributes(self) -> None:
        """Copy the session into the entity attributes."""
        item = self.now_playing
        state = self.play_state
        position = _ticks_to_seconds(state.get("PositionTicks")) if item else None
        # The frontend extrapolates position + (now - updated_at) while
        # playing. Refreshing the timestamp only when the position moved made
        # a resume after a long pause jump ahead by the whole pause.
        marker = (item.get("Id") if item else None, state.get("IsPaused"), position)
        if marker != self._last_position:
            self._attr_media_position_updated_at = dt_util.utcnow() if item else None
            self._last_position = marker
        self._attr_media_position = position
        # Cleared when absent: the previous session's level stayed displayed.
        volume = state.get("VolumeLevel")
        self._attr_volume_level = None if volume is None else float(volume) / 100
        self._attr_is_volume_muted = bool(state.get("IsMuted"))
        self._attr_repeat = REPEAT_FROM_JELLYFIN.get(
            str(state.get("RepeatMode")), RepeatMode.OFF
        )
        order = state.get("PlaybackOrder") or state.get("ShuffleMode")
        self._attr_shuffle = order == "Shuffle"
        session = self.session
        if session is not None:
            self._attr_app_name = session.get("Client")

    @property
    @override
    def state(self) -> MediaPlayerState:
        """Return the player state."""
        if self.session is None:
            return MediaPlayerState.OFF
        if self.now_playing is None:
            return MediaPlayerState.IDLE
        if self.play_state.get("IsPaused"):
            return MediaPlayerState.PAUSED
        return MediaPlayerState.PLAYING

    @property
    @override
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Derive the features from the commands the client advertises."""
        session = self.session
        features = LIBRARY_FEATURES
        if session is None:
            return features
        if supports_media_control(session):
            features |= MEDIA_CONTROL_FEATURES
        if self.supports("SetVolume"):
            features |= MediaPlayerEntityFeature.VOLUME_SET
        if self.supports("VolumeUp") and self.supports("VolumeDown"):
            features |= MediaPlayerEntityFeature.VOLUME_STEP
        if (self.supports("Mute") and self.supports("Unmute")) or self.supports(
            "ToggleMute"
        ):
            features |= MediaPlayerEntityFeature.VOLUME_MUTE
        if self.supports("SetShuffleQueue"):
            features |= MediaPlayerEntityFeature.SHUFFLE_SET
        if self.supports("SetRepeatMode"):
            features |= MediaPlayerEntityFeature.REPEAT_SET
        return features

    @property
    @override
    def media_content_id(self) -> str | None:
        """Return the id of the playing item."""
        item = self.now_playing
        return str(item["Id"]) if item and item.get("Id") else None

    @property
    @override
    def media_content_type(self) -> MediaType | str | None:
        """Return the type of the playing item."""
        item = self.now_playing
        if item is None:
            return None
        item_type = str(item.get("Type"))
        if item_type == "Episode":
            return MediaType.TVSHOW
        return CONTENT_TYPE_MAP.get(item_type, MediaType.VIDEO)

    @property
    @override
    def media_title(self) -> str | None:
        """Return the title of the playing item."""
        item = self.now_playing
        return str(item.get("Name")) if item else None

    @property
    @override
    def media_duration(self) -> int | None:
        """Return the duration in seconds."""
        item = self.now_playing
        return _ticks_to_seconds(item.get("RunTimeTicks")) if item else None

    @property
    @override
    def media_series_title(self) -> str | None:
        """Return the series of the playing episode."""
        item = self.now_playing
        return item.get("SeriesName") if item else None

    @property
    @override
    def media_season(self) -> str | None:
        """Return the season number of the playing episode."""
        item = self.now_playing
        season = item.get("ParentIndexNumber") if item else None
        return None if season is None else str(season)

    @property
    @override
    def media_episode(self) -> str | None:
        """Return the episode number of the playing episode."""
        item = self.now_playing
        if not item or item.get("Type") != "Episode":
            return None
        episode = item.get("IndexNumber")
        return None if episode is None else str(episode)

    @property
    @override
    def media_album_name(self) -> str | None:
        """Return the album of the playing track."""
        item = self.now_playing
        return item.get("Album") if item else None

    @property
    @override
    def media_album_artist(self) -> str | None:
        """Return the album artist of the playing track."""
        item = self.now_playing
        return item.get("AlbumArtist") if item else None

    @property
    @override
    def media_artist(self) -> str | None:
        """Return the artists of the playing track."""
        item = self.now_playing
        artists = item.get("Artists") if item else None
        return ", ".join(str(a) for a in artists) if artists else None

    @property
    @override
    def media_track(self) -> int | None:
        """Return the track number of the playing track."""
        item = self.now_playing
        if not item or item.get("Type") != "Audio":
            return None
        track = item.get("IndexNumber")
        return None if track is None else int(track)

    @property
    @override
    def media_channel(self) -> str | None:
        """Return the channel of a live TV program."""
        item = self.now_playing
        return item.get("ChannelName") if item else None

    @property
    def _image(self) -> ImageRef | None:
        item = self.now_playing
        return image_ref(item) if item else None

    @property
    @override
    def media_image_hash(self) -> str | None:
        """Change whenever the artwork changes so the proxy refetches it."""
        image = self._image
        return image.key() if image else None

    @property
    @override
    def media_image_url(self) -> str | None:
        """Return the artwork URL (fetched through the HA image proxy)."""
        image = self._image
        if image is None:
            return None
        return self.coordinator.client.image_url(
            image.item_id, image.image_type, image.tag, MAX_IMAGE_WIDTH
        )

    @override
    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Fetch the artwork with the integration's own HTTP session."""
        return await self._fetch_image(self._image)

    @override
    async def async_get_browse_image(
        self,
        media_content_type: str,
        media_content_id: str,
        media_image_id: str | None = None,
    ) -> tuple[bytes | None, str | None]:
        """Fetch a media browser thumbnail."""
        image = ImageRef.parse(media_image_id) if media_image_id else None
        return await self._fetch_image(image)

    async def _fetch_image(
        self, image: ImageRef | None
    ) -> tuple[bytes | None, str | None]:
        if image is None:
            return None, None
        try:
            result = await self.coordinator.client.image(
                image.item_id,
                image.image_type,
                image.tag,
                MAX_IMAGE_WIDTH,
                MAX_IMAGE_BYTES,
            )
        except JellyfinError as err:
            LOGGER.debug("Artwork %s unavailable: %s", image.key(), err)
            return None, None
        return (None, None) if result is None else result

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the session details automations need."""
        session = self.session
        if session is None:
            return None
        state = self.play_state
        queue = session.get("NowPlayingQueue") or []
        attributes: dict[str, Any] = {
            "session_id": session.get("Id"),
            "user_name": session.get("UserName"),
            "client": session.get("Client"),
            "client_version": session.get("ApplicationVersion"),
            # Tell apart clients sharing a name (every browser tab is
            # "Chrome"): the machine it runs on. The address itself stays
            # private (README); only its local DNS name is shown.
            "host_name": self.coordinator.host_name(session),
            "last_activity": session.get("LastActivityDate"),
            "queue_size": len(queue),
            "play_method": state.get("PlayMethod"),
            "audio_stream_index": state.get("AudioStreamIndex"),
            "subtitle_stream_index": state.get("SubtitleStreamIndex"),
        }
        if browser := client_browser(session.get("DeviceId")):
            attributes["operating_system"] = browser.system
            attributes["browser"] = browser.label
        if item := self.now_playing:
            attributes["media_type"] = item.get("Type")
        return attributes

    # -- playback commands ------------------------------------------------

    @override
    async def async_media_play(self) -> None:
        """Resume playback."""
        await self.async_playstate("Unpause")

    @override
    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self.async_playstate("Pause")

    @override
    async def async_media_play_pause(self) -> None:
        """Toggle playback."""
        await self.async_playstate("PlayPause")

    @override
    async def async_media_stop(self) -> None:
        """Stop playback."""
        await self.async_playstate("Stop")

    @override
    async def async_media_next_track(self) -> None:
        """Skip to the next item."""
        await self.async_playstate("NextTrack")

    @override
    async def async_media_previous_track(self) -> None:
        """Go back to the previous item."""
        await self.async_playstate("PreviousTrack")

    @override
    async def async_media_seek(self, position: float) -> None:
        """Seek to a position in seconds."""
        await self.async_playstate("Seek", int(position * TICKS_PER_SECOND))

    async def async_seek_relative(self, seconds: float) -> None:
        """Seek forward or backward from the current position."""
        current = int(self.play_state.get("PositionTicks") or 0)
        # The position is as old as the last poll (up to the scan interval):
        # while playing, add the time elapsed since, as the frontend does.
        updated = self._attr_media_position_updated_at
        if self.state == MediaPlayerState.PLAYING and updated is not None:
            elapsed = (dt_util.utcnow() - updated).total_seconds()
            current += int(max(0.0, elapsed) * TICKS_PER_SECOND)
        target = current + int(seconds * TICKS_PER_SECOND)
        if duration := (self.now_playing or {}).get("RunTimeTicks"):
            target = min(target, int(duration))
        await self.async_playstate("Seek", max(0, target))

    @override
    async def async_set_volume_level(self, volume: float) -> None:
        """Set the volume (0..1)."""
        await self.async_general_command("SetVolume", {"Volume": round(volume * 100)})

    @override
    async def async_volume_up(self) -> None:
        """Raise the volume one step."""
        await self.async_general_command("VolumeUp")

    @override
    async def async_volume_down(self) -> None:
        """Lower the volume one step."""
        await self.async_general_command("VolumeDown")

    @override
    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute."""
        if self.supports("Mute") and self.supports("Unmute"):
            await self.async_general_command("Mute" if mute else "Unmute")
        elif mute != self.is_volume_muted:
            await self.async_general_command("ToggleMute")

    @override
    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Shuffle or restore the queue order."""
        await self.async_general_command(
            "SetShuffleQueue", {"ShuffleMode": "Shuffle" if shuffle else "Sorted"}
        )

    @override
    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Set the repeat mode."""
        await self.async_general_command(
            "SetRepeatMode", {"RepeatMode": REPEAT_TO_JELLYFIN[repeat]}
        )

    # -- play media --------------------------------------------------------

    def _item_id(self, media_id: str) -> str:
        """Accept a bare item id or a media-source id of this integration."""
        if not media_source.is_media_source_id(media_id):
            return media_id
        if media_id.rstrip("/") == f"media-source://{DOMAIN}":
            return ""
        prefix = f"media-source://{DOMAIN}/"
        if not media_id.startswith(prefix):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_media_source",
                translation_placeholders={"media_id": media_id},
            )
        identifier = media_id[len(prefix) :]
        # Media-source identifiers are <entry_id>/<item_id>; an item of
        # another server would be looked up here and reported missing.
        entry_id, _, item_id = identifier.rpartition("/")
        if entry_id and entry_id != self.coordinator.config_entry.entry_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unsupported_media_source",
                translation_placeholders={"media_id": media_id},
            )
        return item_id

    @override
    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play or queue an item, expanding folders into their contents."""
        item_id = self._item_id(media_id)
        if not item_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="nothing_to_play"
            )
        enqueue = kwargs.get(ATTR_MEDIA_ENQUEUE) or MediaPlayerEnqueue.PLAY
        await self.async_play_items([item_id], enqueue=MediaPlayerEnqueue(enqueue))

    async def async_play_media_shuffle(self, media_content_id: str) -> None:
        """Play an item or folder shuffled, replacing the queue."""
        await self.async_play_items([self._item_id(media_content_id)], shuffle=True)

    async def async_play_items(
        self,
        item_ids: list[str],
        *,
        enqueue: MediaPlayerEnqueue = MediaPlayerEnqueue.PLAY,
        shuffle: bool = False,
        start_index: int | None = None,
        start_position: float | None = None,
    ) -> None:
        """Send items to the client, expanding folders into their contents."""
        session_id = self.session_id
        # An empty id would reach /Items/ (the query endpoint), not an item.
        item_ids = [item_id for item_id in item_ids if item_id]
        ids: list[str] = []
        for item_id in item_ids:
            ids += await call_api(
                partial(self._library.playable_ids, item_id, shuffle=shuffle)
            )
        ids = list(dict.fromkeys(ids))
        if shuffle:
            # playable_ids only shuffles inside a folder: single items and the
            # order between folders stayed as given.
            random.shuffle(ids)
        ids = ids[:MAX_QUEUE_ITEMS]
        if not ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="nothing_to_play"
            )
        ticks = (
            int(start_position * TICKS_PER_SECOND)
            if start_position is not None
            else None
        )
        command = ENQUEUE_TO_COMMAND[enqueue]
        await call_api(
            lambda: self.coordinator.client.play(
                session_id,
                command,
                ids,
                start_index=start_index,
                start_position_ticks=ticks,
            )
        )
        await self.coordinator.async_request_refresh()

    async def async_play_instant_mix(self, item_id: str) -> None:
        """Play a mix built by the server from an item."""
        result = await call_api(
            lambda: self.coordinator.client.instant_mix(item_id, MAX_QUEUE_ITEMS)
        )
        ids = [str(i["Id"]) for i in result["Items"] if i.get("Id")]
        if not ids:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="nothing_to_play"
            )
        session_id = self.session_id
        await call_api(lambda: self.coordinator.client.play(session_id, "PlayNow", ids))
        await self.coordinator.async_request_refresh()

    # -- client UI ---------------------------------------------------------

    async def async_send_message(
        self, message: str, title: str = "", timeout: float = 5
    ) -> None:
        """Show a message on the client screen."""
        self.require_command("DisplayMessage")
        session_id = self.session_id
        await call_api(
            lambda: self.coordinator.client.send_message(
                session_id, title, message, int(timeout * 1000)
            )
        )

    async def async_send_text(self, text: str) -> None:
        """Type text into the focused field of the client."""
        self.require_command("SendString")
        await self.async_general_command("SendString", {"String": text})

    async def async_display_content(self, item_id: str) -> None:
        """Open an item's page on the client."""
        self.require_command("DisplayContent")
        item = await call_api(lambda: self.coordinator.client.item(item_id))
        await self.async_general_command(
            "DisplayContent",
            {
                "ItemId": item_id,
                "ItemType": str(item.get("Type") or ""),
                "ItemName": str(item.get("Name") or ""),
            },
        )

    async def async_send_command(
        self, command: str, arguments: dict[str, Any] | None = None
    ) -> None:
        """Send any general command with arguments."""
        await self.async_general_command(
            command, {k: str(v) for k, v in (arguments or {}).items()}
        )

    async def async_set_audio_stream(self, index: int) -> None:
        """Switch the audio track."""
        self.require_command("SetAudioStreamIndex")
        await self.async_general_command("SetAudioStreamIndex", {"Index": index})

    async def async_set_subtitle_stream(self, index: int) -> None:
        """Switch the subtitle track; -1 turns subtitles off."""
        self.require_command("SetSubtitleStreamIndex")
        await self.async_general_command("SetSubtitleStreamIndex", {"Index": index})

    # -- library -------------------------------------------------------------

    async def async_get_queue(self) -> ServiceResponse:
        """Return the play queue with titles."""
        session = self.session
        if session is None:
            return {"items": []}
        items = await call_api(lambda: self._library.queue(session))
        return cast(ServiceResponse, {"items": items})

    async def async_browse_library(
        self, parent_id: str | None = None, limit: int = MAX_BROWSE_RESULTS
    ) -> ServiceResponse:
        """Return the children of a library node (root when omitted)."""
        node = await self._node(parent_id)
        shown = node.children[:limit]
        return {
            "id": node.node_id,
            "title": node.title,
            "not_shown": node.not_shown + len(node.children) - len(shown),
            "items": [summarize(child) for child in shown],
        }

    async def async_search_library(
        self,
        query: str,
        media_types: list[str] | None = None,
        limit: int = MAX_SEARCH_RESULTS,
    ) -> ServiceResponse:
        """Search the library."""
        # Selector keys are lowercase; raw Jellyfin item types pass through.
        types = [MEDIA_TYPE_OPTIONS.get(t, t) for t in media_types or []] or None
        include_artists = types is None or "MusicArtist" in types
        if types is not None:
            types = [t for t in types if t != "MusicArtist"]
        nodes = await call_api(
            lambda: self._library.search(
                query, types, include_artists=include_artists, limit=limit
            )
        )
        return {"items": [summarize(node) for node in nodes]}

    async def async_set_user_data(
        self,
        item_id: str,
        played: bool | None = None,
        favorite: bool | None = None,
    ) -> None:
        """Mark an item played/unplayed or favorite/not favorite."""
        client = self.coordinator.client
        if played is not None:
            await call_api(lambda: client.set_played(item_id, played))
        if favorite is not None:
            await call_api(lambda: client.set_favorite(item_id, favorite))

    async def _node(self, node_id: str | None) -> LibraryNode:
        if not node_id:
            return await call_api(
                lambda: self._library.root(self.coordinator.server_name)
            )
        return await call_api(lambda: self._library.browse(node_id))

    def _to_browse(self, node: LibraryNode, *, with_children: bool) -> BrowseMedia:
        thumbnail = None
        if node.image is not None:
            thumbnail = self.get_browse_image_url(
                node.content_type, node.node_id, node.image.key()
            )
        return BrowseMedia(
            media_class=node.media_class,
            media_content_id=node.node_id,
            media_content_type=node.content_type,
            title=node.title,
            can_play=node.can_play,
            can_expand=node.can_expand,
            thumbnail=thumbnail,
            children=(
                [self._to_browse(c, with_children=False) for c in node.children]
                if with_children
                else None
            ),
            children_media_class=node.children_media_class,
            not_shown=node.not_shown,
            can_search=not node.node_id,
        )

    @override
    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Browse the library from the media panel."""
        node_id = self._item_id(media_content_id) if media_content_id else None
        return self._to_browse(await self._node(node_id), with_children=True)

    @override
    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        """Search the library from the media panel."""
        types, include_artists = search_types(query.media_filter_classes)
        nodes = await call_api(
            lambda: self._library.search(
                query.search_query,
                types,
                include_artists=include_artists,
                limit=MAX_SEARCH_RESULTS,
            )
        )
        return SearchMedia(
            result=[self._to_browse(n, with_children=False) for n in nodes]
        )
