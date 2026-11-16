# 7. Troubleshooting

[← Recording](06-recording.md) · [Contents](README.md) · [User guide index](README.md)

## 7.1 The application does not appear in the menu

For a source checkout, run:

```bash
./packaging/linux/install_desktop.sh
```

Then launch StemKart from the menu or run the `.venv/bin/python main.py` command directly. The source launcher requires the checkout's `.venv` to remain in place.

## 7.2 The Effects tab cannot change pitch or speed

Confirm that Rubber Band is installed and discoverable:

```bash
command -v rubberband
```

The dials remain usable at their defaults, but non-default pitch or speed processing requires Rubber Band.

## 7.3 Splitting fails or takes a long time

The first Demucs split downloads the model. Confirm that the Python environment has the requirements installed and that the source track is readable. CPU splitting can take several minutes for long files.

## 7.4 Recording sources are missing

Confirm that PipeWire is running and that `pw-dump`, `pw-record`, and `pw-link` are available. For application capture, make sure the application is actively producing audio before opening StemKart's source list.

## 7.5 References look stale

Wait for the background reference update to finish, then use **Refresh** in the Library view. Check that the linked directory still exists and that the files have supported audio extensions. Linking records paths; it does not copy files into the managed library.

## 7.6 Where to report a bug

Include the operating system, installation method, exact action that failed, and any terminal output. Do not attach private audio unless it is safe to share; a short reproduction using a generated or public WAV is preferable.

[← Recording](06-recording.md) · [Contents](README.md) · [User guide index](README.md)
