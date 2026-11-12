"""Build small synthetic export.pdb files for tests: real layout, made-up library."""
import struct

PS = 4096


def short(s):
    b = s.encode("ascii")
    return bytes([((len(b) + 1) << 1) | 1]) + b


def long_ascii(s):
    b = s.encode("ascii")
    return bytes([0x40]) + struct.pack("<H", len(b) + 4) + b"\x00" + b


def utf16(s):
    b = s.encode("utf-16-le")
    return bytes([0x90]) + struct.pack("<H", len(b) + 4) + b"\x00" + b


def track_row(tid, title, path, artist_id=1, size=1000, duration=200, title_enc=short):
    fixed = bytearray(0x88)
    struct.pack_into("<I", fixed, 0x10, size)
    struct.pack_into("<I", fixed, 0x44, artist_id)
    struct.pack_into("<I", fixed, 0x48, tid)
    struct.pack_into("<H", fixed, 0x54, duration)
    strings = {17: title_enc(title), 19: short(path.split("/")[-1]), 20: short(path),
               14: short(f"/PIONEER/USBANLZ/P000/{tid:08X}/ANLZ0000.DAT")}
    empty = short("")
    body = bytearray()
    for i in range(21):
        struct.pack_into("<H", fixed, 0x5E + 2 * i, 0x88 + len(body))
        body += strings.get(i, empty)
    return bytes(fixed + body)


def tree_row(pid, name, parent=0, sort=0, folder=0):
    return struct.pack("<5I", parent, 0, sort, pid, folder) + short(name)


def entry_row(pid, idx, tid):
    return struct.pack("<3I", idx, tid, pid)


def hist_list_row(hid, name):
    return struct.pack("<I", hid) + short(name)


def hist_entry_row(hid, idx, tid):
    return struct.pack("<3I", tid, hid, idx)


def column_row(cid, name):
    return struct.pack("<2H", cid, 0x80 + cid) + utf16("￺" + name + "￻")


def menu_row(cat, label, flags, sort):
    return struct.pack("<4H", cat, label, flags, sort)


def artist_row(aid, name):
    return struct.pack("<HHIBB", 0x60, 0, aid, 3, 10) + short(name)


COLUMNS = {1: "GENRE", 2: "ARTIST", 3: "ALBUM", 4: "TRACK", 5: "BPM", 7: "YEAR", 11: "KEY", 14: "TIME",
           15: "BITRATE", 16: "FILE NAME", 17: "PLAYLIST", 18: "HOT CUE BANK", 19: "HISTORY"}


def page(index, typ, rows, nxt=0, present=None, second_word=False):
    """One data page holding `rows`; present: set of slots that are present (default all)."""
    p = bytearray(PS)
    present = set(range(len(rows))) if present is None else present
    data = bytearray()
    offsets = []
    for r in rows:
        offsets.append(len(data))
        data += r
        data += b"\x00" * (-len(data) % 4)
    n = len(rows)
    groups = (n + 15) // 16
    footer = (groups - 1) * 0x24 + ((n - (groups - 1) * 16) + 2) * 2 if n else 0
    used = len(data)
    free = PS - 0x28 - used - footer
    assert free >= 0, "rows don't fit one page"
    struct.pack_into("<4I", p, 0, 0, index, typ, nxt)
    struct.pack_into("<I", p, 0x10, 1)
    p[0x18:0x1B] = (n | len(present) << 13).to_bytes(3, "little")
    p[0x1B] = 0x24
    struct.pack_into("<2H", p, 0x1C, free, used)
    p[0x28:0x28 + used] = data
    for g in range(groups):
        base = PS - g * 0x24
        bits = sum(1 << (s % 16) for s in present if s // 16 == g)
        struct.pack_into("<H", p, base - 4, 0 if second_word else bits)
        struct.pack_into("<H", p, base - 2, bits if second_word else 0)
        for s in range(g * 16, min(n, g * 16 + 16)):
            struct.pack_into("<H", p, base - 6 - 2 * (s % 16), offsets[s])
    return bytes(p)


def build(tracks=3, entries_per_list=None, heavy_menu=True, extra_pages=None):
    """A complete little export.pdb as bytes. Tables 0-19 each get one data page (tracks get two,
    chained), so chains, rows and cross-references are all exercised."""
    entries_per_list = entries_per_list or {1: [1, 2], 2: [3]}
    rows = {t: [] for t in range(20)}
    rows[0] = [track_row(i, f"Track {i}", f"/Contents/Artist/Album/{i:02d} Track {i}.mp3") for i in range(1, tracks + 1)]
    rows[2] = [artist_row(1, "Artist")]
    rows[7] = [tree_row(1, "Warmup", sort=0), tree_row(2, "Peak", sort=1)]
    rows[8] = [entry_row(pid, k, tid) for pid, tids in entries_per_list.items() for k, tid in enumerate(tids, 1)]
    rows[11] = [hist_list_row(1, "HISTORY 001")]
    rows[12] = [hist_entry_row(1, 1, 1), hist_entry_row(1, 2, 2)]
    rows[16] = [column_row(c, n) for c, n in COLUMNS.items()]
    shown = [2, 3, 4, 11, 17, 19, 5, 7] + ([14, 18, 16, 15] if heavy_menu else [])
    rows[17] = [menu_row(c, c, 99, shown.index(c) + 1 if c in shown else 0) for c in COLUMNS]
    pages = [None]
    table_pages = {}
    for typ in range(20):
        r = rows[typ]
        if typ == 0 and len(r) > 1:
            half = len(r) // 2
            first = len(pages)
            pages.append(page(first, typ, r[:half], nxt=first + 1))
            pages.append(page(first + 1, typ, r[half:]))
            table_pages[typ] = (first, first + 1)
        else:
            idx = len(pages)
            pages.append(page(idx, typ, r))
            table_pages[typ] = (idx, idx)
    for extra in extra_pages or []:
        pages.append(extra(len(pages)))
    header = bytearray(PS)
    struct.pack_into("<5I", header, 4, PS, 20, len(pages) + 1, 1, 100)
    for typ in range(20):
        first, last = table_pages[typ]
        struct.pack_into("<4I", header, 0x1C + 16 * typ, typ, len(pages), first, last)
    pages[0] = bytes(header)
    return b"".join(pages)
