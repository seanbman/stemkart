# 3. Library and References

[← Samples and the Explorer](02-samples.md) · [Contents](README.md) · [Next: Stem Splitter →](04-stem-splitter.md)

## 3.1 Managed library folders

StemKart's local library normally contains:

- `imported/` — files copied into the library.
- `trimmed/` — WAV files exported from the waveform editor or Effects tab.
- `recorded/` — recordings made in StemKart.

You can create and rename folders from the Library view. The protected top-level managed folders cannot be deleted accidentally. User-created empty folders can be deleted.

## 3.2 References

The **References** tree contains paths linked from outside the managed library. StemKart stores a dictionary of file paths in `library.json`; it does not make duplicate audio copies for a reference.

After a folder is linked, a background watcher and synchronizer update the reference records when files are added, removed, or renamed. The Library view can show a path optimistically while the background scan reconciles the cached records.

Use **Refresh** when you want to request an immediate view refresh. A refresh should update the current list from the existing reference cache and filesystem state; it should not require a full rescan of every referenced directory.

## 3.3 File actions

In the Library view:

- **New Folder** creates a user-managed folder.
- **Rename** changes a user-managed folder name and updates paths in the metadata.
- **Delete** removes an eligible empty user-managed folder.
- **Delete Files** removes selected managed library files.
- **Refresh** refreshes the visible tree and current file list.

Deleting a reference from the Library view does not delete the original source file.

## 3.4 Keeping the library current

Leave StemKart running while linked folders change if you want live updates. If an external application moves a folder outside the watched path, link the new folder or use Refresh after the move. Source files remain at their original paths; only the metadata cache is updated.

[← Samples and the Explorer](02-samples.md) · [Contents](README.md) · [Next: Stem Splitter →](04-stem-splitter.md)
