import csv
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from rekfix import check, clone, names, readtest, snapshot
from rekfix.pdb import Pdb

from . import synth

# two artist folders a letter apart, like a real pair on a stick: Windows (code page 437) reads
# both as MICHAL NOWAK, and opened one for the other
TWINS = ("Michał Nowak", "Michal Nowak")

US_WINDOWS = not names.WINDOWS or __import__("ctypes").windll.kernel32.GetOEMCP() == 437


def write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def stick_with_twins(root):
    """A stick: the synthetic database, its three tracks' audio, and two look-alike folders."""
    write(os.path.join(root, "PIONEER", "rekordbox", "export.pdb"), synth.build())
    for i in (1, 2, 3):
        write(check.on_stick(root, synth.audio_path(i)), b"a" * (100 + i))
    write(os.path.join(root, "Contents", TWINS[0], "UnknownAlbum", "Dawn.mp3"), b"d" * 300)
    write(os.path.join(root, "Contents", TWINS[1], "UnknownAlbum", "Qi.mp3"), b"q" * 400)


class Keys(unittest.TestCase):
    @unittest.skipUnless(US_WINDOWS, "Windows' answer depends on its code page; these are for 437")
    def test_which_names_windows_mixes_up(self):
        keys = [names._key_437] + ([names.key] if names.WINDOWS else [])
        for key in keys:
            same = lambda a, b: key(a) is not None and key(a) == key(b)
            self.assertTrue(same(*TWINS))
            self.assertTrue(same("Røyksopp", "Royksopp"))
            self.assertTrue(same("Ártist", "Artist"))           # Á isn't in 437 (á is): upper case first
            self.assertFalse(same("Beyoncé", "Beyonce"))       # É is in 437
            self.assertFalse(same("Señor", "Senor"))
            self.assertIsNone(key("川口千里"))                    # no stand-in at all: compared by real name

    def test_groups(self):
        self.assertEqual(names.lookalikes(["a", TWINS[0], "b", TWINS[1]]), [sorted(TWINS)])
        self.assertEqual(names.lookalikes(["川口千里", "下村陽子"]), [])
        paths = [f"Contents/{TWINS[0]}/A/1.mp3", f"Contents/{TWINS[1]}/A/2.mp3", "Contents/X/3.mp3"]
        self.assertEqual(names.lookalikes_in(paths), [("Contents", sorted(TWINS))])


class Folders(unittest.TestCase):
    def test_walk_and_resolver(self):
        with tempfile.TemporaryDirectory() as root:
            stick_with_twins(root)
            groups = []
            files = {rel.replace("\\", "/"): e for rel, e in names.walk(root, groups=groups)}
            dawn = files[f"Contents/{TWINS[0]}/UnknownAlbum/Dawn.mp3"]
            with open(dawn.path, "rb") as fh:
                self.assertEqual(fh.read(), b"d" * 300)
            self.assertEqual(groups, [("Contents", sorted(TWINS))])
            stick = names.Resolver(root)
            self.assertEqual(stick.find(f"/Contents/{TWINS[0]}/UnknownAlbum/Dawn.mp3").size, 300)
            self.assertEqual(stick.find(f"/contents/{TWINS[1].upper()}/unknownalbum/qi.MP3").size, 400)
            self.assertIsNone(stick.find(f"/Contents/{TWINS[1]}/UnknownAlbum/Dawn.mp3"))
            self.assertIsNone(stick.find("/Contents/Artist/Album/01 Track 1.mp3/more"))
            self.assertEqual(stick.lookalikes(), [("Contents", sorted(TWINS))])

    def test_place(self):
        with tempfile.TemporaryDirectory() as root:
            stick_with_twins(root)
            stick = names.Resolver(root)
            dst = stick.place("/Contents/New Artist/New Album/new.mp3")
            write(dst, b"n")
            self.assertEqual(names.Resolver(root).find("/Contents/New Artist/New Album/new.mp3").size, 1)
            self.assertEqual(os.path.normcase(stick.place(f"/Contents/{TWINS[0]}/UnknownAlbum/Dawn.mp3")),
                             os.path.normcase(names.Resolver(root).find(f"/Contents/{TWINS[0]}/UnknownAlbum/Dawn.mp3").path))
            if names.WINDOWS and US_WINDOWS:                    # next to a look-alike, Windows could misplace it
                os.makedirs(os.path.join(root, "Contents", "Royksopp"))
                with self.assertRaises(ValueError):
                    names.Resolver(root).place("/Contents/Røyksopp/x.mp3")


class Commands(unittest.TestCase):
    def test_check_reports_lookalikes_and_finds_the_audio(self):
        with tempfile.TemporaryDirectory() as root:
            stick_with_twins(root)
            found = check.files(Pdb.open(os.path.join(root, "PIONEER", "rekordbox", "export.pdb")), root)
            codes = {f.code for f in found}
            self.assertIn("files.lookalike", codes)
            self.assertNotIn("files.audio", codes)
            self.assertIn(TWINS[0], next(f.message for f in found if f.code == "files.lookalike"))

    def test_snapshot_copies_the_audio_and_notes_lookalikes(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as dest:
            stick_with_twins(root)
            out = io.StringIO()
            with redirect_stdout(out):
                snap = snapshot.take(root, dest, "snap", full=True, audio=True)
            with open(os.path.join(snap, "Contents", TWINS[0], "UnknownAlbum", "Dawn.mp3"), "rb") as fh:
                self.assertEqual(fh.read(), b"d" * 300)
            with open(os.path.join(snap, "manifest.csv"), newline="", encoding="utf-8") as fh:
                listed = {r["path"].replace("\\", "/") for r in csv.DictReader(fh)}
            self.assertIn(f"Contents/{TWINS[0]}/UnknownAlbum/Dawn.mp3", listed)
            self.assertIn(f"Contents/{TWINS[1]}/UnknownAlbum/Qi.mp3", listed)
            self.assertIn("names Windows can mix up", out.getvalue())

    def test_readtest_reads_through_and_notes_lookalikes(self):
        with tempfile.TemporaryDirectory() as root:
            stick_with_twins(root)
            lines = []
            self.assertTrue(readtest.run(root, n_audio=10, n_anlz=10, progress=lines.append))
            self.assertTrue(any("names Windows can mix up" in line for line in lines), lines)

    def test_clone_refuses_lookalikes_on_windows(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as dst:
            stick_with_twins(root)
            refused = [r for r in clone.plan(root, dst).refusals if "can't keep apart" in r]
            self.assertEqual(bool(refused), names.WINDOWS)
