"""Read (and carefully edit) a rekordbox USB export.pdb, the DeviceSQL database older Pioneer
players read (CDJ-2000NXS2, XDJ-1000MK2 and earlier; firmware that doesn't use exportLibrary.db).

The layout notes behind this are in docs/format.md. In short:
- page 0 is the file header: page size, table count, next unused page, a sequence number and one
  16-byte descriptor per table (type, empty candidate page, first page, last page);
- every other page has a 0x28-byte header and its rows from 0x28 on; the row index sits at the
  END of the page, in groups of 16 (offsets, then a "present" bitmask) growing backwards;
- a 3-byte field at +0x18 packs the number of row slots (low 13 bits) and the number of rows
  still present (high 11 bits). Reading only the byte at +0x18 silently loses rows on any page
  with more than 255 of them.

Nothing here needs third-party packages.
"""
import struct
from dataclasses import dataclass, field

HEADER = 0x28           # page header size; rows start here
GROUP = 0x24            # one row group in the footer: 16 offsets + present flags + other flags

TABLES = {0: "tracks", 1: "genres", 2: "artists", 3: "albums", 4: "labels", 5: "keys", 6: "colors",
          7: "playlist_tree", 8: "playlist_entries", 9: "unknown_9", 10: "unknown_10",
          11: "history_playlists", 12: "history_entries", 13: "artwork", 14: "unknown_14",
          15: "unknown_15", 16: "columns", 17: "menu", 18: "unknown_18", 19: "history"}

# track row: fixed fields, then 21 u16 string offsets (relative to the row) from +0x5E
TRACK_STRINGS = {"isrc": 0, "lyricist": 1, "message": 5, "kuvo_public": 6, "autoload_hotcues": 7,
                 "date_added": 10, "release_date": 11, "mix_name": 12, "analyze_path": 14,
                 "analyze_date": 15, "comment": 16, "title": 17, "filename": 19, "file_path": 20}


@dataclass
class Table:
    type: int
    empty_candidate: int
    first: int
    last: int

    @property
    def name(self):
        return TABLES.get(self.type, f"table_{self.type}")


@dataclass
class Page:
    index: int
    pos: int                # byte offset of the page in the file
    type: int
    next: int
    slots: int              # row slots used (13 bits)
    present: int            # rows present, as the header says (11 bits)
    flags: int
    free: int
    used: int

    @property
    def is_data(self):
        return not self.flags & 0x40


@dataclass
class Row:
    table: int
    page: int
    slot: int
    pos: int                # absolute byte offset of the row


@dataclass
class Track:
    id: int
    row: Row
    file_size: int
    sample_rate: int
    bitrate: int
    tempo: int              # BPM x 100
    duration: int           # seconds
    year: int
    artist_id: int
    album_id: int
    strings: dict = field(default_factory=dict)
    string_errors: list = field(default_factory=list)

    @property
    def title(self):
        return self.strings.get("title", "")

    @property
    def path(self):
        return self.strings.get("file_path", "")


@dataclass
class MenuItem:
    row: Row
    category: int           # column id (see the columns table: 14 = TIME, 15 = BITRATE, ...)
    label: int
    flags: int
    sort: int               # position in the players' root menu; 0 = hidden

    @property
    def visible(self):
        return self.sort > 0


def read_string(data, pos, limit=None):
    """A DeviceSQL string at pos -> (text, error or None). Three encodings:
    short ASCII (odd first byte: (length + 1) << 1 | 1 ... total length = byte >> 1),
    long ASCII (0x40, u16 total length, pad, bytes) and long UTF-16LE (0x90, same header)."""
    if pos >= len(data) or (limit is not None and pos >= limit):
        return "", "string offset outside the page"
    kind = data[pos]
    if kind & 1:
        end = pos + (kind >> 1)
        if limit is not None and end > limit:
            return "", "short string runs past the page"
        return data[pos + 1:end].decode("ascii", "replace"), None
    if kind in (0x40, 0x90):
        if pos + 4 > len(data):
            return "", "long string header cut off"
        length = struct.unpack_from("<H", data, pos + 1)[0]
        end = pos + length
        if length < 4 or (limit is not None and end > limit):
            return "", f"long string length {length} runs past the page"
        raw = data[pos + 4:end]
        if kind == 0x40:
            return raw.decode("ascii", "replace").split("\x00")[0], None
        if len(raw) % 2:
            return raw[:-1].decode("utf-16-le", "replace"), "odd UTF-16 length"
        return raw.decode("utf-16-le", "replace").split("\x00")[0], None
    return "", f"unknown string kind 0x{kind:02x}"


class Pdb:
    def __init__(self, data, path=None, scan=False, second_word=False):
        self.data = bytearray(data)
        self.path = path
        self.scan = scan                # read rows by page type (recovery), not by chain
        # also count rows whose present bit is only in the second footer word: rows rekordbox
        # deleted, or - on some older exports - live rows (see docs/format.md); recovery only
        self.second_word = second_word
        d = self.data
        if len(d) < 0x1C:
            raise ValueError("too short to be an export.pdb")
        self.page_size, self.num_tables, self.next_unused, _unknown, self.sequence = \
            struct.unpack_from("<5I", d, 4)
        if self.page_size not in (512, 1024, 2048, 4096, 8192, 16384):
            raise ValueError(f"page size {self.page_size} isn't a DeviceSQL page size")
        self.tables = {}
        for t in range(self.num_tables):
            typ, empty, first, last = struct.unpack_from("<4I", d, 0x1C + 16 * t)
            self.tables[typ] = Table(typ, empty, first, last)
        self._touched = set()
        self._legacy = {}

    @classmethod
    def open(cls, path, scan=False, second_word=False):
        with open(path, "rb") as fh:
            return cls(fh.read(), path, scan, second_word)

    # ------------------------------------------------------------ pages and rows
    @property
    def num_pages(self):
        return len(self.data) // self.page_size

    def page(self, index):
        pos = index * self.page_size
        d = self.data
        _, idx, typ, nxt = struct.unpack_from("<4I", d, pos)
        packed = int.from_bytes(d[pos + 0x18:pos + 0x1B], "little")
        free, used = struct.unpack_from("<2H", d, pos + 0x1C)
        return Page(index, pos, typ, nxt, packed & 0x1FFF, packed >> 13, d[pos + 0x1B], free, used)

    def chain(self, typ):
        """The pages of a table in chain order, stopping (not raising) at a loop or a page
        outside the file; check.walk() reports those."""
        table = self.tables[typ]
        index, seen = table.first, set()
        while 0 < index < self.num_pages and index not in seen:
            seen.add(index)
            page = self.page(index)
            yield page
            if index == table.last:
                return
            index = page.next

    def _group_base(self, page, slot):
        return page.pos + self.page_size - (slot // 16) * GROUP

    def legacy(self, typ):
        """Which footer word holds a table's present bits. Current exports use the word just
        after the offsets (group end - 4); some older ones leave it 0 and use the last word
        (group end - 2) instead, for whole tables. Decided per table by majority, so a group of
        rows deleted in a current file (first word 0, stale second word) stays deleted."""
        if typ not in self._legacy:
            standard = variant = 0
            pages = self.scan_pages(typ) if self.scan else self.chain(typ) if typ in self.tables else ()
            for page in pages:
                if not page.is_data:
                    continue
                for g in range((page.slots + 15) // 16):
                    base = page.pos + self.page_size - g * GROUP
                    first, second = struct.unpack_from("<2H", self.data, base - 4)
                    if first:
                        standard += 1
                    elif second:
                        variant += 1
            self._legacy[typ] = variant > standard
        return self._legacy[typ]

    def present_flags(self, page, slot):
        base = self._group_base(page, slot)
        first, second = struct.unpack_from("<2H", self.data, base - 4)
        if first or not (self.second_word or self.legacy(page.type)):
            return first
        return second

    def row_offset(self, page, slot):
        return struct.unpack_from("<H", self.data, self._group_base(page, slot) - 6 - 2 * (slot % 16))[0]

    def is_present(self, page, slot):
        return self.present_flags(page, slot) >> (slot % 16) & 1

    def rows(self, typ):
        if self.scan:
            return self.scan_rows(typ)
        out = []
        if typ not in self.tables:
            return out
        for page in self.chain(typ):
            if not page.is_data:
                continue
            for slot in range(page.slots):
                if self.is_present(page, slot):
                    out.append(Row(typ, page.index, slot, page.pos + HEADER + self.row_offset(page, slot)))
        return out

    def scan_pages(self, typ):
        """Recovery: every data page whose own header says it belongs to `typ`, in file order,
        whether or not the table's chain reaches it."""
        for index in range(1, self.num_pages):
            pos = index * self.page_size
            if struct.unpack_from("<I", self.data, pos + 8)[0] != typ:
                continue
            page = self.page(index)
            if page.index == index and page.is_data:
                yield page

    def scan_rows(self, typ):
        """Recovery reader: present rows on every page of `typ` (see scan_pages). Use it when the
        header or the chains are damaged; on a healthy file it can also return rows from pages
        rekordbox has let go of."""
        out = []
        for page in self.scan_pages(typ):
            for slot in range(page.slots):
                if self.is_present(page, slot):
                    out.append(Row(typ, page.index, slot, page.pos + HEADER + self.row_offset(page, slot)))
        return out

    def u16(self, pos):
        return struct.unpack_from("<H", self.data, pos)[0]

    def u32(self, pos):
        return struct.unpack_from("<I", self.data, pos)[0]

    def string_at(self, row, rel):
        page_end = (row.page + 1) * self.page_size
        return read_string(self.data, row.pos + rel, page_end)

    # ------------------------------------------------------------ typed tables
    def tracks(self):
        out = {}
        for row in self.rows(0):
            p = row.pos
            t = Track(id=self.u32(p + 0x48), row=row, file_size=self.u32(p + 0x10), sample_rate=self.u32(p + 0x08),
                      bitrate=self.u32(p + 0x30), tempo=self.u32(p + 0x38), duration=self.u16(p + 0x54),
                      year=self.u16(p + 0x50), artist_id=self.u32(p + 0x44), album_id=self.u32(p + 0x40))
            for name, i in TRACK_STRINGS.items():
                text, err = self.string_at(row, self.u16(p + 0x5E + 2 * i))
                t.strings[name] = text
                if err:
                    t.string_errors.append(f"{name}: {err}")
            out.setdefault(t.id, []).append(t)
        return out

    def id_name_table(self, typ, id_at=0, name_at=None):
        """Simple id + name tables (genres, labels, keys, history playlists...)."""
        out = {}
        for row in self.rows(typ):
            if name_at is None:
                continue
            out[self.u32(row.pos + id_at)] = self.string_at(row, name_at)[0]
        return out

    def artists(self):
        out = {}
        for row in self.rows(2):
            subtype = self.u16(row.pos)
            near = self.data[row.pos + 9]
            rel = self.u16(row.pos + 10) if subtype == 0x64 else near
            out[self.u32(row.pos + 4)] = self.string_at(row, rel)[0]
        return out

    def playlist_tree(self):
        """{id: (parent, sort, is_folder, name)}; rows can appear twice - the last one wins."""
        out = {}
        for row in self.rows(7):
            parent, _, sort, pid, folder = struct.unpack_from("<5I", self.data, row.pos)
            out[pid] = (parent, sort, bool(folder), self.string_at(row, 20)[0])
        return out

    def playlist_entries(self):
        """[(playlist id, entry index, track id, row)]"""
        out = []
        for r in self.rows(8):
            idx, tid, pid = struct.unpack_from("<3I", self.data, r.pos)
            out.append((pid, idx, tid, r))
        return out

    def history_playlists(self):
        return {self.u32(r.pos): self.string_at(r, 4)[0] for r in self.rows(11)}

    def history_entries(self):
        """[(history playlist id, entry index, track id, row)]"""
        out = []
        for r in self.rows(12):
            tid, pid, idx = struct.unpack_from("<3I", self.data, r.pos)
            out.append((pid, idx, tid, r))
        return out

    def columns(self):
        """{column id: name} - the category names players show (GENRE, ARTIST, ... BITRATE)."""
        out = {}
        for r in self.rows(16):
            cid = self.u16(r.pos)
            text, _ = self.string_at(r, 4)
            out[cid] = text.strip("￺￻").strip()
        return out

    def menu(self):
        """The players' root browse menu: one MenuItem per category, in table order."""
        out = []
        for r in self.rows(17):
            cat, label, flags, sort = struct.unpack_from("<4H", self.data, r.pos)
            out.append(MenuItem(r, cat, label, flags, sort))
        return out

    # ------------------------------------------------------------ editing
    def drop_row(self, row):
        """Delete a row the way rekordbox does: clear its present bit; fix_counts() then updates
        the page's packed present-row count. Nothing is moved or zeroed."""
        page = self.page(row.page)
        if not self.is_present(page, row.slot):
            raise ValueError(f"row {row.slot} on page {row.page} is already deleted")
        bit = 1 << (row.slot % 16)
        base = self._group_base(page, row.slot)
        words = (base - 4, base - 2) if self.legacy(page.type) else (base - 4,)
        for at in words:
            struct.pack_into("<H", self.data, at, struct.unpack_from("<H", self.data, at)[0] & ~bit)
        self._touched.add(row.page)

    def fix_counts(self):
        for index in sorted(self._touched):
            page = self.page(index)
            present = sum(self.is_present(page, s) for s in range(page.slots))
            packed = page.slots | present << 13
            self.data[page.pos + 0x18:page.pos + 0x1B] = packed.to_bytes(3, "little")
        self._touched.clear()

    def set_u16(self, pos, value):
        struct.pack_into("<H", self.data, pos, value)

    def bump_sequence(self):
        """rekordbox raises the header's sequence number on every write; do the same."""
        self.sequence += 1
        struct.pack_into("<I", self.data, 0x14, self.sequence)

    def save(self, path):
        import os
        tmp = str(path) + ".rekfix-tmp"
        with open(tmp, "wb") as fh:
            fh.write(self.data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
