# StemKart bundled runtime notices

This release package includes executable copies of FFmpeg and the Rubber Band
CLI under `/usr/lib/stemkart/bin/`. The exact binary versions and license
files shipped in a package are recorded next to the package build output.

The package also contains the Python, PyQt6, NumPy, PyTorch, and Demucs
runtime collected by PyInstaller. Their license and notice requirements remain
applicable to the distributed binary.

The package includes StemKart's `LICENSE` file and the copyright/license files
from the FFmpeg and Rubber Band packages available in the build environment.
Do not remove those files from a release artifact.
