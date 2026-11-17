"""Progress lines for long copies: how far along, how fast, and how long is left.

A clone of a full stick runs for hours (a USB stick writes at 10-30 MB/s, and every file is
read back), so a line every few seconds says the percentage done and the time left at the
speed so far.
"""
import time


def time_left(seconds):
    """'about 7 h 05 min left', 'about 12 min left', 'under a minute left'."""
    if seconds < 60:
        return "under a minute left"
    h, m = divmod(round(seconds / 60), 60)
    return f"about {h} h {m:02d} min left" if h else f"about {m} min left"


class Meter:
    """Counts a copy's files as they finish and, at most every `every` seconds and once at the
    end, calls progress with a line like
    '   8.0%  18.3 of 235.9 GB, 1,678 of 117,048 files, 8 MB/s, about 7 h 33 min left'."""

    def __init__(self, total_bytes, total_files, progress, every=10, clock=time.time):
        self.total, self.files, self.progress, self.every, self.clock = (total_bytes, total_files, progress,
                                                                           every, clock)
        self.t0 = self.last = clock()
        self.bytes = self.done = 0

    def add(self, nbytes):
        self.bytes += nbytes
        self.done += 1
        now = self.clock()
        if now - self.last >= self.every or self.done == self.files:
            self.last = now
            self.progress(self.line(now))

    def line(self, now=None):
        now = self.clock() if now is None else now
        rate = self.bytes / max(now - self.t0, 0.001)
        pct = 100 * self.bytes / self.total if self.total else 100.0
        out = (f"  {pct:5.1f}%  {self.bytes / 1e9:.1f} of {self.total / 1e9:.1f} GB, "
               f"{self.done:,} of {self.files:,} files, {rate / 1e6:.0f} MB/s")
        if self.done < self.files and rate > 0:
            out += ", " + time_left((self.total - self.bytes) / rate)
        return out
