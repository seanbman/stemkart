# 1. Development setup

[← Developer documentation](README.md) · [Contents](README.md) · [Next: Architecture →](02-architecture.md)

## 1.1 Checkout and environment

StemKart is a PyQt6 application. Use Python 3.10 or newer and keep the project's dependencies isolated in `.venv`:

```bash
git clone https://github.com/seanbman/stemkart.git
cd stemkart
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Run the app with:

```bash
.venv/bin/python main.py
```

The `uv` workflow is intentional: it also supports environments where `pip` is not installed inside the virtual environment.

## 1.2 External tools

The application expects these tools for the associated features:

- FFmpeg for audio playback/decoding paths.
- Rubber Band CLI for independent pitch and time processing.
- PipeWire's `pw-dump`, `pw-record`, and `pw-link` for system/application recording.

## 1.3 Useful project files

- `main.py` — application window, Qt widgets, library, scanning, recording, waveform, and tab integration.
- `effects.py` — dependency-light effects chain plus Rubber Band process wrapper.
- `tests/test_effects.py` — effects-engine unit tests.
- `packaging/linux/` — desktop entry, icon assets, and the source-checkout desktop installer.
- `requirements.txt` — runtime Python dependencies.
- `pyproject.toml` — package metadata, entry point, and resource declarations.

[← Developer documentation](README.md) · [Contents](README.md) · [Next: Architecture →](02-architecture.md)
