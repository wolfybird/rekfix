"""Copy a stick's databases (and optionally its whole PIONEER folder) somewhere safe before
anything else happens to it, with a list of every file on the stick. Read-only on the stick."""
import csv
import datetime as dt
import hashlib
import os
import shutil

DB_FILES = ("export.pdb", "exportExt.pdb", "exportLibrary.db", "exportLibrary.db-wal", "exportLibrary.db-shm",
            "RBFLTR.DAT")
TOP_FILES = ("DEVSETTING.DAT", "djprofile.nxs")


def md5(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def take(root, dest_parent, label=None, full=False, progress=print):
    """root: the stick (e.g. "E:\\"); dest_parent: where the snapshot folder goes. Returns its path."""
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
    if full:
        progress("copying the whole PIONEER folder (analysis files, artwork) - this can take a while")
        shutil.copytree(pioneer, os.path.join(dest, "PIONEER"), dirs_exist_ok=True, copy_function=shutil.copy2)
    with open(os.path.join(dest, "checksums.md5"), "w", encoding="utf-8") as fh:
        for rel in copied:
            src, dst = os.path.join(root, rel), os.path.join(dest, rel)
            a, b = md5(src), md5(dst)
            if a != b:
                raise SystemExit(f"copy of {rel} doesn't match the stick ({a} vs {b})")
            fh.write(f"{a} *{rel}\n")
    n = size = 0
    with open(os.path.join(dest, "manifest.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "size", "modified"])
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "System Volume Information"]
            for f in filenames:
                p = os.path.join(dirpath, f)
                try:
                    st = os.stat(p)
                except OSError as e:
                    w.writerow([os.path.relpath(p, root), "", f"unreadable: {e}"])
                    continue
                w.writerow([os.path.relpath(p, root), st.st_size,
                            dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")])
                n += 1
                size += st.st_size
    progress(f"snapshot: {len(copied)} database files (checksums verified), list of {n:,} files "
             f"({size / 1e9:.1f} GB) -> {dest}")
    return dest
