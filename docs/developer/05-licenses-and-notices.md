# 5. Licenses and third-party notices

[← Packaging and release](04-packaging.md) · [Contents](README.md) · [Developer documentation](README.md)

## 5.1 StemKart

StemKart is intended to be distributed under the GNU General Public License, version 3. The repository should include the complete GPLv3 text as `LICENSE`, and source or binary releases should make that license available to recipients.

## 5.2 Python dependencies

Record the licenses and required notices for PyQt6, Demucs, NumPy, PyTorch, and their transitive dependencies in the release process. The exact notice set should be generated or reviewed from the versions actually shipped.

## 5.3 FFmpeg

FFmpeg's obligations depend on the specific build configuration and enabled components. A bundled build must ship the corresponding license text and notices, preserve required attribution, and make the build configuration identifiable. Do not assume that every FFmpeg binary has the same licensing mode.

## 5.4 Rubber Band

Rubber Band has separate licensing terms for its library/CLI and for different distribution scenarios. A bundled binary must be checked against the version and build being shipped, with its required license text and notices included in the package.

## 5.5 Demucs model weights

Demucs source code and pretrained model weights are separate licensing questions. Do not bundle Meta's official pretrained weights in a commercial release until their distribution terms and the training-data restrictions have been reviewed and documented. A release should state clearly whether model weights are downloaded by the user at runtime or included in the artifact.

## 5.6 Release discipline

Keep third-party notices beside the release packaging configuration, not only in a developer's local environment. When a dependency or bundled binary changes, refresh the notice inventory and rerun the clean-machine packaging checklist.

[← Packaging and release](04-packaging.md) · [Contents](README.md) · [Developer documentation](README.md)
