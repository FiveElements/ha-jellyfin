"""Library navigation shared by the media player, media source and services.

Browsing is expressed once as plain `LibraryNode` values; the media player
and the media source only adapt them to their own BrowseMedia flavour.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import random

from homeassistant.components.media_player import MediaClass

from .api import JellyfinClient, JsonObject
from .const import (
    CONTAINER_ITEM_TYPES,
    CONTENT_TYPE_MAP,
    DEFAULT_SEARCH_TYPES,
    ITEM_FIELDS,
    MAX_BROWSE_ITEMS,
    MAX_QUEUE_ITEMS,
    MEDIA_CLASS_MAP,
    SEARCH_ITEM_TYPES,
)

type Params = dict[str, str | int | bool]

# Virtual folders. Item ids are 32 hex digits, so a colon cannot collide.
SPECIAL_RESUME = "special:resume"
SPECIAL_NEXT_UP = "special:next_up"
SPECIAL_LATEST = "special:latest"
SPECIAL_FAVORITES = "special:favorites"
PREFIX_ARTISTS = "artists:"
PREFIX_ALBUMS = "albums:"
PREFIX_SONGS = "songs:"

SPECIAL_TITLES: dict[str, str] = {
    SPECIAL_RESUME: "Continue watching",
    SPECIAL_NEXT_UP: "Next up",
    SPECIAL_LATEST: "Recently added",
    SPECIAL_FAVORITES: "Favorites",
}

PLAYABLE_MEDIA_TYPES = frozenset({"Video", "Audio"})

_SORT_BY_NAME = {"SortBy": "SortName", "SortOrder": "Ascending"}
_SORT_TRACKS = {"SortBy": "ParentIndexNumber,IndexNumber,SortName"}


@dataclass(frozen=True, slots=True)
class ImageRef:
    """Where an item's artwork lives."""

    item_id: str
    image_type: str
    tag: str | None

    def key(self) -> str:
        """Encode the reference as one opaque string."""
        return f"{self.item_id}/{self.image_type}/{self.tag or ''}"

    @staticmethod
    def parse(key: str) -> ImageRef | None:
        """Decode a key built by `key()`; None when malformed."""
        parts = key.split("/")
        if len(parts) != 3 or not parts[0] or not parts[1].isalpha():
            return None
        return ImageRef(parts[0], parts[1], parts[2] or None)


@dataclass(slots=True)
class LibraryNode:
    """One entry of the library tree, independent of any HA flavour."""

    node_id: str
    title: str
    media_class: MediaClass | str
    content_type: str
    can_play: bool
    can_expand: bool
    image: ImageRef | None = None
    item: JsonObject | None = None
    children: list[LibraryNode] = field(default_factory=list)
    not_shown: int = 0
    children_media_class: MediaClass | str | None = None


def item_title(item: JsonObject) -> str:
    """Return a readable title, with series and episode numbers if any."""
    name = str(item.get("Name") or item.get("Id") or "")
    item_type = item.get("Type")
    if item_type == "Episode":
        season = item.get("ParentIndexNumber")
        episode = item.get("IndexNumber")
        prefix = ""
        if season is not None and episode is not None:
            prefix = f"S{season:02d}E{episode:02d} - "
        series = item.get("SeriesName")
        return f"{series} - {prefix}{name}" if series else f"{prefix}{name}"
    if item_type == "Audio" and item.get("IndexNumber") is not None:
        return f"{item['IndexNumber']}. {name}"
    if item_type in ("Movie", "Series") and item.get("ProductionYear"):
        return f"{name} ({item['ProductionYear']})"
    return name


def image_ref(item: JsonObject) -> ImageRef | None:
    """Pick the best artwork of an item, falling back to its parents."""
    item_id = str(item.get("Id") or "")
    tags = item.get("ImageTags") or {}
    if isinstance(tags, dict) and item_id:
        for image_type in ("Primary", "Thumb"):
            if tag := tags.get(image_type):
                return ImageRef(item_id, image_type, str(tag))
    if (album := item.get("AlbumId")) and (tag := item.get("AlbumPrimaryImageTag")):
        return ImageRef(str(album), "Primary", str(tag))
    if (series := item.get("SeriesId")) and (tag := item.get("SeriesPrimaryImageTag")):
        return ImageRef(str(series), "Primary", str(tag))
    backdrops = item.get("BackdropImageTags") or []
    if backdrops and item_id:
        return ImageRef(item_id, "Backdrop", str(backdrops[0]))
    parent = item.get("ParentBackdropItemId")
    parent_tags = item.get("ParentBackdropImageTags") or []
    if parent and parent_tags:
        return ImageRef(str(parent), "Backdrop", str(parent_tags[0]))
    return None


def is_container(item: JsonObject) -> bool:
    """Return whether playing the item means playing its children."""
    return item.get("Type") in CONTAINER_ITEM_TYPES or bool(item.get("IsFolder"))


def is_playable(item: JsonObject) -> bool:
    """Return whether the item can be sent to a player."""
    if item.get("Type") == "TvChannel":
        return True
    if item.get("MediaType") in PLAYABLE_MEDIA_TYPES:
        return True
    return is_container(item) and item.get("CollectionType") not in (
        "livetv",
        "boxsets",
    )


def item_node(item: JsonObject) -> LibraryNode:
    """Turn a Jellyfin item into a library node."""
    item_type = str(item.get("Type") or "")
    container = is_container(item) or item_type in ("MusicArtist", "Series")
    return LibraryNode(
        node_id=str(item["Id"]),
        title=item_title(item),
        media_class=MEDIA_CLASS_MAP.get(
            item_type, MediaClass.DIRECTORY if container else MediaClass.VIDEO
        ),
        content_type=CONTENT_TYPE_MAP.get(item_type, item_type or "item"),
        can_play=is_playable(item),
        can_expand=container,
        image=image_ref(item),
        item=item,
    )


def folder_node(node_id: str, title: str, *, can_play: bool = False) -> LibraryNode:
    """Return a virtual folder node."""
    return LibraryNode(
        node_id=node_id,
        title=title,
        media_class=MediaClass.DIRECTORY,
        content_type="library",
        can_play=can_play,
        can_expand=True,
    )


def _nodes(items: Iterable[JsonObject]) -> list[LibraryNode]:
    return [item_node(i) for i in items if i.get("Id")]


def _with_children(
    node: LibraryNode, result: JsonObject, *, child_class: MediaClass | None = None
) -> LibraryNode:
    items: list[JsonObject] = result.get("Items", [])
    node.children = _nodes(items)
    node.not_shown = max(0, int(result.get("TotalRecordCount", 0)) - len(items))
    node.children_media_class = child_class
    return node


class Library:
    """Navigate a user's Jellyfin library."""

    def __init__(self, client: JellyfinClient) -> None:
        """Bind the navigator to a client."""
        self.client = client

    async def root(self, title: str) -> LibraryNode:
        """Return the root: shortcuts then the user's libraries."""
        node = folder_node("", title)
        node.children = [folder_node(k, v) for k, v in SPECIAL_TITLES.items()]
        node.children += _nodes(await self.client.user_views())
        return node

    async def browse(self, node_id: str) -> LibraryNode:
        """Return a node with its children."""
        limit: Params = {"Limit": MAX_BROWSE_ITEMS, "Fields": ITEM_FIELDS}
        if node_id in SPECIAL_TITLES:
            return await self._special(node_id, limit)
        for prefix in (PREFIX_ARTISTS, PREFIX_ALBUMS, PREFIX_SONGS):
            if node_id.startswith(prefix):
                return await self._music(prefix, node_id[len(prefix) :], limit)

        item = await self.client.item(node_id)
        node = item_node(item)
        item_type = item.get("Type")
        collection = item.get("CollectionType")
        client = self.client

        if item_type == "Series":
            return _with_children(node, await client.seasons(node_id, limit))
        if item_type == "Season" and item.get("SeriesId"):
            params = {**limit, "seasonId": node_id}
            return _with_children(
                node, await client.episodes(str(item["SeriesId"]), params)
            )
        if item_type == "MusicArtist":
            params = {
                **limit,
                "AlbumArtistIds": node_id,
                "IncludeItemTypes": "MusicAlbum",
                "Recursive": True,
                "SortBy": "ProductionYear,SortName",
                "SortOrder": "Descending",
            }
            return _with_children(node, await client.items(params))
        if item_type == "MusicAlbum":
            params = {**limit, "ParentId": node_id, **_SORT_TRACKS}
            return _with_children(node, await client.items(params))
        if item_type == "Playlist":
            # No SortBy: a playlist keeps the order its owner gave it.
            return _with_children(
                node, await client.items({**limit, "ParentId": node_id})
            )
        if collection == "music":
            node.children = [
                folder_node(f"{PREFIX_ARTISTS}{node_id}", "Artists"),
                folder_node(f"{PREFIX_ALBUMS}{node_id}", "Albums"),
                folder_node(f"{PREFIX_SONGS}{node_id}", "Songs", can_play=True),
            ]
            return node
        if collection == "livetv":
            return _with_children(node, await client.live_tv_channels(limit))
        if collection in ("movies", "tvshows"):
            item_types = "Movie" if collection == "movies" else "Series"
            params = {
                **limit,
                "ParentId": node_id,
                "IncludeItemTypes": item_types,
                "Recursive": True,
                **_SORT_BY_NAME,
            }
            return _with_children(node, await client.items(params))
        params = {**limit, "ParentId": node_id, **_SORT_BY_NAME}
        return _with_children(node, await client.items(params))

    async def _special(self, node_id: str, limit: Params) -> LibraryNode:
        node = folder_node(node_id, SPECIAL_TITLES[node_id])
        client = self.client
        if node_id == SPECIAL_RESUME:
            return _with_children(node, await client.resume_items(limit))
        if node_id == SPECIAL_NEXT_UP:
            return _with_children(node, await client.next_up(limit))
        if node_id == SPECIAL_LATEST:
            items = await client.latest_items({"Limit": 100, "Fields": ITEM_FIELDS})
            node.children = _nodes(items)
            return node
        params = {
            **limit,
            "Filters": "IsFavorite",
            "Recursive": True,
            **_SORT_BY_NAME,
        }
        return _with_children(node, await client.items(params))

    async def _music(self, prefix: str, library_id: str, limit: Params) -> LibraryNode:
        client = self.client
        if prefix == PREFIX_ARTISTS:
            node = folder_node(f"{prefix}{library_id}", "Artists")
            params = {**limit, "ParentId": library_id, **_SORT_BY_NAME}
            return _with_children(
                node, await client.album_artists(params), child_class=MediaClass.ARTIST
            )
        if prefix == PREFIX_ALBUMS:
            node = folder_node(f"{prefix}{library_id}", "Albums")
            item_type = "MusicAlbum"
            child_class = MediaClass.ALBUM
        else:
            node = folder_node(f"{prefix}{library_id}", "Songs", can_play=True)
            item_type = "Audio"
            child_class = MediaClass.TRACK
        params = {
            **limit,
            "ParentId": library_id,
            "IncludeItemTypes": item_type,
            "Recursive": True,
            **_SORT_BY_NAME,
        }
        return _with_children(node, await client.items(params), child_class=child_class)

    async def search(
        self,
        term: str,
        item_types: Iterable[str] | None = None,
        *,
        include_artists: bool = True,
        limit: int = 50,
    ) -> list[LibraryNode]:
        """Search the library; artists come from their own endpoint."""
        term = term.strip()
        if not term:
            return []
        types = (
            list(item_types) if item_types is not None else list(DEFAULT_SEARCH_TYPES)
        )
        base: dict[str, str | int | bool] = {
            "searchTerm": term,
            "Limit": limit,
            "Fields": ITEM_FIELDS,
        }
        found: list[JsonObject] = []
        if include_artists:
            found += (await self.client.artists(base))["Items"]
        if types:
            params: Params = {
                **base,
                "Recursive": True,
                "IncludeItemTypes": ",".join(types),
            }
            found += (await self.client.items(params))["Items"]
        unique = {str(i["Id"]): i for i in reversed(found) if i.get("Id")}
        ordered = [
            unique.pop(str(i["Id"])) for i in found if str(i.get("Id")) in unique
        ]
        return _nodes(ordered[:limit])

    async def playable_ids(self, node_id: str, *, shuffle: bool = False) -> list[str]:
        """Resolve a node into the item ids a player should queue."""
        if node_id.startswith(PREFIX_SONGS):
            params: dict[str, str | int | bool] = {
                "ParentId": node_id[len(PREFIX_SONGS) :],
                "IncludeItemTypes": "Audio",
                "Recursive": True,
                "Limit": MAX_QUEUE_ITEMS,
                "SortBy": "Random" if shuffle else "AlbumArtist,Album,SortName",
            }
            ids = [str(i["Id"]) for i in (await self.client.items(params))["Items"]]
            return ids
        item = await self.client.item(node_id)
        if not is_container(item) or item.get("Type") == "TvChannel":
            return [node_id]
        ids = await self._children_ids(item, shuffle=shuffle)
        if shuffle:
            random.shuffle(ids)
        return ids

    async def _children_ids(self, item: JsonObject, *, shuffle: bool) -> list[str]:
        item_id = str(item["Id"])
        item_type = item.get("Type")
        params: dict[str, str | int | bool] = {
            "Recursive": True,
            "IsFolder": False,
            "MediaTypes": "Video,Audio",
            "Limit": MAX_QUEUE_ITEMS,
        }
        if item_type == "MusicArtist":
            params |= {"AlbumArtistIds": item_id, "IncludeItemTypes": "Audio"}
            params["SortBy"] = (
                "Random"
                if shuffle
                else "ProductionYear,Album,ParentIndexNumber,IndexNumber,SortName"
            )
        elif item_type in ("MusicGenre", "Genre"):
            params |= {"GenreIds": item_id}
            params["SortBy"] = "Random" if shuffle else "SortName"
        elif item_type == "Playlist":
            params["ParentId"] = item_id
        else:
            params["ParentId"] = item_id
            params["SortBy"] = (
                "Random" if shuffle else "ParentIndexNumber,IndexNumber,SortName"
            )
        result = await self.client.items(params)
        return [str(i["Id"]) for i in result["Items"] if i.get("Id")]

    async def queue(self, session: JsonObject) -> list[JsonObject]:
        """Return the session's play queue with item names."""
        queue = [q for q in session.get("NowPlayingQueue") or [] if q.get("Id")]
        if not queue:
            return []
        ids = list(dict.fromkeys(str(q["Id"]) for q in queue))
        result = await self.client.items({"Ids": ",".join(ids), "Fields": ITEM_FIELDS})
        by_id = {str(i["Id"]): i for i in result["Items"] if i.get("Id")}
        current = session.get("PlaylistItemId")
        now_playing = (session.get("NowPlayingItem") or {}).get("Id")
        entries: list[JsonObject] = []
        for position, entry in enumerate(queue):
            item = by_id.get(str(entry["Id"]), {})
            is_current = (
                entry.get("PlaylistItemId") == current
                if current
                else entry["Id"] == now_playing
            )
            entries.append(
                {
                    "position": position,
                    "id": entry["Id"],
                    "title": item_title(item) if item else entry["Id"],
                    "type": item.get("Type"),
                    "current": is_current,
                }
            )
        return entries


def summarize(node: LibraryNode) -> JsonObject:
    """Return a compact, automation-friendly view of a node."""
    item = node.item or {}
    summary: JsonObject = {
        "id": node.node_id,
        "title": node.title,
        "name": item.get("Name", node.title),
        "type": item.get("Type", "Folder"),
        "media_class": str(node.media_class),
        "can_play": node.can_play,
        "can_expand": node.can_expand,
    }
    for key, source in (
        ("year", "ProductionYear"),
        ("series", "SeriesName"),
        ("season", "ParentIndexNumber"),
        ("episode", "IndexNumber"),
        ("album", "Album"),
        ("collection_type", "CollectionType"),
        ("overview", "Overview"),
        ("community_rating", "CommunityRating"),
        ("official_rating", "OfficialRating"),
    ):
        if item.get(source) is not None:
            summary[key] = item[source]
    if artists := item.get("Artists"):
        summary["artists"] = artists
    if ticks := item.get("RunTimeTicks"):
        summary["duration"] = int(ticks) // 10_000_000
    user_data = item.get("UserData")
    if isinstance(user_data, dict):
        summary["played"] = bool(user_data.get("Played"))
        summary["favorite"] = bool(user_data.get("IsFavorite"))
    return summary


def search_types(
    media_classes: Iterable[MediaClass | str] | None,
) -> tuple[list[str], bool]:
    """Map media-class filters to item types and whether artists are wanted."""
    classes = list(media_classes or [])
    if not classes:
        return list(DEFAULT_SEARCH_TYPES), True
    types: list[str] = []
    for media_class in classes:
        try:
            known = MediaClass(media_class)
        except ValueError:
            continue
        for item_type in SEARCH_ITEM_TYPES.get(known, ()):
            if item_type not in types:
                types.append(item_type)
    return types, MediaClass.ARTIST in classes
