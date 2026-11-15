import io
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout

from rekfix import anlz, check, rebuild
from rekfix.cli import main

from . import synth

BEATS = [(1 + i % 4, 12800, 100 + i * 469) for i in range(16)]             # 128 BPM, first beat at 0.1 s
CUES_1 = [(1, 1000, None, "", (40, 226, 20), False),                       # hot cue A, green
          (0, 5000, None, "", None, False),                                # memory cue
          (2, 8000, 12000, "drop loop", None, False)]                      # hot loop B, named
CUES_2 = [(3, 29264, None, "", None, True)]                                # hot cue C, written by a deck


def put(path, size=1000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"\x00" * size)


def stick(root, tracks=3, audio=(1, 2, 3), lists=None, history=(1, 2)):
    rb = os.path.join(root, "PIONEER", "rekordbox")
    os.makedirs(rb)
    with open(os.path.join(rb, "export.pdb"), "wb") as fh:
        fh.write(synth.build(tracks=tracks, entries_per_list=lists or {1: [1, 2], 2: [3]}, history=history))
    for i in audio:
        put(check.on_stick(root, synth.audio_path(i)))
    synth.write_anlz(root, "P000/00000001", synth.audio_path(1), BEATS, CUES_1)
    synth.write_anlz(root, "P000/00000002", synth.audio_path(2), BEATS[:4], CUES_2)


def parse(path):
    root = ET.parse(path).getroot()
    tracks = {t.get("TrackID"): t for t in root.find("COLLECTION")}
    return root, tracks


class Analysis(unittest.TestCase):
    def test_reads_path_grid_and_cues(self):
        with tempfile.TemporaryDirectory() as root:
            dat = synth.write_anlz(root, "P000/00000001", "/Contents/a.mp3", BEATS, CUES_1)
            a = anlz.read(dat)
            self.assertEqual(a.path, "/Contents/a.mp3")
            self.assertEqual(a.bpm, 128.0)
            self.assertEqual([(c.hot, c.start_ms, c.end_ms, c.name, c.rgb) for c in a.cues],
                             [(1, 1000, None, "", (40, 226, 20)), (2, 8000, 12000, "drop loop", None),
                              (0, 5000, None, "", None)])
            self.assertTrue(all(c.source == "EXT" for c in a.cues))       # the .EXT's entries win

    def test_deck_entry_with_zero_loop_end_is_a_cue(self):
        with tempfile.TemporaryDirectory() as root:
            dat = synth.write_anlz(root, "P000/00000002", "/Contents/b.mp3", (), CUES_2)
            (c,) = anlz.read(dat).cues
            self.assertEqual((c.hot, c.start_ms, c.is_loop), (3, 29264, False))

    def test_damaged_cue_list_falls_back_to_signatures(self):
        data = bytearray(synth.anlz_file("/Contents/a.mp3", BEATS, CUES_1, ext=True))
        pos = data.find(b"PCO2", data.find(b"PCO2") + 4)                  # the hot cue list
        data[pos + 16:pos + 18] = b"\x00\x09"                               # claims 9 entries
        problems = []
        cues = anlz.read_cues(bytes(data), "EXT", problems)
        self.assertEqual(sorted(c.start_ms for c in cues), [1000, 5000, 8000])
        self.assertTrue(problems)

    def test_path_index(self):
        with tempfile.TemporaryDirectory() as root:
            synth.write_anlz(root, "P001/0000ABCD", "/Contents/X/y.mp3", BEATS)
            idx = anlz.index_paths(os.path.join(root, "PIONEER", "USBANLZ"))
            self.assertEqual(list(idx), ["/contents/x/y.mp3"])

    def test_tempo_segments(self):
        beats = [(1, 12800, 0), (2, 12800, 469), (3, 12805, 938), (4, 13000, 1400)]
        self.assertEqual(rebuild.tempo_segments(beats), [(0.0, 128.0, 1), (1.4, 130.0, 4)])


class Rebuild(unittest.TestCase):
    def test_everything_comes_through(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out:
            stick(root)
            rb = rebuild.collect(root)
            self.assertEqual(len(rb.items), 3)
            self.assertFalse(rb.missing)
            xml = os.path.join(out, "lib.xml")
            rebuild.write_xml(rb, xml)
            self.assertEqual(rebuild.verify_xml(xml, rb), [])
            doc, tracks = parse(xml)
            t1 = tracks["1"]
            self.assertEqual((t1.get("Name"), t1.get("Artist"), t1.get("Album"), t1.get("Genre"), t1.get("Tonality")),
                             ("Track 1", "Artist", "Album", "House", "Am"))
            self.assertEqual(t1.get("AverageBpm"), "128.00")
            self.assertIn("01%20Track%201.mp3", t1.get("Location"))
            self.assertEqual([(t.get("Inizio"), t.get("Bpm"), t.get("Battito")) for t in t1.findall("TEMPO")],
                             [("0.100", "128.00", "1")])
            marks = {(m.get("Num"), m.get("Type"), m.get("Start"), m.get("End"), m.get("Name"), m.get("Red"))
                     for m in t1.findall("POSITION_MARK")}
            self.assertEqual(marks, {("0", "0", "1.000", None, "", "40"), ("-1", "0", "5.000", None, "", None),
                                     ("1", "4", "8.000", "12.000", "drop loop", None)})
            (deck,) = tracks["2"].findall("POSITION_MARK")
            self.assertEqual((deck.get("Num"), deck.get("Start")), ("2", "29.264"))
            self.assertEqual(tracks["3"].findall("POSITION_MARK"), [])     # no analysis: still a track
            nodes = {n.get("Name"): [t.get("Key") for t in n.findall("TRACK")] for n in doc.iter("NODE")}
            self.assertEqual(nodes["Warmup"], ["1", "2"])
            self.assertEqual(nodes["Peak"], ["3"])
            self.assertEqual(nodes["HISTORY 001"], ["1", "2"])
            self.assertIn(rebuild.HISTORY_FOLDER, nodes)

    def test_damaged_path_and_moved_analysis(self):
        with tempfile.TemporaryDirectory() as root:
            stick(root)
            # track 2's analysis isn't where the database says; another folder names its file
            import shutil
            shutil.rmtree(os.path.join(root, "PIONEER", "USBANLZ", "P000", "00000002"))
            synth.write_anlz(root, "P05A/0000FFFF", synth.audio_path(2), BEATS[:4], CUES_2)
            rb = rebuild.collect(root)
            item = next(i for i in rb.items if i.xml_id == 2)
            self.assertEqual(item.analysis_how, "analysis path")
            self.assertEqual([c.start_ms for c in item.analysis.cues], [29264])

    def test_missing_audio_and_extra_files(self):
        with tempfile.TemporaryDirectory() as root:
            stick(root, audio=(1, 2))
            put(os.path.join(root, "Contents", "Other", "Loose", "loose.mp3"), 777)
            synth.write_anlz(root, "P009/00000009", "/Contents/Other/Loose/loose.mp3", BEATS)
            rb = rebuild.collect(root)
            self.assertEqual([t.id for t in rb.missing], [3])
            self.assertEqual(len(rb.extra), 1)
            self.assertEqual(rb.stats["playlist entries dropped (no audio)"], 1)
            rb = rebuild.collect(root, all_audio=True)
            loose = next(i for i in rb.items if i.how == "audio only")
            self.assertEqual((loose.xml_id, loose.analysis.bpm), (4, 128.0))

    def test_shortened_names_in_a_library_copy(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as lib:
            stick(root, audio=())
            for i in (1, 2, 3):                                             # the library's names are longer
                put(os.path.join(lib, "Music", f"{i:02d} Track {i} (Original Mix).mp3"))
            rb = rebuild.collect(root, music=[lib])
            self.assertEqual(sorted(i.how for i in rb.items), ["name start"] * 3)
            self.assertEqual(len(rb.fuzzy), 3)
            self.assertTrue(all(i.analysis for i in rb.items if i.xml_id in (1, 2)))

    def test_no_database_at_all(self):
        with tempfile.TemporaryDirectory() as root:
            stick(root)
            os.remove(os.path.join(root, "PIONEER", "rekordbox", "export.pdb"))
            rb = rebuild.collect(root)
            self.assertEqual(len(rb.items), 3)                              # from the audio files
            self.assertEqual(sum(1 for i in rb.items if i.analysis), 2)     # their analysis by path
            self.assertTrue(rb.problems)

    def test_command(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as out:
            stick(root)
            xml = os.path.join(out, "lib.xml")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["rebuild", root, xml])
            self.assertEqual(code, 0, buf.getvalue())
            self.assertTrue(os.path.exists(os.path.join(out, "lib.report.txt")))
            self.assertIn("beat grids 2", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
