import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from rekfix import clone, snapshot
from rekfix.cli import main

from . import synth

FILES = {
    os.path.join("Contents", "Artist", "Album", "01 Track 1.mp3"): b"a" * 3000,
    os.path.join("Contents", "Artist", "Album", "02 Track 2.mp3"): b"b" * 5000,
    os.path.join("PIONEER", "USBANLZ", "P000", "00000001", "ANLZ0000.DAT"): b"PMAI" + b"\x00" * 60,
    os.path.join("PIONEER", "DEVSETTING.DAT"): b"settings",
    os.path.join("PIONEER", "rekordbox", "exportExt.pdb"): b"ext" * 100,
}


def write(path, data, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    if mtime:
        os.utime(path, (mtime, mtime))


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def make_stick(root):
    stamp = time.time() - 86400
    for rel, data in FILES.items():
        write(os.path.join(root, rel), data, stamp)
    write(os.path.join(root, "PIONEER", "rekordbox", "export.pdb"), synth.build(), stamp)
    write(os.path.join(root, "System Volume Information", "IndexerVolumeGuid"), b"x")


def same_tree(a, b):
    """{rel: bytes} of every file except system folders and the marker, for both."""
    def files(root):
        out = {}
        for d, dirs, fs in os.walk(root):
            dirs[:] = [x for x in dirs if x.lower() not in clone.SYSTEM]
            for f in fs:
                rel = os.path.relpath(os.path.join(d, f), root)
                if rel != clone.MARKER:
                    out[rel] = read(os.path.join(d, f))
        return out
    return files(a) == files(b), files(a).keys() ^ files(b).keys()


class Clone(unittest.TestCase):
    def test_fresh_clone_is_exact_and_databases_go_last(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            make_stick(src)
            p = clone.plan(src, dst)
            self.assertEqual(p.refusals, [])
            self.assertEqual(len(p.of("copy")), 6)                       # the system folder is left out
            order = []
            real = clone.copy_one
            with mock.patch.object(clone, "copy_one", side_effect=lambda s, d, m: (order.append(d), real(s, d, m))[1]):
                done, nbytes, failures = clone.run(p, progress=lambda *_: None)
            self.assertEqual((done, failures), (6, []))
            ok, diff = same_tree(src, dst)
            self.assertTrue(ok, diff)
            self.assertTrue(all("rekordbox" in o for o in order[-2:]))   # the databases are written last
            self.assertTrue(os.path.join("Contents", "") in order[0])
            src_t = os.path.getmtime(os.path.join(src, "PIONEER", "DEVSETTING.DAT"))
            self.assertAlmostEqual(os.path.getmtime(os.path.join(dst, "PIONEER", "DEVSETTING.DAT")), src_t, delta=2)
            with open(os.path.join(dst, clone.MARKER)) as fh:
                self.assertEqual(json.load(fh)["files"], 6)

    def test_refuses_a_target_with_another_library(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            make_stick(src)
            write(os.path.join(dst, "PIONEER", "rekordbox", "export.pdb"), b"someone else's")
            p = clone.plan(src, dst)
            self.assertTrue(any("already holds a rekordbox library" in r for r in p.refusals))
            with self.assertRaises(clone.CloneError):
                clone.run(p)

    def test_refresh_copies_only_what_changed(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            make_stick(src)
            clone.run(clone.plan(src, dst), progress=lambda *_: None)
            write(os.path.join(src, "PIONEER", "DEVSETTING.DAT"), b"changed settings")
            write(os.path.join(src, "Contents", "New", "new.mp3"), b"n" * 10)
            os.remove(os.path.join(src, "Contents", "Artist", "Album", "02 Track 2.mp3"))
            p = clone.plan(src, dst)
            self.assertTrue(p.update)
            self.assertEqual(sorted(os.path.basename(e.rel) for e in p.of("copy")), ["DEVSETTING.DAT", "new.mp3"])
            self.assertEqual([os.path.basename(x) for x in p.extra], ["02 Track 2.mp3"])
            clone.run(p, remove_extra=True, progress=lambda *_: None)
            ok, diff = same_tree(src, dst)
            self.assertTrue(ok, diff)

    def test_from_a_snapshot_and_a_library(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as snaps, \
                tempfile.TemporaryDirectory() as lib, tempfile.TemporaryDirectory() as dst:
            make_stick(src)
            with redirect_stdout(io.StringIO()):
                snap = snapshot.take(src, snaps, "snap", full=True)
            for rel, data in FILES.items():                               # the library, laid out its own way
                if rel.startswith("Contents"):
                    write(os.path.join(lib, "My Music", os.path.basename(rel)), data)
            p = clone.plan(snap, dst, audio_from=[lib])
            self.assertEqual(p.refusals, [])
            self.assertEqual(sorted(e.origin for e in p.of("copy") if e.rel.startswith("Contents")), ["library"] * 2)
            clone.run(p, progress=lambda *_: None)
            ok, diff = same_tree(src, dst)
            self.assertTrue(ok, diff)

    def test_missing_too_big_and_no_space_are_refused(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as snaps, \
                tempfile.TemporaryDirectory() as dst:
            make_stick(src)
            with redirect_stdout(io.StringIO()):
                snap = snapshot.take(src, snaps, "snap", full=True)       # no audio in a snapshot
            p = clone.plan(snap, dst)
            self.assertEqual(len(p.of("missing")), 2)
            self.assertTrue(any("can't be found" in r for r in p.refusals))
            self.assertFalse(clone.plan(snap, dst, allow_missing=True).refusals)
            with mock.patch.object(clone, "FAT32_LIMIT", 4000), \
                    mock.patch.object(clone, "filesystem", return_value="FAT32"):
                self.assertTrue(any("4 GB" in r for r in clone.plan(src, dst).refusals))
            with mock.patch.object(clone, "FAT32_LIMIT", 4000), \
                    mock.patch.object(clone, "filesystem", return_value="NTFS"):
                self.assertFalse(any("4 GB" in r for r in clone.plan(src, dst).refusals))
            with mock.patch.object(clone.shutil, "disk_usage", return_value=mock.Mock(free=10)):
                self.assertTrue(any("GB free" in r for r in clone.plan(src, dst).refusals))

    def test_a_bad_read_back_stops_the_clone(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            make_stick(src)
            with mock.patch.object(clone, "read_back", return_value="not the same"):
                with self.assertRaises(clone.CloneError) as e:
                    clone.run(clone.plan(src, dst), progress=lambda *_: None)
            self.assertIn("read back wrong twice", str(e.exception))
            self.assertFalse(os.path.exists(os.path.join(dst, clone.MARKER)))

    def test_verify_only_finds_a_changed_byte(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            make_stick(src)
            clone.run(clone.plan(src, dst), progress=lambda *_: None)
            self.assertEqual(clone.verify_only(clone.plan(src, dst)), [])
            path = os.path.join(dst, "Contents", "Artist", "Album", "01 Track 1.mp3")
            write(path, b"A" + read(path)[1:])
            self.assertEqual(clone.verify_only(clone.plan(src, dst)),
                             [f"different content: {os.path.join('Contents', 'Artist', 'Album', '01 Track 1.mp3')}"])

    def test_command_dry_run_and_apply(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            make_stick(src)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["clone", src, dst]), 0)
            self.assertIn("dry run", out.getvalue())
            self.assertFalse(os.listdir(dst))
            with redirect_stdout(out), mock.patch("rekfix.cli.rekordbox_running", return_value=False):
                self.assertEqual(main(["clone", src, dst, "--apply"]), 0)
            self.assertIn("read back", out.getvalue())
            self.assertTrue(same_tree(src, dst)[0])


if __name__ == "__main__":
    unittest.main()
