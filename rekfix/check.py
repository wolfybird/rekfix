"""Read-only health checks for an export.pdb, and optionally the stick it came from.

Every finding has a level:
- error: something a player may choke on, or data that is plainly inconsistent;
- warn:  worth a look (e.g. a track whose audio file isn't on the stick);
- info:  facts that help a diagnosis (sizes, counts).
"""
import os
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass

from .pdb import GROUP, HEADER, LOOKUPS, TABLES, Pdb

# the root categories a player builds by scanning every track when you land on them; on a large
# library (~13-14k tracks on an XDJ-1000MK2) they can freeze the deck - see docs/playbook.md
HEAVY_CATEGORIES = {14: "TIME", 15: "BITRATE", 16: "FILE NAME", 18: "HOT CUE BANK"}
LARGE_LIBRARY = 10_000


@dataclass
class Finding:
    level: str
    code: str
    message: str

    def __str__(self):
        return f"{self.level.upper():5} {self.code:24} {self.message}"


def walk(pdb):
    """Follow every table's page chain the long way, reporting what a reader would trip over.
    Returns ({table type: (pages, rows present)}, findings)."""
    out, found = {}, []
    if len(pdb.data) % pdb.page_size:
        found.append(Finding("warn", "file.size", f"{len(pdb.data)} bytes isn't a whole number of "
                                                  f"{pdb.page_size}-byte pages"))
    highest = max((t.last for t in pdb.tables.values()), default=0)
    if pdb.next_unused < pdb.num_pages // 2 or pdb.next_unused <= highest:
        found.append(Finding("error", "header.stale", f"the header says the next free page is {pdb.next_unused}, "
                                                       f"but the file has {pdb.num_pages} pages (tables end as far "
                                                       f"as page {highest}): the header doesn't describe this file"))
    for typ, table in pdb.tables.items():
        name = table.name
        index, seen, pages, rows, variants = table.first, [], 0, 0, []
        while True:
            if not 0 < index < pdb.num_pages:
                found.append(Finding("error", "chain.outside", f"{name}: next page {index} is outside the file "
                                                                f"({pdb.num_pages} pages)"))
                break
            if index in seen:
                found.append(Finding("error", "chain.loop", f"{name}: page chain loops back to page {index}"))
                break
            seen.append(index)
            page = pdb.page(index)
            pages += 1
            if page.index != index:
                found.append(Finding("error", "page.index", f"{name}: page {index} says it is page {page.index}"))
            if page.type != typ:
                found.append(Finding("error", "page.type", f"{name}: page {index} belongs to table {page.type} "
                                                            f"({TABLES.get(page.type, '?')})"))
            if page.is_data:
                rows += _check_page(pdb, page, name, found, variants)
            if index == table.last:
                break
            index = page.next
            if index == 0:
                found.append(Finding("error", "chain.short", f"{name}: chain ends before its last page {table.last}"))
                break
        # data the chain doesn't reach: past the recorded last page, or on pages no chain visits
        unreached = [(pg.index, sum(pdb.is_present(pg, s) for s in range(pg.slots)))
                     for pg in pdb.scan_pages(typ) if pg.index not in seen]
        unreached = [(i, n) for i, n in unreached if n]
        if unreached:
            n_rows = sum(n for _, n in unreached)
            level = "error" if n_rows > rows else "warn"
            found.append(Finding(level, "pages.unreached", f"{name}: {len(unreached)} pages hold {n_rows:,} rows the "
                                                            f"chain doesn't reach (it reaches {rows:,}); "
                                                            f"`rekfix check --scan` reads them"))
        if pdb.legacy(typ):
            found.append(Finding("info", "format.legacy-flags", f"{name}: present bits are in the older-export "
                                                                 f"footer word (see docs/format.md)"))
        if variants:
            found.append(Finding("info", "page.flags-word", f"{name}: {len(variants)} row groups have no present bits "
                                                             f"but a non-zero second flags word (pages "
                                                             f"{', '.join(map(str, variants[:5]))}...); deleted rows, "
                                                             f"or the older-export word order - see docs/format.md"))
        out[typ] = (pages, rows)
    return out, found


def footer_slots(pdb, page):
    """Row slots implied by the page's free and used sizes: what's left is the footer, 18 u16s
    per full group of 16 and (rows + 2) u16s for the last one."""
    footer = pdb.page_size - HEADER - page.used - page.free
    full, rem = divmod(footer // 2, 18)
    return full * 16 + (rem - 2 if rem >= 3 else 0)


def _check_page(pdb, page, name, found, variants):
    space = pdb.page_size - HEADER
    groups = (page.slots + 15) // 16
    if page.free + page.used + groups * GROUP > space + GROUP:
        found.append(Finding("error", "page.space", f"{name}: page {page.index} free {page.free} + used "
                                                     f"{page.used} + index doesn't fit the page"))
    implied = footer_slots(pdb, page)
    if page.slots and implied != page.slots:
        found.append(Finding("warn", "page.footer", f"{name}: page {page.index} header says {page.slots} row slots, "
                                                     f"free/used sizes imply {implied}"))
    present = 0
    for slot in range(page.slots):
        if not pdb.is_present(page, slot):
            continue
        present += 1
        off = pdb.row_offset(page, slot)
        if off >= page.used:
            found.append(Finding("error", "row.offset", f"{name}: page {page.index} row {slot} starts at {off}, "
                                                         f"outside the page's {page.used} used bytes"))
    if present != page.present:
        found.append(Finding("warn", "page.count", f"{name}: page {page.index} header says {page.present} rows "
                                                    f"present, the bitmasks say {present}"))
    # an older-export variant: present bits in the other footer word (see docs/format.md)
    if pdb.legacy(page.type):
        return present
    for g in range(groups):
        base = page.pos + pdb.page_size - g * GROUP
        first, second = struct.unpack_from("<2H", pdb.data, base - 4)
        if first == 0 and second:
            variants.append(page.index)
    return present


def content(pdb):
    """Cross-table consistency. Returns (tracks by id, findings)."""
    found = []
    by_id = pdb.tracks()
    dups = {tid: ts for tid, ts in by_id.items() if len(ts) > 1}
    if dups:
        sample = ", ".join(f"{tid} x{len(ts)}" for tid, ts in list(dups.items())[:5])
        found.append(Finding("error", "tracks.duplicate-id", f"{len(dups)} track ids appear more than once ({sample})"))
    bad_strings = [t for ts in by_id.values() for t in ts if t.string_errors]
    if bad_strings:
        t = bad_strings[0]
        found.append(Finding("error", "tracks.strings", f"{len(bad_strings)} track rows have unreadable text, "
                                                         f"e.g. id {t.id}: {t.string_errors[0]}"))
    tids = set(by_id)
    tree = pdb.playlist_tree()
    for pid, (parent, sort, folder, name) in tree.items():
        if parent and parent not in tree:
            found.append(Finding("error", "playlists.parent", f"playlist {name!r} ({pid}) has a missing parent {parent}"))
    siblings = defaultdict(list)
    for pid, (parent, sort, folder, name) in tree.items():
        siblings[parent].append(sort)
    for parent, sorts in siblings.items():
        dup = [s for s, c in Counter(sorts).items() if c > 1]
        if dup:
            found.append(Finding("warn", "playlists.sort", f"{len(dup)} duplicate sort positions under parent {parent}"))
    entries = pdb.playlist_entries()
    orphans = [(p, t) for p, i, t, r in entries if t not in tids or p not in tree]
    if orphans:
        found.append(Finding("error", "playlists.orphans", f"{len(orphans)} playlist entries point at a missing "
                                                            f"track or playlist"))
    per_list = defaultdict(list)
    for p, i, t, r in entries:
        per_list[p].append(i)
    gaps = [p for p, idx in per_list.items() if sorted(idx) != list(range(1, len(idx) + 1))]
    if gaps:
        found.append(Finding("warn", "playlists.index", f"{len(gaps)} playlists' entry numbers aren't 1..n"))
    hists = pdb.history_playlists()
    hent = pdb.history_entries()
    horph = [t for p, i, t, r in hent if t not in tids or p not in hists]
    if horph:
        found.append(Finding("error", "history.orphans", f"{len(horph)} history entries point at a missing track "
                                                          f"or history list"))
    found.append(Finding("info", "summary", f"{len(tids):,} tracks, {sum(1 for v in tree.values() if not v[2]):,} "
                                            f"playlists, {len(entries):,} playlist entries, {len(hists)} play "
                                            f"histories ({len(hent):,} plays)"))
    found += leftovers(pdb, [ts[0] for ts in by_id.values()])
    menu = pdb.menu()
    names = pdb.columns()
    shown = [m for m in sorted(menu, key=lambda m: m.sort) if m.visible]
    heavy = [names.get(m.category, HEAVY_CATEGORIES[m.category]) for m in shown if m.category in HEAVY_CATEGORIES]
    if heavy and len(tids) >= LARGE_LIBRARY:
        found.append(Finding("warn", "menu.heavy", f"{len(tids):,} tracks and the root menu shows {', '.join(heavy)}: "
                                                   f"on older players (e.g. XDJ-1000MK2) these can freeze the deck. "
                                                   f"See `rekfix menu --hide`"))
    return by_id, found


def unused(pdb, tracks):
    """Rows of the lookup tables (genres, artists, albums, labels, artwork) that none of `tracks`
    uses, directly or through an album: {table type: [rows]}. Exports rekordbox writes have none.
    It's been seen deleting a removed track's artwork row along with the track; whether it also
    deletes an artist (album, genre, label) whose last track goes hasn't been observed."""
    used = {typ: set() for typ in LOOKUPS}
    for t in tracks:
        for typ, (_, fields) in LOOKUPS.items():
            used[typ].update(getattr(t, f) for f in fields)
    for album, artists in pdb.albums().items():
        if album in used[3]:
            used[2].update(artists)
    out = {}
    for typ in LOOKUPS:
        if typ in pdb.tables:
            rows = [r for i, r in pdb.lookup_rows(typ) if i not in used[typ]]
            if rows:
                out[typ] = rows
    return out


def leftovers(pdb, tracks):
    """What removing tracks without rekordbox leaves behind: a stale song count, artwork (and
    artists...) no track uses, pages missing the has-deleted-rows flag."""
    found = []
    row, count = pdb.song_count()
    if row is not None and count != len(tracks):
        found.append(Finding("warn", "tracks.count", f"players show {count:,} songs on their USB info panel, but "
                                                     f"{len(tracks):,} tracks are present (left over from tracks "
                                                     f"removed without updating it); `rekfix prune` corrects it"))
    left = unused(pdb, tracks)
    artwork = left.pop(13, [])
    if artwork:
        found.append(Finding("info", "artwork.unused", f"{len(artwork):,} artwork entries no track uses (left over "
                                                       f"from removed tracks); `rekfix prune` removes them, as "
                                                       f"rekordbox does when it removes a track"))
    if left:
        what = ", ".join(f"{len(rows):,} {TABLES[typ]}" for typ, rows in left.items())
        found.append(Finding("info", "rows.unused", f"{what} that no track uses. rekordbox's exports have none, so "
                                                    f"they're likely left over from tracks removed by another tool. "
                                                    f"Left alone for now: see docs/format.md"))
    flags = pdb.unflagged_pages()
    if flags:
        found.append(Finding("info", "page.deleted-flag", f"{len(flags):,} pages have deleted rows without the flag "
                                                           f"rekordbox sets on such pages (an edit by another tool); "
                                                           f"`rekfix prune` sets it"))
    return found


def files(pdb, root, by_id=None):
    """Tracks whose audio or analysis file isn't on the stick at `root`."""
    found = []
    by_id = by_id if by_id is not None else pdb.tracks()
    missing_audio, missing_anlz = [], []
    for ts in by_id.values():
        t = ts[0]
        if t.path and not os.path.exists(on_stick(root, t.path)):
            missing_audio.append(t)
        anlz = t.strings.get("analyze_path")
        if anlz and not os.path.exists(on_stick(root, anlz)):
            missing_anlz.append(t)
    if missing_audio:
        found.append(Finding("warn", "files.audio", f"{len(missing_audio):,} tracks' audio files aren't on the stick "
                                                    f"(listed but won't load), e.g. {missing_audio[0].path}. "
                                                    f"`rekfix prune` removes them or copies them back"))
    if missing_anlz:
        found.append(Finding("warn", "files.analysis", f"{len(missing_anlz):,} tracks' analysis files aren't on the "
                                                        f"stick, e.g. {missing_anlz[0].strings['analyze_path']}"))
    return found


def on_stick(root, path):
    """A path from the database ("/Contents/...") as a file on the stick mounted at root."""
    return os.path.join(root, *[p for p in path.replace("\\", "/").split("/") if p])


def run(pdb_path, root=None):
    pdb = Pdb.open(pdb_path)
    counts, found = walk(pdb)
    by_id, more = content(pdb)
    found += more
    if root:
        found += files(pdb, root, by_id)
    return pdb, counts, found
