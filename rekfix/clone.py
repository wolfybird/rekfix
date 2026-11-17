"""rekfix clone: copy a stick exactly onto another stick, without rekordbox, and read it back.

A rekordbox export to a second stick would lose what exists only on the first: the decks' play
histories, cues set on the decks, hand repairs. A clone copies every file instead.

**Sources:**
- the stick itself;
- or a full snapshot of it (`rekfix snapshot --full`, whose manifest lists every file) plus your
  music library (`--audio-from`), so a failing stick is barely read at all;
- audio is taken from the library whenever the same file (name and size) is there, also when
  cloning from the stick.

**How it copies:**
- audio first, then the analysis files and settings, and the databases last. A copy cut short
  holds no database pointing at files that aren't there;
- each file under a temporary name, then renamed, with its date kept;
- each file read back from the target, bypassing the system cache, and checksummed against what
  was written. A clean chkdsk doesn't vouch for a stick (the July case), so a mismatch is retried
  once, then stops the clone;
- the source is walked through rekfix's own listings (short names on Windows), and a source with
  names Windows can't keep apart is refused on Windows (see names.py);
- a marker file (rekfix-clone.json) at the target's root is written last. Its presence means the
  clone completed, and it lets a later run refresh the clone by copying only what changed.

**The target** must be a stick with no rekordbox library on it (format it as FAT32 first), or an
earlier clone. Players like the XDJ-1000MK2 read FAT32 only, which caps files at 4 GB.
"""
import csv
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass, field

from . import __version__, names
from .meter import Meter
from .readtest import read_uncached

SYSTEM = {"system volume information", "$recycle.bin", ".spotlight-v100", ".fseventsd", ".trashes",
          ".temporaryitems"}
DATABASES = os.path.join("pioneer", "rekordbox")        # copied last
FAT32_LIMIT = 4 * 1024 ** 3 - 1
MARKER = "rekfix-clone.json"
CLUSTER = 32 * 1024                                     # for the space estimate (FAT32 on big sticks)


@dataclass
class Entry:
    rel: str                    # path on the stick
    size: int
    mtime: float
    src: str = None             # where the bytes come from
    origin: str = ""            # "source" or "library"
    action: str = "copy"        # copy / same / missing / too big


@dataclass
class Plan:
    source: str
    target: str
    entries: list = field(default_factory=list)
    extra: list = field(default_factory=list)       # files on the target that the source doesn't have
    update: bool = False                            # the target is an earlier clone
    refusals: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    fs: str = ""
    free: int = 0
    needed: int = 0

    def of(self, action):
        return [e for e in self.entries if e.action == action]


# ------------------------------------------------------------ what's on the source
def _top(rel):
    return rel.replace("\\", "/").split("/", 1)[0].lower()


def _walk(root, skip):
    """(path on the stick, size, date, path to read it at) for every file; folders are listed by
    rekfix and entered through short names on Windows (see names.py)."""
    for rel, e in names.walk(root, skip=SYSTEM | set(skip)):
        if rel == MARKER or rel.endswith(".rekfix-tmp"):
            continue
        yield rel, e.size, e.mtime, e.path


def is_snapshot(path):
    return os.path.exists(os.path.join(path, "manifest.csv")) and os.path.exists(os.path.join(path, "checksums.md5"))


def inventory(source, skip=()):
    """[Entry] for every file on the stick: from a snapshot's manifest, or by walking it."""
    skip = {s.lower() for s in skip}
    out = []
    if is_snapshot(source):
        with open(os.path.join(source, "manifest.csv"), newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rel = row["path"].replace("\\", os.sep).replace("/", os.sep)
                if _top(rel) in SYSTEM or _top(rel) in skip:
                    continue
                size = int(row["size"]) if row["size"] else -1
                try:
                    mtime = dt.datetime.fromisoformat(row["modified"]).timestamp()
                except ValueError:
                    mtime = 0.0
                copy = os.path.join(source, rel)
                have = os.path.exists(copy) and os.path.getsize(copy) == size
                out.append(Entry(rel, size, mtime, copy if have else None, "source" if have else ""))
    else:
        for rel, size, mtime, p in _walk(source, skip):
            out.append(Entry(rel, size, mtime, p if size >= 0 else None, "source" if size >= 0 else ""))
    return out


class Library:
    """Audio in your music library, findable by the stick's path or by name and size."""

    def __init__(self, roots):
        self.by_rel, self.by_name = {}, defaultdict(list)
        for root in roots:
            for rel, size, mtime, p in _walk(root, set()):
                self.by_rel.setdefault(rel.replace("\\", "/").lower(), (p, size))
                self.by_name[os.path.basename(rel).lower()].append((p, size, mtime))   # p may use short names

    def find(self, rel, size, mtime):
        key = rel.replace("\\", "/").lower()
        for k in (key, key.split("/", 1)[1] if "/" in key else key):
            hit = self.by_rel.get(k)
            if hit and hit[1] == size:
                return hit[0]
        same = [c for c in self.by_name.get(os.path.basename(rel).lower(), ()) if c[1] == size]
        if same:
            return min(same, key=lambda c: (abs(c[2] - mtime), c[0]))[0]
        return None


# ------------------------------------------------------------ the target
def filesystem(path):
    """The volume's file system name ("FAT32", "exFAT", "NTFS"...), or "" if unknown."""
    if sys.platform != "win32":
        return ""
    import ctypes
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    name = ctypes.create_unicode_buffer(64)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, None, 0, None, None, None, name, 64)
    return name.value if ok else ""


def _marker(target):
    try:
        with open(os.path.join(target, MARKER), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _same(dst, e):
    try:
        st = os.stat(dst)
    except OSError:
        return False
    # FAT keeps times to 2 s, and a daylight-saving change can shift them by an hour
    return st.st_size == e.size and min(abs(st.st_mtime - e.mtime + d) for d in (0, 3600, -3600)) <= 2


def plan(source, target, audio_from=(), skip=(), allow_missing=False):
    """Work out the clone. Reads the source, the library and the target; writes nothing."""
    source, target = os.path.abspath(source), os.path.abspath(target)
    p = Plan(source, target)
    if not os.path.isdir(source):
        p.refusals.append(f"no source at {source}")
        return p
    if not os.path.isdir(target):
        p.refusals.append(f"no target at {target}")
        return p
    a, b = os.path.normcase(source) + os.sep, os.path.normcase(target) + os.sep
    if a.startswith(b) or b.startswith(a):
        p.refusals.append("the source and the target overlap")
        return p
    if not os.path.isdir(os.path.join(source, "PIONEER")):
        p.refusals.append(f"{source} has no PIONEER folder: not a rekordbox stick or a snapshot of one")
    marker = _marker(target)
    p.update = marker is not None
    if not p.update and os.path.isdir(os.path.join(target, "PIONEER")):
        p.refusals.append("the target already holds a rekordbox library. A clone needs an empty stick: copy off "
                          "anything you want from it, then format it as FAT32")
    if p.update and os.path.normcase(marker.get("source", "")) != os.path.normcase(source):
        p.notes.append(f"the target is a clone of {marker.get('source')}; refreshing it from {source}")
    p.fs = filesystem(target)
    if p.fs and p.fs.upper() not in ("FAT32", "FAT"):
        p.notes.append(f"the target is {p.fs}: an XDJ-1000MK2 or CDJ-2000NXS2 reads FAT32 only")

    p.entries = inventory(source, skip)
    twins = names.lookalikes_in(e.rel for e in p.entries)
    if twins and names.WINDOWS:
        p.refusals.append(f"the source has names Windows can't keep apart on a FAT stick: {names.describe(twins)}. "
                          f"Copying them from Windows can put one's files into the other, or overwrite them. Clone "
                          f"this stick from a Mac or Linux instead")
    library = Library(audio_from) if audio_from else None
    for e in p.entries:
        if library and _top(e.rel) == "contents" and e.size >= 0:
            hit = library.find(e.rel, e.size, e.mtime)
            if hit:
                e.src, e.origin = hit, "library"
        if e.src is None:
            e.action = "missing"
        elif e.size > FAT32_LIMIT and (p.fs.upper() in ("FAT32", "FAT") or not p.fs):
            e.action = "too big"
        elif p.update and _same(os.path.join(target, e.rel), e):
            e.action = "same"
    if p.update:
        wanted = {os.path.normcase(e.rel) for e in p.entries}
        p.extra = [rel for rel, *_ in _walk(target, set()) if os.path.normcase(rel) not in wanted]

    p.free = shutil.disk_usage(target).free
    replaced = sum(os.path.getsize(os.path.join(target, e.rel)) for e in p.of("copy")
                   if os.path.exists(os.path.join(target, e.rel)))
    p.needed = sum(-(-e.size // CLUSTER) * CLUSTER for e in p.of("copy")) - replaced
    if p.needed > p.free:
        big = sorted(p.of("copy"), key=lambda e: -e.size)[:3]
        p.refusals.append(f"the target has {p.free / 1e9:.1f} GB free and the copy needs about {p.needed / 1e9:.1f} GB "
                          f"(largest: {', '.join(f'{e.rel} {e.size / 1e9:.1f} GB' for e in big)}); --skip a folder "
                          f"or use a bigger stick")
    if p.of("too big"):
        p.refusals.append(f"{len(p.of('too big'))} files are over FAT32's 4 GB limit, e.g. {p.of('too big')[0].rel}; "
                          f"--skip their folder")
    if p.of("missing") and not allow_missing:
        p.refusals.append(f"{len(p.of('missing'))} files can't be found (not in the snapshot, the library or readable "
                          f"on the stick), e.g. {p.of('missing')[0].rel}; --audio-from another folder, or "
                          f"--allow-missing to clone without them")
    return p


def describe(p):
    gb = lambda es: sum(e.size for e in es) / 1e9
    copy = p.of("copy")
    lib = [e for e in copy if e.origin == "library"]
    out = [f"{p.source} -> {p.target}" + (f" ({p.fs})" if p.fs else "") + (", refreshing an earlier clone" if p.update else ""),
           f"  {len(p.entries):,} files, {gb(p.entries):.1f} GB on the source",
           f"  to copy: {len(copy):,} files, {gb(copy):.1f} GB ({len(lib):,} of them, {gb(lib):.1f} GB, from the library)"]
    if p.of("same"):
        out.append(f"  already on the target, unchanged: {len(p.of('same')):,} files")
    if p.of("missing"):
        out.append(f"  not found anywhere: {len(p.of('missing')):,} files")
    if p.extra:
        out.append(f"  on the target but not the source: {len(p.extra):,} files (--remove-extra deletes them)")
    out.append(f"  space: about {p.needed / 1e9:.1f} GB needed, {p.free / 1e9:.1f} GB free")
    out += [f"  note: {n}" for n in p.notes]
    out += [f"  REFUSED: {r}" for r in p.refusals]
    return out


# ------------------------------------------------------------ copying
def _order(e):
    rel = e.rel.lower()
    return (rel.startswith(DATABASES), _top(rel) == "pioneer", rel)


def copy_one(src, dst, mtime):
    """Copy through a temporary file, synced and renamed; returns the md5 of what was written."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".rekfix-tmp"
    h = hashlib.md5()
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        for block in iter(lambda: fi.read(1 << 20), b""):
            h.update(block)
            fo.write(block)
        fo.flush()
        os.fsync(fo.fileno())
    os.replace(tmp, dst)
    os.utime(dst, (mtime, mtime))
    return h.hexdigest()


def read_back(path):
    h = hashlib.md5()
    for block in read_uncached(path):
        h.update(block)
    return h.hexdigest()


class CloneError(Exception):
    pass


def run(p, verify=True, remove_extra=False, progress=print):
    """Do the planned clone. Returns (files copied, bytes, [failures])."""
    if p.refusals:
        raise CloneError("; ".join(p.refusals))
    marker = os.path.join(p.target, MARKER)
    if os.path.exists(marker):
        os.remove(marker)                       # a refresh in progress isn't a finished clone
    if remove_extra:
        for rel in p.extra:
            os.remove(os.path.join(p.target, rel))
    todo = sorted(p.of("copy"), key=_order)
    meter = Meter(sum(e.size for e in todo), len(todo), progress)
    done = done_bytes = 0
    failures = []
    for e in todo:
        dst = os.path.join(p.target, e.rel)
        try:
            for attempt in (1, 2):
                digest = copy_one(e.src, dst, e.mtime)
                if not verify or read_back(dst) == digest:
                    break
                if attempt == 2:
                    raise CloneError(f"{e.rel} read back wrong twice: the target stick isn't storing data reliably. "
                                     f"Stopped; don't use it")
        except OSError as err:
            failures.append(f"{e.rel}: {err}")
            continue
        done += 1
        done_bytes += e.size
        meter.add(e.size)
    if failures:
        progress(f"  {len(failures)} files couldn't be copied; the clone is incomplete and has no marker")
        return done, done_bytes, failures
    with open(marker, "w", encoding="utf-8") as fh:
        json.dump({"source": p.source, "made": dt.datetime.now().isoformat(timespec="seconds"), "rekfix": __version__,
                   "files": len(p.entries), "bytes": sum(e.size for e in p.entries if e.size > 0),
                   "verified": verify}, fh, indent=1)
    return done, done_bytes, failures


def verify_only(p, progress=print):
    """Compare an existing copy with its source, file by file and byte by byte (uncached).
    Returns [problems]."""
    problems = []
    todo = [e for e in sorted(p.entries, key=_order) if e.src is not None]
    meter = Meter(sum(max(e.size, 0) for e in todo), len(todo), progress)
    for e in todo:
        dst = os.path.join(p.target, e.rel)
        if not os.path.exists(dst):
            problems.append(f"missing on the target: {e.rel}")
        elif os.path.getsize(dst) != e.size:
            problems.append(f"different size: {e.rel}")
        else:
            h = hashlib.md5()
            with open(e.src, "rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
            if read_back(dst) != h.hexdigest():
                problems.append(f"different content: {e.rel}")
        meter.add(max(e.size, 0))
    return problems
