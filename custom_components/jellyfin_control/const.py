"""Constants for the Jellyfin Control integration."""

import logging
from typing import Final

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.const import Platform

DOMAIN: Final = "jellyfin_control"
LOGGER = logging.getLogger(__package__)

PLATFORMS: Final = [
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
    Platform.SELECT,
    Platform.SENSOR,
]

CLIENT_NAME: Final = "Home Assistant"

CONF_ACCESS_TOKEN: Final = "access_token"
CONF_AUDIO_CODEC: Final = "audio_codec"
CONF_CLIENT_DEVICE_ID: Final = "client_device_id"
CONF_SERVER_ID: Final = "server_id"
CONF_USER_ID: Final = "user_id"

OPTION_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 5
MIN_SCAN_INTERVAL: Final = 2
MAX_SCAN_INTERVAL: Final = 300

SUPPORTED_AUDIO_CODECS: Final = ["aac", "mp3", "opus", "flac"]
DEFAULT_AUDIO_CODEC: Final = "aac"

# 1 tick = 100 ns.
TICKS_PER_SECOND: Final = 10_000_000

# Jellyfin answers in one page; the media browser has no paging, so a folder
# longer than this is truncated and reports the remainder as not_shown.
MAX_BROWSE_ITEMS: Final = 1000
# itemIds travel in the query string of POST /Sessions/{id}/Playing; past a few
# hundred ids the URL exceeds what reverse proxies accept.
MAX_QUEUE_ITEMS: Final = 200
MAX_IMAGE_WIDTH: Final = 600
MAX_IMAGE_BYTES: Final = 5 * 1024 * 1024
MAX_SEARCH_RESULTS: Final = 50
MAX_STREAMING_BITRATE: Final = "140000000"

DEFAULT_MESSAGE_TIMEOUT: Final = 5

# Playstate commands go to /Sessions/{id}/Playing/{command}; everything else
# is a general command posted to /Sessions/{id}/Command.
PLAYSTATE_COMMANDS: Final = frozenset(
    {
        "Stop",
        "Pause",
        "Unpause",
        "NextTrack",
        "PreviousTrack",
        "Seek",
        "Rewind",
        "FastForward",
        "PlayPause",
    }
)

# Short names accepted by remote.send_command, mapped to Jellyfin commands.
# Names that are not listed are sent as given, so every general command the
# client advertises stays reachable.
REMOTE_ALIASES: Final[dict[str, str]] = {
    "up": "MoveUp",
    "down": "MoveDown",
    "left": "MoveLeft",
    "right": "MoveRight",
    "select": "Select",
    "ok": "Select",
    "back": "Back",
    "home": "GoHome",
    "menu": "ToggleContextMenu",
    "context_menu": "ToggleContextMenu",
    "info": "ToggleOsdMenu",
    "osd": "ToggleOsdMenu",
    "stats": "ToggleStats",
    "fullscreen": "ToggleFullscreen",
    "settings": "GoToSettings",
    "search": "GoToSearch",
    "page_up": "PageUp",
    "page_down": "PageDown",
    "channel_up": "ChannelUp",
    "channel_down": "ChannelDown",
    "guide": "Guide",
    "volume_up": "VolumeUp",
    "volume_down": "VolumeDown",
    "mute": "ToggleMute",
    "play_pause": "PlayPause",
    "play": "Unpause",
    "pause": "Pause",
    "stop": "Stop",
    "next": "NextTrack",
    "previous": "PreviousTrack",
    "rewind": "Rewind",
    "fast_forward": "FastForward",
}

# Item types whose children are expanded into a play queue.
CONTAINER_ITEM_TYPES: Final = frozenset(
    {
        "AggregateFolder",
        "BoxSet",
        "CollectionFolder",
        "Folder",
        "MusicAlbum",
        "MusicArtist",
        "MusicGenre",
        "Genre",
        "PhotoAlbum",
        "Playlist",
        "Season",
        "Series",
        "UserView",
    }
)

CONTENT_TYPE_MAP: Final[dict[str, str]] = {
    "Audio": MediaType.MUSIC,
    "AudioBook": MediaType.MUSIC,
    "Episode": MediaType.EPISODE,
    "Season": MediaType.SEASON,
    "Series": MediaType.TVSHOW,
    "Movie": MediaType.MOVIE,
    "MusicAlbum": MediaType.ALBUM,
    "MusicArtist": MediaType.ARTIST,
    "Playlist": MediaType.PLAYLIST,
    "Video": MediaType.VIDEO,
    "MusicVideo": MediaType.VIDEO,
    "Trailer": MediaType.VIDEO,
    "TvChannel": MediaType.CHANNEL,
    "Photo": MediaType.IMAGE,
}

MEDIA_CLASS_MAP: Final[dict[str, MediaClass]] = {
    "Audio": MediaClass.TRACK,
    "AudioBook": MediaClass.TRACK,
    "MusicAlbum": MediaClass.ALBUM,
    "MusicArtist": MediaClass.ARTIST,
    "MusicGenre": MediaClass.GENRE,
    "Genre": MediaClass.GENRE,
    "Series": MediaClass.TV_SHOW,
    "Season": MediaClass.SEASON,
    "Episode": MediaClass.EPISODE,
    "Movie": MediaClass.MOVIE,
    "Video": MediaClass.VIDEO,
    "MusicVideo": MediaClass.VIDEO,
    "Trailer": MediaClass.VIDEO,
    "Playlist": MediaClass.PLAYLIST,
    "TvChannel": MediaClass.CHANNEL,
    "Photo": MediaClass.IMAGE,
    "PhotoAlbum": MediaClass.ALBUM,
    "BoxSet": MediaClass.DIRECTORY,
    "Folder": MediaClass.DIRECTORY,
    "CollectionFolder": MediaClass.DIRECTORY,
    "UserView": MediaClass.DIRECTORY,
    "AggregateFolder": MediaClass.DIRECTORY,
}

# Media classes the search filter understands, mapped to Jellyfin item types.
SEARCH_ITEM_TYPES: Final[dict[MediaClass, tuple[str, ...]]] = {
    MediaClass.MOVIE: ("Movie",),
    MediaClass.TV_SHOW: ("Series",),
    MediaClass.SEASON: ("Season",),
    MediaClass.EPISODE: ("Episode",),
    MediaClass.ALBUM: ("MusicAlbum",),
    MediaClass.TRACK: ("Audio",),
    MediaClass.MUSIC: ("Audio",),
    MediaClass.PLAYLIST: ("Playlist",),
    MediaClass.VIDEO: ("Video", "MusicVideo"),
    MediaClass.CHANNEL: ("TvChannel",),
    MediaClass.DIRECTORY: ("BoxSet",),
}
DEFAULT_SEARCH_TYPES: Final = (
    "Movie",
    "Series",
    "Episode",
    "MusicAlbum",
    "Audio",
    "Playlist",
    "BoxSet",
)

# Options of the search_library media type selector (translation keys must be
# lowercase), mapped to Jellyfin item types.
MEDIA_TYPE_OPTIONS: Final[dict[str, str]] = {
    "movie": "Movie",
    "series": "Series",
    "episode": "Episode",
    "music_album": "MusicAlbum",
    "music_artist": "MusicArtist",
    "audio": "Audio",
    "playlist": "Playlist",
    "box_set": "BoxSet",
    "tv_channel": "TvChannel",
}

ITEM_FIELDS: Final = (
    "PrimaryImageAspectRatio,Overview,MediaSources,ParentId,ChildCount,CollectionType"
)
