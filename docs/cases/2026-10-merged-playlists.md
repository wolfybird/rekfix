# October 2026: playlists merged by an export

**Setup:**
- A new 256 GB stick for XDJ-1000MK2s, about 14,000 tracks.
- Eleven new playlists were dragged onto it one at a time in rekordbox (Device Library).
- A big playlist had been removed from the stick shortly before; its audio files were deleted
  from the stick by hand.

## Symptoms

Found by an audit that compared every playlist on the stick, in both databases, with rekordbox,
track by track.

**`exportLibrary.db` (newer gear):** 259 of 261 playlists were exact.

**`export.pdb` (what the XDJs read):**
- Nine of the eleven new playlists also contained the playlist dragged before them; one held
  three playlists' worth (1,709 tracks).
- 5,646 tracks of the removed playlist were still listed, with their files gone. They'd show in
  the decks' track list and fail to load.

Re-syncing in rekordbox rewrote `export.pdb` but fixed none of it.

## What it wasn't

The first audit also reported 29 playlists "missing tracks". That was a bug in the audit's own
reader: the row-count trap, on pages with more than 255 rows (see
[format.md](../format.md#pages)). With the reader fixed, those playlists were exact.

## The fix: in place, not a re-export

A fresh export would have rebuilt both databases, but it would have lost what existed only on
the stick:
- play histories the decks had saved;
- 21 hot cues set on the decks.

Instead, with rekordbox closed and copies of the databases saved:

1. **The dead tracks:** removed from `export.pdb` the way rekordbox deletes rows (present bit
   cleared, page count updated). Four of them were still listed in a deck's play history, so
   their audio was copied back instead, keeping the histories intact.
2. **The merged playlists:** each cut back to its own tracks, in rekordbox's order. The playlist
   entries it already had were reused and renumbered, and the extras dropped. Every entry is a
   fixed 12 bytes, so nothing had to grow or move.
3. **`exportLibrary.db`:** a stray folder of empty duplicates and 210 superseded rows removed.
4. **The decks' hot cues:** imported into rekordbox first, so a future export won't lose them.
5. **Checks:**
   - every playlist re-read and compared with rekordbox: 260 of 261 exact, the one exception
     intended;
   - page counts consistent on all 2,936 pages.

**Deck test:** the removed playlist's tracks were gone, histories worked, and the playlists were
right.

## A month later: what the hand fix missed

The decks' USB info panel showed **19,771 songs** for a database of 14,129 tracks. The players
don't count tracks; they show a number rekordbox stores in table 19 and adjusts as it adds and
removes tracks. The fix had removed 5,642 tracks without lowering it (19,398 → still 19,398).
rekordbox's next export then added its 373 new tracks to the stale number: 19,398 + 373 =
19,771, and 14,129 + 5,642 = 19,771.

Comparing rekordbox's own deletions on the same stick showed three more differences:
- **Artwork:** rekordbox deletes a removed track's artwork row too. The fix left 5,643 behind.
- **Artists:** the fix left 1,640 artists no track used. rekordbox's exports have none of
  those. Whether rekordbox deletes them itself is still open
  ([roadmap](../roadmap.md)).
- **The page flag:** rekordbox flags every page with deleted rows (0x24 → 0x34); the fix didn't.

None of it stopped the decks from working.

`rekfix prune` now does the whole removal. Replayed on a copy of the database from before the
fix, it deleted exactly the same 5,642 track rows, bit for bit. It also:
- set the song count to 13,756;
- removed the 5,643 artwork rows;
- flagged the pages.

On the stick as it is now, `rekfix check` reports what's left (`tracks.count`, `artwork.unused`,
`rows.unused`, `page.deleted-flag`), and `rekfix prune --apply` would tidy it.

## Lessons

- **Drop onto empty space** in the stick's playlist list, never onto an existing playlist.
- **Audit both databases.** Older players read `export.pdb` only, and it can be wrong while
  `exportLibrary.db` is right.
- **Don't delete files from a stick** while its database still lists them; let rekordbox remove
  the tracks.
- **Take a snapshot before every export.** There was none here, so "roll back" wasn't an option.
- **Compare a hand edit with what rekordbox writes for the same change,** not only with what
  the decks accept. The decks accepted a partial removal; its traces only showed up a month
  later as a wrong song count.
