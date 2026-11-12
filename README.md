# rekfix

Diagnose, and carefully repair, rekordbox USB exports, without wiping the stick and
re-exporting.

A re-export rebuilds the stick from your rekordbox library. That is the usual advice when a
stick misbehaves, but it throws away what exists only on the stick: play histories the decks
saved, cue points set on the decks, and everything, if the library it came from is gone. It
also doesn't help when the problem isn't damage at all. rekfix starts by finding out what's
actually wrong.

> rekfix is an independent project. It is not made, endorsed or supported by AlphaTheta or
> Pioneer DJ; rekordbox, CDJ and XDJ are trademarks of their owners. It reads the stick's
> database in a format that isn't officially documented. Its few repairs edit that database
> in place, after saving a copy, but keep your own backups and test on your decks before
> relying on a repaired stick.

## Install

Python 3.11 or newer, no other packages:

```
pipx install "git+https://github.com/wolfybird/rekfix"
```

(or clone it and run `python -m rekfix ...`).

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
3. **Look up the symptom** in [docs/playbook.md](docs/playbook.md): what it usually means, how
   to confirm it, and what fixes it.

## Commands

| command | does |
|---|---|
| `check STICK\|FILE [--scan] [--second-word] [--tables]` | health check; `--scan` reads a damaged file by page instead of by its (possibly broken) header and chains |
| `diff OLD NEW` | what changed between two copies of export.pdb: rows, pages, playlists, root menu, histories |
| `menu STICK [--hide ...\|--show ...] [--apply]` | the players' root browse menu; hide the categories that freeze older decks on big libraries |
| `prune STICK [--restore-from DIR] [--restore-all] [--apply]` | remove tracks whose audio is gone from the stick, the way rekordbox removes tracks; keeps (or copies back) any a play history lists |
| `history STICK [--tracks] [--scan]` | the play histories the decks saved on the stick |
| `snapshot STICK DEST [--full]` | databases + file list, before you change anything |
| `readtest STICK` | how fast and evenly the stick reads (Windows: bypassing the cache) |

Only `menu --apply` and `prune --apply` write.

**Before writing,** they:
- refuse if rekordbox is running;
- refuse to edit a database `check` finds damaged;
- save a copy of `export.pdb`.

**When writing,** they edit fixed-size fields in place, with the same bookkeeping rekordbox
does: page counts and flags, and a new sequence number.

**After editing,** `prune` re-reads the result from scratch before saving. It checks that the
result is what was planned, and that no byte outside the fields it meant to change is
different.

## What it's built from

Four real cases on XDJ-1000MK2 decks, written up in [docs/cases](docs/cases):

- [a stick whose database rotted](docs/cases/2026-07-database-bitrot.md);
- [playlists merged by an export](docs/cases/2026-10-merged-playlists.md);
- [a stick that started listing files inconsistently](docs/cases/2026-10-flaky-listing.md);
- [a healthy stick that froze the decks anyway](docs/cases/2026-11-category-freeze.md): the
  library was simply too big for four of the root categories.

The database format notes, including the traps that cost the most time, are in
[docs/format.md](docs/format.md).

## Licence

MIT, see [LICENSE](LICENSE). Thanks to [Deep Symmetry](https://github.com/Deep-Symmetry/crate-digger)
for their documentation of the export format, and to the
[pyrekordbox](https://github.com/dylanljones/pyrekordbox) project.
