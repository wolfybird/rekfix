# October 2026: cloning a stick without rekordbox, and a stick that listed files inconsistently

**Goal:** a second stick, an exact copy of the main one, to keep on a keychain.

A rekordbox export to the second stick was ruled out, because it would:
- lose what exists only on the main stick: deck histories, cues set on the decks, hand repairs
  ([merged playlists](2026-10-merged-playlists.md));
- risk the merge problem again;
- take hours.

## The snapshot-and-manifest approach

1. **Copy the main stick's `PIONEER` folder** to the computer: 99,654 files, 6 GB of databases
   and analysis files. Verify it: same file count and sizes, databases byte-identical, plus 300
   random analysis files.
2. **Map every audio file** on the stick (13,756 files, 224 GB) to the identical copy in the
   music library: same size and date, with 40 random pairs checked byte for byte. Save the list.
3. **Build the copy** from those two: the `PIONEER` snapshot plus the audio from the library.
   No rekordbox and no export; the result is a byte-for-byte clone.
4. **Read every file back** from the new stick and compare it with its source.

The snapshot doubles as the main stick's restore point. Refreshing it later is quick, because
only changed files copy.

## The second stick misbehaved

The intended second stick was an older one. Listing the same folder six times, two MP3s showed
up in the first two listings and in none of the other four, with nothing writing to the stick.
`chkdsk` found nothing.

**Treat that as a failing stick.** A clean `chkdsk` doesn't vouch for the flash or the controller
(see the [July case](2026-07-database-bitrot.md)). The plan was to read back every file after
building the copy, and to replace the stick at the first sign of trouble.

## Lessons

- **A clone beats an export** when the stick holds anything rekordbox doesn't.
- **Keep the snapshot's file list** (path, size, date). It says what each file on the stick is,
  and where its source lives.
- **Unstable directory listings** are a hardware warning, even when every check passes.
