"""Expose every Jellyfin control to LLMs (Assist agents and the MCP server).

Assist only offers intents, and none of them changes a select entity or
reaches an integration action: "turn the subtitles off" had no tool to call.
This API gives each control of the integration its own tool. The tools go
through the integration's actions and entities, so feature checks, error
messages and the caller's permissions stay those of a normal action call.

Only media players exposed to Assist (conversation) can be targeted, like
the entities the Assist API offers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field as dc_field
from typing import Any, NamedTuple, override
import unicodedata

import probatio

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.homeassistant.exposed_entities import (
    async_should_expose,
)
from homeassistant.components.media_player import (
    DOMAIN as MP_DOMAIN,
    MediaPlayerEntityFeature,
)
from homeassistant.components.remote import DOMAIN as REMOTE_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    llm,
)
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN, MAX_BROWSE_ITEMS, MAX_SEARCH_RESULTS

LLM_API_ID = DOMAIN
LLM_API_NAME = "Jellyfin Control"
# Every tool weighs on each step of the model: a voice assistant gets the
# controls people actually say out loud, not the remote keys or admin ones.
VOICE_API_ID = f"{DOMAIN}_voice"
VOICE_API_NAME = "Jellyfin Control (voice)"
VOICE_TOOLS = frozenset(
    {
        "jellyfin_get_players",
        "jellyfin_search",
        "jellyfin_browse",
        "jellyfin_get_queue",
        "jellyfin_play",
        "jellyfin_playback",
        "jellyfin_seek",
        "jellyfin_get_chapters",
        "jellyfin_volume",
        "jellyfin_shuffle_repeat",
        "jellyfin_set_subtitles",
        "jellyfin_set_audio_track",
    }
)
# Assist's exposure settings are stored under the conversation assistant.
ASSISTANT = "conversation"

API_PROMPT = (
    "Tools to control Jellyfin players (media_player entities of the Jellyfin "
    "Control integration) and to search their library. A player is named by "
    "its name, an alias, its area or its entity id; it can be left out when "
    "only one player is connected. Find item ids with jellyfin_search or "
    "jellyfin_browse before playing them. jellyfin_get_players tells what each "
    "player is doing, its audio and subtitle tracks and the last item it played."
)

READ_ONLY = llm.ToolAnnotations(
    read_only=True, destructive=False, idempotent=True, open_world=False
)
SETTING = llm.ToolAnnotations(
    read_only=False, destructive=False, idempotent=True, open_world=False
)
ACTION = llm.ToolAnnotations(
    read_only=False, destructive=False, idempotent=False, open_world=False
)

PLAYER_FIELD = probatio.Optional(
    "player", description="Player name, alias, area or entity id"
)
SUBTITLES_OFF = "off"
# Spoken words that mean "no subtitles", accents removed.
OFF_WORDS = frozenset(
    {"off", "none", "no", "disable", "disabled", "aucun", "sans", "desactive", "non"}
)
# Jellyfin labels tracks with English language names; people ask in French.
LANGUAGE_NAMES = {
    "anglais": "english",
    "francais": "french",
    "allemand": "german",
    "espagnol": "spanish",
    "italien": "italian",
    "japonais": "japanese",
    "portugais": "portuguese",
    "russe": "russian",
    "arabe": "arabic",
    "polonais": "polish",
    "chinois": "chinese",
    "coreen": "korean",
    "neerlandais": "dutch",
}
PLAYBACK_SERVICES = {
    "play": "media_play",
    "pause": "media_pause",
    "play_pause": "media_play_pause",
    "stop": "media_stop",
    "next": "media_next_track",
    "previous": "media_previous_track",
}


class ToolError(Exception):
    """A request the tool cannot satisfy, told back to the model."""


def normalize(text: str) -> str:
    """Return text casefolded and without accents, for name matching."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


@dataclass(frozen=True)
class Player:
    """A Jellyfin media player the LLM may target."""

    entity_id: str
    name: str
    aliases: tuple[str, ...]
    area: str | None
    device_id: str | None
    state: State

    @property
    def connected(self) -> bool:
        """Return whether the app is connected."""
        return self.state.state not in (STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN)

    def names(self) -> list[str]:
        """Return every name the player answers to."""
        names = [self.entity_id, self.name, *self.aliases]
        if self.area:
            names.append(self.area)
        return names

    def describe(self) -> str:
        """Return a one-line description for the model."""
        parts = [f"{self.name} ({self.entity_id})"]
        if self.aliases:
            parts.append("aliases: " + ", ".join(self.aliases))
        if self.area:
            parts.append(f"area: {self.area}")
        parts.append(f"state: {self.state.state}")
        return "; ".join(parts)


@callback
def async_players(hass: HomeAssistant) -> list[Player]:
    """Return the Jellyfin media players exposed to Assist."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    areas = ar.async_get(hass)
    players = []
    for entry in entities.entities.values():
        if (
            entry.platform != DOMAIN
            or entry.domain != MP_DOMAIN
            or entry.disabled
            or not async_should_expose(hass, ASSISTANT, entry.entity_id)
            or (state := hass.states.get(entry.entity_id)) is None
        ):
            continue
        device = devices.async_get(entry.device_id) if entry.device_id else None
        area_id = entry.area_id or (device.area_id if device else None)
        area = areas.async_get_area(area_id) if area_id else None
        players.append(
            Player(
                entity_id=entry.entity_id,
                name=state.name,
                aliases=tuple(a for a in entry.aliases if isinstance(a, str) and a),
                area=area.name if area else None,
                device_id=entry.device_id,
                state=state,
            )
        )
    return sorted(players, key=lambda p: p.entity_id)


def resolve_player(players: list[Player], wanted: str | None) -> Player:
    """Find the player the model named, or the only connected one."""
    if not players:
        raise ToolError("No Jellyfin player is exposed to Assist")
    choices = "; ".join(p.describe() for p in players)
    if not wanted or not wanted.strip():
        connected = [p for p in players if p.connected]
        if len(connected) == 1:
            return connected[0]
        if len(players) == 1:
            return players[0]
        raise ToolError(f"Several players, say which one. Players: {choices}")
    key = normalize(wanted)
    matches = [p for p in players if key in map(normalize, p.names())]
    if not matches:
        matches = [
            p for p in players if any(key in normalize(n) for n in p.names()[1:])
        ]
    if len(matches) > 1:
        # "Jellyfin salon" vs an offline browser in the same room.
        matches = [p for p in matches if p.connected] or matches
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ToolError(f"No player matches {wanted!r}. Players: {choices}")
    names = ", ".join(p.name for p in matches)
    raise ToolError(f"{wanted!r} matches several players ({names}), be precise")


@callback
def sibling_entity(hass: HomeAssistant, player: Player, domain: str, key: str) -> str:
    """Return the entity of the player's device with this unique id suffix."""
    if player.device_id:
        for entry in er.async_entries_for_device(er.async_get(hass), player.device_id):
            if (
                entry.platform == DOMAIN
                and entry.domain == domain
                and entry.unique_id.endswith(f"-{key}")
            ):
                return entry.entity_id
    raise ToolError(f"{player.name} has no {key.replace('_', ' ')}")


def track_index(options: list[str], track: str, *, subtitles: bool) -> int:
    """Turn "off", an index or a language/title into a track index."""
    wanted = normalize(str(track))
    if subtitles and wanted in OFF_WORDS:
        return -1
    indexed = {}
    for option in options:
        number, _, label = option.partition(": ")
        if option != SUBTITLES_OFF and number.lstrip("-").isdigit():
            indexed[int(number)] = normalize(label)
    if wanted.lstrip("-").isdigit():
        if int(wanted) in indexed:
            return int(wanted)
    else:
        for word in (wanted, LANGUAGE_NAMES.get(wanted)):
            for index, label in indexed.items():
                if word and word in label:
                    return index
    raise ToolError(f"No track matches {track!r}. Tracks: {options}")


def player_status(hass: HomeAssistant, player: Player) -> dict[str, Any]:
    """Return what a player is doing, its tracks and its last played item."""
    attributes = player.state.attributes
    status: dict[str, Any] = {
        "player": player.name,
        "entity_id": player.entity_id,
        "aliases": list(player.aliases),
        "area": player.area,
        "state": player.state.state,
    }
    for key in (
        "media_title",
        "media_series_title",
        "media_season",
        "media_episode",
        "media_content_id",
        "media_content_type",
        "media_position",
        "media_duration",
        "volume_level",
        "is_volume_muted",
        "shuffle",
        "repeat",
        "host_name",
    ):
        if attributes.get(key) is not None:
            status[key] = attributes[key]
    features = MediaPlayerEntityFeature(attributes.get("supported_features", 0))
    status["features"] = sorted(
        str(f.name).lower() for f in MediaPlayerEntityFeature if f in features
    )
    for name, domain, key in (
        ("audio_tracks", SELECT_DOMAIN, "audio_track"),
        ("subtitle_tracks", SELECT_DOMAIN, "subtitle_track"),
        ("last_played", SENSOR_DOMAIN, "last_played"),
    ):
        try:
            entity_id = sibling_entity(hass, player, domain, key)
        except ToolError:
            continue
        state = hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            continue
        if domain == SELECT_DOMAIN:
            status[name] = {
                "current": state.state,
                "options": state.attributes.get("options", []),
            }
        else:
            status[name] = {"title": state.state, **state.attributes}
            status[name].pop("friendly_name", None)
            status[name].pop("icon", None)
    return status


type Handler = Callable[[HomeAssistant, dict[str, Any], llm.LLMContext], Awaitable[Any]]


class ToolSpec(NamedTuple):
    """One tool: its name, what it does, its parameters and its handler."""

    name: str
    description: str
    parameters: dict[Any, Any]
    handler: Handler
    annotations: llm.ToolAnnotations


async def call(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
    domain: str,
    service: str,
    data: dict[str, Any],
    *,
    response: bool = False,
) -> Any:
    """Call an action as the requesting user."""
    result = await hass.services.async_call(
        domain,
        service,
        data,
        blocking=True,
        context=llm_context.context,
        return_response=response,
    )
    if response and result is not None:
        return result.get(data[ATTR_ENTITY_ID])
    return {"success": True}


def target(hass: HomeAssistant, args: dict[str, Any]) -> Player:
    """Return the player named in the tool arguments."""
    return resolve_player(async_players(hass), args.get("player"))


def any_player(hass: HomeAssistant) -> Player:
    """Return a player to run library requests through (any server works)."""
    players = async_players(hass)
    if not players:
        raise ToolError("No Jellyfin player is exposed to Assist")
    return next((p for p in players if p.connected), players[0])


async def _get_players(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    return {"players": [player_status(hass, p) for p in async_players(hass)]}


async def _search(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    data = {ATTR_ENTITY_ID: any_player(hass).entity_id, "query": args["query"]}
    data["limit"] = args["limit"]
    if args.get("media_types"):
        data["media_types"] = args["media_types"]
    return await call(hass, ctx, DOMAIN, "search_library", data, response=True)


async def _browse(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    data = {ATTR_ENTITY_ID: any_player(hass).entity_id, "limit": args["limit"]}
    if args.get("parent_id"):
        data["parent_id"] = args["parent_id"]
    return await call(hass, ctx, DOMAIN, "browse_library", data, response=True)


async def _queue(hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext) -> Any:
    data = {ATTR_ENTITY_ID: target(hass, args).entity_id}
    return await call(hass, ctx, DOMAIN, "get_queue", data, response=True)


async def _play(hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext) -> Any:
    data: dict[str, Any] = {
        ATTR_ENTITY_ID: target(hass, args).entity_id,
        "item_ids": args["item_ids"],
        "enqueue": args["enqueue"],
        "shuffle": args["shuffle"],
    }
    if args.get("start_position") is not None:
        data["start_position"] = args["start_position"]
    return await call(hass, ctx, DOMAIN, "play_items", data)


async def _playback(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    data = {ATTR_ENTITY_ID: target(hass, args).entity_id}
    return await call(hass, ctx, MP_DOMAIN, PLAYBACK_SERVICES[args["action"]], data)


async def _seek(hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext) -> Any:
    entity_id = target(hass, args).entity_id
    if args.get("position") is not None:
        data = {ATTR_ENTITY_ID: entity_id, "seek_position": args["position"]}
        return await call(hass, ctx, MP_DOMAIN, "media_seek", data)
    if args.get("offset") is not None:
        data = {ATTR_ENTITY_ID: entity_id, "seconds": args["offset"]}
        return await call(hass, ctx, DOMAIN, "seek_relative", data)
    raise ToolError("Give a position or an offset, in seconds")


async def _chapters(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    data = {ATTR_ENTITY_ID: target(hass, args).entity_id}
    return await call(hass, ctx, DOMAIN, "get_chapters", data, response=True)


async def _seek_chapter(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    data = {ATTR_ENTITY_ID: target(hass, args).entity_id, "chapter": args["chapter"]}
    return await call(hass, ctx, DOMAIN, "seek_chapter", data)


async def _volume(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    entity_id = target(hass, args).entity_id
    if all(args.get(key) is None for key in ("level", "mute", "step")):
        raise ToolError("Give a level, mute or a step")
    if args.get("mute") is not None:
        data = {ATTR_ENTITY_ID: entity_id, "is_volume_muted": args["mute"]}
        await call(hass, ctx, MP_DOMAIN, "volume_mute", data)
    if args.get("level") is not None:
        data = {ATTR_ENTITY_ID: entity_id, "volume_level": args["level"] / 100}
        await call(hass, ctx, MP_DOMAIN, "volume_set", data)
    if args.get("step") is not None:
        service = "volume_up" if args["step"] == "up" else "volume_down"
        await call(hass, ctx, MP_DOMAIN, service, {ATTR_ENTITY_ID: entity_id})
    return {"success": True}


async def _shuffle_repeat(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    entity_id = target(hass, args).entity_id
    if args.get("shuffle") is None and args.get("repeat") is None:
        raise ToolError("Give shuffle or repeat")
    if args.get("shuffle") is not None:
        data = {ATTR_ENTITY_ID: entity_id, "shuffle": args["shuffle"]}
        await call(hass, ctx, MP_DOMAIN, "shuffle_set", data)
    if args.get("repeat") is not None:
        data = {ATTR_ENTITY_ID: entity_id, "repeat": args["repeat"]}
        await call(hass, ctx, MP_DOMAIN, "repeat_set", data)
    return {"success": True}


def _track_tool(stream: str, service: str) -> Handler:
    subtitles = stream == "subtitle_track"

    async def handler(
        hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
    ) -> Any:
        player = target(hass, args)
        state = hass.states.get(sibling_entity(hass, player, SELECT_DOMAIN, stream))
        if state is None or state.state == STATE_UNAVAILABLE:
            raise ToolError(
                f"Nothing with selectable tracks is playing on {player.name}"
            )
        options = list(state.attributes.get("options", []))
        index = track_index(options, args["track"], subtitles=subtitles)
        data = {ATTR_ENTITY_ID: player.entity_id, "index": index}
        await call(hass, ctx, DOMAIN, service, data)
        return {"success": True, "index": index}

    return handler


async def _instant_mix(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    data = {ATTR_ENTITY_ID: target(hass, args).entity_id, "item_id": args["item_id"]}
    return await call(hass, ctx, DOMAIN, "play_instant_mix", data)


async def _message(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    data = {
        ATTR_ENTITY_ID: target(hass, args).entity_id,
        "message": args["message"],
        "title": args["title"],
        "timeout": args["timeout"],
    }
    return await call(hass, ctx, DOMAIN, "send_message", data)


async def _navigate(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    remote = sibling_entity(hass, target(hass, args), REMOTE_DOMAIN, "remote")
    data = {
        ATTR_ENTITY_ID: remote,
        "command": args["commands"],
        "num_repeats": args["repeats"],
    }
    return await call(hass, ctx, REMOTE_DOMAIN, "send_command", data)


async def _send_text(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    data = {ATTR_ENTITY_ID: target(hass, args).entity_id, "text": args["text"]}
    return await call(hass, ctx, DOMAIN, "send_text", data)


async def _display(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    data = {ATTR_ENTITY_ID: target(hass, args).entity_id, "item_id": args["item_id"]}
    return await call(hass, ctx, DOMAIN, "display_content", data)


async def _user_data(
    hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext
) -> Any:
    if args.get("played") is None and args.get("favorite") is None:
        raise ToolError("Give played or favorite")
    data = {ATTR_ENTITY_ID: any_player(hass).entity_id, "item_id": args["item_id"]}
    for key in ("played", "favorite"):
        if args.get(key) is not None:
            data[key] = args[key]
    return await call(hass, ctx, DOMAIN, "set_user_data", data)


async def _scan(hass: HomeAssistant, args: dict[str, Any], ctx: llm.LLMContext) -> Any:
    buttons = [
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.platform == DOMAIN
        and entry.domain == BUTTON_DOMAIN
        and not entry.disabled
    ]
    if not buttons:
        raise ToolError("Library scan is disabled (it needs a Jellyfin administrator)")
    return await call(
        hass, ctx, BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: buttons}
    )


def _track_parameters(what: str) -> dict[Any, Any]:
    return {
        PLAYER_FIELD: cv.string,
        probatio.Required(
            "track",
            description=(
                "Index, language or title"
                + (", or 'off'" if what == "Subtitles" else "")
            ),
        ): cv.string,
    }


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "jellyfin_get_players",
        "Players: state, playing item, position, volume, tracks, last played.",
        {},
        _get_players,
        READ_ONLY,
    ),
    ToolSpec(
        "jellyfin_search",
        "Search the library by title; returns item ids to play.",
        {
            probatio.Required("query", description="Words to search for"): cv.string,
            probatio.Optional(
                "media_types",
                description="Restrict to types: Movie, Series, Episode, Audio, "
                "MusicAlbum, MusicArtist, Playlist, BoxSet...",
            ): probatio.All(cv.ensure_list, [cv.string]),
            probatio.Optional("limit", default=20): probatio.All(
                probatio.Coerce(int), probatio.Range(min=1, max=MAX_SEARCH_RESULTS)
            ),
        },
        _search,
        READ_ONLY,
    ),
    ToolSpec(
        "jellyfin_browse",
        "Browse the library. No parent_id: libraries, special:resume, "
        "special:next_up, special:latest; latest:LIBRARY_ID for one library.",
        {
            probatio.Optional(
                "parent_id", description="Folder, library or virtual id to open"
            ): cv.string,
            probatio.Optional("limit", default=50): probatio.All(
                probatio.Coerce(int), probatio.Range(min=1, max=MAX_BROWSE_ITEMS)
            ),
        },
        _browse,
        READ_ONLY,
    ),
    ToolSpec(
        "jellyfin_get_queue",
        "Return the play queue of a player.",
        {PLAYER_FIELD: cv.string},
        _queue,
        READ_ONLY,
    ),
    ToolSpec(
        "jellyfin_play",
        "Play item ids (a series, album or folder plays its content).",
        {
            PLAYER_FIELD: cv.string,
            probatio.Required("item_ids", description="Item ids"): probatio.All(
                cv.ensure_list, [cv.string], probatio.Length(min=1)
            ),
            probatio.Optional(
                "enqueue",
                default="play",
                description="play now, next, add at the end, replace",
            ): probatio.In(["play", "next", "add", "replace"]),
            probatio.Optional("shuffle", default=False): cv.boolean,
            probatio.Optional(
                "start_position", description="Start at this second"
            ): probatio.All(probatio.Coerce(float), probatio.Range(min=0)),
        },
        _play,
        ACTION,
    ),
    ToolSpec(
        "jellyfin_playback",
        "Play, pause, toggle, stop, or go to the next or previous item.",
        {
            PLAYER_FIELD: cv.string,
            probatio.Required("action"): probatio.In(list(PLAYBACK_SERVICES)),
        },
        _playback,
        ACTION,
    ),
    ToolSpec(
        "jellyfin_seek",
        "Skip by offset seconds ('recule de 2 minutes' is -120) or jump to "
        "position seconds from the start.",
        {
            PLAYER_FIELD: cv.string,
            probatio.Optional("position"): probatio.All(
                probatio.Coerce(float), probatio.Range(min=0)
            ),
            probatio.Optional("offset"): probatio.Coerce(float),
        },
        _seek,
        ACTION,
    ),
    ToolSpec(
        "jellyfin_get_chapters",
        "Chapters of the playing item (index, name, start) and the current one.",
        {PLAYER_FIELD: cv.string},
        _chapters,
        READ_ONLY,
    ),
    ToolSpec(
        "jellyfin_seek_chapter",
        "Go to the next or previous chapter, or to a chapter index.",
        {
            PLAYER_FIELD: cv.string,
            probatio.Required(
                "chapter", description="next, previous, or a chapter index"
            ): cv.string,
        },
        _seek_chapter,
        ACTION,
    ),
    ToolSpec(
        "jellyfin_volume",
        "Set the volume level (0-100), mute, or step it up or down.",
        {
            PLAYER_FIELD: cv.string,
            probatio.Optional("level"): probatio.All(
                probatio.Coerce(int), probatio.Range(min=0, max=100)
            ),
            probatio.Optional("mute"): cv.boolean,
            probatio.Optional("step"): probatio.In(["up", "down"]),
        },
        _volume,
        ACTION,
    ),
    ToolSpec(
        "jellyfin_shuffle_repeat",
        "Turn shuffle on or off and set the repeat mode.",
        {
            PLAYER_FIELD: cv.string,
            probatio.Optional("shuffle"): cv.boolean,
            probatio.Optional("repeat"): probatio.In(["off", "all", "one"]),
        },
        _shuffle_repeat,
        SETTING,
    ),
    ToolSpec(
        "jellyfin_set_subtitles",
        "Change or remove the subtitles of the playing item.",
        _track_parameters("Subtitles"),
        _track_tool("subtitle_track", "set_subtitle_stream"),
        SETTING,
    ),
    ToolSpec(
        "jellyfin_set_audio_track",
        "Change the audio track (language or index) of the playing item.",
        _track_parameters("Audio track"),
        _track_tool("audio_track", "set_audio_stream"),
        SETTING,
    ),
    ToolSpec(
        "jellyfin_instant_mix",
        "Play an instant mix (similar music) from a song, album or artist.",
        {
            PLAYER_FIELD: cv.string,
            probatio.Required("item_id"): cv.string,
        },
        _instant_mix,
        ACTION,
    ),
    ToolSpec(
        "jellyfin_show_message",
        "Show a message on the player's screen.",
        {
            PLAYER_FIELD: cv.string,
            probatio.Required("message"): cv.string,
            probatio.Optional("title", default=""): cv.string,
            probatio.Optional("timeout", default=5, description="Seconds"): (
                probatio.All(probatio.Coerce(float), probatio.Range(min=1, max=3600))
            ),
        },
        _message,
        ACTION,
    ),
    ToolSpec(
        "jellyfin_navigate",
        "Press remote control keys on the player: up, down, left, right, "
        "select, back, home, menu, info, search, settings, page_up, page_down.",
        {
            PLAYER_FIELD: cv.string,
            probatio.Required("commands", description="Keys, in order"): probatio.All(
                cv.ensure_list, [cv.string], probatio.Length(min=1)
            ),
            probatio.Optional("repeats", default=1): probatio.All(
                probatio.Coerce(int), probatio.Range(min=1, max=20)
            ),
        },
        _navigate,
        ACTION,
    ),
    ToolSpec(
        "jellyfin_send_text",
        "Type text into the focused field of the player (a search box).",
        {PLAYER_FIELD: cv.string, probatio.Required("text"): cv.string},
        _send_text,
        ACTION,
    ),
    ToolSpec(
        "jellyfin_display_item",
        "Open an item's page on the player without playing it.",
        {PLAYER_FIELD: cv.string, probatio.Required("item_id"): cv.string},
        _display,
        ACTION,
    ),
    ToolSpec(
        "jellyfin_set_user_data",
        "Mark an item as played or unplayed, or add or remove it from favorites.",
        {
            probatio.Required("item_id"): cv.string,
            probatio.Optional("played"): cv.boolean,
            probatio.Optional("favorite"): cv.boolean,
        },
        _user_data,
        SETTING,
    ),
    ToolSpec(
        "jellyfin_scan_library",
        "Scan the Jellyfin libraries for new or removed files.",
        {},
        _scan,
        ACTION,
    ),
)


class JellyfinTool(llm.Tool):
    """A tool calling one Jellyfin control."""

    def __init__(self, spec: ToolSpec) -> None:
        """Initialize the tool."""
        self.name = spec.name
        self.description = spec.description
        self.parameters = probatio.Schema(spec.parameters)
        self.annotations = spec.annotations
        self.integration = DOMAIN
        self._handler = spec.handler

    @override
    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Validate the arguments and run the control."""
        args = self.parameters(tool_input.tool_args)
        try:
            data = await self._handler(hass, args, llm_context)
        except (ToolError, HomeAssistantError) as err:
            # Told to the model, which can pick another player or track.
            return llm.ToolResult(data={"error": str(err)}, error=True)
        if not isinstance(data, dict):
            data = {"result": data}
        result: JsonObjectType = data
        return llm.ToolResult(data=result)


@dataclass(kw_only=True)
class JellyfinLLMAPI(llm.API):
    """LLM API exposing the Jellyfin controls."""

    # Names of the tools to offer; None offers them all.
    tool_names: frozenset[str] | None = None
    _tools: list[llm.Tool] = dc_field(init=False)

    def __post_init__(self) -> None:
        """Build the tools once: they do not depend on the request."""
        self._tools = [
            JellyfinTool(spec)
            for spec in TOOLS
            if self.tool_names is None or spec.name in self.tool_names
        ]

    @override
    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return the tools, with the current players in the prompt."""
        players = async_players(self.hass)
        prompt = API_PROMPT
        if players:
            prompt += " Players: " + " | ".join(p.describe() for p in players)
        return llm.APIInstance(
            api=self,
            api_prompt=prompt,
            llm_context=llm_context,
            tools=self._tools,
        )


@callback
def async_register_llm_api(hass: HomeAssistant) -> CALLBACK_TYPE:
    """Register the full Jellyfin Control LLM API and its voice subset."""
    unregister = [
        llm.async_register_api(
            hass, JellyfinLLMAPI(hass=hass, id=LLM_API_ID, name=LLM_API_NAME)
        ),
        llm.async_register_api(
            hass,
            JellyfinLLMAPI(
                hass=hass,
                id=VOICE_API_ID,
                name=VOICE_API_NAME,
                tool_names=VOICE_TOOLS,
            ),
        ),
    ]

    @callback
    def async_unregister() -> None:
        for remove in unregister:
            remove()

    return async_unregister
