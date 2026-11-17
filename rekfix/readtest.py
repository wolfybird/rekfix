"""How fast, and how evenly, a stick reads: the database in small reads (as a player browsing
does), a sample of music files, and a sample of the small analysis files. On Windows the reads
bypass the system cache, so the numbers are the stick's own; elsewhere the cache can flatter
them (eject and re-insert the stick first)."""
import os
import random
import statistics
import sys
import time

from . import names

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wt.HANDLE
    _k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
    _k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    _k32.ReadFile.restype = wt.BOOL
    _INVALID = wt.HANDLE(-1).value

    def _read(path, chunk):
        h = _k32.CreateFileW(path, 0x80000000, 3, None, 3, 0x20000000, None)   # read, share, no buffering
        if h == _INVALID:
            raise OSError(ctypes.get_last_error(), path)
        raw = ctypes.create_string_buffer(chunk + 4096)
        buf = (ctypes.addressof(raw) + 4095) & ~4095
        n, out = wt.DWORD(), []
        try:
            while True:
                t = time.perf_counter()
                if not _k32.ReadFile(h, buf, chunk, ctypes.byref(n), None):
                    raise OSError(ctypes.get_last_error(), path)
                dt = time.perf_counter() - t
                if not n.value:
                    break
                out.append((dt, n.value))
                if n.value < chunk:
                    break
        finally:
            _k32.CloseHandle(h)
        return out
else:
    def _read(path, chunk):
        out = []
        with open(path, "rb", buffering=0) as fh:
            while True:
                t = time.perf_counter()
                b = fh.read(chunk)
                dt = time.perf_counter() - t
                if not b:
                    break
                out.append((dt, len(b)))
        return out

def read_uncached(path, chunk=1 << 20):
    """Yield a file's bytes as the device returns them, not from the system's cache: what's
    really on the stick. Windows: unbuffered reads. macOS: F_NOCACHE. Linux: the file's cached
    pages are dropped first (they're clean once the copy was synced)."""
    if sys.platform == "win32":
        h = _k32.CreateFileW(path, 0x80000000, 3, None, 3, 0x20000000, None)
        if h == _INVALID:
            raise OSError(ctypes.get_last_error(), path)
        raw = ctypes.create_string_buffer(chunk + 4096)
        buf = (ctypes.addressof(raw) + 4095) & ~4095
        n = wt.DWORD()
        try:
            while True:
                if not _k32.ReadFile(h, buf, chunk, ctypes.byref(n), None):
                    raise OSError(ctypes.get_last_error(), path)
                if not n.value:
                    return
                yield ctypes.string_at(buf, n.value)
                if n.value < chunk:
                    return
        finally:
            _k32.CloseHandle(h)
    with open(path, "rb", buffering=0) as fh:
        try:
            if sys.platform == "darwin":
                import fcntl
                fcntl.fcntl(fh.fileno(), getattr(fcntl, "F_NOCACHE", 48), 1)
            elif hasattr(os, "posix_fadvise"):
                os.posix_fadvise(fh.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass
        while True:
            b = fh.read(chunk)
            if not b:
                return
            yield b


AUDIO = (".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a", ".mp4", ".alac")


def _line(name, rows):
    ts = [t for t, _ in rows]
    mb = sum(b for _, b in rows) / 1e6
    return (f"{name}: {mb:,.1f} MB at {mb / max(sum(ts), 1e-9):,.1f} MB/s; per read median "
            f"{statistics.median(ts) * 1000:.2f} ms, slowest {max(ts) * 1000:.1f} ms, over 100 ms: "
            f"{sum(1 for t in ts if t > 0.1)}")


def run(root, n_audio=60, n_anlz=1500, seed=1, progress=print):
    rng = random.Random(seed)
    pdb = os.path.join(root, "PIONEER", "rekordbox", "export.pdb")
    if os.path.exists(pdb):
        progress(_line("export.pdb, 64 KB reads", _read(pdb, 65536)))
    errors, slow, groups = [], [], []
    # files are reached through rekfix's own listings (short names on Windows): a look-alike
    # folder name can't send a read into the wrong folder
    contents = os.path.join(root, "Contents")
    audio = sorted((os.path.join("Contents", rel), e.path)
                   for rel, e in names.walk(contents, groups=groups) if rel.lower().endswith(AUDIO))
    rows = []
    for rel, f in rng.sample(audio, min(n_audio, len(audio))):
        try:
            r = _read(f, 1 << 20)
        except OSError as e:
            errors.append(f"{rel}: {e}")
            continue
        rows += r
        worst = max((t for t, _ in r), default=0)
        if worst > 0.25:
            slow.append((worst, rel))
    if rows:
        progress(_line(f"{min(n_audio, len(audio))} music files, 1 MB reads", rows))
    anlz = sorted((os.path.join("PIONEER", "USBANLZ", rel), e.path)
                  for rel, e in names.walk(os.path.join(root, "PIONEER", "USBANLZ")))
    lat = []
    for rel, f in rng.sample(anlz, min(n_anlz, len(anlz))):
        t = time.perf_counter()
        try:
            _read(f, 1 << 20)
        except OSError as e:
            errors.append(f"{rel}: {e}")
            continue
        lat.append(time.perf_counter() - t)
    if lat:
        progress(f"{len(lat)} analysis files: per file median {statistics.median(lat) * 1000:.2f} ms, slowest "
                 f"{max(lat) * 1000:.1f} ms, over 100 ms: {sum(1 for x in lat if x > 0.1)}")
    progress(f"read errors: {len(errors)}" + "".join(f"\n  {e}" for e in errors[:10]))
    for worst, f in sorted(slow, reverse=True)[:10]:
        progress(f"  slow read {worst * 1000:.0f} ms: {f}")
    if groups:
        groups = [(os.path.join("Contents", folder) if folder else "Contents", g) for folder, g in groups]
        progress(f"note: names Windows can mix up: {names.describe(groups)}. Windows programs can open one for the "
                 f"other, so files there can seem to come and go; this test reads them correctly. See `rekfix check`")
    if sys.platform != "win32":
        progress("(not Windows: the system cache may have served some reads - eject and re-insert for real numbers)")
    return not errors
