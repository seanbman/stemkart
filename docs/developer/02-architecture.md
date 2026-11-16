# 2. Architecture

[← Development setup](01-development-setup.md) · [Contents](README.md) · [Next: Testing →](03-testing.md)

## 2.1 Application shell

`main.py` creates the `QApplication`, applies the theme, loads the bundled font and icon resources, and constructs the main `AudioSplitter` window. The main window owns the three application tabs:

1. **Samples** — Explorer/Library views, file selection, references, waveform editing, and recording.
2. **Stem Splitter** — Demucs source selection, progress, stem playback, and export.
3. **Effects** — selected-sample rendering, pitch/time controls, effect-chain ordering, presets, and WAV export.

## 2.2 Data and ownership

`SampleLibrary` owns the managed sample root and `library.json`. It stores managed-file records and reference paths without duplicating linked source audio. Reference roots are watched with `QFileSystemWatcher`; background workers reconcile changes into the metadata cache.

The application uses XDG locations through `app_data_dir()` and `app_config_dir()`:

- data: normally `~/.local/share/stemkart/`
- configuration: normally `~/.config/stemkart/`

## 2.3 Audio processing

The Effects tab renders asynchronously through `EffectRenderWorker`. `effects.py` applies the reorderable effects chain and invokes the Rubber Band CLI when pitch or speed differs from the defaults. Octave and Key are combined into total semitones; Speed is passed independently as tempo/time-stretch.

Demucs splitting runs through a worker thread and invokes the Python Demucs module using the active interpreter. Generated stems are temporary until an export action persists them.

## 2.4 UI conventions

The project favors native Qt widgets and small, explicit layouts. Keep the existing black/white/orange visual language, preserve fixed geometry where a control contract depends on it, and avoid introducing broad application-wide repolishing that would make theme changes slow for large file lists.

[← Development setup](01-development-setup.md) · [Contents](README.md) · [Next: Testing →](03-testing.md)
