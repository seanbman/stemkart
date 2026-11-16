# 3. Testing

[← Architecture](02-architecture.md) · [Contents](README.md) · [Next: Packaging and release →](04-packaging.md)

## 3.1 Run the automated tests

Run the effects tests through the project environment:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

The Rubber Band integration test is skipped when the `rubberband` executable is not installed. Install it when testing pitch and speed processing end to end.

## 3.2 Static checks

Before submitting a change, compile the Python modules and inspect the working tree:

```bash
.venv/bin/python -m py_compile main.py effects.py tests/test_effects.py
git diff --check
git status --short
```

## 3.3 Manual smoke test

For UI changes, verify at minimum:

1. The application starts from `main.py`.
2. Samples can be selected and loaded into the waveform editor.
3. The three tabs switch without a visible crash or stale selection.
4. Playback, trim, and undo/redo work on a short WAV.
5. Effects render at default and non-default settings.
6. A preset can be saved, loaded, and deleted.
7. A stem split can be started and exported when Demucs is available.
8. The source desktop launcher starts the same checkout.

Do not treat a passing unit test as visual proof of layout, focus, hover, or theme behavior; inspect the live Qt application for those changes.

## 3.4 Documentation checks

The repository documentation is intentionally plain Markdown. Validate that every relative Markdown link points to a file:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import re

root = Path.cwd()
errors = []
for document in root.rglob("*.md"):
    if ".git" in document.parts:
        continue
    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]+)?\)", document.read_text(encoding="utf-8")):
        if "://" in target or target.startswith("mailto:"):
            continue
        if not (document.parent / target).resolve().exists():
            errors.append(f"{document}: {target}")
if errors:
    raise SystemExit("Broken documentation links:\n" + "\n".join(errors))
print("Documentation links OK")
PY
```

[← Architecture](02-architecture.md) · [Contents](README.md) · [Next: Packaging and release →](04-packaging.md)
