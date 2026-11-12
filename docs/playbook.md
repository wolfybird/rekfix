# Playbook: symptom → cause → fix

Before trying any fix:

1. **Snapshot the stick**: `rekfix snapshot E:\ D:\stick-backups`. Most fixes are reversible
   only if you have a copy.
2. **Close rekordbox.** It writes to a stick as soon as it sees it: it bumps the database's
   sequence number and keeps a working file next to `exportLibrary.db`. Don't let it run
   while you test or edit.
3. **Eject properly** before pulling the stick. An unplug mid-write is a classic way to
   corrupt a database.
4. **Change one thing per deck test.** A stick that misbehaves on the decks has to be tested on
   the decks; nothing on a computer reproduces a player's firmware.

---

## The deck freezes when you scroll the root menu

**What you see:**
- The stick loads, and PLAYLIST, ARTIST and the top categories open.
- Scrolling down the root menu (TIME, HOT CUE BANK, FILE NAME, BITRATE) freezes the deck.
- Scrolling fast makes it happen sooner; waiting for each category's list to fill gets you
  further.
- A second deck playing from this stick over Link drops into emergency loop.

**Usually means:** the library is too big for those categories on that player. Unlike ARTIST
or PLAYLIST, these have no ready-made list in the database: the player sorts every track the
moment you land on one. On an XDJ-1000MK2 this tipped over at roughly 13,500–14,000 tracks.
The stick is fine.

**Confirm:**
- `rekfix check E:\` is clean apart from a `menu.heavy` warning.
- The same stick model with a small library works on the same decks.
- An older copy of the database (`rekfix diff` shows it's the same library, give or take) freezes
  too.

**Fix:** hide the heavy categories.
- **In rekordbox:** Preferences › Category, move TIME, HOT CUE BANK, FILE NAME and BITRATE to
  *Inactive*, then plug the stick in so rekordbox updates it. This survives future exports.
- **Without rekordbox:**
  `rekfix menu E:\ --hide time,"hot cue bank","file name",bitrate --apply`. It writes the same
  bytes rekordbox does for that change.

Case: [2026-11-category-freeze](cases/2026-11-category-freeze.md).

## The deck freezes when it loads the stick, or rekordbox says the device library is corrupted

**What you see:**
- The USB light flashes for a minute or so, then the deck hangs.
- It happens on every deck, linked or not.
- Other sticks work.
- rekordbox offers to delete the device library.

**Usually means:** `export.pdb` itself is damaged. The file system and the audio are often fine.
Flash wear ("bit rot") on an old, hot-running stick is a common cause.

**Confirm:** `rekfix check E:\`. These are the patterns seen so far:
- `header.stale`: the header describes a different, smaller database than the file holds.
- `pages.unreached`: rows the table's page chain never reaches.
- `tracks.strings`: track text that can't be read.
- `tracks.duplicate-id`: the same track id on several rows.

**Fix:**
1. Copy the stick off first: `rekfix snapshot E:\ D:\backup --full`.
2. **Recover what's in the database:** `rekfix check E:\ --scan --tables` and
   `rekfix history E:\ --scan --tracks` read it page by page, ignoring the damaged header and
   chains.
3. **Rebuild the library in rekordbox from that,** then export a fresh stick. With no
   rekordbox library behind the stick, a rekordbox XML import (tracks, cues from the analysis
   files, playlists, histories) worked. Patching the binary file by hand didn't: the players
   showed 0 songs every time. See the case.
4. **Replace the stick** if it also shows the "flaky stick" signs below.

Case: [2026-07-database-bitrot](cases/2026-07-database-bitrot.md).

## Playlists on the stick contain other playlists' tracks

**What you see:**
- After dragging playlists onto the stick in rekordbox, some playlists on the decks are the size
  of two or three playlists combined.
- Or a folder's playlists arrive empty while copies sit at the top level.

**Usually means:** the export merged them. When a drop lands on an existing playlist on the
stick, rather than on the empty space below the list, its tracks are added to that playlist.
Older players read `export.pdb`, newer gear reads `exportLibrary.db`, and the two can disagree.

**Confirm:** compare each playlist on the stick with rekordbox, track by track. Here it showed
`export.pdb` wrong and `exportLibrary.db` almost right.

**Fix:**
- **If nothing on the stick is precious:** clear it and re-drag once, onto empty space.
- **If it holds deck histories or cues you want:** remove the extra entries in place, the way
  rekordbox deletes rows (clear the row's present bit, fix the page's count and flag). That
  kept every history and cue. A `rekfix` command for this is planned; `rekfix.pdb.Pdb.drop_row`
  and `commit()` are the primitives.

Case: [2026-10-merged-playlists](cases/2026-10-merged-playlists.md).

## Tracks show on the stick but won't load

**Usually means:** the database still lists tracks whose files were deleted from the stick, for
example when a playlist's files were removed by hand rather than in rekordbox.

**Confirm:** `rekfix check E:\` reports `files.audio`.

**Fix:** either put the files back or remove the tracks.
- **In rekordbox:** remove the tracks from the stick's device library and let it update the stick.
- **Without rekordbox:** `rekfix prune E:\` shows what removing them would change; add `--apply`
  to do it. It removes them the way rekordbox removes tracks, along with their playlist entries
  and artwork, and it corrects the song count the decks show.
  - It **keeps any track a deck's play history lists**, since removing it would break that
    history. `--restore-from D:\Music` copies those tracks' audio back from your library or a
    snapshot: same file size, name or the start of it.
  - `--restore-all` copies back every missing track it can find, and removes only the rest.

`prune` edits `export.pdb`, which older players read. `exportLibrary.db`, which newer players
and rekordbox's view of the stick use, still lists the tracks until rekordbox removes them.

## The deck's info panel shows more songs than the stick has

**Usually means:** tracks were removed from `export.pdb` by a tool other than rekordbox, which
left the song count alone. The decks show a stored number; they don't count. Harmless, but it's
a sign of an incomplete removal: artwork and artists the removed tracks used are usually still
there too.

**Confirm:** `rekfix check E:\` reports `tracks.count`.

**Fix:** `rekfix prune E:\ --apply` sets the count right, removes the leftover artwork, and flags
the pages the earlier edit changed, as rekordbox would have. Check the dry run first; it lists
everything it will change.

## Files come and go, copies fail, the stick drops off

**What you see:**
- The same folder lists different files on different tries.
- Copies fail with "insufficient system resources" (ERROR 1450).
- The drive vanishes mid-copy.
- `chkdsk` finds nothing wrong.

**Usually means:** the stick's controller is failing, often from heat. Small, enclosed sticks
run hot.

**Fix:**
- Copy what you can now: `rekfix snapshot E:\ D:\backup --full`, then the music.
- Stop writing to it, and replace it.
- Rebuild the new stick from the snapshot plus your library's copies of the audio, with no
  rekordbox export. That keeps deck histories and cues; see
  [2026-10-flaky-listing](cases/2026-10-flaky-listing.md).
- `rekfix readtest E:\` shows how the stick reads. A healthy one reads its database at well over
  50 MB/s with no read slower than a few milliseconds.

## Lost deck histories or cues after a re-export

A re-export replaces the stick's databases with rekordbox's view of your library. Anything the
decks wrote is gone unless rekordbox imported it first: play histories, and cues or loops set on
the decks.

Snapshot before exporting, and import the stick's histories into rekordbox first. `rekfix history`
shows what's there.

---

## Things that look suspicious but aren't

- **`.Spotlight-V100` and `.fseventsd` at the stick's root** (a Mac mounted it): harmless.
  Removing them changed nothing.
- **`PIONEER\rekordbox\RBFLTR.DAT`** is the deck's saved track filter (BPM range and keys). The
  deck recreates it if it's missing.
- **A `PIONEER REC` folder** holds recordings from players or mixers that record to USB.
  It doesn't affect browsing.
- **Odd file dates (1979, 2015, 2017, 2024):** players have no clock, so anything they write gets
  a fixed date. File dates don't tell you when a deck wrote something.
- **rekordbox changing `export.pdb` when you only plugged the stick in:** it bumps the sequence
  number. `rekfix diff` shows nothing else changed.
