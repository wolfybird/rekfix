"""Folder and file names Windows can mix up on a FAT stick, and how to reach the right one.

Windows' FAT driver compares names through the computer's OEM code page (437 on US systems),
and a letter that page lacks is replaced by a look-alike: "ł" becomes "l", so "Michał Nowak"
and "Michal Nowak" both read as MICHAL NOWAK. When a stick has both side by side, a path to
one can land in the other, depending on which one Windows has open or cached. Listings seem to
change, files seem to come and go, and copies (Explorer, backup tools) can take one folder's
files twice and miss the other's. Names with letters the page can't show at all (Japanese,
Cyrillic...) aren't affected: Windows looks those up by their real name.

Each entry's 8.3 short name always reaches the right one. So on Windows, rekfix lists folders
itself and opens files through short names (`walk`, `Resolver`), and reports the look-alikes
(`lookalikes`)."""
import os
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

WINDOWS = sys.platform == "win32"

# best-fit replacements Windows uses for letters code page 437 lacks that don't decompose into a
# base letter; only for the approximation used on other systems
_STROKES = {"Ł": "L", "Đ": "D", "Ø": "O", "Ħ": "H", "Ŧ": "T", "Ŀ": "L", "Ƚ": "L", "Ɨ": "I", "Ƶ": "Z"}


def _upper(text):
    """Upper case one letter at a time, as Windows does ("ß" stays "ß")."""
    return "".join(u if len(u := c.upper()) == 1 else c for c in text)


if WINDOWS:
    import ctypes
    import ctypes.wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.WideCharToMultiByte.argtypes = [wt.UINT, wt.DWORD, wt.LPCWSTR, ctypes.c_int, ctypes.c_char_p,
                                         ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(wt.BOOL)]

    class _FindData(ctypes.Structure):
        _fields_ = [("attrs", wt.DWORD), ("created", wt.FILETIME), ("accessed", wt.FILETIME),
                    ("written", wt.FILETIME), ("size_high", wt.DWORD), ("size_low", wt.DWORD),
                    ("reserved0", wt.DWORD), ("reserved1", wt.DWORD), ("name", wt.WCHAR * 260),
                    ("short", wt.WCHAR * 14)]

    _k32.FindFirstFileW.restype = wt.HANDLE
    _k32.FindFirstFileW.argtypes = [wt.LPCWSTR, ctypes.POINTER(_FindData)]
    _k32.FindNextFileW.argtypes = [wt.HANDLE, ctypes.POINTER(_FindData)]
    _k32.FindClose.argtypes = [wt.HANDLE]
    _INVALID = wt.HANDLE(-1).value
    _CP_OEMCP = 1

    def _key_windows(name):
        """The name as Windows' FAT driver compares it: upper case, then the OEM code page with
        best fit. None when a letter has no stand-in at all: Windows then compares the real name."""
        text = _upper(name)
        buf = ctypes.create_string_buffer(len(text) * 4 + 8)
        used_default = wt.BOOL(0)
        n = _k32.WideCharToMultiByte(_CP_OEMCP, 0, text, len(text), buf, len(buf), None, ctypes.byref(used_default))
        return None if used_default.value or not n else buf.raw[:n]


def _key_437(name):
    """An approximation of Windows on code page 437, for other systems: letters 437 has stay,
    others become their base letter, and a name with any letter that has neither gives None."""
    out = []
    for c in _upper(name):
        try:
            c.encode("cp437")
            out.append(c)
            continue
        except UnicodeEncodeError:
            pass
        base = unicodedata.normalize("NFKD", c)[:1]
        if base and base.isascii() and base != c:
            out.append(base)
        elif c in _STROKES:
            out.append(_STROKES[c])
        else:
            return None
    return "".join(out).encode("cp437")


key = _key_windows if WINDOWS else _key_437


def lookalikes(names):
    """Groups of different names (from one folder) that Windows can mix up."""
    groups = defaultdict(set)
    for n in names:
        k = key(n)
        if k is not None:
            groups[k].add(n)
    return [sorted(g) for g in groups.values() if len(g) > 1]


def lookalikes_in(paths):
    """[(folder, [names])] for look-alike siblings anywhere in a list of relative file paths."""
    children = defaultdict(set)
    for p in paths:
        parts = [x for x in p.replace("\\", "/").split("/") if x]
        for i in range(len(parts)):
            children["/".join(parts[:i])].add(parts[i])
    return [(folder, g) for folder, kids in sorted(children.items()) for g in lookalikes(kids)]


@dataclass
class Entry:
    name: str           # the real (long) name
    path: str           # a path that reaches exactly this entry: through 8.3 short names on Windows
    is_dir: bool
    size: int
    mtime: float


def listdir(folder):
    """[Entry] for a folder's contents, by its own listing. Raises OSError if it can't be listed."""
    if not WINDOWS:
        out = []
        with os.scandir(folder) as it:
            for e in it:
                if e.is_symlink():
                    continue
                st = e.stat(follow_symlinks=False)
                out.append(Entry(e.name, e.path, e.is_dir(follow_symlinks=False), st.st_size, st.st_mtime))
        return out
    data = _FindData()
    h = _k32.FindFirstFileW(os.path.join(folder, "*"), ctypes.byref(data))
    if h == _INVALID:
        err = ctypes.get_last_error()
        if err == 2:                                    # an empty folder (only on a drive root)
            return []
        raise OSError(err, f"can't list {folder}", folder)
    out = []
    try:
        while True:
            name = data.name
            if name not in (".", "..") and not data.attrs & 0x400:         # 0x400: a link
                ft = data.written.dwHighDateTime << 32 | data.written.dwLowDateTime
                out.append(Entry(name, os.path.join(folder, data.short or name), bool(data.attrs & 0x10),
                                 data.size_high << 32 | data.size_low, ft / 1e7 - 11644473600))
            if not _k32.FindNextFileW(h, ctypes.byref(data)):
                break
    finally:
        _k32.FindClose(h)
    return out


def walk(root, skip=(), groups=None, skip_anywhere=False):
    """(relative path by real names, Entry) for every file under root, folders listed by rekfix
    and entered through their short names, so no look-alike can send it into the wrong one.
    skip: folder names to leave out, at the top only unless skip_anywhere. groups: a list to
    collect look-alikes into, as (folder, [names]). Folders that can't be listed are passed over."""
    skip = {s.lower() for s in skip}
    stack = [("", root)]
    while stack:
        rel, real = stack.pop()
        try:
            entries = listdir(real)
        except OSError:
            continue
        if groups is not None:
            groups += [(rel, g) for g in lookalikes(e.name for e in entries)]
        for e in sorted(entries, key=lambda e: e.name.lower(), reverse=True):
            r = os.path.join(rel, e.name) if rel else e.name
            if e.is_dir:
                if (rel and not skip_anywhere) or e.name.lower() not in skip:
                    stack.append((r, e.path))
            else:
                yield r, e


class Resolver:
    """Finds files by the paths a database stores ("/Contents/A/b.mp3"), matching each folder's
    own listing (in any case, as FAT does), so a look-alike can't stand in for the real one.
    Each folder is listed once."""

    def __init__(self, root):
        self.root = root
        self._listed = {}           # real path of a folder -> (its path by real names, {upper name: Entry})

    def _folder(self, real, rel):
        got = self._listed.get(real)
        if got is None:
            try:
                entries = listdir(real)
            except OSError:
                entries = []
            got = self._listed[real] = (rel, {_upper(e.name): e for e in entries})
        return got[1]

    def find(self, path):
        """The Entry a stick path names, or None if it isn't there."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        real, rel, entry = self.root, "", None
        for i, part in enumerate(parts):
            entry = self._folder(real, rel).get(_upper(part))
            if entry is None or (i < len(parts) - 1 and not entry.is_dir):
                return None
            real, rel = entry.path, os.path.join(rel, entry.name) if rel else entry.name
        return entry

    def place(self, path):
        """A path to write a file at, given its stick path: existing folders are reached exactly,
        missing ones are created. On Windows it refuses (ValueError) to create a folder or a file
        next to a look-alike of it, since Windows could put it in the look-alike instead."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        real, rel = self.root, ""
        for i, part in enumerate(parts):
            listing = self._folder(real, rel)
            entry = listing.get(_upper(part))
            last = i == len(parts) - 1
            if entry is None and WINDOWS and key(part) is not None:
                twin = next((e.name for e in listing.values() if key(e.name) == key(part)), None)
                if twin:
                    raise ValueError(f'"{os.path.join(rel, part)}" would sit next to "{twin}", which Windows can\'t '
                                     f'tell apart from it')
            if last:
                return entry.path if entry else os.path.join(real, part)
            if entry is None:
                os.mkdir(os.path.join(real, part))
                del self._listed[real]                          # list it again, with the new folder
                entry = self._folder(real, rel).get(_upper(part))
                if entry is None:
                    raise OSError(f"made {os.path.join(rel, part)} but can't find it")
            elif not entry.is_dir:
                raise ValueError(f"{os.path.join(rel, entry.name)} is a file, not a folder")
            real, rel = entry.path, os.path.join(rel, entry.name) if rel else entry.name

    def lookalikes(self):
        """(folder, [names]) for look-alikes in the folders looked at so far."""
        out = []
        for rel, entries in sorted(self._listed.values(), key=lambda v: v[0]):
            out += [(rel, g) for g in lookalikes(e.name for e in entries.values())]
        return out


def describe(groups, limit=2):
    """'"Contents/Michał Nowak" and "Contents/Michal Nowak"' for the first groups."""
    out = []
    for folder, names in groups[:limit]:
        out.append(" and ".join(f'"{os.path.join(folder, n) if folder else n}"'.replace("\\", "/") for n in names))
    return "; ".join(out) + (f" (and {len(groups) - limit} more)" if len(groups) > limit else "")
