import io
import os
import struct
import tempfile
import unittest
from contextlib import redirect_stdout

from rekfix import check, prune
from rekfix.cli import main
from rekfix.pdb import FLAG_HAS_DELETED, STAMP, Pdb

from . import synth

# six tracks; a play history lists 1 and 3; the stick has audio for 1, 2, 4 and 5 only.
# So 3 has to stay (history) and 6 goes, taking its own artist, album, genre, label, artwork.
LISTS = {1: [1, 6, 2, 6], 2: [3, 6, 5]}


def make_stick(root, data=None, audio=(1, 2, 4, 5), size=1000):
    rb = os.path.join(root, "PIONEER", "rekordbox")
    os.makedirs(rb)
    with open(os.path.join(rb, "export.pdb"), "wb") as fh:
        fh.write(data if data is not None else synth.build(tracks=6, entries_per_list=LISTS, history=(1, 3)))
    for i in audio:
        put(check.on_stick(root, synth.audio_path(i)), size)
    return os.path.join(rb, "export.pdb")


def put(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\x00" * size)


def codes(found):
    return {f.code for f in found}


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


class Plan(unittest.TestCase):
    def test_removes_dead_tracks_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as root:
            pdb = Pdb.open(make_stick(root))
            p = prune.plan(pdb, root)
            self.assertEqual([t.id for t in p.remove], [6])
            self.assertEqual([t.id for t in p.kept], [3])
            self.assertEqual(p.missing, 2)
            self.assertEqual(sum(len(v) for v in p.entries.values()), 3)
            self.assertEqual(p.count[1:], (6, 5))
            self.assertEqual([pdb.u32(r.pos) for r in p.artwork], [6])     # track 6's artwork
            self.assertFalse(p.unflagged)

    def test_edit_matches_rekordbox_and_verifies(self):
        with tempfile.TemporaryDirectory() as root:
            pdb = Pdb.open(make_stick(root))
            before = Pdb(bytes(pdb.data))
            p = prune.plan(pdb, root)
            changed = prune.apply(pdb, p)
            self.assertEqual(prune.verify(before, pdb.data, p, changed), [])
            after = Pdb(bytes(pdb.data))
            self.assertEqual(sorted(after.tracks()), [1, 2, 3, 4, 5])
            lists = {}
            for pid, idx, tid, _ in after.playlist_entries():
                lists.setdefault(pid, []).append((idx, tid))
            self.assertEqual(sorted(lists[1]), [(1, 1), (2, 2)])          # order kept, numbered 1..n
            self.assertEqual(sorted(lists[2]), [(1, 3), (2, 5)])
            self.assertEqual(after.song_count()[1], 5)
            self.assertEqual(sorted(i for i, _ in after.lookup_rows(13)), [1, 2, 3, 4, 5])
            self.assertEqual(sorted(after.artists()), [1, 2])               # artists are left alone
            self.assertEqual(after.sequence, before.sequence + 1)
            for index in changed:                                          # flagged and stamped
                page = after.page(index)
                self.assertEqual(struct.unpack_from("<I", after.data, page.pos + STAMP)[0], after.sequence)
                if any(not after.is_present(page, s) for s in range(page.slots)):
                    self.assertTrue(page.flags & FLAG_HAS_DELETED)
            self.assertFalse(after.unflagged_pages())
            _, found = check.walk(after)
            by_id, more = check.content(after)
            self.assertFalse({"page.count", "tracks.count", "artwork.unused", "playlists.orphans"} & codes(found + more))
            self.assertIn("rows.unused", codes(more))                      # reported, not removed

    def test_restore_from_copies_history_tracks_back(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as lib:
            put(os.path.join(lib, "music", "03 Track 3 (Original Mix).mp3"), 1000)   # a longer name
            put(os.path.join(lib, "music", "06 Track 6.mp3"), 999)                    # wrong size
            pdb = Pdb.open(make_stick(root))
            p = prune.plan(pdb, root, restore_from=[lib])
            self.assertEqual([(t.id, os.path.basename(src)) for t, src in p.restore],
                             [(3, "03 Track 3 (Original Mix).mp3")])
            self.assertFalse(p.kept)
            self.assertEqual([t.id for t in p.remove], [6])            # not in a history: removed
            self.assertEqual(prune.restore_files(p, root), [check.on_stick(root, synth.audio_path(3))])
            self.assertEqual(os.path.getsize(check.on_stick(root, synth.audio_path(3))), 1000)

    def test_restore_all_and_a_mirror_of_the_stick(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as mirror:
            for i in (3, 6):
                put(check.on_stick(mirror, synth.audio_path(i)), 1000)
            pdb = Pdb.open(make_stick(root))
            p = prune.plan(pdb, root, restore_from=[mirror], restore_all=True)
            self.assertEqual(sorted(t.id for t, _ in p.restore), [3, 6])
            self.assertFalse(p.remove)
            self.assertFalse(p.entries)

    def test_finder_handles_shortened_and_numbered_names(self):
        with tempfile.TemporaryDirectory() as lib:
            put(os.path.join(lib, "Some Artist - A Long Title (Extended Mix).mp3"), 1234)
            t = synth_track("/Contents/Some Artist/Some Artist - A Long Title (Ex-1.mp3", 1234)
            self.assertTrue(prune.Finder([lib]).find(t).endswith("(Extended Mix).mp3"))
            self.assertIsNone(prune.Finder([lib]).find(synth_track("/Contents/x/Other.mp3", 1234)))

    def test_refuses_when_most_audio_is_missing(self):
        with tempfile.TemporaryDirectory() as root:
            pdb = Pdb.open(make_stick(root, audio=()))
            with self.assertRaises(prune.PruneError) as e:
                prune.plan(pdb, root)
            self.assertIn("Is that the stick", str(e.exception))
            self.assertEqual(len(prune.plan(pdb, root, force=True).remove), 4)    # 1 and 3 are in a history

    def test_refuses_a_damaged_database(self):
        data = bytearray(synth.build(tracks=6, entries_per_list=LISTS, history=(1, 3)))
        first = struct.unpack_from("<I", data, 0x1C + 8)[0]
        struct.pack_into("<I", data, first * synth.PS + 12, first)          # tracks page 1 -> itself
        struct.pack_into("<I", data, 0x1C + 12, 9999)
        with tempfile.TemporaryDirectory() as root:
            pdb = Pdb.open(make_stick(root, data=bytes(data)))
            with self.assertRaises(prune.PruneError) as e:
                prune.plan(pdb, root)
            self.assertIn("chain.loop", str(e.exception))

    def test_tidies_what_an_older_removal_left(self):
        # a track removed the way rekfix 0.1 or a hand edit did it: present bit and page count
        # only, so the song count, its artist etc. and the page flag are left behind
        pdb = Pdb(synth.build(tracks=6, entries_per_list={1: [1, 2]}, history=(1,)))
        row = pdb.tracks()[6][0].row
        pdb.drop_row(row)
        pdb.fix_counts()
        pdb.data[pdb.page(row.page).pos + 0x1B] &= ~FLAG_HAS_DELETED
        old = Pdb(bytes(pdb.data))
        _, found = check.content(old)
        self.assertTrue({"tracks.count", "artwork.unused", "rows.unused", "page.deleted-flag"} <= codes(found))
        with tempfile.TemporaryDirectory() as root:
            make_stick(root, data=bytes(old.data), audio=range(1, 6))
            pdb = Pdb.open(os.path.join(root, "PIONEER", "rekordbox", "export.pdb"))
            p = prune.plan(pdb, root)
            self.assertFalse(p.remove)
            self.assertEqual(p.count[1:], (6, 5))
            self.assertEqual(p.unflagged, [row.page])
            before = Pdb(bytes(pdb.data))
            changed = prune.apply(pdb, p)
            self.assertEqual(prune.verify(before, pdb.data, p, changed), [])
            _, found = check.content(Pdb(bytes(pdb.data)))
            self.assertFalse({"tracks.count", "artwork.unused", "page.deleted-flag"} & codes(found))

    def test_verify_catches_a_stray_change(self):
        with tempfile.TemporaryDirectory() as root:
            pdb = Pdb.open(make_stick(root))
            before = Pdb(bytes(pdb.data))
            p = prune.plan(pdb, root)
            changed = prune.apply(pdb, p)
            artist = pdb.rows(2)[0]
            pdb.data[artist.pos + 12] ^= 0xFF                              # damage a name
            self.assertTrue(any("outside the edited fields" in x for x in prune.verify(before, pdb.data, p, changed)))


def synth_track(path, size):
    pdb = Pdb(synth.build(tracks=1))
    t = pdb.tracks()[1][0]
    t.strings["file_path"] = path
    t.file_size = size
    return t


class Command(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(list(args))
        return code, out.getvalue()

    def test_dry_run_then_apply(self):
        with tempfile.TemporaryDirectory() as root:
            path = make_stick(root)
            before = read(path)
            code, out = self.run_cli("prune", root)
            self.assertEqual(code, 0, out)
            self.assertIn("dry run", out)
            self.assertIn("6 -> 5", out)
            self.assertEqual(read(path), before)
            code, out = self.run_cli("prune", root, "--apply")
            self.assertEqual(code, 0, out)
            self.assertIn("1 tracks removed", out)
            self.assertTrue(any(f.endswith(".bak") for f in os.listdir(os.path.dirname(path))))
            self.assertEqual(sorted(Pdb.open(path).tracks()), [1, 2, 3, 4, 5])
            code, out = self.run_cli("prune", root)
            self.assertIn("nothing to do", out)

    def test_needs_the_stick_not_the_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = make_stick(root)
            with self.assertRaises(SystemExit):
                self.run_cli("prune", path)


if __name__ == "__main__":
    unittest.main()
