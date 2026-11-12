# Roadmap

What exists:
- read-only checks, and a recovery read for damaged files;
- diff, play histories, snapshots and a read test;
- two repairs:
  - hiding root categories (`menu`);
  - removing tracks whose audio is gone (`prune`). It keeps tracks a play history lists, can
    copy audio back, and tidies what earlier removals left behind.

`prune` reproduces the [merged-playlists case](cases/2026-10-merged-playlists.md)'s hand fix
exactly: replayed on a copy of that stick's database, it deleted the same 5,642 track rows,
bit for bit.

Next, roughly in order of how often they'd have helped:

1. **Deck-test `prune`'s tidy-up on a real stick:** the corrected song count, the leftover
   artwork and the page flags. See the open questions below.
2. **`rekfix playlists`: compare and repair playlists against a rekordbox library.**
   - Read rekordbox's `master.db` (optional pyrekordbox dependency) or a rekordbox XML export.
   - Report merged, missing and reordered entries in both stick databases.
   - Cut a merged playlist back to its own tracks by reusing its entry rows.
3. **`rekfix rebuild-xml`: rebuild a lost library as a rekordbox XML file** from a damaged stick:
   - the recovery read of `export.pdb`;
   - the audio files' tags;
   - cues, loops and beat grids from the analysis files.

   Import the result into rekordbox, then export a fresh stick. This is the path that worked in
   the [bit-rot case](cases/2026-07-database-bitrot.md); the tooling exists and needs
   generalising.
4. **`rekfix cues`: read and write cues in the analysis files.**
   - Handle the 44-byte entries decks write.
   - Import deck-set cues into rekordbox before a re-export, so they survive it.
5. **`rekfix clone`: make an exact copy of a stick** from a snapshot plus a music library,
   without a rekordbox export, then read back every file
   ([flaky-listing case](cases/2026-10-flaky-listing.md)).
6. **OneLibrary (`exportLibrary.db`) checks** next to the `export.pdb` ones, for newer players.
   The file is encrypted SQLite; pyrekordbox can open it.
7. **A library-size advisor:**
   - track counts against what each player model handled in practice;
   - which categories to hide.

   Needs reports from other players: CDJ-2000NXS2, CDJ-900, XDJ-RX2...

Open questions worth testing on real decks:

- What makes players accept a rebuilt `export.pdb`? Hand-built files showed 0 songs. Candidates:
  - the header's next-unused page and the empty-candidate pages;
  - index pages;
  - consistency with `exportExt.pdb`.
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
