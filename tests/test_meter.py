import tempfile
import unittest

from rekfix import clone
from rekfix.meter import Meter, time_left

from .test_clone import make_stick


class TimeLeft(unittest.TestCase):
    def test_wording(self):
        self.assertEqual(time_left(30), "under a minute left")
        self.assertEqual(time_left(12 * 60 + 20), "about 12 min left")
        self.assertEqual(time_left(3600 + 4 * 60), "about 1 h 04 min left")
        self.assertEqual(time_left(7 * 3600 + 33 * 60), "about 7 h 33 min left")


class Lines(unittest.TestCase):
    def test_every_ten_seconds_and_at_the_end(self):
        now, lines = [0.0], []
        m = Meter(1000 * 10**6, 4, lines.append, clock=lambda: now[0])
        now[0] = 5
        m.add(100 * 10**6)                      # too soon for a line
        self.assertEqual(lines, [])
        now[0] = 20
        m.add(100 * 10**6)                      # 200 MB in 20 s: 10 MB/s, 800 MB (80 s) to go
        self.assertEqual(lines, ["   20.0%  0.2 of 1.0 GB, 2 of 4 files, 10 MB/s, about 1 min left"])
        now[0] = 25
        m.add(300 * 10**6)
        now[0] = 26
        m.add(500 * 10**6)                      # the last file always gets a line, without time left
        self.assertEqual(lines[1:], ["  100.0%  1.0 of 1.0 GB, 4 of 4 files, 38 MB/s"])

    def test_clone_ends_on_a_full_line(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            make_stick(src)
            lines = []
            clone.run(clone.plan(src, dst), progress=lines.append)
            self.assertTrue(lines[-1].startswith("  100.0%"), lines)
            self.assertIn("6 of 6 files", lines[-1])


if __name__ == "__main__":
    unittest.main()
