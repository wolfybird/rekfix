# July 2026: a stick whose database rotted

**Setup:**
- A 256 GB "fit"-style flash drive (small, enclosed, runs hot) with about 13,200 tracks, played
  on two XDJ-1000MK2s.
- The rekordbox library it was exported from no longer existed, so the stick was the only copy
  of the playlists, cues and years of play history.

## Symptoms

- On loading the stick, the USB light flashed for about a minute, then the deck froze solid.
  This happened on both decks, linked or not; other sticks worked.
- rekordbox called the device library corrupted and offered to delete it.

## What checked out

- The file system: `chkdsk` was clean, the dirty bit wasn't set, and there was nothing in the
  event log.
- Every audio file (13,216) and every analysis file (39,627, all with valid headers).
- The database's page chains, followed link by link.

## What was wrong

**Inside `export.pdb`, individual records were damaged:**
- about 10% of track rows had corrupt file-path strings, scattered across 626 of 1,630 track
  pages;
- 1,156 duplicate track rows.

The pattern looked like gradual flash wear rather than one bad block. Later, copying off the
stick hit repeated "insufficient system resources" errors, and directory listings sometimes came
back empty. A few days after that, the drive dropped off the bus entirely.

**Found later with rekfix:** the file's header described a different database. It claimed 43
pages with every table one page long, while the file held 1,957 pages; some tables' page links
also ran into other tables. `rekfix check` now reports both (`header.stale`, `pages.unreached`),
and `--scan` reads the data regardless.

## What didn't work

**Patching or rebuilding `export.pdb` by hand.** Five attempts were made:
- shortening damaged strings: the deck still froze;
- a full rebuild from the audio files' tags: the freeze was gone, but the decks showed 0 songs
  and 0 playlists;
- several variations, including byte-identical copies of the original pages: still 0 / 0.

The decks clearly validate something beyond the page format, and it was never identified.

## What worked

**Rebuilding the library in rekordbox instead:**
1. Read everything recoverable from the damaged database: tracks, playlists (57),
   **191 play histories with 2,700 plays**, and keys.
2. Read every audio file's tags, and every hot cue, memory cue, loop and beat grid from the
   analysis files.
3. Write all of it as a rekordbox XML file, import that into a fresh rekordbox library, and
   export a new stick from it.

**`rekfix rebuild` now does this in one command.** Replayed in November on the same backup, with
the recovery read and the WAV/AIFF files that had since moved to another folder, it took under a
minute and:
- found all 13,216 audio files, 12,962 at their database paths and 245 by name;
- placed **all 7,416 playlist entries and all 2,700 history plays**;
- recovered 12,749 beat grids and 396 hot cues. July's hand-built pipeline got 9,714 and 288;
  its cue parser had a bug.

It also refused 407 analysis folders the damaged database pointed at: the file each folder names
is a different track. Using them would have given those tracks someone else's beat grid and cues.

Later still, the hand-built `export.pdb` from the attempts above, still on the old stick, was
compared with a database rekordbox wrote. It differed in ways July never checked: every table's
"empty candidate" page was a page in use, and every index page was blank. With both fixed,
rekordbox got further, then crashed reading it. See
[the roadmap's open questions](../roadmap.md#open-questions).

## Lessons

- **Copy everything off a misbehaving stick first.** This one died mid-recovery; the backup made
  the recovery possible.
- **When the DJ says a playlist, a history or a key "should be there", believe them.** Four times
  the extraction said something was missing, and four times a reader bug was hiding it:
  - the row count over 255;
  - the second flags word;
  - history living in tables 11/12, not 19;
  - key ids read from the wrong offset.
- **Check a parser's coverage:** the rows found on a page should account for its used bytes.
- **Replace hot-running, enclosed flash drives** in a DJ bag, and keep a snapshot of the
  `PIONEER` folder.
