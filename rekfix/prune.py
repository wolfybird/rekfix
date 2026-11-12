"""rekfix prune: remove tracks whose audio is gone from the stick, the way rekordbox removes
tracks, and tidy up what earlier removals by other tools left behind.

When rekordbox removes a track from a stick, it (as observed on real exports, see
docs/format.md):
- deletes the track's row: clears its present bit, lowers the page's present count and flags
  the page as having deleted rows. Nothing is moved or zeroed;
- deletes the track's artwork row the same way;
- keeps every playlist numbered 1..n;
- adjusts the song count players show on their USB info panel;
- stamps every page it changed with the write's new sequence number.

prune does the same, in one write, and deletes the track's playlist entries. It keeps tracks a
deck's play history lists, because removing them would break that history. Their audio can be
copied back instead (restore_from).

Artists, albums, genres and labels no track uses any more are left alone. rekordbox's exports
have none, but whether rekordbox deletes them itself, and what it does when it next exports a
track by an artist whose row is gone, hasn't been observed (docs/roadmap.md).

Nothing is written until the edited database has been re-read from scratch and checked:
- the result says what the plan meant;
- no byte changed outside the fields the edit is allowed to touch.
"""
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field

from . import check
from .pdb import GROUP, Pdb

TOO_MANY = 0.5      # more than this share of tracks without audio: probably the wrong folder


class PruneError(Exception):
    pass


@dataclass
class Plan:
    tracks: int = 0                                 # tracks in the database before
    missing: int = 0                                # tracks whose audio isn't on the stick
    remove: list = field(default_factory=list)      # Tracks to delete
    restore: list = field(default_factory=list)     # (Track, source file) to copy back instead
    kept: list = field(default_factory=list)        # no audio, but a play history lists them
    unreadable: list = field(default_factory=list)  # file path can't be read: left alone
    entries: dict = field(default_factory=dict)     # playlist id -> [entry rows to delete]
    broken_entries: int = 0                         # of those, entries that already pointed at no track
    renumber: list = field(default_factory=list)    # (entry row, new index)
    artwork: list = field(default_factory=list)     # artwork rows no remaining track uses
    count: tuple = None                             # (table 19 row, song count now, after)
    unflagged: list = field(default_factory=list)   # pages with deleted rows but not the flag

    @property
    def edits_database(self):
        return bool(self.remove or self.entries or self.renumber or self.artwork or self.count
                    or self.unflagged)


# ------------------------------------------------------------ finding audio to copy back
def _files(folder):
    """(path, size) of every file under folder; Windows gives the size without an extra call."""
    stack = [folder]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            yield e.path, e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue


class Finder:
    """Finds a missing track's audio in the folders you name. It looks at the same relative path
    first (a mirror or snapshot of the stick), then for a file of the same size whose name
    matches. rekordbox can shorten a file's name on the stick ("Title (Orig.mp3") and add "-1" when
    two would clash, so a library file whose name starts with the stick's name counts too. The
    size always has to match to the byte."""

    def __init__(self, folders):
        self.folders = list(folders)
        self._by_size = None

    def find(self, track):
        if not track.file_size or not track.path:
            return None
        parts = [p for p in track.path.replace("\\", "/").split("/") if p]
        for folder in self.folders:
            for rel in (parts, parts[1:]):          # a copy of the stick, or of its Contents folder
                cand = os.path.join(folder, *rel) if rel else None
                if cand and os.path.isfile(cand) and os.path.getsize(cand) == track.file_size:
                    return cand
        if self._by_size is None:
            self._by_size = defaultdict(list)
            for folder in self.folders:
                for path, size in _files(folder):
                    self._by_size[size].append(path)
        same_size = sorted(self._by_size.get(track.file_size, ()))
        name = parts[-1].lower()
        exact = [c for c in same_size if os.path.basename(c).lower() == name]
        if exact:
            return exact[0]
        stem, ext = os.path.splitext(name)
        stem = re.sub(r"-\d+$", "", stem)
        if len(stem) < 4:
            return None
        prefix = [c for c in same_size if os.path.splitext(c)[1].lower() == ext
                  and os.path.basename(c).lower().startswith(stem)]
        return prefix[0] if len(prefix) == 1 else None


# ------------------------------------------------------------ plan, edit, verify
def plan(pdb, root, restore_from=(), restore_all=False, force=False):
    """Work out what to change. Reads the stick (which audio files exist) but writes nothing."""
    _, found = check.walk(pdb)
    errors = [f for f in found if f.level == "error"]
    if errors:
        raise PruneError("the database has structural errors and prune only edits a healthy one ("
                         + ", ".join(sorted({f.code for f in errors})) + "); see `rekfix check`")
    by_id = pdb.tracks()
    dups = [tid for tid, ts in by_id.items() if len(ts) > 1]
    if dups:
        raise PruneError(f"{len(dups)} track ids are on more than one row, so which one is live is "
                         f"unclear; see `rekfix check`")
    tracks = {tid: ts[0] for tid, ts in by_id.items()}
    p = Plan(tracks=len(tracks))
    played = {tid for _, _, tid, _ in pdb.history_entries()}
    gone = []
    for t in tracks.values():
        if not t.path or any(e.startswith("file_path") for e in t.string_errors):
            p.unreadable.append(t)
        elif not os.path.exists(check.on_stick(root, t.path)):
            gone.append(t)
    p.missing = len(gone)
    if gone and len(gone) > TOO_MANY * len(tracks) and not force:
        raise PruneError(f"{len(gone):,} of {len(tracks):,} tracks have no audio under {root}. Is that the "
                         f"stick, with its Contents folder? (--force if it really is)")
    finder = Finder(restore_from) if restore_from else None
    for t in gone:
        src = finder.find(t) if finder and (restore_all or t.id in played) else None
        if src:
            p.restore.append((t, src))
        elif t.id in played:
            p.kept.append(t)
        else:
            p.remove.append(t)
    live = set(tracks) - {t.id for t in p.remove}

    tree = pdb.playlist_tree()
    lists = defaultdict(list)
    for pid, idx, tid, row in pdb.playlist_entries():
        lists[pid].append((idx, row.page, row.slot, tid, row))
    for pid, entries in lists.items():
        if pid not in tree:             # entries of a playlist that isn't there: no deck shows them
            continue
        drop = [e for e in entries if e[3] not in live]
        if not drop:
            continue
        p.entries[pid] = [e[4] for e in drop]
        p.broken_entries += sum(1 for e in drop if e[3] not in tracks)
        keep = sorted(e for e in entries if e[3] in live)
        p.renumber += [(e[4], k) for k, e in enumerate(keep, 1) if e[0] != k]

    p.artwork = check.unused(pdb, [t for t in tracks.values() if t.id in live]).get(13, [])
    row, count = pdb.song_count()
    if row is not None and count != len(live):
        p.count = (row, count, len(live))
    p.unflagged = pdb.unflagged_pages()
    return p


def apply(pdb, p):
    """Make the planned edit in memory, as one write. Returns the pages changed."""
    for t in p.remove:
        pdb.drop_row(t.row)
    for rows in p.entries.values():
        for r in rows:
            pdb.drop_row(r)
    for r, k in p.renumber:
        pdb.set_u32(r.pos, k)
    for r in p.artwork:
        pdb.drop_row(r)
    if p.count:
        pdb.set_u32(p.count[0].pos + 4, p.count[2])
    pdb.flag_pages(p.unflagged)
    return pdb.commit()


def _allowed(before, p, changed):
    """Byte offsets the edit may change; anything else changing means a bug."""
    ps, ok = before.page_size, set(range(0x14, 0x18))          # the header's sequence number
    for index in changed:
        pos = index * ps
        ok.update(range(pos + 0x10, pos + 0x14))                # the page's stamp
        ok.update(range(pos + 0x18, pos + 0x1C))                # packed counts, flags
    dropped = [t.row for t in p.remove] + [r for rows in p.entries.values() for r in rows] \
        + p.artwork
    for r in dropped:
        base = (r.page + 1) * ps - (r.slot // 16) * GROUP
        ok.update(range(base - 4, base))                        # the group's present-bit words
    for r, _ in p.renumber:
        ok.update(range(r.pos, r.pos + 4))
    if p.count:
        ok.update(range(p.count[0].pos + 4, p.count[0].pos + 8))
    return ok


def _changed_bytes(old, new, ps):
    for index in range(len(old) // ps):
        a, b = old[index * ps:(index + 1) * ps], new[index * ps:(index + 1) * ps]
        if a == b:
            continue
        for k in range(0, ps, 16):
            if a[k:k + 16] != b[k:k + 16]:
                yield from (index * ps + j for j in range(k, k + 16) if a[j] != b[j])


def verify(before, new_data, p, changed):
    """Re-read the edited bytes from scratch: problems found (an empty list means it's right)."""
    problems = []
    if len(new_data) != len(before.data):
        return ["the file changed size"]
    ok = _allowed(before, p, changed)
    stray = [i for i in _changed_bytes(before.data, new_data, before.page_size) if i not in ok]
    if stray:
        problems.append(f"{len(stray)} bytes changed outside the edited fields (first at 0x{stray[0]:x})")
    after = Pdb(bytes(new_data))
    _, found = check.walk(after)
    problems += [str(f) for f in found if f.level == "error" or f.code == "page.count"]
    old_tracks = before.tracks()
    new_tracks = after.tracks()
    want = set(old_tracks) - {t.id for t in p.remove}
    if set(new_tracks) != want:
        problems.append(f"expected {len(want):,} tracks, found {len(new_tracks):,}")
    tree = after.playlist_tree()

    def order(pdb):     # same tie-break as plan(): index, then where the row is
        lists = defaultdict(list)
        for pid, idx, tid, r in pdb.playlist_entries():
            lists[pid].append((idx, r.page, r.slot, tid))
        return {pid: sorted(v) for pid, v in lists.items()}
    old_lists, new_lists = order(before), order(after)
    for pid in tree:
        got = new_lists.get(pid, [])
        if pid in p.entries and [e[0] for e in got] != list(range(1, len(got) + 1)):
            problems.append(f"playlist {tree[pid][3]!r} isn't numbered 1..n")
        if [e[3] for e in got] != [e[3] for e in old_lists.get(pid, []) if e[3] in want]:
            problems.append(f"playlist {tree[pid][3]!r} lost the wrong entries or changed order")
    hist = lambda pdb: sorted((pid, idx, tid) for pid, idx, tid, _ in pdb.history_entries())
    if hist(before) != hist(after):
        problems.append("play histories changed")
    if 13 in check.unused(after, [ts[0] for ts in new_tracks.values()]):
        problems.append("artwork no track uses is still there")
    row, count = after.song_count()
    if row is not None and count != len(new_tracks):
        problems.append(f"song count {count:,} for {len(new_tracks):,} tracks")
    if after.unflagged_pages():
        problems.append("pages with deleted rows still lack the flag")
    if after.sequence != before.sequence + 1:
        problems.append("sequence number not raised by one")
    return problems


def restore_files(p, root):
    """Copy the planned tracks' audio back onto the stick, each checked for size. Returns them."""
    done = []
    for t, src in p.restore:
        dst = check.on_stick(root, t.path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".rekfix-tmp"
        shutil.copy2(src, tmp)
        if os.path.getsize(tmp) != t.file_size:
            os.remove(tmp)
            raise PruneError(f"the copy of {src} came out the wrong size; stopped (export.pdb not written)")
        os.replace(tmp, dst)
        done.append(dst)
    return done


# ------------------------------------------------------------ report
def describe(pdb, p, verbose=False):
    """The plan as lines of text."""
    artists = pdb.artists() if verbose else {}
    tree = pdb.playlist_tree()

    def name(t):
        who = artists.get(t.artist_id, "")
        return f"{who + ' - ' if who else ''}{t.title or '(no title)'}  [{t.path}]"

    out = [f"{p.tracks:,} tracks; audio missing for {p.missing:,}"]
    for label, items, why in (
            ("remove", p.remove, ""),
            ("copy back", p.restore, ""),
            ("keep", p.kept, "a deck's play history lists them; --restore-from copies their audio back")):
        if not items:
            continue
        sample = items[0][0] if label == "copy back" else items[0]
        out.append(f"  {label:10} {len(items):7,}   " + (why or f"e.g. {sample.path}"))
        if verbose:
            for it in items:
                out.append(f"      {name(it[0])}  <- {it[1]}" if label == "copy back" else f"      {name(it)}")
    if p.unreadable:
        out.append(f"  {len(p.unreadable):,} tracks' file paths can't be read; left alone")
    if p.entries:
        n = sum(len(v) for v in p.entries.values())
        lists = sorted(p.entries.items(), key=lambda kv: -len(kv[1]))
        shown = ", ".join(f"{tree[pid][3]} (-{len(rows)})" for pid, rows in lists[:5])
        more = f" and {len(lists) - 5} more" if len(lists) > 5 else ""
        out.append(f"playlist entries removed: {n:,} in {len(lists)} playlists: {shown}{more}; "
                   f"each renumbered 1..n")
        if p.broken_entries:
            out.append(f"  ({p.broken_entries:,} of them already pointed at no track)")
    if p.artwork:
        out.append(f"artwork entries no track uses: {len(p.artwork):,}; removed, as rekordbox does")
    if p.count:
        out.append(f"song count on the players' info panel: {p.count[1]:,} -> {p.count[2]:,}")
    if p.unflagged:
        out.append(f"pages with deleted rows but not rekordbox's flag for that: {len(p.unflagged):,}; flagged")
    return out
