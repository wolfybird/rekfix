"""rekfix rebuild: recover a stick's library - even a damaged one - as a rekordbox XML file.

What it gathers, and from where:
- export.pdb, read normally or with the recovery read (scan): each track's title, artist,
  album, genre, label, key, BPM, comment, rating, colour, play count and date added; the
  playlists and folders in order; the play histories the decks saved.
- The analysis files (PIONEER/USBANLZ): beat grids, hot cues, memory cues and loops, with their
  names and colours, including the ones set on the decks. Each analysis file names its audio
  file, so analysis is matched to its track even when the database row, or the whole database,
  is damaged.
- The audio files: where each track's file actually is, on the stick or in a copy of it, matched
  by path, then name, then exact size, then a shortened name (rekordbox shortens long names on
  sticks).

Import the XML into rekordbox, check it, then export a fresh stick. Play histories come in as
playlists, in a folder of their own; rekordbox can't import them as histories.
"""
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from urllib.parse import quote

from . import __version__, anlz, check, names
from .pdb import Pdb

AUDIO = {".mp3": "MP3 File", ".wav": "WAV File", ".aif": "AIFF File", ".aiff": "AIFF File",
         ".flac": "FLAC File", ".m4a": "M4A File", ".mp4": "M4A File", ".aac": "AAC File",
         ".alac": "ALAC File", ".ogg": "OGG File"}
COLOURS = {"pink": "0xFF007F", "red": "0xFF0000", "orange": "0xFFA500", "yellow": "0xFFFF00",
           "green": "0x00FF00", "aqua": "0x25FDE9", "blue": "0x0000FF", "purple": "0x660099"}
SKIP_DIRS = {"pioneer", "system volume information", ".spotlight-v100", ".fseventsd", ".trashes",
             "$recycle.bin"}
HISTORY_FOLDER = "Play histories (from the stick)"


def xml_safe(text):
    """Text without the control characters XML 1.0 forbids (damaged strings carry them)."""
    return "".join(c for c in (text or "") if c in "\t\n\r" or ord(c) >= 0x20).strip()


# ------------------------------------------------------------ the audio files
def _walk(root):
    """(path, size) of every audio file under root. Folders are listed by rekfix and entered
    through short names on Windows, so a look-alike folder name can't hide files (names.py); the
    paths given are the real names, which is what rekordbox's XML needs."""
    for rel, e in names.walk(root, skip=SKIP_DIRS, skip_anywhere=True):
        if os.path.splitext(rel)[1].lower() in AUDIO:
            yield os.path.join(root, rel), e.size


def stick_keys(path):
    """Lower-case lookup keys for a database path ("/Contents/A/b.mp3"): as is, and without its
    first folder, so a copy of just the Contents folder matches too."""
    parts = [p for p in path.replace("\\", "/").lower().split("/") if p]
    keys = ["/" + "/".join(parts)]
    if len(parts) > 1:
        keys.append("/" + "/".join(parts[1:]))
    return keys


class AudioIndex:
    """Every audio file under the given folders, findable by stick path, name and size."""

    def __init__(self, roots):
        self.by_key, self.by_name, self.by_size, self.files = {}, defaultdict(list), defaultdict(list), []
        self.size_of, self.stick_path, self.claimed = {}, {}, set()
        for root in roots:
            for path, size in _walk(root):
                rel = "/" + os.path.relpath(path, root).replace("\\", "/")
                self.by_key.setdefault(rel.lower(), path)
                self.by_name[os.path.basename(path).lower()].append(path)
                self.by_size[size].append(path)
                self.files.append((path, size, root))
                self.size_of[path] = size
                self.stick_path[path] = rel

    def find(self, db_path, size=0, name=""):
        """(file, how) for a track, or (None, None). Fuzzy matches never reuse a file."""
        for key in stick_keys(db_path) if db_path else ():
            if key in self.by_key:
                return self.by_key[key], "path"
        name = (name or os.path.basename(db_path.replace("\\", "/")) if db_path else name).lower()
        free = lambda paths: sorted(p for p in paths if p not in self.claimed)
        if name:
            cands = free(self.by_name.get(name, ()))
            same = [p for p in cands if size and self.size_of[p] == size]
            if same or len(cands) == 1:
                return (same or cands)[0], "name"
        if size:
            ext = os.path.splitext(name)[1]
            cands = free(p for p in self.by_size.get(size, ()) if not ext or p.lower().endswith(ext))
            stem = re.sub(r"-\d+$", "", os.path.splitext(name)[0])
            if len(stem) >= 4:
                pref = [p for p in cands if os.path.basename(p).lower().startswith(stem)]
                if len(pref) == 1:
                    return pref[0], "name start"
            if len(cands) == 1:                         # last resort: the only free file of that size
                return cands[0], "size"
        return None, None


# ------------------------------------------------------------ the rebuilt library
@dataclass
class Item:
    xml_id: int
    file: str
    how: str                        # path / name / size / name start / audio only
    track: object = None            # the database's Track, if it lists this file
    analysis: object = None         # anlz.Analysis, if found
    analysis_how: str = ""          # "database" / "analysis path" / ""


@dataclass
class Node:
    name: str
    folder: bool
    children: list = field(default_factory=list)    # Nodes (folders)
    ids: list = field(default_factory=list)          # xml ids (playlists)


@dataclass
class Rebuilt:
    items: list = field(default_factory=list)
    tree: list = field(default_factory=list)
    names: dict = field(default_factory=dict)
    stats: Counter = field(default_factory=Counter)
    missing: list = field(default_factory=list)      # database tracks whose audio wasn't found
    fuzzy: list = field(default_factory=list)        # (track, file, how) found by size / name start
    extra: list = field(default_factory=list)        # audio files the database doesn't list
    problems: list = field(default_factory=list)


def _analysis_for(source, track, index_holder, stick_path):
    """(Analysis, how) for a track: from the database's analysis path, checked against the path
    the analysis file itself names; else looked up by the track's path among all analysis files."""
    raw = track.strings.get("analyze_path", "") if track else ""
    m = re.search(r"ANLZ\d{4}\.(DAT|EXT)", raw, re.I)
    if m:
        dat = check.on_stick(source, raw[:m.start()] + "ANLZ0000.DAT")
        if os.path.exists(dat) or os.path.exists(dat[:-4] + ".EXT"):
            a = anlz.read(dat)
            if not stick_path or not a.path or a.path.lower() == stick_path.lower():
                return a, "database"
    if not stick_path:
        return None, ""
    if index_holder[0] is None:
        index_holder[0] = anlz.index_paths(os.path.join(source, "PIONEER", "USBANLZ"))
    for key in (stick_path.lower(),) + tuple(stick_keys(stick_path)):
        dat = index_holder[0].get(key) or index_holder[0].get("/contents" + key)
        if dat:
            return anlz.read(dat), "analysis path"
    return None, ""


def collect(source, music=(), pdb_path=None, scan=False, second_word=False, all_audio=False):
    """Gather everything recoverable. Reads only; writes nothing."""
    rb = Rebuilt()
    pdb_path = pdb_path or os.path.join(source, "PIONEER", "rekordbox", "export.pdb")
    pdb = Pdb.open(pdb_path, scan=scan, second_word=second_word) if os.path.exists(pdb_path) else None
    if pdb is None:
        rb.problems.append(f"no database at {pdb_path}: rebuilding from the audio and analysis files only")
        all_audio = True
    audio = AudioIndex(list(music) or [source])
    rb.stats["audio files"] = len(audio.files)
    anlz_index = [None]
    tracks = {}
    if pdb:
        for tid, rows in pdb.tracks().items():
            tracks[tid] = rows[0]
            if len(rows) > 1:
                rb.problems.append(f"track id {tid} is on {len(rows)} rows; the first is used")
        rb.names = {"artists": pdb.artists(), "albums": pdb.album_names(), "genres": pdb.names(1),
                    "labels": pdb.names(4), "keys": pdb.names(5), "colors": pdb.names(6)}
    rb.stats["database tracks"] = len(tracks)

    by_db_id = {}
    for tid, t in sorted(tracks.items()):
        damaged = any(e.startswith(("file_path", "filename")) for e in t.string_errors)
        rb.stats["rows with damaged text"] += bool(t.string_errors)
        path = "" if any(e.startswith("file_path") for e in t.string_errors) else t.path
        name = "" if any(e.startswith("filename") for e in t.string_errors) else t.strings.get("filename", "")
        f, how = audio.find(path, t.file_size, name)
        if f is None:
            rb.missing.append(t)
            continue
        audio.claimed.add(f)
        rb.stats[f"found by {how}"] += 1
        if how in ("size", "name start") or damaged:
            rb.fuzzy.append((t, f, how))
        stick_path = path or audio.stick_path[f]
        a, ahow = _analysis_for(source, t, anlz_index, stick_path)
        item = Item(tid, f, how, t, a, ahow)
        rb.items.append(item)
        by_db_id[tid] = item

    next_id = max(tracks, default=0) + 1
    for f, size, root in audio.files:
        if f in audio.claimed:
            continue
        rb.extra.append(f)
        if all_audio:
            a, ahow = _analysis_for(source, None, anlz_index, audio.stick_path[f])
            rb.items.append(Item(next_id, f, "audio only", None, a, ahow))
            audio.claimed.add(f)
            next_id += 1

    for it in rb.items:
        a = it.analysis
        rb.stats[f"analysis via {it.analysis_how}" if a else "no analysis"] += 1
        if not a:
            continue
        rb.stats["beat grids"] += bool(a.beats)
        for c in a.cues:
            rb.stats["hot cues" if c.hot else "memory cues"] += 1
            rb.stats["loops"] += c.is_loop
            rb.stats["named cues"] += bool(c.name)
        rb.stats["tracks with cues"] += bool(a.cues)
        rb.problems += [f"{os.path.basename(it.file)}: {p}" for p in a.problems]

    if pdb:
        rb.tree = _playlists(pdb, by_db_id, rb.stats)
        hist = _histories(pdb, by_db_id, rb.stats)
        if hist.children:
            rb.tree.append(hist)
    return rb


def _playlists(pdb, by_db_id, stats):
    tree = pdb.playlist_tree()
    entries = defaultdict(list)
    for pid, idx, tid, row in pdb.playlist_entries():
        entries[pid].append((idx, row.page, row.slot, tid))
    kids = defaultdict(list)
    for pid, (parent, sort, folder, name) in tree.items():
        kids[parent if parent in tree else 0].append((sort, name, pid))
    stats["playlist parents missing"] += sum(1 for p, *_ in tree.values() if p and p not in tree)

    def build(pid, seen):
        parent, sort, folder, name = tree[pid]
        node = Node(xml_safe(name) or f"(playlist {pid})", folder)
        if folder:
            for _, _, child in sorted(kids[pid]):
                if child not in seen:
                    node.children.append(build(child, seen | {child}))
            stats["folders"] += 1
        else:
            for *_, tid in sorted(entries[pid]):
                if tid in by_db_id:
                    node.ids.append(by_db_id[tid].xml_id)
                else:
                    stats["playlist entries dropped (no audio)"] += 1
            stats["playlists"] += 1
            stats["playlist entries"] += len(node.ids)
        return node
    return [build(pid, {pid}) for _, _, pid in sorted(kids[0])]


def _histories(pdb, by_db_id, stats):
    folder = Node(HISTORY_FOLDER, True)
    plays = defaultdict(list)
    for pid, idx, tid, row in pdb.history_entries():
        plays[pid].append((idx, row.page, row.slot, tid))
    for hid, name in sorted(pdb.history_playlists().items(), key=lambda kv: (kv[1], kv[0])):
        node = Node(xml_safe(name) or f"HISTORY {hid}", False)
        for *_, tid in sorted(plays[hid]):
            if tid in by_db_id:
                node.ids.append(by_db_id[tid].xml_id)
            else:
                stats["history plays dropped (no audio)"] += 1
        folder.children.append(node)
        stats["play histories"] += 1
        stats["history plays"] += len(node.ids)
    return folder


# ------------------------------------------------------------ the XML
def tempo_segments(beats, most=300):
    """rekordbox XML TEMPO anchors from a beat grid: the first beat, then one wherever the tempo
    changes by 0.1 BPM or more. A grid that changes more often than `most` times (a live
    recording) keeps just the first anchor."""
    out, current = [], None
    for beat, tempo, ms in beats:
        if current is None or abs(tempo - current) >= 10:
            out.append((ms / 1000, tempo / 100, beat if 1 <= beat <= 4 else 1))
            current = tempo
    return out[:1] if len(out) > most else out


def location(path):
    p = os.path.abspath(path).replace("\\", "/")
    return "file://localhost" + quote(p if p.startswith("/") else "/" + p, safe="/:")


def _track_element(coll, it, names):
    t, a = it.track, it.analysis
    get = lambda key, i: xml_safe(names.get(key, {}).get(i, "")) if i else ""
    s = (lambda k: xml_safe(t.strings.get(k, ""))) if t else (lambda k: "")
    bpm = (a.bpm if a and a.bpm else 0) or (t.tempo / 100 if t and t.tempo else 0)
    bpm = bpm if 30 <= bpm <= 300 else 0
    ext = os.path.splitext(it.file)[1].lower()
    title = s("title") or os.path.splitext(os.path.basename(it.file))[0]
    attrs = {
        "TrackID": it.xml_id, "Name": xml_safe(title), "Artist": get("artists", t and t.artist_id),
        "Composer": get("artists", t and t.composer_id), "Album": get("albums", t and t.album_id),
        "Grouping": "", "Genre": get("genres", t and t.genre_id), "Kind": AUDIO.get(ext, ext[1:].upper() + " File"),
        "Size": os.path.getsize(it.file) if os.path.exists(it.file) else (t.file_size if t else 0),
        "TotalTime": t.duration if t else 0, "DiscNumber": t.disc_number if t else 0,
        "TrackNumber": t.track_number if t else 0, "Year": t.year if t else 0, "AverageBpm": f"{bpm:.2f}",
        "DateAdded": s("date_added"), "BitRate": t.bitrate if t else 0, "SampleRate": t.sample_rate if t else 0,
        "Comments": s("comment"), "PlayCount": t.play_count if t else 0,
        "Rating": min(t.rating, 5) * 51 if t else 0, "Location": location(it.file),
        "Remixer": get("artists", t and t.remixer_id), "Tonality": get("keys", t and t.key_id),
        "Label": get("labels", t and t.label_id), "Mix": s("mix_name"),
    }
    colour = COLOURS.get(names.get("colors", {}).get(t.color_id, "").lower()) if t and t.color_id else None
    if colour:
        attrs["Colour"] = colour
    el = ET.SubElement(coll, "TRACK", {k: str(v) for k, v in attrs.items()})
    if a:
        for start, tempo, beat in tempo_segments(a.beats):
            ET.SubElement(el, "TEMPO", Inizio=f"{start:.3f}", Bpm=f"{tempo:.2f}", Metro="4/4", Battito=str(beat))
        for c in a.cues:
            mark = {"Name": xml_safe(c.name), "Type": "4" if c.is_loop else "0", "Start": f"{c.start_ms / 1000:.3f}"}
            if c.is_loop:
                mark["End"] = f"{c.end_ms / 1000:.3f}"
            mark["Num"] = str(c.hot - 1 if c.hot else -1)
            if c.hot and c.rgb:
                mark.update(Red=str(c.rgb[0]), Green=str(c.rgb[1]), Blue=str(c.rgb[2]))
            ET.SubElement(el, "POSITION_MARK", mark)


def _node_element(parent, node):
    if node.folder:
        el = ET.SubElement(parent, "NODE", Type="0", Name=node.name, Count=str(len(node.children)))
        for child in node.children:
            _node_element(el, child)
    else:
        el = ET.SubElement(parent, "NODE", Name=node.name, Type="1", KeyType="0", Entries=str(len(node.ids)))
        for i in node.ids:
            ET.SubElement(el, "TRACK", Key=str(i))


def write_xml(rb, out):
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="rekfix", Version=__version__, Company="")
    coll = ET.SubElement(root, "COLLECTION", Entries=str(len(rb.items)))
    for it in rb.items:
        _track_element(coll, it, rb.names)
    top = ET.SubElement(ET.SubElement(root, "PLAYLISTS"), "NODE", Type="0", Name="ROOT", Count=str(len(rb.tree)))
    for node in rb.tree:
        _node_element(top, node)
    ET.indent(root, space=" ")
    tmp = out + ".rekfix-tmp"
    ET.ElementTree(root).write(tmp, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp, out)


def verify_xml(out, rb):
    """Read the written file back: it parses, every track is there, every playlist entry points
    at a track. Returns a list of problems."""
    problems = []
    try:
        root = ET.parse(out).getroot()
    except ET.ParseError as e:
        return [f"the XML doesn't parse: {e}"]
    ids = {t.get("TrackID") for t in root.find("COLLECTION")}
    if len(ids) != len(rb.items):
        problems.append(f"{len(ids)} tracks in the file, {len(rb.items)} expected")
    dangling = sum(1 for t in root.iter("TRACK") if t.get("Key") is not None and t.get("Key") not in ids)
    if dangling:
        problems.append(f"{dangling} playlist entries point at no track")
    return problems


def report_lines(rb):
    s = rb.stats
    added = sum(1 for i in rb.items if i.how == "audio only")
    lines = [f"{s['database tracks']:,} tracks in the database; {s['audio files']:,} audio files found",
             f"  audio matched by path {s['found by path']:,}, name {s['found by name']:,}, size {s['found by size']:,}, "
             f"name start {s['found by name start']:,}; not found {len(rb.missing):,}",
             f"  audio files the database doesn't list: {len(rb.extra):,}"
             + (f" (added to the XML: {added:,})" if added else " (not added; --all-audio adds them)" if rb.extra else ""),
             f"analysis: {s['analysis via database']:,} via the database, {s['analysis via analysis path']:,} via the "
             f"path in the analysis file, {s['no analysis']:,} without",
             f"  beat grids {s['beat grids']:,}; cues on {s['tracks with cues']:,} tracks: hot {s['hot cues']:,}, "
             f"memory {s['memory cues']:,}, of them loops {s['loops']:,}, named {s['named cues']:,}",
             f"playlists {s['playlists']:,} in {s['folders']:,} folders, {s['playlist entries']:,} entries"
             + (f" ({s['playlist entries dropped (no audio)']:,} dropped: no audio)" if s['playlist entries dropped (no audio)'] else ""),
             f"play histories {s['play histories']:,} with {s['history plays']:,} plays"
             + (f" ({s['history plays dropped (no audio)']:,} dropped: no audio)" if s['history plays dropped (no audio)'] else "")]
    if s["rows with damaged text"]:
        lines.append(f"database rows with damaged text: {s['rows with damaged text']:,}")
    return lines


def write_report(rb, path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("rekfix rebuild report\n\n" + "\n".join(report_lines(rb)) + "\n")
        artists = rb.names.get("artists", {})
        who = lambda t: f"{artists.get(t.artist_id, '')} - {t.title}".strip(" -")
        fh.write(f"\nTracks whose audio wasn't found ({len(rb.missing)}):\n")
        fh.writelines(f"  {who(t)}  [{t.path}]\n" for t in rb.missing)
        fh.write(f"\nMatched by size or the start of the name - worth a look ({len(rb.fuzzy)}):\n")
        fh.writelines(f"  {who(t)}  [{t.path}] -> {f} ({how})\n" for t, f, how in rb.fuzzy)
        fh.write(f"\nAudio files the database doesn't list ({len(rb.extra)}):\n")
        fh.writelines(f"  {f}\n" for f in rb.extra)
        fh.write(f"\nProblems ({len(rb.problems)}):\n")
        fh.writelines(f"  {p}\n" for p in rb.problems)
