"""Copy a stick's databases (and optionally its whole PIONEER folder and its audio) somewhere safe
before anything else happens to it, with a list of every file on the stick. Read-only on the
stick.

The stick is walked through rekfix's own folder listings, entering folders by their short names
on Windows, so names Windows can mix up ("Michał" and "Michal") can't make the copy or the list
miss files or take one folder's files twice (see names.py). Copy to the computer's own drive:
written to another FAT stick from Windows, such names could still collide."""
import csv
import datetime as dt
import hashlib
import os
import shutil

from . import names

DB_FILES = ("export.pdb", "exportExt.pdb", "exportLibrary.db", "exportLibrary.db-wal", "exportLibrary.db-shm",
            "RBFLTR.DAT")
TOP_FILES = ("DEVSETTING.DAT", "djprofile.nxs")
SKIP = ("System Volume Information",)


def md5(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _copy_folder(src, dst, progress):
    """Copy every file under src to dst, each checked for size. Returns (files, bytes)."""
    n = size = 0
    for rel, e in names.walk(src):
        to = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(to), exist_ok=True)
        shutil.copy2(e.path, to)
        if os.path.getsize(to) != e.size:
            raise SystemExit(f"the copy of {os.path.join(os.path.basename(src), rel)} came out the wrong size")
        n += 1
        size += e.size
        if n % 5000 == 0:
            progress(f"  {n:,} files, {size / 1e9:.1f} GB")
    return n, size


def take(root, dest_parent, label=None, full=False, audio=False, progress=print):
    """root: the stick (e.g. "E:\\"); dest_parent: where the snapshot folder goes. full: the whole
    PIONEER folder (analysis files, artwork); audio: the Contents folder too. Returns its path."""
    pioneer = os.path.join(root, "PIONEER")
    if not os.path.isdir(pioneer):
        raise SystemExit(f"{root} has no PIONEER folder - is it a rekordbox USB export?")
    name = label or dt.datetime.now().strftime("%Y-%m-%d %H%M%S")
    dest = os.path.join(dest_parent, name)
    if os.path.exists(dest):
        raise SystemExit(f"{dest} already exists")
    os.makedirs(os.path.join(dest, "PIONEER", "rekordbox"))
    copied = []
    for f in DB_FILES:
        src = os.path.join(pioneer, "rekordbox", f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, "PIONEER", "rekordbox", f))
            copied.append(os.path.join("PIONEER", "rekordbox", f))
    for f in TOP_FILES:
        src = os.path.join(pioneer, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, "PIONEER", f))
            copied.append(os.path.join("PIONEER", f))
    for top, wanted in (("PIONEER", full), ("Contents", audio)):
        if wanted and os.path.isdir(os.path.join(root, top)):
            progress(f"copying the {top} folder - this can take a while")
            n, size = _copy_folder(os.path.join(root, top), os.path.join(dest, top), progress)
            progress(f"  {top}: {n:,} files, {size / 1e9:.1f} GB")
    with open(os.path.join(dest, "checksums.md5"), "w", encoding="utf-8") as fh:
        for rel in copied:
            src, dst = os.path.join(root, rel), os.path.join(dest, rel)
            a, b = md5(src), md5(dst)
            if a != b:
                raise SystemExit(f"copy of {rel} doesn't match the stick ({a} vs {b})")
            fh.write(f"{a} *{rel}\n")
    n = size = 0
    groups = []
    with open(os.path.join(dest, "manifest.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "size", "modified"])
        for rel, e in names.walk(root, skip=SKIP, groups=groups):
            w.writerow([rel, e.size, dt.datetime.fromtimestamp(e.mtime).isoformat(timespec="seconds")])
            n += 1
            size += e.size
    progress(f"snapshot: {len(copied)} database files (checksums verified), list of {n:,} files "
             f"({size / 1e9:.1f} GB) -> {dest}")
    if groups:
        progress(f"note: the stick has names Windows can mix up: {names.describe(groups)}. This snapshot has "
                 f"them right, but Explorer and backup tools on Windows can open one for the other, so copy "
                 f"those folders with rekfix (--audio), not by hand")
    return dest
