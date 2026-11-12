# Roadmap

What exists (0.1): read-only checks, a recovery read for damaged files, diff, play histories,
snapshots, a read test, and one repair (hiding root categories).

Next, roughly in order of how often they'd have helped:

1. **`rekfix prune`: remove dead tracks.** Tracks whose audio is gone from the stick, removed
   the way rekordbox does it (present bit + page count). It would keep any that a deck's play
   history still lists, and offer to restore their audio instead. Used by hand in the
   [merged-playlists case](cases/2026-10-merged-playlists.md).
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
