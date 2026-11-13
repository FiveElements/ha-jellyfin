# Library browsing

The media panel of each Jellyfin player opens the library of the signed-in
user:

- **Continue watching**, **Next up**, **Recently added**, **Favorites**;
- every library: movies and shows sorted by name, music split into
  **Artists**, **Albums** and **Songs**, Live TV channels, playlists,
  collections and plain folders;
- a series lists its seasons, a season its episodes in airing order, an artist
  their albums (newest first), an album its tracks in disc order, a playlist
  its entries in the owner's order.

Playing a folder plays its content: a series, season, album, artist, genre,
playlist or collection is expanded into a queue (at most 200 items). **Search**
covers movies, series, episodes, albums, songs, playlists, collections and
artists.

The same tree is offered as a **media source** (*Media > Jellyfin Control*),
so library items can be streamed to any Home Assistant player (Cast, Sonos,
browser). Videos are streamed as they are stored; music is offered in the
codec chosen in the options (MP3 by default). Streams and thumbnails are
relayed by Home Assistant through short-lived signed paths: the player never
receives the Jellyfin token and only needs to reach Home Assistant.
