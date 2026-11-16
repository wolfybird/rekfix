# rekfix

The last-ditch rescue for rekordbox USB sticks. Diagnose, repair, or rebuild the library from
it, before anything gets wiped.

> **Stick acting up, rekordbox offering to "delete the device library", or your library gone?
> Stop before you click Yes, format the stick or re-export to it.** Each of those destroys what
> exists only on the stick: the play histories from your gigs, cues set on the decks, and, if
> rekordbox's library is gone, the library itself. Copy the stick off and run rekfix first
> ([the checklist](https://github.com/wolfybird/rekfix#last-ditch-checklist)).

A re-export rebuilds the stick from your rekordbox library. That is the usual advice when a
stick misbehaves, but it throws away what exists only on the stick: play histories the decks
saved, cue points set on the decks, and everything, if the library it came from is gone. It
also doesn't help when the problem isn't damage at all. rekfix starts by finding out what's
actually wrong.

**When the stick is all that's left** (a dead laptop, a reinstall, a library that's gone), even a
damaged stick, `rekfix rebuild` gets your library back into rekordbox:
- every track with its key, BPM and beat grid;
- the hot cues, memory cues and loops, including ones set on the decks;
- your playlists, and the play histories from your gigs.

That's how a 13,000-track library came back after its only copy, a rotting stick, failed
([the case](https://github.com/wolfybird/rekfix/blob/main/docs/cases/2026-07-database-bitrot.md)).

> rekfix is an independent project. It is not made, endorsed or supported by AlphaTheta or
> Pioneer DJ; rekordbox, CDJ and XDJ are trademarks of their owners. It reads the stick's
> database in a format that isn't officially documented. Its few repairs edit that database
> in place, after saving a copy, but keep your own backups and test on your decks before
> relying on a repaired stick.

## Install

Python 3.11 or newer, no other packages:

```
pipx install rekfix
```

`pip install rekfix` works too. To run the latest from GitHub:
`pipx install "git+https://github.com/wolfybird/rekfix"`, or clone it and run
`python -m rekfix ...`.

## Start here

1. **Copy before anything else.** `rekfix snapshot E:\ D:\stick-backups` copies the stick's
   databases (checksum-verified) and writes a list of every file on it. Add `--full` to copy
   the analysis files too. Nothing on the stick changes.
2. **Check it.** `rekfix check E:\` reads `export.pdb`, the database older players read, and
   reports anything a deck could trip over. It checks:
   - the database's structure;
   - playlists and play histories that point at missing tracks;
   - tracks whose audio or analysis file isn't on the stick;
   - a root menu that's too heavy for the deck;
   - what an earlier removal of tracks left behind, such as a song count the decks show wrong.
3. **Look up the symptom** in [docs/playbook.md](https://github.com/wolfybird/rekfix/blob/main/docs/playbook.md): what it usually means, how
   to confirm it, and what fixes it.
4. **If the database is beyond repair,** `rekfix rebuild` gathers everything it can into a
   rekordbox XML file, to import into rekordbox and export a fresh stick from. Do this before
   anything wipes the stick.

## Last-ditch checklist

When a stick fails before a gig, or it's the only copy of your library left:

1. **Stop.** Don't answer "Yes" to rekordbox's "delete the device library", don't format the
   stick, don't export to it, and don't let a deck write to it. Keep it away from rekordbox
   until it's copied: rekordbox has been seen writing damage into a stick it then called
   corrupted. Eject it and set it aside.
2. **Copy everything off:** `rekfix snapshot E:\ D:\rescue --full` (the databases, analysis
   files and a list of every file), then the `Contents` folder (the audio). If the stick is
   failing, copy the most precious things first. Copying is all a dying stick may still allow.
3. **Find out what's wrong:** `rekfix check E:\` (add `--scan` if it reports damage) and
   [docs/playbook.md](https://github.com/wolfybird/rekfix/blob/main/docs/playbook.md). Often it isn't damage at all: a freezing deck can be a
   menu setting, a "corrupted" prompt can be a leftover marker.
4. **Repair in place** when the playbook says that's safe: `menu`, `prune`. Both are dry runs
   until `--apply`, and both save a copy first.
5. **Or rebuild** when the database is beyond repair or rekordbox's library is gone:
   `rekfix rebuild E:\ library.xml --music D:\rescue-audio`. Import it into rekordbox and export
   a fresh stick, and keep the old stick untouched until the new one has played on your decks.

## Commands

| command | does |
|---|---|
| `check STICK\|FILE [--scan] [--second-word] [--tables]` | health check; `--scan` reads a damaged file by page instead of by its (possibly broken) header and chains |
| `diff OLD NEW` | what changed between two copies of export.pdb: rows, pages, playlists, root menu, histories |
| `menu STICK [--hide ...\|--show ...] [--apply]` | the players' root browse menu; hide the categories that freeze older decks on big libraries |
| `prune STICK [--restore-from DIR] [--restore-all] [--apply]` | remove tracks whose audio is gone from the stick, the way rekordbox removes tracks; keeps (or copies back) any a play history lists |
| `rebuild STICK OUT.xml [--music DIR] [--scan] [--all-audio]` | recover the library, even from a damaged stick, as rekordbox XML: metadata, key, BPM, beat grids, hot and memory cues, loops, playlists, play histories |
| `clone SOURCE TARGET [--audio-from DIR] [--apply]` | an exact copy of a stick on another, without rekordbox: keeps deck histories and deck cues, reads every file back, refreshes by copying only what changed |
| `history STICK [--tracks] [--scan]` | the play histories the decks saved on the stick |
| `snapshot STICK DEST [--full]` | databases + file list, before you change anything |
| `readtest STICK` | how fast and evenly the stick reads (Windows: bypassing the cache) |

Only three commands change a stick, and only with `--apply` (without it, each shows what it
would do): `menu` and `prune` edit the stick's database, and `clone` writes to another stick.
None of them runs while rekordbox is running.

**Before editing,** `menu` and `prune`:
- refuse to edit a database `check` finds damaged;
- save a copy of `export.pdb`.

**When editing,** they change fixed-size fields in place, with the same bookkeeping rekordbox
does: page counts and flags, and a new sequence number.

**After editing,** `prune` re-reads the result from scratch before saving. It checks that the
result is what was planned, and that no byte outside the fields it meant to change is
different.

`clone --apply` writes only to the target stick, and never to the source. It refuses a target
that holds another library, too little space, or files FAT32 can't hold. Every file is read back
and checked against what was written.

## What it's built from

Four real cases on XDJ-1000MK2 decks, written up in [docs/cases](https://github.com/wolfybird/rekfix/blob/main/docs/cases):

- [a stick whose database rotted](https://github.com/wolfybird/rekfix/blob/main/docs/cases/2026-07-database-bitrot.md);
- [playlists merged by an export](https://github.com/wolfybird/rekfix/blob/main/docs/cases/2026-10-merged-playlists.md);
- [a stick that started listing files inconsistently](https://github.com/wolfybird/rekfix/blob/main/docs/cases/2026-10-flaky-listing.md);
- [a healthy stick that froze the decks anyway](https://github.com/wolfybird/rekfix/blob/main/docs/cases/2026-11-category-freeze.md): the
  library was simply too big for four of the root categories.

The database format notes, including the traps that cost the most time, are in
[docs/format.md](https://github.com/wolfybird/rekfix/blob/main/docs/format.md).

## Buy us a coffee

lots of hard work went into the making, testing and refining of these DJ tools. if you're able to
do so, buy us a coffee or a drink to thank us, we'd greatly appreciate it! thank you for giving
them a try, we hope you enjoy using them and that they inspire creativity and save a lot of
trouble. have a good set and happy playing. ~rfrank

[Ko-fi](https://ko-fi.com/rfrank) · [Buy Me a Coffee](https://buymeacoffee.com/wolfybird)

## Licence

GPL-3.0-or-later, see [LICENSE](https://github.com/wolfybird/rekfix/blob/main/LICENSE). rekfix is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License, version 3 or (at your option) any
later version. It comes with no warranty.

Thanks to [Deep Symmetry](https://github.com/Deep-Symmetry/crate-digger)
for their documentation of the export format, and to the
[pyrekordbox](https://github.com/dylanljones/pyrekordbox) project.
