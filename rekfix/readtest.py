"""How fast, and how evenly, a stick reads: the database in small reads (as a player browsing
does), a sample of music files, and a sample of the small analysis files. On Windows the reads
bypass the system cache, so the numbers are the stick's own; elsewhere the cache can flatter
them (eject and re-insert the stick first)."""
import os
import random
import statistics
import sys
import time

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
    audio, errors, slow = [], [], []
    for dirpath, _, files in os.walk(os.path.join(root, "Contents")):
        audio += [os.path.join(dirpath, f) for f in files if f.lower().endswith(AUDIO)]
    rows = []
    for f in rng.sample(audio, min(n_audio, len(audio))):
        try:
            r = _read(f, 1 << 20)
        except OSError as e:
            errors.append(f"{os.path.relpath(f, root)}: {e}")
            continue
        rows += r
        worst = max((t for t, _ in r), default=0)
        if worst > 0.25:
            slow.append((worst, os.path.relpath(f, root)))
    if rows:
        progress(_line(f"{min(n_audio, len(audio))} music files, 1 MB reads", rows))
    anlz = []
    for dirpath, _, files in os.walk(os.path.join(root, "PIONEER", "USBANLZ")):
        anlz += [os.path.join(dirpath, f) for f in files]
    lat = []
    for f in rng.sample(anlz, min(n_anlz, len(anlz))):
        t = time.perf_counter()
        try:
            _read(f, 1 << 20)
        except OSError as e:
            errors.append(f"{os.path.relpath(f, root)}: {e}")
            continue
        lat.append(time.perf_counter() - t)
    if lat:
        progress(f"{len(lat)} analysis files: per file median {statistics.median(lat) * 1000:.2f} ms, slowest "
                 f"{max(lat) * 1000:.1f} ms, over 100 ms: {sum(1 for x in lat if x > 0.1)}")
    progress(f"read errors: {len(errors)}" + "".join(f"\n  {e}" for e in errors[:10]))
    for worst, f in sorted(slow, reverse=True)[:10]:
        progress(f"  slow read {worst * 1000:.0f} ms: {f}")
    if sys.platform != "win32":
        progress("(not Windows: the system cache may have served some reads - eject and re-insert for real numbers)")
    return not errors
