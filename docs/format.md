# What's on a rekordbox USB stick, and the traps in reading it

These are working notes from repairing real sticks for XDJ-1000MK2 decks. They build on
[Deep Symmetry's crate-digger documentation](https://djl-analysis.deepsymmetry.org/rekordbox-export-analysis/exports.html),
which is the best general reference. Where something here was found the hard way, it says so.
Byte offsets are hexadecimal. `export.pdb` is little-endian; the analysis files are big-endian.

## The files

```
PIONEER/
  rekordbox/export.pdb        the database older players read (DeviceSQL)
  rekordbox/exportExt.pdb     extra tables (tags...), same page format
  rekordbox/exportLibrary.db  "OneLibrary": an encrypted SQLite database newer gear reads
  rekordbox/RBFLTR.DAT        the deck's track filter (BPM range, keys), written by the deck
  USBANLZ/Pxxx/xxxxxxxx/      per-track analysis: ANLZ0000.DAT, .EXT, .2EX (waveforms, beat grid, cues)
  Artwork/                    cover art thumbnails
  DEVSETTING.DAT, djprofile.nxs, CDJ/, MPJ/   player settings, your DJ profile
Contents/                     the audio, as Contents/<artist>/<album>/<file>
```

The XDJ-1000MK2s here read `export.pdb`, not `exportLibrary.db`. When the two disagree, what
those decks show comes from `export.pdb`. rekordbox keeps both up to date when it exports, but
they can drift apart.

## export.pdb

### File header (page 0)

| offset | size | |
|---|---|---|
| 04 | u32 | page size (4096) |
| 08 | u32 | number of tables (20) |
| 0C | u32 | next unused page |
| 10 | u32 | unknown (1) |
| 14 | u32 | sequence number: rekordbox raises it on every write, even just from seeing the stick |
| 1C | 16 × n | table descriptors: type, empty candidate page, first page, last page (u32 each) |

**Trap: the header can describe a different database than the file holds.** On one damaged
stick, the header claimed 43 pages with every table one page long, but the file had 1,957
pages. The tables' page chains (and every page's own type field) still led to all the data.
`rekfix check` reports this as `header.stale`; `--scan` reads past it.

### Tables

| type | table | notes |
|---|---|---|
| 0 | tracks | see below |
| 1–6 | genres, artists, albums, labels, keys, colors | id + name |
| 7 | playlist tree | parent, ?, sort, id, is-folder (u32 each), name at +14 |
| 8 | playlist entries | entry index, track id, playlist id (u32 each) |
| 11 | history playlists | id (u32), name at +4 ("HISTORY 001"...) |
| 12 | history entries | track id, history playlist id, entry index (u32 each) |
| 13 | artwork | id + path |
| 16 | columns | the category names players show (GENRE ... BITRATE) |
| 17 | menu | the players' root browse menu (below) |
| 19 | "history" | **not** the play history; holds a date stamp and scaffolding |

**Trap: play history is in tables 11 and 12, not 19.** Table 19 looked like history and was
empty, which once led to the wrong conclusion that a stick's histories were lost.

### Pages

Every page has a 0x28-byte header; rows follow from +28:

| offset | size | |
|---|---|---|
| 04 | u32 | page index (equals its position) |
| 08 | u32 | table type |
| 0C | u32 | next page in the table's chain |
| 18 | 3 bytes | **packed counts**: low 13 bits = row slots, high 11 bits = rows still present |
| 1B | u8 | flags: 0x40 set = not a data page (index pages), skip its rows |
| 1C | u16 | free bytes |
| 1E | u16 | used bytes (the rows' area) |

**Trap: the byte at +18 is not the row count.** It's only the low byte of the 13-bit slot
count. Reading it alone silently loses rows on any page holding more than 255, which happens
often in the playlist-entry and history tables. It once made full playlists look empty.

A cross-check that needs no header field is the slot count implied by the free and used sizes.
What's left of the page is the row index:

```
footer = page_size - 0x28 - used - free        (bytes)
full, rem = divmod(footer / 2, 18)
slots  = full * 16 + (rem - 2 if rem >= 3 else 0)
```

### The row index (page footer)

Rows are indexed from the **end** of the page, in groups of 16, each group 0x24 bytes long and
growing backwards. For group `g`, let `base = page_end - g * 0x24`:

| position | |
|---|---|
| base - 2 | a second flags word |
| base - 4 | **present** bits, one per row in the group |
| base - 6 - 2 × r | offset of row r (relative to +28) |

The last group is shorter: 2 bytes per row plus the two flags words.

**Deleting a row** the way rekordbox does: clear its present bit and lower the page's 11-bit
present count. Nothing is moved or zeroed, so the row's bytes stay readable.
`rekfix.pdb.Pdb.drop_row` does exactly this.

**Trap: the second flags word.** Normally the present bits are at base - 4, and rows rekordbox
deleted have their bit cleared there while the second word may still have it set. One older
export had whole groups, thousands of playlist entries, with the first word 0 and the second
set. Either:
- they were deleted rows (one whole playlist's worth), or
- that export used the words the other way round.

The data alone can't settle which. `rekfix` uses the first word, unless a whole table uses
the second (then it's clearly that export's layout). `--second-word` includes the rest, and
says how many rows that adds.

### Strings

| first byte | layout |
|---|---|
| odd | short ASCII: `(byte >> 1)` is the total length including this byte |
| 0x40 | long ASCII: u16 total length, pad byte, then the text |
| 0x90 | long UTF-16LE: u16 total length, pad byte, then the text |

**Trap:** 0x40 strings are ASCII, not UTF-16. Decoding them as UTF-16 turns paths into CJK-looking
garbage.

### Track rows

| offset | size | |
|---|---|---|
| 08 | u32 | sample rate |
| 10 | u32 | file size |
| 30 | u32 | bitrate |
| 38 | u32 | tempo × 100 |
| 3C / 40 / 44 | u32 | genre / album / artist id |
| 48 | u32 | track id |
| 50 | u16 | year |
| 54 | u16 | duration (seconds) |
| 5E | 21 × u16 | string offsets, relative to the row |

String slots used here: 10 date added, 14 analysis file path, 17 title, 19 file name,
20 file path.

rekordbox leaves slack after each track row (zeros, at least 36 bytes in practice). A longer
title can grow into it by shifting the later strings and their offsets, without moving the row.

### The root menu (table 17) and the heavy categories

Each root category is one 8-byte row: category id (a column id from table 16), label id, a
flags word, and its position in the menu (1, 2, 3...; **0 = not shown**). When rekordbox hides
categories (Preferences › Category), it:
- sets their position to 0;
- adds 0x100 to their flags;
- renumbers the visible ones 1..n in the order you chose.

`rekfix menu --hide` does the same in place. On one stick, rekordbox's own result and rekfix's
were byte-identical for the hidden rows.

TIME (14), BITRATE (15), FILE NAME (16) and HOT CUE BANK (18) have no ready-made list in the
database. The player builds them by scanning every track when you land on them in the menu.
On an XDJ-1000MK2 with ~14,000 tracks that froze the deck; with the four hidden, the same
stick browsed fine.

## RBFLTR.DAT (the deck's track filter)

Big-endian, unlike `export.pdb`. An `FMAI` header carries the writing model and version as text
("XDJ-1000MK2", "1.45"), then `FCND` conditions:
- **0x?106:** a BPM range, as two u32 values × 100.
- **0x?111:** a list of key ids.

The high byte looks like an on/off flag (01 = on). The deck recreates the file if it's
missing.

## Analysis files: cues

Cues live in the analysis files, not in `export.pdb`:
- **ANLZ0000.DAT:** `PCOB` lists of 56-byte `PCPT` entries (memory cues: list type 0;
  hot cues: type 1).
- **ANLZ0000.EXT:** `PCO2` lists of `PCP2` entries:
  - rekordbox writes them 88 bytes long, plus a comment;
  - an XDJ-1000MK2 writes them **44 bytes** long, with no comment or colour.

Hot cue numbers in a PCPT/PCP2 entry are 1–8 for A–H, and 0 for a memory cue.

**Trap:** some parsers (pyrekordbox's `AnlzFile`, at the time of writing) fail on the 44-byte
entries a deck writes. Walk the tags by hand: each tag has a fourcc, u32 header length and
u32 total length.

rekordbox's own database numbers hot cues A–C as kinds 1–3 and D–H as 5–9 (there is no 4);
memory cues are 0.

## Players and dates

Players have no clock. Files they write get fixed dates (1979, 2015, 2017 or 2024, depending
on the model), and those dates sort to the bottom of a date-sorted list. A file date is no
evidence of when a deck wrote it.
