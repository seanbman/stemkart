# 2. Samples and the Explorer

[← Installation and first launch](01-installation.md) · [Contents](README.md) · [Next: Library and References →](03-library.md)

## 2.1 Explorer and Library views

The **Samples** tab has two views:

- **Explorer** shows the current directory on disk.
- **Library** shows StemKart-managed folders and linked References.

Use the directory tree to choose a folder. The current path is shown above the file list. The directory search field filters the current directory without changing its location.

## 2.2 Scanning folders

Select **Scan subfolders** before scanning or linking a folder when audio files inside nested directories should be included. The scan runs in the background. The file count and References view update as the scan discovers changes.

StemKart ignores common macOS metadata artifacts such as `._filename` rather than showing them as playable audio files.

## 2.3 Loading a sample

Select an audio file in the Explorer. StemKart loads the selection into the waveform editor automatically without changing the file on disk or splitting it. The selected sample is also available to the Effects tab.

The waveform editor supports playback, zoom, trim markers, undo, and redo. Set a trim start or end from the current playback position, then export the trimmed result as a WAV file.

## 2.4 Playback

The transport controls provide skip-to-start, rewind, play, fast-forward, skip-to-end, stop, and pause. Starting playback again after reaching the end returns the playhead to the current start position.

## 2.5 Linking versus copying

- **Link to Library** adds references to the selected source files without copying them.
- **Copy to Library** copies selected files into the managed `imported` folder.
- **Link Folder to Library** registers a directory and tracks its audio files by path.
- **Copy Folder to Library** copies a folder's audio files into the managed library.

Use linking when the original location should remain authoritative. Use copying when the files should be owned by StemKart's local library.

[← Installation and first launch](01-installation.md) · [Contents](README.md) · [Next: Library and References →](03-library.md)
