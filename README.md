# Stem Splitter

A small PyQt6 desktop app for splitting one music track into four WAV stems with Demucs:

- vocals
- drums
- bass
- other

The app has playback for every stem and three export choices: save one stem, put every stem directly into an existing folder, or create a new named folder containing every stem.

## Setup

FFmpeg and Python 3.10+ are required. The setup below uses the CPU-only PyTorch wheel so it does not install CUDA/NVIDIA packages:

```bash
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Run it with:

```bash
.venv/bin/python main.py
```

The first split downloads the Demucs model and may take a few minutes. Generated stems are held in a temporary directory until the app closes; use one of the export buttons to keep them.

## Notes

The window is intentionally fixed-size and uses no scrollable panels. The default model is Demucs `htdemucs`, which produces the four stems above.
