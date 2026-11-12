"""rekfix - diagnose (and carefully repair) rekordbox USB exports.

  rekfix check E:\\                 health of the stick's export.pdb (+ its files)
  rekfix check export.pdb --scan    recovery read: rows by page, ignoring a damaged header/chains
  rekfix diff OLD.pdb NEW.pdb       what changed between two copies
  rekfix menu E:\\                  the players' root menu
  rekfix menu E:\\ --hide time,bitrate,"file name","hot cue bank" --apply
  rekfix history E:\\               play histories the decks saved
  rekfix snapshot E:\\ D:\\backups   copy the databases + list every file, before anything else
  rekfix readtest E:\\              how fast and evenly the stick reads

Commands that write (menu --hide/--show --apply) save a copy of the database first, refuse while
rekordbox is running, and only edit fixed-size fields in place.
"""
import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict

from . import __version__, check, menu, readtest, snapshot
from .pdb import TABLES, Pdb


def pdb_path(target):
    if os.path.isdir(target):
        p = os.path.join(target, "PIONEER", "rekordbox", "export.pdb")
        if not os.path.exists(p):
            raise SystemExit(f"no PIONEER\\rekordbox\\export.pdb under {target}")
        return p, target
    if not os.path.exists(target):
        raise SystemExit(f"not found: {target}")
    return target, None


def rekordbox_running():
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq rekordbox.exe", "/NH"], capture_output=True,
                               text=True, errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return "rekordbox.exe" in r.stdout.lower()
        return subprocess.run(["pgrep", "-x", "rekordbox"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except FileNotFoundError:
        return False


def backup(path, dest=None):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(dest, f"export.pdb.{stamp}") if dest else f"{path}.rekfix-{stamp}.bak"
    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
    shutil.copy2(path, target)
    return target


# ------------------------------------------------------------ commands
def cmd_check(a):
    path, root = pdb_path(a.target)
    pdb = Pdb.open(path, scan=a.scan, second_word=a.second_word)
    counts, found = check.walk(pdb)
    by_id, more = check.content(pdb)
    found += more
    if root and not a.no_files:
        found += check.files(pdb, root, by_id)
    print(f"{path}: {pdb.num_pages:,} pages of {pdb.page_size}, sequence {pdb.sequence}"
          + (" (recovery read)" if a.scan else ""))
    for level in ("error", "warn", "info"):
        for f in found:
            if f.level == level and (a.verbose or f.level != "info" or f.code in ("summary", "format.legacy-flags")):
                print(" ", f)
    if a.tables:
        for typ, (pages, rows) in sorted(counts.items()):
            print(f"    {typ:2} {TABLES.get(typ, '?'):18} {pages:6} pages {rows:8,} rows")
    errors = sum(f.level == "error" for f in found)
    print(f"{errors} error(s), {sum(f.level == 'warn' for f in found)} warning(s)")
    return 1 if errors else 0


def cmd_diff(a):
    old, new = Pdb.open(pdb_path(a.old)[0]), Pdb.open(pdb_path(a.new)[0])
    print(f"sequence {old.sequence} -> {new.sequence}; pages {old.num_pages:,} -> {new.num_pages:,}")
    for typ in sorted(set(old.tables) | set(new.tables)):
        ro, rn = len(old.rows(typ)), len(new.rows(typ))
        if ro != rn:
            print(f"  {TABLES.get(typ, typ):18} rows {ro:,} -> {rn:,}")
    changed = Counter()
    for i in range(1, min(old.num_pages, new.num_pages)):
        a_, b_ = old.page_size * i, old.page_size * (i + 1)
        if old.data[a_:b_] != new.data[a_:b_]:
            changed[TABLES.get(new.page(i).type, new.page(i).type)] += 1
    if changed:
        print("  pages changed by table:", dict(changed.most_common()))
    to, tn = old.tracks(), new.tracks()
    print(f"  tracks: +{len(set(tn) - set(to)):,} new, -{len(set(to) - set(tn)):,} gone")
    po, pn = old.playlist_tree(), new.playlist_tree()
    for pid in sorted(set(po) | set(pn)):
        if pid not in po:
            print(f"  + playlist {pn[pid][3]!r}")
        elif pid not in pn:
            print(f"  - playlist {po[pid][3]!r}")
        elif po[pid][3] != pn[pid][3] or po[pid][0] != pn[pid][0]:
            print(f"  ~ playlist {po[pid][3]!r} -> {pn[pid][3]!r} (parent {po[pid][0]} -> {pn[pid][0]})")
    mo = [n for s, c, n, m in menu.items(old) if s]
    mn = [n for s, c, n, m in menu.items(new) if s]
    if mo != mn:
        print("  root menu:", ", ".join(mo), "\n         ->", ", ".join(mn))
    ho, hn = old.history_playlists(), new.history_playlists()
    if ho != hn:
        print(f"  play histories: {len(ho)} -> {len(hn)}")
    return 0


def cmd_menu(a):
    path, root = pdb_path(a.target)
    pdb = Pdb.open(path)
    change = a.hide or a.show
    if change:
        wanted = [w for part in change for w in part.split(",") if w.strip()]
        ids = menu.resolve(pdb, wanted)
        done = menu.hide(pdb, ids) if a.hide else menu.show(pdb, ids)
        verb = "hidden" if a.hide else "shown"
        if not done:
            print(f"nothing to change - already {verb}")
            return 0
        print(f"{verb}: {', '.join(done)}")
    for pos, cat, name, m in menu.items(pdb):
        print(f"  {pos if pos else '-':>3}  {name}")
    if not change:
        return 0
    if not a.apply:
        print("dry run: add --apply to write it (a copy of export.pdb is saved first)")
        return 0
    if rekordbox_running():
        raise SystemExit("rekordbox is running - close it first (it may rewrite the stick)")
    saved = backup(path, a.backup_dir)
    pdb.bump_sequence()
    pdb.save(path)
    print(f"written. The original is saved as {saved}")
    return 0


def cmd_history(a):
    pdb = Pdb.open(pdb_path(a.target)[0], scan=a.scan, second_word=a.second_word)
    lists = pdb.history_playlists()
    entries = defaultdict(list)
    for pid, idx, tid, r in pdb.history_entries():
        entries[pid].append((idx, tid))
    tracks = pdb.tracks()
    artists = pdb.artists()
    for pid, name in sorted(lists.items()):
        print(f"  {name:20} {len(entries[pid]):4} plays")
        if a.tracks:
            for idx, tid in sorted(entries[pid]):
                t = tracks.get(tid, [None])[0]
                who = artists.get(t.artist_id, "") if t else ""
                print(f"      {idx:3}. {who + ' - ' if who else ''}{t.title if t else f'(missing track {tid})'}")
    orphans = [pid for pid in entries if pid not in lists]
    if orphans:
        print(f"  ({sum(len(entries[p]) for p in orphans)} plays belong to history lists that are missing)")
    return 0


def cmd_snapshot(a):
    snapshot.take(a.stick, a.dest, a.label, a.full)
    return 0


def cmd_readtest(a):
    return 0 if readtest.run(a.stick, a.music, a.analysis) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rekfix", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"rekfix {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="health check of export.pdb (a stick or a file)")
    p.add_argument("target", help="the stick (e.g. E:\\) or an export.pdb file")
    p.add_argument("--scan", action="store_true", help="recovery read: rows by page type, ignoring header and chains")
    p.add_argument("--second-word", action="store_true", help="also count rows present only in the second footer "
                                                              "word (deleted rows, or older exports' live rows)")
    p.add_argument("--no-files", action="store_true", help="skip checking audio/analysis files on the stick")
    p.add_argument("--tables", action="store_true", help="list pages and rows per table")
    p.add_argument("-v", "--verbose", action="store_true", help="show every finding")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("diff", help="what changed between two export.pdb copies")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser("menu", help="the players' root menu; --hide / --show categories")
    p.add_argument("target")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--hide", action="append", help="categories to hide (comma-separated names)")
    g.add_argument("--show", action="append", help="categories to bring back")
    p.add_argument("--apply", action="store_true", help="write the change (otherwise a dry run)")
    p.add_argument("--backup-dir", help="where the copy of the original goes (default: next to it)")
    p.set_defaults(fn=cmd_menu)

    p = sub.add_parser("history", help="play histories the decks saved")
    p.add_argument("target")
    p.add_argument("--tracks", action="store_true", help="list each history's tracks")
    p.add_argument("--scan", action="store_true", help="recovery read (see check --scan)")
    p.add_argument("--second-word", action="store_true", help="see check --second-word")
    p.set_defaults(fn=cmd_history)

    p = sub.add_parser("snapshot", help="copy the stick's databases + list every file, read-only on the stick")
    p.add_argument("stick")
    p.add_argument("dest", help="folder to put the snapshot in")
    p.add_argument("--label", help="snapshot folder name (default: date and time)")
    p.add_argument("--full", action="store_true", help="copy the whole PIONEER folder (analysis files too)")
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("readtest", help="read speed and slow spots of the stick")
    p.add_argument("stick")
    p.add_argument("--music", type=int, default=60, help="music files to sample")
    p.add_argument("--analysis", type=int, default=1500, help="analysis files to sample")
    p.set_defaults(fn=cmd_readtest)

    for stream in (sys.stdout, sys.stderr):     # track names in any script, on any console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    a = ap.parse_args(argv)
    return a.fn(a)
