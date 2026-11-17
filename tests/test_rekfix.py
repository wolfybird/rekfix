import io
import os
import struct
import tempfile
import unittest
from contextlib import redirect_stdout

from rekfix import check, menu, snapshot
from rekfix.cli import main
from rekfix.pdb import Pdb, read_string

from . import synth


def codes(found, level=None):
    return {f.code for f in found if level is None or f.level == level}


def read(path, mode="rb"):
    with open(path, mode) as fh:
        return fh.read()


class Strings(unittest.TestCase):
    def test_three_encodings(self):
        for enc, text in ((synth.short, "Short"), (synth.long_ascii, "Long ascii " * 20), (synth.utf16, "Ünïcødé ✓")):
            data = enc(text) + b"\x00" * 8
            self.assertEqual(read_string(data, 0), (text, None))

    def test_unknown_kind_is_reported_not_raised(self):
        text, err = read_string(b"\x22rest", 0)
        self.assertIn("unknown string kind", err)

    def test_long_string_past_page_is_reported(self):
        data = bytes([0x90]) + struct.pack("<H", 500) + b"\x00" + b"ab"
        self.assertIn("past the page", read_string(data, 0, limit=len(data))[1])


class Reading(unittest.TestCase):
    def test_healthy_file(self):
        pdb = Pdb(synth.build())
        counts, found = check.walk(pdb)
        by_id, more = check.content(pdb)
        self.assertEqual(sorted(by_id), [1, 2, 3])
        self.assertEqual(by_id[2][0].title, "Track 2")
        self.assertEqual(counts[0], (2, 3))                         # tracks: two chained pages
        self.assertFalse(codes(found + more, "error"), [str(f) for f in found + more])
        self.assertEqual(pdb.history_playlists(), {1: "HISTORY 001"})
        self.assertEqual(pdb.columns()[15], "BITRATE")

    def test_more_than_255_rows_on_a_page(self):
        # the byte at +0x18 alone would say 280 & 0xff = 24 rows
        pdb = Pdb(synth.build(tracks=3, entries_per_list={1: [1, 2, 3] * 93 + [1]}))
        entries = pdb.playlist_entries()
        self.assertEqual(len(entries), 280)
        self.assertEqual(sorted(i for _, i, _, _ in entries), list(range(1, 281)))
        self.assertFalse(codes(check.walk(pdb)[1], "error"))

    def test_utf16_title(self):
        data = bytearray(synth.build(tracks=1))
        pdb = Pdb(bytes(data))
        self.assertEqual(pdb.tracks()[1][0].title, "Track 1")


class Editing(unittest.TestCase):
    def test_drop_row_like_rekordbox(self):
        pdb = Pdb(synth.build())
        row = next(r for _, _, tid, r in pdb.playlist_entries() if tid == 3)
        pdb.drop_row(row)
        pdb.fix_counts()
        again = Pdb(bytes(pdb.data))
        self.assertNotIn(3, [t for _, _, t, _ in again.playlist_entries()])
        page = again.page(row.page)
        self.assertEqual(page.present, page.slots - 1)              # packed count updated
        self.assertNotIn("page.count", codes(check.walk(again)[1]))
        with self.assertRaises(ValueError):
            pdb.drop_row(row)

    def test_menu_hide_matches_rekordbox(self):
        pdb = Pdb(synth.build())
        done = menu.hide(pdb, menu.resolve(pdb, ["time", "Hot Cue Bank", "FILE NAME", "bitrate"]))
        self.assertEqual(sorted(done), ["BITRATE", "FILE NAME", "HOT CUE BANK", "TIME"])
        items = {m.category: m for m in pdb.menu()}
        for cat in (14, 15, 16, 18):
            self.assertEqual(items[cat].sort, 0)
            self.assertTrue(items[cat].flags & menu.HIDDEN_FLAG)
        shown = sorted(m.sort for m in pdb.menu() if m.visible)
        self.assertEqual(shown, list(range(1, len(shown) + 1)))
        _, found = check.content(Pdb(bytes(pdb.data)))
        self.assertNotIn("menu.heavy", codes(found))
        menu.show(pdb, {15})
        self.assertTrue(next(m for m in pdb.menu() if m.category == 15).visible)

    def test_unknown_category(self):
        with self.assertRaises(ValueError):
            menu.resolve(Pdb(synth.build()), ["nonsense"])


class Damage(unittest.TestCase):
    def test_chain_loop(self):
        data = bytearray(synth.build())
        first, last = struct.unpack_from("<2I", data, 0x1C + 8)
        struct.pack_into("<I", data, first * synth.PS + 12, first)          # tracks page 1 -> itself
        struct.pack_into("<I", data, 0x1C + 12, 9999)                        # last page never reached
        self.assertIn("chain.loop", codes(check.walk(Pdb(bytes(data)))[1], "error"))

    def test_a_page_header_rekordbox_never_writes(self):
        # what rekordbox itself left on a test stick before calling it corrupted: ~1,990 rows, flags 0x03
        data = bytearray(synth.build(tracks=6))
        first = struct.unpack_from("<I", data, 0x1C + 8)[0]
        pos = first * synth.PS
        data[pos + 0x18:pos + 0x1C] = bytes([0x00, 0xD4, 0xF8, 0x03])
        found = check.walk(Pdb(bytes(data)))[1]
        self.assertIn("page.flags", codes(found, "error"))
        self.assertFalse({"row.offset", "page.space", "page.count"} & codes(found))   # one finding, not one per slot
        data = bytearray(synth.build(tracks=6))
        data[pos + 0x1B] = 0x2C                                               # rekordbox's bits, unfamiliar value
        self.assertIn("page.flags", codes(check.walk(Pdb(bytes(data)))[1], "warn"))

    def test_impossible_row_count_is_one_finding(self):
        data = bytearray(synth.build(tracks=6))
        pos = struct.unpack_from("<I", data, 0x1C + 8)[0] * synth.PS
        data[pos + 0x18:pos + 0x1B] = (5000 | 3 << 13).to_bytes(3, "little")   # 5,000 slots can't fit a page
        found = [f for f in check.walk(Pdb(bytes(data)))[1] if f.level == "error"]
        self.assertEqual([f.code for f in found], ["page.space"])

    def test_empty_candidate_in_use(self):
        data = bytearray(synth.build())
        first = struct.unpack_from("<I", data, 0x1C + 8)[0]
        struct.pack_into("<I", data, 0x1C + 4, first)                         # tracks' spare page: its own first
        self.assertIn("header.empty-in-use", codes(check.walk(Pdb(bytes(data)))[1], "error"))

    def test_blank_index_page(self):
        for filled in (False, True):
            data = bytearray(synth.build())
            n = len(data) // synth.PS
            keys = 0x1C + 16 * 5
            index = bytearray(synth.PS)
            struct.pack_into("<4I", index, 0, 0, n, 5, struct.unpack_from("<I", data, keys + 8)[0])
            index[0x1B] = 0x64
            if filled:
                struct.pack_into("<HHI", index, 0x24, 0x3EC, 0, n)            # rekordbox's layout names its own page
            data += index
            struct.pack_into("<I", data, keys + 8, n)                         # keys now start at their index page
            for t in range(20):
                struct.pack_into("<I", data, 0x1C + 16 * t + 4, n + 1)        # spare pages past the end
            struct.pack_into("<I", data, 0x0C, n + 2)
            found = check.walk(Pdb(bytes(data)))[1]
            self.assertEqual("index.blank" in codes(found, "warn"), not filled)
            self.assertFalse(codes(found, "error"), [str(f) for f in found])

    def test_many_bad_row_offsets_are_one_finding(self):
        data = bytearray(synth.build(entries_per_list={1: [1, 2, 3] * 10}))
        pos = struct.unpack_from("<I", data, 0x1C + 16 * 8 + 8)[0] * synth.PS
        used = struct.unpack_from("<H", data, pos + 0x1E)[0]
        for slot in range(30):
            g, s = divmod(slot, 16)
            struct.pack_into("<H", data, pos + synth.PS - g * 0x24 - 6 - 2 * s, used + 4)
        found = [f for f in check.walk(Pdb(bytes(data)))[1] if f.code == "row.offset"]
        self.assertEqual(len(found), 1)
        self.assertIn("30 rows", found[0].message)

    def test_stale_header_and_recovery_scan(self):
        data = bytearray(synth.build(tracks=6))
        first, last = struct.unpack_from("<2I", data, 0x1C + 8)
        struct.pack_into("<I", data, 0x1C + 12, first)                       # header: tracks end at first page
        struct.pack_into("<I", data, 0x0C, 3)                                # header: tiny database
        pdb = Pdb(bytes(data))
        found = check.walk(pdb)[1]
        self.assertIn("header.stale", codes(found, "error"))
        self.assertIn("pages.unreached", codes(found))
        self.assertEqual(len(pdb.tracks()), 3)                               # chain stops early
        self.assertEqual(len(Pdb(bytes(data), scan=True).tracks()), 6)       # recovery read gets all

    def test_orphans_and_duplicate_ids(self):
        pdb = Pdb(synth.build(tracks=2, entries_per_list={1: [1, 2, 7]}))
        _, found = check.content(pdb)
        self.assertIn("playlists.orphans", codes(found, "error"))

    def test_second_word(self):
        extra = lambda i: synth.page(i, 8, [synth.entry_row(2, 2, 1)], second_word=True)
        data = synth.build(extra_pages=[extra])
        self.assertEqual(len(Pdb(data, scan=True).playlist_entries()), 3)
        self.assertEqual(len(Pdb(data, scan=True, second_word=True).playlist_entries()), 4)

    def test_missing_audio_on_stick(self):
        with tempfile.TemporaryDirectory() as root:
            pdb = Pdb(synth.build(tracks=2))
            os.makedirs(os.path.join(root, "Contents", "Artist", "Album"))
            open(os.path.join(root, "Contents", "Artist", "Album", "01 Track 1.mp3"), "wb").close()
            found = check.files(pdb, root)
            self.assertIn("files.audio", codes(found))
            self.assertIn("1 tracks", next(str(f) for f in found if f.code == "files.audio").replace(",", ""))
            self.assertNotIn("rekordbox.broken-marker", codes(found))
            os.makedirs(os.path.join(root, "PIONEER", "rekordbox"))
            with open(os.path.join(root, "PIONEER", "rekordbox", "brokendb"), "wb") as fh:
                fh.write(b" ")
            self.assertIn("rekordbox.broken-marker", codes(check.files(pdb, root)))


class Commands(unittest.TestCase):
    def stick(self, root):
        rb = os.path.join(root, "PIONEER", "rekordbox")
        os.makedirs(rb)
        with open(os.path.join(rb, "export.pdb"), "wb") as fh:
            fh.write(synth.build())
        return rb

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(list(args))
        return code, out.getvalue()

    def test_check_and_menu_dry_run_and_apply(self):
        with tempfile.TemporaryDirectory() as root:
            rb = self.stick(root)
            code, out = self.run_cli("check", root, "--no-files")
            self.assertEqual(code, 0, out)
            before = read(os.path.join(rb, "export.pdb"))
            code, out = self.run_cli("menu", root, "--hide", "time,bitrate")
            self.assertIn("dry run", out)
            self.assertEqual(read(os.path.join(rb, "export.pdb")), before)
            code, out = self.run_cli("menu", root, "--hide", "time,bitrate", "--apply")
            self.assertIn("written", out)
            after = Pdb.open(os.path.join(rb, "export.pdb"))
            self.assertEqual(after.sequence, 101)
            self.assertFalse(any(m.visible for m in after.menu() if m.category in (14, 15)))
            self.assertTrue(any(f.endswith(".bak") for f in os.listdir(rb)))

    def test_menu_refuses_a_damaged_database(self):
        with tempfile.TemporaryDirectory() as root:
            rb = self.stick(root)
            path = os.path.join(rb, "export.pdb")
            data = bytearray(read(path))
            first = struct.unpack_from("<I", data, 0x1C + 8)[0]
            struct.pack_into("<I", data, first * synth.PS + 12, first)      # tracks page -> itself
            struct.pack_into("<I", data, 0x1C + 12, 9999)
            with open(path, "wb") as fh:
                fh.write(data)
            code, out = self.run_cli("menu", root, "--hide", "time", "--apply")
            self.assertEqual(code, 1)
            self.assertIn("structural errors", out)
            self.assertEqual(read(path), bytes(data))

    def test_snapshot(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as dest:
            self.stick(root)
            out = io.StringIO()
            with redirect_stdout(out):
                path = snapshot.take(root, dest, "snap")
            self.assertTrue(os.path.exists(os.path.join(path, "PIONEER", "rekordbox", "export.pdb")))
            self.assertIn("export.pdb", read(os.path.join(path, "checksums.md5"), "r"))
            self.assertIn("manifest.csv", os.listdir(path))


if __name__ == "__main__":
    unittest.main()
