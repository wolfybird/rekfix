import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from rekfix import clone, meter
from rekfix.meter import Meter, done_around, time_left

from .test_clone import make_stick

T0 = 1_700_000_000.0          # a real date: done_around turns it into a clock time


class Wording(unittest.TestCase):
    def test_time_left(self):
        self.assertEqual(time_left(30), "under a minute left")
        self.assertEqual(time_left(12 * 60 + 20), "about 12 min left")
        self.assertEqual(time_left(3600 + 4 * 60), "about 1 h 04 min left")
        self.assertEqual(time_left(7 * 3600 + 33 * 60), "about 7 h 33 min left")

    def test_done_around(self):
        self.assertRegex(done_around(T0), r"^done around (1[0-2]|[1-9]):[0-5]\d [AP]M$")


class Lines(unittest.TestCase):
    def test_every_ten_seconds_and_at_the_end(self):
        now, lines = [T0], []
        m = Meter(1000 * 10**6, 4, lines.append, clock=lambda: now[0])
        self.assertFalse(m.live)                # not print: lines, on any output
        now[0] = T0 + 5
        m.add(100 * 10**6)                      # too soon for a line
        self.assertEqual(lines, [])
        now[0] = T0 + 20
        m.add(100 * 10**6)                      # 200 MB in 20 s: 10 MB/s, 800 MB (80 s) to go
        self.assertEqual(lines, ["   20.0%  0.2 of 1.0 GB, 2 of 4 files, 10 MB/s, about 1 min left "
                                 f"({done_around(T0 + 100)})"])
        now[0] = T0 + 25
        m.add(300 * 10**6)
        now[0] = T0 + 26
        m.add(500 * 10**6)                      # the last file always gets a line, without time left
        self.assertEqual(lines[1:], ["  100.0%  1.0 of 1.0 GB, 4 of 4 files, 38 MB/s"])

    def test_print_to_a_file_is_lines_too(self):
        with redirect_stdout(io.StringIO()):
            self.assertFalse(Meter(1, 1, print).live)

    def test_live_line_redraws_in_place(self):
        now, out = [T0], io.StringIO()
        with redirect_stdout(out), mock.patch.object(meter, "set_title") as title, \
                mock.patch("shutil.get_terminal_size", return_value=os.terminal_size((120, 30))):
            m = Meter(1000 * 10**6, 2, print, clock=lambda: now[0], live=True, title="rekfix clone")
            now[0] = T0 + 2
            m.add(500 * 10**6)
            title.assert_called_with("rekfix clone 50.0%")
            now[0] = T0 + 4
            m.add(500 * 10**6)
            m.end()                             # already ended: no second newline
        first, last = out.getvalue().split("\r")[1:]
        self.assertIn("]  50.0%  0.5 of 1.0 GB  250 MB/s  under a minute left, done around", first)
        self.assertIn("#" * 14 + "-" * 15 + "]  50.0%", first)        # a 29-wide bar on 120 columns
        self.assertIn("[" + "#" * 29 + "] 100.0%", last)
        self.assertTrue(last.endswith("\n") and not last.endswith("\n\n"))
        self.assertEqual(len(first), 119)                            # padded over the last line
        title.assert_called_with("rekfix clone: done")

    def test_clone_ends_on_a_full_line(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            make_stick(src)
            lines = []
            clone.run(clone.plan(src, dst), progress=lines.append)
            self.assertTrue(lines[-1].startswith("  100.0%"), lines)
            self.assertIn("6 of 6 files", lines[-1])


if __name__ == "__main__":
    unittest.main()
