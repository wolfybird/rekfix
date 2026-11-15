"""Read rekordbox's per-track analysis files: PIONEER/USBANLZ/.../ANLZ0000.DAT and .EXT.

Big-endian, unlike export.pdb. A file starts with "PMAI" and its header length; then tagged
sections follow, each starting with a fourcc, a u32 header length and a u32 section length.
What's read here:
- PPTH: the audio file's path on the stick (UTF-16BE). It ties the analysis to its track even
  when export.pdb is damaged or gone.
- PQTZ (in the .DAT): the beat grid, one 8-byte entry per beat: beat in the bar (u16), tempo x 100
  (u16), time in ms (u32). The beat count is at +20 of the section.
- PCOB (.DAT) and PCO2 (.EXT): two cue lists each, list type 0 = memory cues, 1 = hot cues.
  - PCOB entries are PCPT, 56 bytes.
  - PCO2 entries are PCP2. rekordbox writes them 88 bytes or longer, with a name (UTF-16BE) and
    a colour; a deck writes 44 bytes, with neither.

  In an entry, the hot cue number is a u32 at +12 (0 = memory cue, 1-8 = A-H).

Field offsets were checked against thousands of real files written by rekordbox 5 and 6 and by
XDJ-1000MK2 decks (docs/format.md). Damaged files are read as far as they make sense; if the cue
lists can't be walked, the entries are found by their signatures instead.
"""
import os
import struct
from dataclasses import dataclass, field

NO_LOOP = (0, 0xFFFFFFFF)       # rekordbox writes 0xFFFFFFFF as "no loop end", a deck writes 0
MAX_MS = 24 * 3600 * 1000       # anything later than this is garbage


@dataclass
class Cue:
    hot: int                    # 0 = memory cue; 1-8 = hot cue A-H (up to 16 on newer gear)
    start_ms: int
    end_ms: int = None          # set for loops
    name: str = ""
    rgb: tuple = None           # hot cue colour, when the entry carries one
    source: str = ""            # "EXT" or "DAT"

    @property
    def is_loop(self):
        return self.end_ms is not None


@dataclass
class Analysis:
    path: str = ""                                  # the audio file, as the stick knows it
    beats: list = field(default_factory=list)       # (beat in bar, tempo x 100, ms)
    cues: list = field(default_factory=list)
    problems: list = field(default_factory=list)

    @property
    def bpm(self):
        """The median tempo of the beat grid, or 0."""
        if not self.beats:
            return 0.0
        tempos = sorted(t for _, t, _ in self.beats)
        return tempos[len(tempos) // 2] / 100


def sections(data, problems=None):
    """(position, fourcc, header length, section length) for each section, stopping at the first
    one that doesn't make sense (and noting it in problems)."""
    if len(data) < 12 or data[:4] != b"PMAI":
        if problems is not None:
            problems.append("not an analysis file (no PMAI header)")
        return
    pos = struct.unpack_from(">I", data, 4)[0]
    while pos + 12 <= len(data):
        fourcc = bytes(data[pos:pos + 4])
        lh, lt = struct.unpack_from(">II", data, pos + 4)
        if lt < 12 or lh > lt or pos + lt > len(data) or not fourcc.isalnum():
            if problems is not None:
                problems.append(f"section at {pos} doesn't make sense; read stopped there")
            return
        yield pos, fourcc, lh, lt
        pos += lt


def read_path(data):
    """The PPTH path. Works on just the start of a file too: sections before it are skipped by
    their length without being read."""
    if len(data) < 12 or data[:4] != b"PMAI":
        return ""
    pos = struct.unpack_from(">I", data, 4)[0]
    while pos + 16 <= len(data):
        lh, lt = struct.unpack_from(">II", data, pos + 4)
        if lt < 12:
            return ""
        if data[pos:pos + 4] == b"PPTH":
            n = struct.unpack_from(">I", data, pos + 12)[0]
            return data[pos + lh:pos + lh + n].decode("utf-16-be", "replace").rstrip("\x00")
        pos += lt
    return ""


def read_beats(data, problems=None):
    for pos, fourcc, lh, lt in sections(data, problems):
        if fourcc == b"PQTZ" and lh >= 24:
            n = struct.unpack_from(">I", data, pos + 20)[0]
            beats, p = [], pos + lh
            for _ in range(min(n, (lt - lh) // 8)):
                beat, tempo, ms = struct.unpack_from(">HHI", data, p)
                if tempo and ms <= MAX_MS:
                    beats.append((beat, tempo, ms))
                p += 8
            return beats
    return []


def _entry(data, e, fourcc, source):
    """One PCPT/PCP2 entry -> Cue, or None if it isn't a usable cue."""
    le = struct.unpack_from(">I", data, e + 8)[0]
    hot = struct.unpack_from(">I", data, e + 12)[0]
    if fourcc == b"PCPT":
        kind = data[e + 28]
        start, end = struct.unpack_from(">II", data, e + 32)
        name, rgb = "", None
    else:
        kind = data[e + 16]
        start, end = struct.unpack_from(">II", data, e + 20)
        name, rgb = "", None
        if le > 44:
            n = struct.unpack_from(">I", data, e + 40)[0]
            if 0 < n <= le - 44:
                name = data[e + 44:e + 44 + n].decode("utf-16-be", "replace").rstrip("\x00")
            after = e + 44 + n
            if 0 <= n and after + 4 <= e + le:
                code, r, g, b = data[after:after + 4]
                if code or r or g or b:
                    rgb = (r, g, b)
    if hot > 16 or start > MAX_MS:
        return None
    loop = kind == 2 and end not in NO_LOOP and start < end <= MAX_MS
    return Cue(hot, start, end if loop else None, "".join(c for c in name if c.isprintable()), rgb, source)


def read_cues(data, source, problems=None):
    """The cues in one file, by walking its cue lists. Falls back to finding the entries by
    their signature when the lists can't be walked (a damaged file)."""
    out, ok = [], True
    for pos, fourcc, lh, lt in sections(data, problems):
        if fourcc not in (b"PCOB", b"PCO2"):
            continue
        count = struct.unpack_from(">H", data, pos + (18 if fourcc == b"PCOB" else 16))[0]
        e, end = pos + lh, pos + lt
        for _ in range(count):
            if e + 44 > end or data[e:e + 4] not in (b"PCPT", b"PCP2"):
                ok = False
                break
            le = struct.unpack_from(">I", data, e + 8)[0]
            if le < 44 or e + le > end:
                ok = False
                break
            cue = _entry(data, e, data[e:e + 4], source)
            if cue:
                out.append(cue)
            e += le
    if ok and out:
        return out
    if not ok and problems is not None:
        problems.append(f"{source}: cue lists damaged; entries found by signature instead")
    found = []
    for sig in (b"PCP2", b"PCPT"):
        i = data.find(sig)
        while 0 <= i:
            if i + 44 <= len(data):
                le = struct.unpack_from(">I", data, i + 8)[0]
                if 44 <= le <= 0x400 and i + le <= len(data):
                    cue = _entry(data, i, sig, source)
                    if cue:
                        found.append(cue)
            i = data.find(sig, i + 4)
    return found or out


def merge_cues(ext, dat):
    """One list from both files: the .EXT's (names and colours) first, then any the .DAT has that
    the .EXT doesn't (same slot, within 2 ms)."""
    out = list(ext)
    for c in dat:
        if not any(o.hot == c.hot and abs(o.start_ms - c.start_ms) <= 2 and o.is_loop == c.is_loop for o in out):
            out.append(c)
    seen, unique = set(), []
    for c in out:
        key = (c.hot, c.start_ms, c.end_ms)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return sorted(unique, key=lambda c: (c.hot == 0, c.hot, c.start_ms))


def _load(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def read(dat_path):
    """Everything usable from a track's ANLZ0000.DAT and the .EXT next to it."""
    a = Analysis()
    dat = _load(dat_path)
    ext = _load(dat_path[:-4] + ".EXT") if dat_path.upper().endswith(".DAT") else None
    if dat is None and ext is None:
        a.problems.append("analysis files missing")
        return a
    if dat is not None:
        a.path = read_path(dat)
        a.beats = read_beats(dat, a.problems)
    if ext is not None and not a.path:
        a.path = read_path(ext)
    a.cues = merge_cues(read_cues(ext, "EXT", a.problems) if ext is not None else [],
                        read_cues(dat, "DAT", a.problems) if dat is not None else [])
    return a


def index_paths(usbanlz):
    """{audio path, lower case: .DAT path} for every analysis folder under USBANLZ, from the path
    each file names (PPTH). Only the start of each file is read."""
    out = {}
    for root, _dirs, files in os.walk(usbanlz):
        for f in files:
            if f.upper() != "ANLZ0000.DAT":
                continue
            p = os.path.join(root, f)
            try:
                with open(p, "rb") as fh:
                    head = fh.read(4096)
            except OSError:
                continue
            path = read_path(head)
            if path:
                out.setdefault(path.lower(), p)
    return out
