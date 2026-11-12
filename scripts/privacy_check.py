"""Scan the repo for personal details before a commit: names, drive labels, paths, serials.

The words to look for are kept in .privacy-words (one per line, case-insensitive, # for
comments), which is git-ignored so the list itself never gets published. Exit 1 on a hit.

  python scripts/privacy_check.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def words():
    path = os.path.join(ROOT, ".privacy-words")
    if not os.path.exists(path):
        raise SystemExit(".privacy-words is missing: create it (one word or phrase per line) first")
    with open(path, encoding="utf-8") as fh:
        return [w.strip() for w in fh if w.strip() and not w.startswith("#")]


def tracked_and_new():
    r = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    return [f for f in r.stdout.splitlines() if f]


def main():
    pattern = re.compile("|".join(re.escape(w) for w in words()), re.I)
    hits = []
    for rel in tracked_and_new():
        if pattern.search(rel):
            hits.append(f"{rel}: in the file name")
        try:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    m = pattern.search(line)
                    if m:
                        hits.append(f"{rel}:{n}: {m.group(0)!r}")
        except (UnicodeDecodeError, OSError):
            continue
    for h in hits:
        print("  !", h)
    print("nothing personal found" if not hits else f"{len(hits)} hit(s) - fix them before committing")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
