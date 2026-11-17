# Roadmap

What exists:
- read-only checks, and a recovery read for damaged files;
- diff, play histories, snapshots and a read test;
- two repairs:
  - hiding root categories (`menu`);
  - removing tracks whose audio is gone (`prune`). It keeps tracks a play history lists, can
    copy audio back, and tidies what earlier removals left behind;
- **`rebuild`: a damaged or lost library as rekordbox XML.**
  - From the database, even a recovery read: tracks with their metadata, key and BPM;
    playlists; play histories.
  - From the analysis files: beat grids, hot cues, memory cues and loops.
  - The audio is found by path, name, size or a shortened name.
  - Analysis is matched by the path each analysis file names. That works with no database at
    all, and it refuses analysis the database points at wrongly.
- **`clone`: an exact copy of a stick on another, without rekordbox.**
  - Copies from the stick, or from a `--full` snapshot plus the music library.
  - Writes the databases last and reads every file back.
  - A later run copies only what changed.

How they were checked against real sticks:
- **`prune`** reproduces the [merged-playlists case](cases/2026-10-merged-playlists.md)'s hand
  fix exactly: replayed on a copy of that stick's database, it deleted the same 5,642 track
  rows, bit for bit.
- **`rebuild`, on the [bit-rot case](cases/2026-07-database-bitrot.md)'s backup:** it found all
  13,216 audio files, placed all 7,416 playlist entries and 2,700 history plays, and recovered
  more beat grids and hot cues than July's hand-built pipeline.
- **`rebuild`'s cue reader, against the user's own rekordbox library:** it matches on 7,531 of
  7,549 tracks with cues. The 18 differences are cues edited in rekordbox since, or set on the
  decks.
- **`clone`, on the [flaky-listing case](cases/2026-10-flaky-listing.md)'s snapshot:**
  - planning all 74,519 files took 8 seconds, matching 13,207 audio files to the library;
  - a real copy of 61,310 files, read back one by one, had no mismatches;
  - planning a refresh found nothing to copy.

Next, in the order the stick owner asked for:

1. **A visual, friendly GUI of its own**, so nobody needs the command line to rescue a stick.
   - **Look:** a desktop window in the same design language as mixarchive's app (HTML), with its skins, Winamp skins included.
   - **Mockups first.**
   - **Flows:**
     - pick a stick, check it, and see what's wrong in plain words;
     - for each repair: the dry run, a confirmation, then apply;
     - long copies (clone, snapshot) with the live progress meter;
     - the last-ditch rescue as a guided path.
2. **`rekfix playlists`: compare and repair playlists against a rekordbox library.**
   - Read rekordbox's `master.db` (optional pyrekordbox dependency) or a rekordbox XML export.
   - Report merged, missing and reordered entries in both stick databases.
   - Cut a merged playlist back to its own tracks by reusing its entry rows.
3. **`rebuild` extras:**
   - fill in metadata from the audio files' tags (optional mutagen) when database rows are
     damaged;
   - carry My Tags over from `exportExt.pdb`.
4. **`rekfix cues`: write cues into the analysis files,** and import deck-set cues into
   rekordbox before a re-export, so they survive it.
5. **OneLibrary (`exportLibrary.db`) checks** next to the `export.pdb` ones, for newer players.
   The file is encrypted SQLite; pyrekordbox can open it.
6. **A library-size advisor:**
   - track counts against what each player model handled in practice;
   - which categories to hide.

   Needs reports from other players: CDJ-2000NXS2, CDJ-900, XDJ-RX2...
7. **Deck-test `prune`'s tidy-up on a real stick:** the corrected song count, the leftover
   artwork and the page flags.

## Open questions

- **What makes rekordbox (and players) accept a rebuilt `export.pdb`?** Hand-built files showed 0
  songs on the decks, and rekordbox called them corrupted. Tested with rekordbox as the judge,
  on a spare stick holding July's hand-built file:
  1. Removing tracks whose audio was gone: still "corrupted".
  2. Giving every index page rekordbox's layout, and every table a real empty-candidate page (see
     [format.md](format.md#index-pages-and-spare-pages)): rekordbox read further, then wrote its
     `brokendb` marker.
  3. Rebuilding `exportExt.pdb`'s header, which had been copied from an old `export.pdb`:
     masked by the marker. With the marker moved aside, rekordbox crashed reading the stick.

  Still to try:
  - the tables every export fills but the hand-built file left empty (colours, the category
    names, the root menu, table 18, the song-count row in table 19);
  - consistency with `exportLibrary.db`.

  Best next step: let rekordbox export a few tracks to a wiped spare stick, as a tiny reference
  database, then change one thing at a time on a copy.
- What the second footer word means. Rows present only there were either deleted, or live on
  older exports.
- At what track count each root category starts to freeze a given player.
- **Artists, albums, genres and labels no track uses.** Does rekordbox delete them when their
  last track goes? And if one is deleted, does rekordbox's next export write it again for a new
  track by that artist, or assume it's still there? Test on a spare stick:
  1. export a few tracks;
  2. remove one artist's only track with `prune`, deleting the artist by hand too;
  3. export another track by that artist;
  4. read the result with `rekfix diff`.

  Until then, `prune` leaves them, and `check` lists them as `rows.unused`.
- **Does a player care about the page flag (0x10) or the page stamp?** rekordbox always sets
  them. A hand edit without them loaded fine on XDJ-1000MK2s.
- **Does the ARTIST list on a player show artists with no tracks?** If it does, removing
  `rows.unused` matters for big libraries.
- **What newer players see:** `prune` edits `export.pdb` only. `exportLibrary.db` (encrypted
  SQLite) still lists the removed tracks until rekordbox updates it.
