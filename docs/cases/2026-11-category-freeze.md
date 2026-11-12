# November 2026: a healthy stick that froze the decks anyway

**Setup:** a one-month-old 256 GB stick, about 14,100 tracks, played on two XDJ-1000MK2s.

## Symptoms

- Browsing the root menu, the deck froze around TIME, HOT CUE BANK, FILE NAME or BITRATE (the
  bottom four).
- PLAYLIST and ARTIST still opened.
- Scrolling slowly, waiting for each category's list to fill, got further but never reached
  BITRATE.
- The other deck, playing a track from this stick over Link, dropped into emergency loop.
- It started after a jam session on another player, where the stick also recorded the mix.

## Investigation

1. **Copied the databases off first,** and kept copies at every step after.
2. **Database health:** structure, every track row, histories and playlists were all consistent.
   The menu, column and sort tables were byte-identical to a 10-day-old snapshot that hadn't
   frozen anything.
3. **Timeline:** the play histories show the order of events. The jam's histories contain none
   of the 373 tracks added since the snapshot; only the later test sessions do. So the new tracks
   went onto the stick after the jam, through an export the next day. The jam was likely a
   coincidence.
4. **Test 1:** moved a Mac's hidden folders and the deck's filter file (`RBFLTR.DAT`) off the
   stick. Still froze, now as early as TIME.
5. **Test 2:** moved the jam recording off. Still froze.
6. **Test 3:** swapped in the 10-day-old `export.pdb` (13,756 tracks). Still froze. So it wasn't
   the new tracks, or anything else that changed in the database.
7. **Stick health:** read uncached, the database came at 120 MB/s with no read slower than
   2.5 ms; music files at 200 MB/s; no read errors, ever.
8. **The decisive clue:** a friend's stick of the same model, with ~16 GB on it, worked fine on
   the same decks.

## The cause

ARTIST, ALBUM and PLAYLIST have ready-made lists in the database. TIME, HOT CUE BANK, FILE NAME
and BITRATE don't: the player scans and sorts every track the moment you land on one. With about
14,000 tracks that overwhelms an XDJ-1000MK2. Scrolling fast starts the next scan before the
last one finishes.

The stick may well have frozen on those categories all along; nobody had scrolled that far
before.

## The fix

The four categories were moved to *Inactive* in rekordbox (Preferences › Category), and rekordbox
updated the stick. Afterwards the deck browsed the whole root menu, including fast scrolling top
to bottom and back, and new tracks loaded and played.

`rekfix menu --hide` writes the same change directly.

**A side puzzle:** the decks' USB info panel said "19771 songs", with 14,129 tracks in the
database. That turned out to be left over from the hand fix in the
[merged-playlists case](2026-10-merged-playlists.md#a-month-later-what-the-hand-fix-missed),
and unrelated to the freeze.

## Lessons

- **"It broke after X" is a lead, not a cause.** Changing one thing per deck test turned the
  timeline's suspects (a Mac, a recording, new tracks) into ruled-out ones, quickly.
- **Comparing with a known-different setup** (a small library, same stick model, same decks)
  separated "the stick" from "the library" in one step.
- **Mind what writes to the stick:** rekordbox updates a stick as soon as it sees it, so keep it
  closed during tests.
