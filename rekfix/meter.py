"""Progress for long copies: how far along, how fast, and how long is left.

A clone of a full stick runs for hours (a USB stick writes at 10-30 MB/s, and every file is
read back). In a terminal the meter is one line, redrawn every second, with a bar, and the
percentage in the window's title:

  [########--------------------------------]  20.0%  47.2 of 235.9 GB  9 MB/s  about 5 h 49 min left, done around 4:05 AM

Anywhere else (a log, the GUI) it's a line every 10 seconds:

   20.0%  47.2 of 235.9 GB, 23,410 of 117,048 files, 9 MB/s, about 5 h 49 min left (done around 4:05 AM)
"""
import datetime as dt
import os
import shutil
import sys
import time


def time_left(seconds):
    """'about 7 h 05 min left', 'about 12 min left', 'under a minute left'."""
    if seconds < 60:
        return "under a minute left"
    h, m = divmod(round(seconds / 60), 60)
    return f"about {h} h {m:02d} min left" if h else f"about {m} min left"


def done_around(timestamp):
    """'done around 4:05 AM' (the clock time a copy should end)."""
    return "done around " + dt.datetime.fromtimestamp(timestamp).strftime("%I:%M %p").lstrip("0")


def _interactive():
    return sys.stdout is not None and hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def set_title(text):
    """The terminal window's title (shows on the taskbar), where there is one."""
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(text)
        else:
            sys.stdout.write(f"\x1b]0;{text}\x07")
    except Exception:          # no console, or one that doesn't take titles: the line still shows
        pass


class Meter:
    """Counts a copy's files as they finish. live (default: when progress is print and the output
    is a terminal) redraws one line in place every second; otherwise progress gets a line every
    10 seconds. Either way there's a last line at the end."""

    def __init__(self, total_bytes, total_files, progress, every=None, clock=time.time, live=None,
                 title="rekfix"):
        self.live = (progress is print and _interactive()) if live is None else live
        self.total, self.files, self.progress, self.clock, self.title = (total_bytes, total_files, progress,
                                                                          clock, title)
        self.every = every if every is not None else (1 if self.live else 10)
        self.t0 = self.last = clock()
        self.bytes = self.done = 0
        self.open = False                       # a live line waiting for its newline

    def add(self, nbytes):
        self.bytes += nbytes
        self.done += 1
        now = self.clock()
        if now - self.last >= self.every or self.done == self.files:
            self.last = now
            if self.live:
                self.draw(now)
            else:
                self.progress(self.line(now))

    def end(self):
        """Close a live line (before an error message, or when files failed)."""
        if self.open:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.open = False

    def _numbers(self, now):
        rate = self.bytes / max(now - self.t0, 0.001)
        pct = 100 * self.bytes / self.total if self.total else 100.0
        left = (self.total - self.bytes) / rate if self.done < self.files and rate > 0 else None
        return pct, rate, left

    def line(self, now=None):
        now = self.clock() if now is None else now
        pct, rate, left = self._numbers(now)
        out = (f"  {pct:5.1f}%  {self.bytes / 1e9:.1f} of {self.total / 1e9:.1f} GB, "
               f"{self.done:,} of {self.files:,} files, {rate / 1e6:.0f} MB/s")
        if left is not None:
            out += f", {time_left(left)} ({done_around(now + left)})"
        return out

    def draw(self, now=None):
        now = self.clock() if now is None else now
        pct, rate, left = self._numbers(now)
        width = shutil.get_terminal_size((100, 20)).columns - 1
        bar = max(10, min(40, width - 90))
        full = round(bar * pct / 100)
        text = (f"  [{'#' * full}{'-' * (bar - full)}] {pct:5.1f}%  {self.bytes / 1e9:.1f} of "
                f"{self.total / 1e9:.1f} GB  {rate / 1e6:.0f} MB/s")
        if left is not None:
            text += f"  {time_left(left)}, {done_around(now + left)}"
        sys.stdout.write("\r" + text[:width].ljust(width))
        self.open = True
        if self.done == self.files:
            self.end()
        sys.stdout.flush()
        set_title(f"{self.title} {pct:.1f}%" if self.done < self.files else f"{self.title}: done")
