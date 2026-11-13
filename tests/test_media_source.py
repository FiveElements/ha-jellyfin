"""Tests of the media source (streaming to other players)."""

from __future__ import annotations

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.jellyfin_control.const import CONF_AUDIO_CODEC, DOMAIN
from custom_components.jellyfin_control.media_source import mime_type
from homeassistant.components import media_source
from homeassistant.components.media_source import Unresolvable
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import TOKEN, FakeJellyfin


@pytest.fixture(autouse=True)
async def setup_media_source(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "media_source", {})


def test_mime_type() -> None:
    assert mime_type({"MediaSources": [{"Path": "/a/b.mkv"}]}) == "video/x-matroska"
    assert mime_type({"MediaSources": [{"Container": "mp3,mpeg"}]}) == "audio/mpeg"
    assert mime_type({"MediaSources": [{"Path": "x.zz9"}], "MediaType": "Audio"}) == (
        "audio/mpeg"
    )
    assert mime_type({"MediaType": "Video"}) == "video/mp4"
    assert mime_type({"MediaType": "Photo"}) is None


async def test_browse_and_resolve(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    root = await media_source.async_browse_media(hass, f"media-source://{DOMAIN}")
    assert root.title == "Media"
    library = await media_source.async_browse_media(
        hass, f"media-source://{DOMAIN}/{loaded.entry_id}/lib_movies"
    )
    movie = library.children[0]
    assert movie.can_play is True
    assert movie.media_content_type == "video/mp4"
    assert movie.thumbnail.endswith("/Items/mov1/Images/Backdrop?maxWidth=600&tag=bd1")
    assert TOKEN not in movie.thumbnail
    shows = await media_source.async_browse_media(
        hass, f"media-source://{DOMAIN}/{loaded.entry_id}/lib_shows"
    )
    assert [c.can_play for c in shows.children] == [True, False]

    resolved = await media_source.async_resolve_media(
        hass, f"media-source://{DOMAIN}/{loaded.entry_id}/mov1", None
    )
    assert resolved.mime_type == "video/mp4"
    assert "/Videos/mov1/stream" in resolved.url and "MediaSourceId=ms2" in resolved.url

    hass.config_entries.async_update_entry(loaded, options={CONF_AUDIO_CODEC: "mp3"})
    resolved = await media_source.async_resolve_media(
        hass, f"media-source://{DOMAIN}/song1", None
    )
    assert resolved.mime_type == "audio/flac"
    assert "AudioCodec=mp3" in resolved.url


async def test_errors(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    with pytest.raises(Unresolvable):
        await media_source.async_resolve_media(
            hass, f"media-source://{DOMAIN}/{loaded.entry_id}/missing", None
        )
    with pytest.raises(Unresolvable):
        await media_source.async_resolve_media(
            hass, f"media-source://{DOMAIN}/{loaded.entry_id}/photo1", None
        )
    with pytest.raises(Unresolvable):
        await media_source.async_browse_media(
            hass, f"media-source://{DOMAIN}/{loaded.entry_id}/missing"
        )
    with pytest.raises(Unresolvable):
        await media_source.async_browse_media(
            hass, f"media-source://{DOMAIN}/other-entry/mov1"
        )
    await hass.config_entries.async_unload(loaded.entry_id)
    with pytest.raises(Unresolvable):
        await media_source.async_resolve_media(
            hass, f"media-source://{DOMAIN}/{loaded.entry_id}/mov1", None
        )


async def test_several_servers(
    hass: HomeAssistant, fake: FakeJellyfin, loaded: MockConfigEntry
) -> None:
    second = MockConfigEntry(
        domain=DOMAIN, title="Other", unique_id="other", data=dict(loaded.data)
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    root = await media_source.async_browse_media(hass, f"media-source://{DOMAIN}")
    assert sorted(c.title for c in root.children) == ["Media", "Other"]
    server = await media_source.async_browse_media(
        hass, f"media-source://{DOMAIN}/{second.entry_id}/"
    )
    assert server.title == "Other"
