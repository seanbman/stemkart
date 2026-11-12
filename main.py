from __future__ import annotations

import re
import os
import json
import signal
import selectors
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from PyQt6.QtCore import QFile, QDir, QIODevice, QThread, QTimer, QUrl, Qt, QSize, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFileSystemModel, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QStyle,
    QSizePolicy,
    QTabWidget,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices, QAudioOutput, QMediaPlayer


APP_NAME = "StemKart"
APP_SLUG = "stemkart"
STEMS = ("vocals", "drums", "bass", "other")
SUPPORTED_AUDIO = "Audio files (*.mp3 *.wav *.flac *.m4a *.ogg *.aac *.opus);;All files (*)"
SUPPORTED_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus"}


def resource_path(*parts: str) -> Path:
    """Return a path to a read-only resource shipped with the application."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    root = Path(frozen_root) if frozen_root else Path(__file__).resolve().parent
    return root.joinpath(*parts)


def _xdg_base(environment_name: str, fallback: str) -> Path:
    configured = os.environ.get(environment_name)
    return Path(configured).expanduser() if configured else Path.home() / fallback


def app_data_dir() -> Path:
    return _xdg_base("XDG_DATA_HOME", ".local/share") / APP_SLUG


def app_config_dir() -> Path:
    return _xdg_base("XDG_CONFIG_HOME", ".config") / APP_SLUG


def app_cache_dir() -> Path:
    return _xdg_base("XDG_CACHE_HOME", ".cache") / APP_SLUG


def bundled_icon(name: str) -> QIcon:
    return QIcon(str(resource_path("icons", name)))


def pretty_name(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()


def safe_folder_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]", "-", value).strip().strip(".")
    return value or "split-stems"


def parse_demucs_progress(message: str) -> tuple[int | None, str | None]:
    """Turn Demucs/tqdm output into a percentage and short Qt status."""
    text = " ".join(message.replace("\r", " ").split())
    if not text:
        return None, None

    ratio = re.search(r"(?P<done>\d+(?:\.\d+)?)\s*/\s*(?P<total>\d+(?:\.\d+)?)", text)
    if ratio:
        total = float(ratio.group("total"))
        if total > 0:
            percent = round(min(100.0, max(0.0, float(ratio.group("done")) / total * 100)))
            return percent, "Splitting track..."

    percent_match = re.search(r"(?P<percent>\d{1,3})\s*%", text)
    if percent_match:
        percent = min(100, int(percent_match.group("percent")))
        return percent, "Splitting track..."

    lowered = text.lower()
    if "separating track" in lowered:
        return None, "Separating track..."
    if "download" in lowered or "model" in lowered or "loading" in lowered:
        return None, "Preparing Demucs model..."
    return None, "Preparing split..."


def wav_availability(path: Path, threshold_dbfs: float = -60.0) -> str:
    """Return usable, empty, or unavailable for a Demucs PCM WAV."""
    try:
        with wave.open(str(path), "rb") as wav_file:
            sample_width = wav_file.getsampwidth()
            frame_count = wav_file.getnframes()
            if frame_count <= 0 or sample_width not in (1, 2, 3, 4):
                return "empty"

            peak = 0
            remaining = frame_count
            while remaining:
                frames = wav_file.readframes(min(4096, remaining))
                if not frames:
                    break
                if sample_width == 1:
                    samples = np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128
                    maximum = 127
                elif sample_width == 2:
                    samples = np.frombuffer(frames, dtype=np.int16)
                    maximum = 32767
                elif sample_width == 4:
                    samples = np.frombuffer(frames, dtype=np.int32)
                    maximum = 2147483647
                else:
                    raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
                    samples = (
                        raw[:, 0].astype(np.int32)
                        | (raw[:, 1].astype(np.int32) << 8)
                        | (raw[:, 2].astype(np.int32) << 16)
                    )
                    samples = np.where(samples & 0x800000, samples - 0x1000000, samples)
                    maximum = 8388607
                if samples.size:
                    peak = max(peak, int(np.max(np.abs(samples))))
                remaining -= len(frames) // sample_width

            if peak == 0:
                return "empty"
            peak_dbfs = 20 * np.log10(peak / maximum)
            return "empty" if peak_dbfs < threshold_dbfs else "usable"
    except (OSError, EOFError, wave.Error, ValueError):
        return "unavailable"


def format_time(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def is_audio_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES


class SampleLibrary:
    def __init__(self, legacy_app_dir: Path | None = None) -> None:
        self.root = app_data_dir() / "samples"
        self.legacy_root = (legacy_app_dir or Path(__file__).resolve().parent) / "samples"
        self._migrate_legacy_library()
        self.imported_dir = self.root / "imported"
        self.trimmed_dir = self.root / "trimmed"
        self.recorded_dir = self.root / "recorded"
        self.metadata_path = self.root / "library.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.imported_dir.mkdir(exist_ok=True)
        self.trimmed_dir.mkdir(exist_ok=True)
        self.recorded_dir.mkdir(exist_ok=True)
        self.data: dict[str, object] = {
            "entries": {},
            "last_directory": str(Path.home()),
            "scan_recursive": False,
        }
        self._load()
        self.sync_legacy_additions()

    def _migrate_legacy_library(self) -> None:
        """Copy the old project-local library into the local app-data directory once."""
        if self.root.exists() or not self.legacy_root.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            return
        if self.root.resolve() == self.legacy_root.resolve():
            return

        self.root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.legacy_root, self.root)
        metadata_path = self.root / "library.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(metadata, dict):
            return

        legacy_root = self.legacy_root.resolve()
        migrated_root = self.root.resolve()

        def rebase(value: object) -> object:
            if not isinstance(value, str) or not value:
                return value
            candidate = Path(value)
            try:
                relative = candidate.resolve().relative_to(legacy_root)
            except ValueError:
                return value
            return str(migrated_root / relative)

        entries = metadata.get("entries")
        if isinstance(entries, dict):
            for record in entries.values():
                if isinstance(record, dict):
                    record["path"] = rebase(record.get("path"))
                record["source"] = rebase(record.get("source"))
        temporary = metadata_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(metadata_path)

    def sync_legacy_additions(self) -> int:
        """Bring new source-tree folders/files into the local library without overwriting it."""
        if not self.legacy_root.is_dir() or self.legacy_root.resolve() == self.root.resolve():
            return 0

        copied = 0
        try:
            source_paths = sorted(self.legacy_root.rglob("*"), key=lambda path: len(path.parts))
        except OSError:
            return 0
        for source in source_paths:
            relative = source.relative_to(self.legacy_root)
            if relative.name == "library.json":
                continue
            destination = self.root / relative
            try:
                if source.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif source.is_file() and not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    copied += 1
            except OSError:
                continue
        return copied

    @property
    def entries(self) -> dict[str, dict[str, str]]:
        entries = self.data.get("entries", {})
        return entries if isinstance(entries, dict) else {}

    @property
    def last_directory(self) -> Path:
        value = self.data.get("last_directory")
        path = Path(value) if isinstance(value, str) else Path.home()
        return path if path.is_dir() else Path.home()

    @property
    def scan_recursive(self) -> bool:
        return bool(self.data.get("scan_recursive", False))

    def _load(self) -> None:
        try:
            loaded = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass

    def save(self) -> None:
        temporary = self.metadata_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.metadata_path)

    def set_last_directory(self, path: Path) -> None:
        if path.is_dir():
            self.data["last_directory"] = str(path.resolve())
            self.save()

    def set_scan_recursive(self, enabled: bool) -> None:
        self.data["scan_recursive"] = enabled
        self.save()

    def is_library_path(self, path: Path) -> bool:
        """Return whether a path is inside StemKart's local library root."""
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError:
            return False
        return True

    def protected_directories(self) -> set[Path]:
        return {self.imported_dir.resolve(), self.recorded_dir.resolve(), self.trimmed_dir.resolve()}

    def _record(self, path: Path, kind: str, source: Path | None = None) -> None:
        resolved = path.resolve()
        self.entries[str(resolved)] = {
            "path": str(resolved),
            "name": resolved.name,
            "kind": kind,
            "source": str(source.resolve()) if source else "",
        }

    def add_references(self, paths: list[Path]) -> int:
        count = 0
        for path in paths:
            if is_audio_file(path):
                key = str(path.resolve())
                if key not in self.entries:
                    count += 1
                self._record(path, "reference")
        self.save()
        return count

    def _unique_destination(self, directory: Path, name: str) -> Path:
        candidate = directory / name
        stem = candidate.stem
        suffix = candidate.suffix
        index = 2
        while candidate.exists():
            candidate = directory / f"{stem} ({index}){suffix}"
            index += 1
        return candidate

    def copy_into_library(self, paths: list[Path], destination: Path | None = None) -> list[Path]:
        destination = destination or self.imported_dir
        if not self.is_library_path(destination):
            destination = self.imported_dir
        destination.mkdir(parents=True, exist_ok=True)
        copied: list[Path] = []
        for source in paths:
            if not is_audio_file(source):
                continue
            target = self._unique_destination(destination, source.name)
            shutil.copy2(source, target)
            self._record(target, "managed", source)
            copied.append(target)
        self.save()
        return copied

    def rename_library_directory(self, path: Path, name: str) -> Path | None:
        resolved = path.resolve()
        if (
            not path.is_dir()
            or not self.is_library_path(path)
            or resolved == self.root.resolve()
            or resolved in self.protected_directories()
        ):
            return None
        destination = path.parent / name
        if destination.exists() or not self.is_library_path(destination):
            return None
        path.rename(destination)

        old_root = resolved
        new_root = destination.resolve()
        for record in self.entries.values():
            if not isinstance(record, dict):
                continue
            for field in ("path", "source"):
                value = record.get(field)
                if not isinstance(value, str) or not value:
                    continue
                candidate = Path(value)
                try:
                    relative = candidate.resolve().relative_to(old_root)
                except ValueError:
                    continue
                record[field] = str(new_root / relative)
        self.save()
        return destination

    def remove_library_directory(self, path: Path) -> bool:
        resolved = path.resolve()
        if (
            not path.is_dir()
            or not self.is_library_path(path)
            or resolved == self.root.resolve()
            or resolved in self.protected_directories()
        ):
            return False
        try:
            path.rmdir()
        except OSError:
            return False
        return True

    def add_trimmed(self, path: Path) -> None:
        self._record(path, "managed")
        self.save()

    def add_recorded(self, path: Path) -> None:
        self._record(path, "recorded")
        self.save()

    def scan_folder(self, folder: Path, recursive: bool) -> list[Path]:
        if not folder.is_dir():
            return []
        paths = folder.rglob("*") if recursive else folder.iterdir()
        return sorted((path for path in paths if is_audio_file(path)), key=lambda path: path.name.casefold())

    def library_files(self) -> list[Path]:
        paths: list[Path] = []
        for record in self.entries.values():
            if not isinstance(record, dict):
                continue
            path = Path(record.get("path", ""))
            if is_audio_file(path):
                paths.append(path)
        return sorted(set(paths), key=lambda path: path.name.casefold())

    def reference_files(self) -> list[Path]:
        paths: list[Path] = []
        for record in self.entries.values():
            if not isinstance(record, dict) or record.get("kind") != "reference":
                continue
            path = Path(record.get("path", ""))
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                paths.append(path)
        return sorted(set(paths), key=lambda path: path.name.casefold())


class SplitThread(QThread):
    progress = pyqtSignal(int, str)
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, source: Path, output_dir: Path) -> None:
        super().__init__()
        self.source = source
        self.output_dir = output_dir

    def run(self) -> None:
        command = [
            sys.executable,
            "-m",
            "demucs.separate",
            "--out",
            str(self.output_dir),
            "-n",
            "htdemucs",
            str(self.source),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except OSError as exc:
            self.failed.emit(f"Could not start Demucs: {exc}")
            return

        output: list[str] = []

        def handle_fragment(fragment: str) -> None:
            cleaned = " ".join(fragment.split())
            if not cleaned:
                return
            percent, status = parse_demucs_progress(cleaned)
            if status:
                self.progress.emit(-1 if percent is None else percent, status)
            if percent is None and cleaned not in output:
                output.append(cleaned)

        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        buffer = ""
        while selector.get_map():
            events = selector.select(timeout=0.25)
            for key, _ in events:
                data = os.read(key.fileobj.fileno(), 4096)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                buffer += data.decode("utf-8", errors="replace")
                fragments = re.split(r"[\r\n]", buffer)
                buffer = fragments.pop()
                for fragment in fragments:
                    handle_fragment(fragment)
            if process.poll() is not None and not events:
                break
        if buffer:
            handle_fragment(buffer)
        selector.close()
        return_code = process.wait()
        if return_code == 0:
            self.succeeded.emit(str(self.output_dir))
        else:
            details = "\n".join(output[-8:])
            self.failed.emit(
                "Demucs could not split this track.\n\n"
                + (details or "The splitter exited without an error message.")
            )


class StemCard(QGroupBox):
    export_requested = pyqtSignal(object)
    status_message = pyqtSignal(str)

    def __init__(self, stem: str) -> None:
        super().__init__(pretty_name(stem))
        self.stem = stem
        self.file_path: Path | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(178)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_state_changed)
        self.player.errorOccurred.connect(self._playback_error)

        layout = QVBoxLayout(self)
        layout.setSpacing(5)

        self.status = QLabel("Waiting for a split")
        layout.addWidget(self.status)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.setEnabled(False)
        self.position_slider.sliderMoved.connect(self._seek)
        layout.addWidget(self.position_slider)

        time_row = QHBoxLayout()
        self.position_label = QLabel("0:00")
        self.duration_label = QLabel("0:00")
        time_row.addWidget(self.position_label)
        time_row.addStretch()
        time_row.addWidget(self.duration_label)
        layout.addLayout(time_row)

        buttons = QHBoxLayout()
        self.play_button = self._icon_button("Play", QStyle.StandardPixmap.SP_MediaPlay)
        self.play_button.setIcon(bundled_icon("play.svg"))
        self.play_button.clicked.connect(self._play)
        self.pause_button = self._icon_button("Pause", QStyle.StandardPixmap.SP_MediaPause)
        self.pause_button.setIcon(bundled_icon("pause.svg"))
        self.pause_button.clicked.connect(self._pause)
        self.stop_button = self._icon_button("Stop", QStyle.StandardPixmap.SP_MediaStop)
        self.stop_button.setIcon(bundled_icon("stop.svg"))
        self.stop_button.clicked.connect(self._stop)
        self.rewind_start_button = self._icon_button(
            "Skip to beginning", QStyle.StandardPixmap.SP_MediaSeekBackward
        )
        self.rewind_start_button.setIcon(bundled_icon("skip-start.svg"))
        self.rewind_start_button.clicked.connect(self._rewind_to_start)
        self.rewind_button = self._icon_button("Rewind 5 seconds", QStyle.StandardPixmap.SP_MediaSkipBackward)
        self.rewind_button.setIcon(bundled_icon("rewind.svg"))
        self.rewind_button.clicked.connect(lambda: self._seek_relative(-5000))
        self.forward_button = self._icon_button("Fast-forward 5 seconds", QStyle.StandardPixmap.SP_MediaSkipForward)
        self.forward_button.setIcon(bundled_icon("fast-forward.svg"))
        self.forward_button.clicked.connect(lambda: self._seek_relative(5000))
        self.skip_end_button = self._icon_button("Skip to end", QStyle.StandardPixmap.SP_MediaSeekForward)
        self.skip_end_button.setIcon(bundled_icon("skip-end.svg"))
        self.skip_end_button.clicked.connect(self._skip_to_end)
        self.export_button = QPushButton("Save WAV")
        self.export_button.clicked.connect(lambda: self.export_requested.emit(self))
        for button in (
            self.rewind_start_button,
            self.rewind_button,
            self.play_button,
            self.forward_button,
            self.skip_end_button,
            self.stop_button,
            self.pause_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.export_button)
        layout.addLayout(buttons)
        self._set_controls_enabled(False)

    def _icon_button(self, name: str, standard_icon: QStyle.StandardPixmap) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(30, 30)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setIcon(self.style().standardIcon(standard_icon))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(name)
        button.setAccessibleName(name)
        return button

    def _set_controls_enabled(self, enabled: bool) -> None:
        for button in (
            self.play_button,
            self.rewind_start_button,
            self.rewind_button,
            self.pause_button,
            self.stop_button,
            self.forward_button,
            self.skip_end_button,
            self.export_button,
        ):
            button.setEnabled(enabled)
        self.position_slider.setEnabled(enabled)

    def set_file(self, path: Path | None) -> None:
        self.player.stop()
        self.file_path = path
        enabled = path is not None and path.exists()
        self.position_slider.setRange(0, 0)
        self.position_label.setText("0:00")
        self.duration_label.setText("0:00")
        if enabled:
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.status.setText(path.name)
        else:
            self.player.setSource(QUrl())
            self.status.setText("Unavailable")
        self._set_controls_enabled(enabled)

    def set_empty(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.file_path = None
        self.position_slider.setRange(0, 0)
        self.position_label.setText("0:00")
        self.duration_label.setText("0:00")
        self.status.setText("Empty stem")
        self._set_controls_enabled(False)

    def reset(self) -> None:
        self.set_file(None)
        self.status.setText("Waiting for a split")

    def _play(self) -> None:
        if self.player.position() >= self.player.duration() > 0:
            self.player.setPosition(0)
        self.player.play()

    def _pause(self) -> None:
        self.player.pause()

    def _stop(self) -> None:
        self.player.stop()
        self.player.setPosition(0)

    def _rewind_to_start(self) -> None:
        self.player.setPosition(0)

    def _skip_to_end(self) -> None:
        self.player.setPosition(self.player.duration())

    def _seek_relative(self, milliseconds: int) -> None:
        position = max(0, min(self.player.duration(), self.player.position() + milliseconds))
        self.player.setPosition(position)

    def _seek(self, position: int) -> None:
        self.player.setPosition(position)

    def _position_changed(self, position: int) -> None:
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self.position_label.setText(format_time(position))

    def _duration_changed(self, duration: int) -> None:
        self.position_slider.setRange(0, max(0, duration))
        self.duration_label.setText(format_time(duration))

    def _playback_state_changed(self, _state: QMediaPlayer.PlaybackState) -> None:
        pass

    def _playback_error(self, _error: QMediaPlayer.Error) -> None:
        if self.player.errorString():
            self.status_message.emit(f"{pretty_name(self.stem)} playback error: {self.player.errorString()}")

    def close_player(self) -> None:
        self.player.stop()


class PreviewPlayer(QGroupBox):
    playhead_changed = pyqtSignal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.file_path: Path | None = None
        self.clip_start = 0
        self.clip_end: int | None = None

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_state_changed)
        self.player.errorOccurred.connect(self._playback_error)

        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        self.file_label = QLabel("Select an audio file")
        layout.addWidget(self.file_label)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self._seek)
        layout.addWidget(self.position_slider)

        time_row = QHBoxLayout()
        self.position_label = QLabel("0:00")
        self.duration_label = QLabel("0:00")
        time_row.addWidget(self.position_label)
        time_row.addStretch()
        time_row.addWidget(self.duration_label)
        layout.addLayout(time_row)

        controls = QHBoxLayout()
        self.play_button = self._icon_button("Play", QStyle.StandardPixmap.SP_MediaPlay)
        self.play_button.setIcon(bundled_icon("play.svg"))
        self.play_button.clicked.connect(self._play)
        self.pause_button = self._icon_button("Pause", QStyle.StandardPixmap.SP_MediaPause)
        self.pause_button.setIcon(bundled_icon("pause.svg"))
        self.pause_button.clicked.connect(self._pause)
        self.stop_button = self._icon_button("Stop", QStyle.StandardPixmap.SP_MediaStop)
        self.stop_button.setIcon(bundled_icon("stop.svg"))
        self.stop_button.clicked.connect(self._stop)
        self.rewind_start_button = self._icon_button(
            "Skip to beginning", QStyle.StandardPixmap.SP_MediaSeekBackward
        )
        self.rewind_start_button.setIcon(bundled_icon("skip-start.svg"))
        self.rewind_start_button.clicked.connect(self._rewind_to_start)
        self.rewind_button = self._icon_button("Rewind 5 seconds", QStyle.StandardPixmap.SP_MediaSkipBackward)
        self.rewind_button.setIcon(bundled_icon("rewind.svg"))
        self.rewind_button.clicked.connect(lambda: self._seek_relative(-5000))
        self.forward_button = self._icon_button("Fast-forward 5 seconds", QStyle.StandardPixmap.SP_MediaSkipForward)
        self.forward_button.setIcon(bundled_icon("fast-forward.svg"))
        self.forward_button.clicked.connect(lambda: self._seek_relative(5000))
        self.skip_end_button = self._icon_button("Skip to end", QStyle.StandardPixmap.SP_MediaSeekForward)
        self.skip_end_button.setIcon(bundled_icon("skip-end.svg"))
        self.skip_end_button.clicked.connect(self._skip_to_end)
        for button in (
            self.rewind_start_button,
            self.rewind_button,
            self.play_button,
            self.forward_button,
            self.skip_end_button,
            self.stop_button,
            self.pause_button,
        ):
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        self._set_controls_enabled(False)

    def _icon_button(self, name: str, standard_icon: QStyle.StandardPixmap) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(30, 30)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setIcon(self.style().standardIcon(standard_icon))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(name)
        button.setAccessibleName(name)
        return button

    def _set_controls_enabled(self, enabled: bool) -> None:
        for button in (
            self.play_button,
            self.rewind_start_button,
            self.rewind_button,
            self.pause_button,
            self.stop_button,
            self.forward_button,
            self.skip_end_button,
        ):
            button.setEnabled(enabled)
        self.position_slider.setEnabled(enabled)

    def set_file(self, path: Path | None) -> None:
        self.player.stop()
        self.file_path = path if path and path.exists() else None
        self.clip_start = 0
        self.clip_end = None
        self.position_slider.setRange(0, 0)
        self.position_label.setText("0:00")
        self.duration_label.setText("0:00")
        if self.file_path:
            self.file_label.setText(self.file_path.name)
            self.player.setSource(QUrl.fromLocalFile(str(self.file_path)))
        else:
            self.file_label.setText("Select an audio file")
            self.player.setSource(QUrl())
        self.playhead_changed.emit(0, 0)
        self._set_controls_enabled(self.file_path is not None)

    def set_clip(self, start_ms: int, end_ms: int | None) -> None:
        self.clip_start = max(0, start_ms)
        self.clip_end = end_ms if end_ms and end_ms > self.clip_start else None

    def _play(self) -> None:
        if not self.file_path:
            return
        if self.player.position() >= (self.clip_end or self.player.duration()):
            self.player.setPosition(self.clip_start)
        self.player.play()

    def _pause(self) -> None:
        self.player.pause()

    def play_selection(self, start_ms: int, end_ms: int | None) -> None:
        self.set_clip(start_ms, end_ms)
        self.player.setPosition(self.clip_start)
        self.player.play()

    def _stop(self) -> None:
        self.player.stop()
        self.player.setPosition(self.clip_start)

    def _rewind_to_start(self) -> None:
        self.player.setPosition(self.clip_start)

    def _skip_to_end(self) -> None:
        self.player.setPosition(self.clip_end or self.player.duration())

    def _seek_relative(self, milliseconds: int) -> None:
        end = self.clip_end or self.player.duration()
        self.player.setPosition(max(self.clip_start, min(end, self.player.position() + milliseconds)))

    def _seek(self, position: int) -> None:
        end = self.clip_end or self.player.duration()
        self.player.setPosition(max(self.clip_start, min(end, position)))

    def seek_absolute(self, position: int) -> None:
        duration = self.player.duration()
        self.player.setPosition(max(0, min(duration, position)) if duration > 0 else max(0, position))

    def _position_changed(self, position: int) -> None:
        if self.clip_end and position >= self.clip_end:
            self.player.pause()
            self.player.setPosition(self.clip_start)
            position = self.clip_start
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)
        self.position_label.setText(format_time(position))
        self.playhead_changed.emit(position, self.player.duration())

    def _duration_changed(self, duration: int) -> None:
        self.position_slider.setRange(0, max(0, duration))
        self.duration_label.setText(format_time(duration))

    def _playback_state_changed(self, _state: QMediaPlayer.PlaybackState) -> None:
        pass

    def _playback_error(self, _error: QMediaPlayer.Error) -> None:
        if self.player.errorString():
            self.file_label.setText(f"Playback error: {self.player.errorString()}")

    def close_player(self) -> None:
        self.player.stop()


class WaveformDecodeThread(QThread):
    loaded = pyqtSignal(object, int)
    failed = pyqtSignal(str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def run(self) -> None:
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(self.path),
            "-f",
            "f32le",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-",
        ]
        try:
            result = subprocess.run(command, capture_output=True, check=False)
        except OSError as exc:
            self.failed.emit(f"Could not run FFmpeg: {exc}")
            return
        if result.returncode != 0:
            details = result.stderr.decode("utf-8", errors="replace").strip()
            self.failed.emit(details or "FFmpeg could not decode this file.")
            return
        samples = np.frombuffer(result.stdout, dtype=np.float32).copy()
        if not samples.size:
            self.failed.emit("This file contains no decodable audio.")
            return
        self.loaded.emit(samples, 44100)


class WaveformWidget(QWidget):
    range_changed = pyqtSignal(int, int)
    playhead_requested = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(120)
        self.samples = np.array([], dtype=np.float32)
        self.sample_rate = 44100
        self.start_ratio = 0.0
        self.end_ratio = 1.0
        self.view_start_ratio = 0.0
        self.view_end_ratio = 1.0
        self.playhead_ratio: float | None = None
        self.dragging: str | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_samples(self, samples: np.ndarray, sample_rate: int) -> None:
        self.samples = samples
        self.sample_rate = sample_rate
        self.start_ratio = 0.0
        self.end_ratio = 1.0
        self.view_start_ratio = 0.0
        self.view_end_ratio = 1.0
        self.playhead_ratio = None
        self.update()
        self._emit_range()

    def set_playhead(self, position_ms: int, duration_ms: int) -> None:
        if not self.samples.size or duration_ms <= 0:
            self.playhead_ratio = None
        else:
            self.playhead_ratio = max(0.0, min(1.0, position_ms / duration_ms))
        self.update()

    def zoom_to_selection(self) -> None:
        if not self.samples.size or self.end_ratio - self.start_ratio <= 0.001:
            return
        self.view_start_ratio = self.start_ratio
        self.view_end_ratio = self.end_ratio
        self.update()

    def reset_zoom(self) -> None:
        self.view_start_ratio = 0.0
        self.view_end_ratio = 1.0
        self.update()

    def set_marker_from_playhead(self, marker: str) -> None:
        if self.playhead_ratio is None:
            return
        self._set_handle(marker, self.playhead_ratio)

    def range_samples(self) -> tuple[int, int]:
        if not self.samples.size:
            return 0, 0
        return round(self.start_ratio * len(self.samples)), round(self.end_ratio * len(self.samples))

    def _emit_range(self) -> None:
        start, end = self.range_samples()
        self.range_changed.emit(start, end)

    def _ratio_for_x(self, x: float) -> float:
        visible_ratio = max(0.001, self.view_end_ratio - self.view_start_ratio)
        local_ratio = max(0.0, min(1.0, x / max(1, self.width())))
        return self.view_start_ratio + local_ratio * visible_ratio

    def _set_handle(self, handle: str, ratio: float) -> None:
        minimum_gap = 1.0 / max(1, len(self.samples))
        if handle == "start":
            self.start_ratio = max(0.0, min(ratio, self.end_ratio - minimum_gap))
        else:
            self.end_ratio = min(1.0, max(ratio, self.start_ratio + minimum_gap))
        self.update()
        self._emit_range()

    def _marker_x_positions(self) -> tuple[float, float]:
        view_ratio = max(0.001, self.view_end_ratio - self.view_start_ratio)
        width = max(1, self.width())
        return (
            (self.start_ratio - self.view_start_ratio) / view_ratio * width,
            (self.end_ratio - self.view_start_ratio) / view_ratio * width,
        )

    def _playhead_x(self) -> float | None:
        if self.playhead_ratio is None:
            return None
        view_ratio = max(0.001, self.view_end_ratio - self.view_start_ratio)
        return (self.playhead_ratio - self.view_start_ratio) / view_ratio * max(1, self.width())

    def _interaction_target(self, x: float) -> str | None:
        candidates: list[tuple[float, str]] = []
        start_x, end_x = self._marker_x_positions()
        start_distance = abs(x - start_x)
        end_distance = abs(x - end_x)
        if start_distance <= 20:
            candidates.append((start_distance, "start"))
        if end_distance <= 20:
            candidates.append((end_distance, "end"))
        playhead_x = self._playhead_x()
        if playhead_x is not None:
            playhead_distance = abs(x - playhead_x)
            if playhead_distance <= 14:
                candidates.append((playhead_distance, "playhead"))
        return min(candidates)[1] if candidates else None

    def _set_playhead_ratio(self, ratio: float) -> None:
        self.playhead_ratio = max(0.0, min(1.0, ratio))
        self.update()
        position_ms = round(self.playhead_ratio * len(self.samples) / self.sample_rate * 1000)
        self.playhead_requested.emit(position_ms)

    def _update_cursor(self, x: float) -> None:
        if self.dragging:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._interaction_target(x):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        if not self.samples.size or event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        ratio = self._ratio_for_x(x)
        target = self._interaction_target(x)
        if target in {"start", "end"}:
            self.dragging = target
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._set_handle(target, ratio)
            return
        if target == "playhead":
            self.dragging = "playhead"
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self.dragging = None
        self._set_playhead_ratio(ratio)

    def mouseMoveEvent(self, event) -> None:
        if self.dragging in {"start", "end"}:
            self._set_handle(self.dragging, self._ratio_for_x(event.position().x()))
        elif self.dragging == "playhead":
            self._set_playhead_ratio(self._ratio_for_x(event.position().x()))
        self._update_cursor(event.position().x())

    def mouseReleaseEvent(self, event) -> None:
        self.dragging = None
        self._update_cursor(event.position().x())

    def leaveEvent(self, event) -> None:
        if not self.dragging:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        if not self.samples.size:
            painter.setPen(self.palette().text().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Select an audio file to load its waveform")
            return

        width = max(1, self.width())
        height = self.height()
        view_ratio = max(0.001, self.view_end_ratio - self.view_start_ratio)
        selected_left = round((self.start_ratio - self.view_start_ratio) / view_ratio * width)
        selected_right = round((self.end_ratio - self.view_start_ratio) / view_ratio * width)
        selected_left = max(0, min(width, selected_left))
        selected_right = max(0, min(width, selected_right))
        painter.fillRect(selected_left, 0, selected_right - selected_left, height, QColor(220, 235, 250))
        view_start = round(self.view_start_ratio * len(self.samples))
        view_end = max(view_start + 1, round(self.view_end_ratio * len(self.samples)))
        view_length = view_end - view_start
        bin_size = max(1, view_length // width)
        pen = QPen(self.palette().highlight().color())
        painter.setPen(pen)
        center = height // 2
        for x in range(width):
            start = min(view_end, view_start + x * bin_size)
            end = min(len(self.samples), start + bin_size)
            if start >= end:
                break
            peak = float(np.max(np.abs(self.samples[start:end])))
            half_height = max(1, round(peak * (height // 2 - 4)))
            painter.drawLine(x, center - half_height, x, center + half_height)
        marker_color = QColor(0, 0, 0)
        painter.setPen(QPen(marker_color, 2))
        painter.drawLine(selected_left, 0, selected_left, height)
        painter.setPen(QPen(marker_color, 2))
        painter.drawLine(selected_right, 0, selected_right, height)
        if self.playhead_ratio is not None and self.view_start_ratio <= self.playhead_ratio <= self.view_end_ratio:
            playhead_x = round(
                (self.playhead_ratio - self.view_start_ratio) / view_ratio * width
            )
            painter.setPen(QPen(QColor(220, 40, 40), 3))
            painter.drawLine(playhead_x, 0, playhead_x, height)


class WaveformEditor(QGroupBox):
    saved = pyqtSignal(object)
    working_file_changed = pyqtSignal(object)
    playhead_requested = pyqtSignal(int)
    split_requested = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__("")
        self.file_path: Path | None = None
        self.samples = np.array([], dtype=np.float32)
        self.sample_rate = 44100
        self.decode_thread: WaveformDecodeThread | None = None
        self.load_serial = 0
        self.working_name = ""
        self.undo_state: tuple[np.ndarray, int, str] | None = None
        self.preview_dir = Path(tempfile.mkdtemp(prefix="sample-preview-"))

        layout = QVBoxLayout(self)
        self.file_label = QLabel()
        self.file_label.setVisible(False)
        layout.addWidget(self.file_label)
        self.waveform = WaveformWidget()
        self.waveform.setMinimumHeight(120)
        self.waveform.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.waveform.range_changed.connect(self._range_changed)
        self.waveform.playhead_requested.connect(self._waveform_playhead_requested)
        layout.addWidget(self.waveform)
        controls = QHBoxLayout()
        controls.setSpacing(3)
        controls.setContentsMargins(0, 0, 0, 0)
        self.range_label = QLabel("Full file")
        self.range_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        controls.addWidget(self.range_label)
        self.set_start_button = QPushButton("Set Start")
        self.set_start_button.setToolTip("Set the start marker to the current playhead position")
        self.set_start_button.clicked.connect(self._set_start_from_playhead)
        controls.addWidget(self.set_start_button)
        self.set_end_button = QPushButton("Set End")
        self.set_end_button.setToolTip("Set the end marker to the current playhead position")
        self.set_end_button.clicked.connect(self._set_end_from_playhead)
        controls.addWidget(self.set_end_button)
        controls.addStretch()
        self.play_button = self._icon_button("Play", QStyle.StandardPixmap.SP_MediaPlay)
        self.play_button.setIcon(self._editor_icon("play"))
        self.preview_button = self.play_button
        self.pause_button = self._icon_button("Pause", QStyle.StandardPixmap.SP_MediaPause)
        self.pause_button.setIcon(self._editor_icon("pause"))
        self.stop_button = self._icon_button("Stop", QStyle.StandardPixmap.SP_MediaStop)
        self.stop_button.setIcon(self._editor_icon("stop"))
        self.rewind_start_button = self._icon_button(
            "Skip to beginning", QStyle.StandardPixmap.SP_MediaSeekBackward
        )
        self.rewind_start_button.setIcon(self._editor_icon("skip-start"))
        self.rewind_button = self._icon_button("Rewind 5 seconds", QStyle.StandardPixmap.SP_MediaSkipBackward)
        self.rewind_button.setIcon(self._editor_icon("rewind"))
        self.forward_button = self._icon_button("Fast-forward 5 seconds", QStyle.StandardPixmap.SP_MediaSkipForward)
        self.forward_button.setIcon(self._editor_icon("fast-forward"))
        self.skip_end_button = self._icon_button("Skip to end", QStyle.StandardPixmap.SP_MediaSeekForward)
        self.skip_end_button.setIcon(self._editor_icon("skip-end"))
        controls.addWidget(self.rewind_start_button)
        controls.addWidget(self.rewind_button)
        controls.addWidget(self.play_button)
        controls.addWidget(self.forward_button)
        controls.addWidget(self.skip_end_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.pause_button)
        self.zoom_button = self._icon_button("Zoom to selection", QStyle.StandardPixmap.SP_ArrowUp)
        self.zoom_button.setIcon(self._editor_icon("zoom-in"))
        self.zoom_button.clicked.connect(self.waveform.zoom_to_selection)
        controls.addWidget(self.zoom_button)
        self.reset_zoom_button = self._icon_button("Reset zoom", QStyle.StandardPixmap.SP_ArrowDown)
        self.reset_zoom_button.setIcon(self._editor_icon("zoom-out"))
        self.reset_zoom_button.clicked.connect(self.waveform.reset_zoom)
        controls.addWidget(self.reset_zoom_button)
        self.undo_button = self._icon_button("Undo trim", QStyle.StandardPixmap.SP_ArrowBack)
        self.undo_button.setIcon(self._editor_icon("undo"))
        self.undo_button.clicked.connect(self._undo_trim)
        controls.addWidget(self.undo_button)
        self.apply_trim_button = self._icon_button("Apply trim", QStyle.StandardPixmap.SP_DialogApplyButton)
        self.apply_trim_button.setIcon(self._editor_icon("scissors"))
        self.apply_trim_button.clicked.connect(self._apply_trim)
        controls.addWidget(self.apply_trim_button)
        self.split_sample_button = self._icon_button("Split sample", QStyle.StandardPixmap.SP_DialogApplyButton)
        self.split_sample_button.setIcon(self._editor_icon("split"))
        self.split_sample_button.clicked.connect(self._confirm_split_sample)
        controls.addWidget(self.split_sample_button)
        self.save_button = self._icon_button("Save trimmed WAV", QStyle.StandardPixmap.SP_DialogSaveButton)
        self.save_button.setIcon(self._editor_icon("floppy"))
        self.save_button.clicked.connect(self.save_trimmed)
        controls.addWidget(self.save_button)
        layout.addLayout(controls)
        layout.setStretch(1, 1)
        self.preview_range_requested = None
        self._set_action_state(False)

    def _icon_button(self, name: str, standard_icon: QStyle.StandardPixmap) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(30, 30)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setIcon(self.style().standardIcon(standard_icon))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(name)
        button.setAccessibleName(name)
        return button

    def _editor_icon(self, kind: str) -> QIcon:
        icon_files = {
            "play": "play.svg",
            "pause": "pause.svg",
            "stop": "stop.svg",
            "scissors": "edit-cut.svg",
            "split": "split.svg",
            "rewind": "rewind.svg",
            "fast-forward": "fast-forward.svg",
            "skip-start": "skip-start.svg",
            "skip-end": "skip-end.svg",
            "undo": "undo.svg",
            "zoom-in": "zoom-in.svg",
            "zoom-out": "zoom-out.svg",
        }
        if kind in icon_files:
            icon = QIcon(str(resource_path("icons", icon_files[kind])))
            if not icon.isNull():
                return icon
            return self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)

        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().buttonText().color(), 2))
        painter.drawRoundedRect(3, 3, 18, 18, 2, 2)
        painter.drawRect(7, 3, 8, 6)
        painter.drawRect(7, 15, 10, 6)
        painter.end()
        return QIcon(pixmap)

    def _set_action_state(self, enabled: bool) -> None:
        for button in (
            self.play_button,
            self.pause_button,
            self.stop_button,
            self.rewind_start_button,
            self.rewind_button,
            self.forward_button,
            self.skip_end_button,
            self.zoom_button,
            self.reset_zoom_button,
            self.apply_trim_button,
            self.split_sample_button,
            self.save_button,
        ):
            button.setEnabled(enabled)
        marker_enabled = enabled and self.waveform.playhead_ratio is not None
        self.set_start_button.setEnabled(marker_enabled)
        self.set_end_button.setEnabled(marker_enabled)
        self.undo_button.setEnabled(enabled and self.undo_state is not None)

    def set_playhead(self, position_ms: int, duration_ms: int) -> None:
        self.waveform.set_playhead(position_ms, duration_ms)
        self._set_action_state(bool(self.samples.size))

    def _set_start_from_playhead(self) -> None:
        self.waveform.set_marker_from_playhead("start")

    def _set_end_from_playhead(self) -> None:
        self.waveform.set_marker_from_playhead("end")

    def _confirm_split_sample(self) -> None:
        if not self.samples.size:
            return
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setWindowTitle("Split sample")
        dialog.setText("Split the current sample into stems now?")
        split_button = dialog.addButton("Split sample", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(split_button)
        dialog.exec()
        if dialog.clickedButton() == split_button:
            self.split_requested.emit(self._write_split_source())

    def _waveform_playhead_requested(self, position_ms: int) -> None:
        self._set_action_state(bool(self.samples.size))
        self.playhead_requested.emit(position_ms)

    def set_preview_callback(self, callback) -> None:
        self.preview_range_requested = callback

    def set_file(self, path: Path | None) -> None:
        self.load_serial += 1
        load_serial = self.load_serial
        if self.decode_thread and self.decode_thread.isRunning():
            self.decode_thread.terminate()
            self.decode_thread.wait(1000)
        self.file_path = path if path and path.exists() else None
        self.samples = np.array([], dtype=np.float32)
        self.working_name = self.file_path.name if self.file_path else ""
        self.undo_state = None
        self.waveform.set_samples(self.samples, self.sample_rate)
        self._set_action_state(False)
        if not self.file_path:
            self.file_label.clear()
            self.file_label.setVisible(False)
            return
        self.file_label.setVisible(True)
        self.file_label.setText(f"Loading {self.file_path.name}...")
        self.decode_thread = WaveformDecodeThread(self.file_path)
        self.decode_thread.loaded.connect(
            lambda samples, sample_rate: self._loaded_if_current(load_serial, samples, sample_rate)
        )
        self.decode_thread.failed.connect(lambda message: self._failed_if_current(load_serial, message))
        self.decode_thread.start()

    def _loaded_if_current(self, load_serial: int, samples: np.ndarray, sample_rate: int) -> None:
        if load_serial != self.load_serial:
            return
        self.samples = samples
        self.sample_rate = sample_rate
        self.waveform.set_samples(samples, sample_rate)
        self.file_label.setText(self.working_name)
        self._set_action_state(True)

    def _failed_if_current(self, load_serial: int, message: str) -> None:
        if load_serial != self.load_serial:
            return
        self.file_label.setText(f"Waveform unavailable: {message}")

    def _range_changed(self, start: int, end: int) -> None:
        if not self.samples.size:
            self.range_label.setText("Full file")
            self._set_action_state(False)
            return
        start_ms = round(start / self.sample_rate * 1000)
        end_ms = round(end / self.sample_rate * 1000)
        self.range_label.setText(f"{format_time(start_ms)} - {format_time(end_ms)}")
        self._set_action_state(True)
        self.zoom_button.setEnabled(self.waveform.end_ratio - self.waveform.start_ratio < 0.999)
        self.reset_zoom_button.setEnabled(
            self.waveform.view_start_ratio > 0.001 or self.waveform.view_end_ratio < 0.999
        )

    def _selected_range_ms(self) -> tuple[int, int]:
        start, end = self.waveform.range_samples()
        return round(start / self.sample_rate * 1000), round(end / self.sample_rate * 1000)

    def _preview_selection(self) -> None:
        if self.preview_range_requested and self.samples.size:
            self.preview_range_requested(*self._selected_range_ms())

    def _write_working_preview(self) -> Path:
        destination = self.preview_dir / "untitled.wav"
        pcm = np.clip(self.samples, -1.0, 1.0)
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            output.writeframes((pcm * 32767).astype(np.int16).tobytes())
        return destination

    def _write_split_source(self) -> Path:
        name = safe_folder_name(Path(self.working_name or "split-source").stem) + ".wav"
        destination = self.preview_dir / name
        pcm = np.clip(self.samples, -1.0, 1.0)
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self.sample_rate)
            output.writeframes((pcm * 32767).astype(np.int16).tobytes())
        return destination

    def _emit_working_preview(self) -> None:
        if self.working_name != "untitled.wav" and self.file_path:
            self.working_file_changed.emit(self.file_path)
        else:
            self.working_file_changed.emit(self._write_working_preview())

    def _apply_trim(self) -> None:
        if not self.samples.size:
            return
        start, end = self.waveform.range_samples()
        if end <= start:
            return
        self.undo_state = (self.samples.copy(), self.sample_rate, self.working_name)
        self.samples = self.samples[start:end].copy()
        self.working_name = "untitled.wav"
        self.waveform.set_samples(self.samples, self.sample_rate)
        self.file_label.setText(self.working_name)
        self._emit_working_preview()
        self._set_action_state(True)
        self.undo_button.setEnabled(True)

    def _undo_trim(self) -> None:
        if not self.undo_state:
            return
        self.samples, self.sample_rate, self.working_name = self.undo_state
        self.undo_state = None
        self.waveform.set_samples(self.samples, self.sample_rate)
        self.file_label.setText(self.working_name)
        self._emit_working_preview()
        self._set_action_state(True)

    def save_trimmed(self) -> None:
        if not self.file_path or not self.samples.size:
            return
        default_name = self.working_name if self.working_name == "untitled.wav" else f"{self.file_path.stem}-trimmed.wav"
        default = app_data_dir() / "samples" / "trimmed" / default_name
        destination, _ = QFileDialog.getSaveFileName(self, "Save trimmed sample", str(default), "WAV files (*.wav)")
        if not destination:
            return
        start, end = self.waveform.range_samples()
        rendered = np.clip(self.samples[start:end], -1.0, 1.0)
        pcm = (rendered * 32767).astype(np.int16).tobytes()
        try:
            with wave.open(destination, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(self.sample_rate)
                output.writeframes(pcm)
        except OSError as exc:
            QMessageBox.critical(self, "Could not save sample", str(exc))
            return
        self.saved.emit(Path(destination))

    def close_editor(self) -> None:
        if self.decode_thread and self.decode_thread.isRunning():
            self.decode_thread.terminate()
            self.decode_thread.wait(1000)
        shutil.rmtree(self.preview_dir, ignore_errors=True)


@dataclass
class RecordingTarget:
    label: str
    kind: str
    device: object | None = None
    node_id: int | None = None


def _pipewire_objects() -> list[dict[str, object]]:
    if not shutil.which("pw-dump"):
        return []
    try:
        result = subprocess.run(
            ["pw-dump"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    try:
        objects = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    return [item for item in objects if isinstance(item, dict)] if isinstance(objects, list) else []


def _pipewire_ports(objects: list[dict[str, object]], node_id: int, direction: str) -> list[int]:
    ports: list[tuple[int, int]] = []
    for object_data in objects:
        if object_data.get("type") != "PipeWire:Interface:Port":
            continue
        port_id = object_data.get("id")
        info = object_data.get("info")
        props = info.get("props") if isinstance(info, dict) else None
        if not isinstance(port_id, int) or not isinstance(props, dict):
            continue
        if props.get("node.id") != node_id or props.get("port.direction") != direction:
            continue
        port_index = props.get("port.id")
        ports.append((port_index if isinstance(port_index, int) else port_id, port_id))
    return [port_id for _, port_id in sorted(ports)]


def pipewire_recording_targets() -> list[RecordingTarget]:
    """Discover PipeWire sinks and active application playback streams."""
    if not shutil.which("pw-record"):
        return []
    objects = _pipewire_objects()
    if not objects:
        return []

    targets: list[RecordingTarget] = []
    application_targets: list[RecordingTarget] = []
    for object_data in objects:
        if not isinstance(object_data, dict) or object_data.get("type") != "PipeWire:Interface:Node":
            continue
        node_id = object_data.get("id")
        info = object_data.get("info")
        props = info.get("props") if isinstance(info, dict) else None
        if not isinstance(node_id, int) or not isinstance(props, dict):
            continue

        media_class = str(props.get("media.class", ""))
        description = str(
            props.get("node.description")
            or props.get("node.nick")
            or props.get("node.name")
            or "Unnamed audio node"
        )
        if media_class == "Audio/Sink":
            targets.append(RecordingTarget(f"System audio — {description}", "pipewire_sink", node_id=node_id))
        elif media_class == "Stream/Output/Audio":
            application = str(
                props.get("application.name")
                or props.get("application.process.binary")
                or description
            )
            application_targets.append(
                RecordingTarget(f"Application audio — {application}", "pipewire_app", node_id=node_id)
            )
    return targets + sorted(application_targets, key=lambda target: target.label.casefold())


class RecordingPanel(QWidget):
    saved = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, destination: Path) -> None:
        super().__init__()
        self.destination = destination
        self.destination.mkdir(parents=True, exist_ok=True)
        self.targets: list[RecordingTarget] = []
        self.audio_source: QAudioSource | None = None
        self.output_file: QFile | None = None
        self.pipewire_process: subprocess.Popen[bytes] | None = None
        self.recording_path: Path | None = None
        self.recording_format: QAudioFormat | None = None
        self.recording_state = "idle"
        self.recording_started_at: float | None = None
        self.elapsed_before_pause = 0.0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.input_combo = QComboBox()
        self.input_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.input_combo.setToolTip("Sound source")
        layout.addWidget(self.input_combo, 1)

        self.refresh_button = QPushButton("↻")
        self.refresh_button.setFixedSize(30, 30)
        self.refresh_button.setToolTip("Refresh audio sources")
        self.refresh_button.setAccessibleName("Refresh audio sources")
        self.refresh_button.clicked.connect(self._refresh_devices)
        layout.addWidget(self.refresh_button)

        self.record_button = self._icon_button("Record", "record.svg")
        self.record_button.clicked.connect(self.start_recording)
        layout.addWidget(self.record_button)

        self.pause_button = self._icon_button("Pause recording", "pause.svg")
        self.pause_button.clicked.connect(self.toggle_pause)
        layout.addWidget(self.pause_button)

        self.stop_button = self._icon_button("Stop recording", "stop.svg")
        self.stop_button.clicked.connect(self.stop_recording)
        layout.addWidget(self.stop_button)

        self.duration_label = QLabel("0:00")
        self.duration_label.setMinimumWidth(42)
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.duration_label)

        self.duration_timer = QTimer(self)
        self.duration_timer.setInterval(100)
        self.duration_timer.timeout.connect(self._update_duration)
        self.duration_timer.timeout.connect(self._check_pipewire_recording)
        self._refresh_devices()
        self._refresh_controls()

    def _icon_button(self, name: str, icon_name: str) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(30, 30)
        button.setIcon(bundled_icon(icon_name))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(name)
        button.setAccessibleName(name)
        return button

    def _refresh_devices(self) -> None:
        if self.recording_state != "idle":
            return
        self.input_combo.clear()
        self.targets = []
        devices = QMediaDevices.audioInputs()
        default = QMediaDevices.defaultAudioInput()
        default_target_index = 0
        for index, device in enumerate(devices):
            self.targets.append(RecordingTarget(f"Microphone — {device.description()}", "qt", device=device))
            self.input_combo.addItem(self.targets[-1].label)
            if device.id() == default.id():
                default_target_index = index

        pipewire_targets = pipewire_recording_targets()
        self.targets.extend(pipewire_targets)
        for target in pipewire_targets:
            self.input_combo.addItem(target.label)

        if self.targets:
            self.input_combo.setCurrentIndex(default_target_index)
        else:
            self.input_combo.addItem("No audio sources available")

    def _refresh_controls(self) -> None:
        active = self.recording_state in {"recording", "paused"}
        self.input_combo.setEnabled(not active and bool(self.targets))
        self.refresh_button.setEnabled(not active)
        self.record_button.setEnabled(not active and bool(self.targets))
        self.pause_button.setEnabled(active)
        self.stop_button.setEnabled(active)

    def _capture_format(self, device) -> QAudioFormat | None:
        preferred = device.preferredFormat()
        if not preferred.isValid():
            return None

        capture_format = QAudioFormat()
        capture_format.setSampleRate(preferred.sampleRate())
        capture_format.setChannelCount(preferred.channelCount())
        capture_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if device.isFormatSupported(capture_format):
            return capture_format
        return preferred

    @staticmethod
    def _wav_header(audio_format: QAudioFormat, data_size: int) -> bytes:
        channels = max(1, audio_format.channelCount())
        sample_rate = max(1, audio_format.sampleRate())
        bytes_per_sample = max(1, audio_format.bytesPerSample())
        bits_per_sample = bytes_per_sample * 8
        block_align = channels * bytes_per_sample
        byte_rate = sample_rate * block_align
        format_code = 3 if audio_format.sampleFormat() == QAudioFormat.SampleFormat.Float else 1
        return struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + data_size,
            b"WAVE",
            b"fmt ",
            16,
            format_code,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
            b"data",
            data_size,
        )

    def _next_path(self) -> Path:
        candidate = self.destination / "recording.wav"
        index = 2
        while candidate.exists():
            candidate = self.destination / f"recording ({index}).wav"
            index += 1
        return candidate

    def start_recording(self) -> None:
        if self.recording_state != "idle" or not self.targets:
            return
        target = self.targets[self.input_combo.currentIndex()]
        if target.kind.startswith("pipewire"):
            self._start_pipewire_recording(target)
            return

        device = target.device
        audio_format = self._capture_format(device)
        if audio_format is None:
            self.failed.emit("The selected sound source has no usable recording format.")
            return

        path = self._next_path()
        output_file = QFile(str(path))
        if not output_file.open(QIODevice.OpenModeFlag.WriteOnly):
            self.failed.emit(f"Could not create {path.name}.")
            return
        if output_file.write(self._wav_header(audio_format, 0)) != 44:
            output_file.close()
            path.unlink(missing_ok=True)
            self.failed.emit(f"Could not initialize {path.name}.")
            return

        source = QAudioSource(device, audio_format, self)
        source.stateChanged.connect(self._source_state_changed)
        source.start(output_file)
        self.audio_source = source
        self.output_file = output_file
        self.recording_path = path
        self.recording_format = audio_format
        self.recording_state = "recording"
        self.recording_started_at = time.monotonic()
        self.elapsed_before_pause = 0.0
        self.duration_label.setText("0:00")
        self.duration_timer.start()
        self._refresh_controls()

    def _start_pipewire_recording(self, target: RecordingTarget) -> None:
        if target.node_id is None:
            self.failed.emit("The selected PipeWire source has no usable node.")
            return
        path = self._next_path()
        target_argument = "0" if target.kind == "pipewire_app" else str(target.node_id)
        command = [
            "pw-record",
            "--target",
            target_argument,
            "--rate",
            "48000",
            "--channels",
            "2",
            "--format",
            "s16",
            "-P",
            "application.name=StemKart",
            str(path),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            self.failed.emit(f"Could not start PipeWire recording: {exc}")
            return

        if target.kind == "pipewire_app":
            error = self._link_pipewire_application(process, target.node_id)
            if error:
                self._finish_pipewire_process(process)
                path.unlink(missing_ok=True)
                self.failed.emit(error)
                return

        self.pipewire_process = process
        self.recording_path = path
        self.recording_format = None
        self.recording_state = "recording"
        self.recording_started_at = time.monotonic()
        self.elapsed_before_pause = 0.0
        self.duration_label.setText("0:00")
        self.duration_timer.start()
        self._refresh_controls()

    def _link_pipewire_application(self, process: subprocess.Popen[bytes], node_id: int | None) -> str | None:
        if node_id is None:
            return "The selected application has no usable PipeWire node."
        if not shutil.which("pw-link"):
            return "The pw-link command is required for application audio capture."

        deadline = time.monotonic() + 2.0
        recorder_node_id: int | None = None
        objects: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            objects = _pipewire_objects()
            for object_data in objects:
                if object_data.get("type") != "PipeWire:Interface:Node":
                    continue
                info = object_data.get("info")
                props = info.get("props") if isinstance(info, dict) else None
                if not isinstance(props, dict) or props.get("application.name") != APP_NAME:
                    continue
                if props.get("media.class") == "Stream/Input/Audio" and isinstance(object_data.get("id"), int):
                    recorder_node_id = object_data["id"]
                    break
            if recorder_node_id is not None:
                break
            if process.poll() is not None:
                return "PipeWire stopped the application recording before it started."
            time.sleep(0.05)

        if recorder_node_id is None:
            return "StemKart could not find its PipeWire recording stream."

        application_ports: list[int] = []
        recorder_ports: list[int] = []
        while time.monotonic() < deadline:
            objects = _pipewire_objects()
            application_ports = _pipewire_ports(objects, node_id, "out")
            recorder_ports = _pipewire_ports(objects, recorder_node_id, "in")
            if application_ports and recorder_ports:
                break
            time.sleep(0.05)
        if not application_ports or not recorder_ports:
            return "The selected application has no capturable audio ports."

        for output_port, input_port in zip(application_ports, recorder_ports):
            try:
                result = subprocess.run(
                    ["pw-link", str(output_port), str(input_port)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return f"Could not connect to the application audio stream: {exc}"
            if result.returncode != 0:
                details = result.stderr.strip() or "unknown PipeWire link error"
                return f"Could not connect to the application audio stream: {details}"
        return None

    def toggle_pause(self) -> None:
        if not self.audio_source and not self.pipewire_process:
            return
        if self.recording_state == "recording":
            if self.audio_source:
                self.audio_source.suspend()
            elif self.pipewire_process:
                self.elapsed_before_pause += time.monotonic() - (self.recording_started_at or time.monotonic())
                self.pipewire_process.send_signal(signal.SIGSTOP)
            self.recording_state = "paused"
            self.duration_timer.stop()
            self.pause_button.setIcon(bundled_icon("play.svg"))
            self.pause_button.setToolTip("Resume recording")
            self.pause_button.setAccessibleName("Resume recording")
        elif self.recording_state == "paused":
            if self.audio_source:
                self.audio_source.resume()
            elif self.pipewire_process:
                self.pipewire_process.send_signal(signal.SIGCONT)
                self.recording_started_at = time.monotonic()
            self.recording_state = "recording"
            self.duration_timer.start()
            self.pause_button.setIcon(bundled_icon("pause.svg"))
            self.pause_button.setToolTip("Pause recording")
            self.pause_button.setAccessibleName("Pause recording")
        self._refresh_controls()

    def stop_recording(self) -> None:
        if self.recording_state == "idle":
            return
        source = self.audio_source
        output_file = self.output_file
        pipewire_process = self.pipewire_process
        path = self.recording_path
        audio_format = self.recording_format
        self.recording_state = "idle"
        self.duration_timer.stop()
        if source:
            source.stop()
        if pipewire_process:
            self._finish_pipewire_process(pipewire_process)
        if output_file and path and audio_format:
            data_size = max(0, int(output_file.size()) - 44)
            output_file.seek(0)
            output_file.write(self._wav_header(audio_format, data_size))
            output_file.close()
        self.audio_source = None
        self.output_file = None
        self.pipewire_process = None
        self.recording_path = None
        self.recording_format = None
        self.recording_started_at = None
        self.elapsed_before_pause = 0.0
        self.pause_button.setIcon(bundled_icon("pause.svg"))
        self.pause_button.setToolTip("Pause recording")
        self.pause_button.setAccessibleName("Pause recording")
        self._refresh_controls()
        if path and path.exists() and path.stat().st_size > 44:
            self.saved.emit(path)
        elif path:
            path.unlink(missing_ok=True)
            self.failed.emit("No audio was captured.")

    def _update_duration(self) -> None:
        if self.audio_source:
            self.duration_label.setText(format_time(self.audio_source.processedUSecs() // 1000))
        elif self.pipewire_process and self.recording_started_at is not None:
            elapsed = self.elapsed_before_pause + time.monotonic() - self.recording_started_at
            self.duration_label.setText(format_time(round(elapsed * 1000)))

    @staticmethod
    def _finish_pipewire_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            process.send_signal(signal.SIGCONT)
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def _pipewire_error(self, process: subprocess.Popen[bytes]) -> str:
        if process.stderr is None:
            return "PipeWire recording failed."
        details = process.stderr.read().decode("utf-8", errors="replace").strip()
        return details or "PipeWire recording failed."

    def _source_state_changed(self, _state) -> None:
        if self.audio_source and self.audio_source.error().name != "NoError":
            error_name = self.audio_source.error().name
            self.stop_recording()
            self.failed.emit(f"Recording error: {error_name}")

    def _check_pipewire_recording(self) -> None:
        process = self.pipewire_process
        if not process or process.poll() is None or self.recording_state == "idle":
            return
        error = self._pipewire_error(process)
        path = self.recording_path
        self.pipewire_process = None
        self.recording_state = "idle"
        self.duration_timer.stop()
        self.recording_path = None
        self.recording_started_at = None
        self.elapsed_before_pause = 0.0
        self._refresh_controls()
        if path:
            path.unlink(missing_ok=True)
        self.failed.emit(error)

    def close_recording(self) -> None:
        if self.recording_state != "idle":
            self.stop_recording()


class AudioSplitter(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.source_path: Path | None = None
        self.split_thread: SplitThread | None = None
        self.temp_root = Path(tempfile.mkdtemp(prefix="audio-splitter-"))
        self.cards: dict[str, StemCard] = {}
        self.sample_library = SampleLibrary()
        self.current_samples_directory: Path | None = None
        self.last_explorer_directory = self.sample_library.last_directory
        self.showing_library = False

        self.setWindowTitle(APP_NAME)
        self.setFixedSize(1120, 760)
        self.setAcceptDrops(True)

        self._build_ui()
        self.statusBar().setVisible(False)

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(6, 6, 6, 6)
        page.setSpacing(10)

        tabs = QTabWidget()
        self.tabs = tabs
        tabs.addTab(self._build_samples_page(), "Samples")
        splitter_page = QWidget()
        splitter_layout = QVBoxLayout(splitter_page)
        splitter_layout.setContentsMargins(8, 8, 8, 8)
        splitter_layout.setSpacing(10)
        tabs.addTab(splitter_page, "Stem Splitter")
        page.addWidget(tabs)

        source = QGroupBox("Source track")
        source_layout = QHBoxLayout(source)
        source_layout.setSpacing(10)
        source_text = QVBoxLayout()
        self.file_label = QLabel("Drop an audio file here, or choose one")
        self.file_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        source_text.addWidget(self.file_label)
        source_layout.addLayout(source_text, 1)
        self.choose_button = QPushButton("Choose track...")
        self.choose_button.clicked.connect(self.choose_track)
        source_layout.addWidget(self.choose_button)
        splitter_layout.addWidget(source)

        self.split_button = QPushButton("Split track")
        self.split_button.setMinimumHeight(36)
        self.split_button.setEnabled(False)
        self.split_button.clicked.connect(self.split_track)
        splitter_layout.addWidget(self.split_button)

        self.progress_panel = QWidget()
        progress_layout = QVBoxLayout(self.progress_panel)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(10)
        self.progress_label = QLabel()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)
        # Keep the progress area in the layout at all times. Only its contents
        # are toggled so starting a split cannot move the stem cards or export
        # controls vertically.
        self.progress_panel.setFixedHeight(54)
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        splitter_layout.addWidget(self.progress_panel)

        stem_grid = QGridLayout()
        stem_grid.setHorizontalSpacing(10)
        stem_grid.setVerticalSpacing(8)
        for index, stem in enumerate(STEMS):
            card = StemCard(stem)
            card.status_message.connect(self.statusBar().showMessage)
            card.export_requested.connect(self.export_card)
            self.cards[stem] = card
            stem_grid.addWidget(card, index // 2, index % 2)
        splitter_layout.addLayout(stem_grid)

        export_panel = QGroupBox("Export WAV files")
        export_layout = QHBoxLayout(export_panel)
        export_copy = QVBoxLayout()
        export_hint = QLabel("Save every stem together in a new named folder or an existing folder.")
        export_hint.setWordWrap(True)
        export_copy.addWidget(export_hint)
        export_layout.addLayout(export_copy, 1)
        self.named_export_button = QPushButton("Named folder...")
        self.named_export_button.setEnabled(False)
        self.named_export_button.clicked.connect(self.export_named_folder)
        self.folder_export_button = QPushButton("Existing folder...")
        self.folder_export_button.setEnabled(False)
        self.folder_export_button.clicked.connect(self.export_existing_folder)
        export_layout.addWidget(self.named_export_button)
        export_layout.addWidget(self.folder_export_button)
        splitter_layout.addWidget(export_panel)

    def _build_samples_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)

        self.recording_panel = RecordingPanel(self.sample_library.recorded_dir)
        self.recording_panel.saved.connect(self._recording_saved)
        self.recording_panel.failed.connect(self._recording_failed)
        layout.addWidget(self.recording_panel)

        view_switch = QHBoxLayout()
        self.explorer_view_button = QPushButton("Explorer")
        self.library_view_button = QPushButton("Library")
        for button in (self.explorer_view_button, self.library_view_button):
            button.setCheckable(True)
        self.explorer_view_button.setChecked(True)
        view_group = QButtonGroup(self)
        view_group.setExclusive(True)
        view_group.addButton(self.explorer_view_button)
        view_group.addButton(self.library_view_button)
        self.explorer_view_button.clicked.connect(lambda: self._set_sample_view("explorer"))
        self.library_view_button.clicked.connect(lambda: self._set_sample_view("library"))
        view_switch.addWidget(self.explorer_view_button)
        view_switch.addWidget(self.library_view_button)
        view_switch.addStretch()
        view_switch.setContentsMargins(8, 8, 0, 0)
        layout.addLayout(view_switch)

        browser = QSplitter(Qt.Orientation.Horizontal)
        browser.setChildrenCollapsible(False)

        folders = QGroupBox("")
        folders_layout = QVBoxLayout(folders)
        folders_layout.setContentsMargins(0, 0, 0, 0)
        self.folder_model = QFileSystemModel(self)
        self.folder_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        self.filesystem_root = Path.home().anchor or "/"
        root_index = self.folder_model.setRootPath(self.filesystem_root)
        self.folder_tree = QTreeView()
        self.folder_tree.setModel(self.folder_model)
        self.folder_tree.setRootIndex(root_index)
        self.folder_tree.setFrameShape(QFrame.Shape.NoFrame)
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setColumnHidden(1, True)
        self.folder_tree.setColumnHidden(2, True)
        self.folder_tree.setColumnHidden(3, True)
        self.folder_tree.selectionModel().currentChanged.connect(self._folder_changed)
        folders_layout.addWidget(self.folder_tree)

        self.library_tree = QTreeWidget()
        self.library_tree.setHeaderHidden(True)
        self.library_tree.setFrameShape(QFrame.Shape.NoFrame)
        self.library_tree.itemClicked.connect(self._library_tree_changed)
        self.library_tree.setVisible(False)
        folders_layout.addWidget(self.library_tree)
        browser.addWidget(folders)

        files = QGroupBox("")
        files_layout = QVBoxLayout(files)
        files_layout.setContentsMargins(0, 0, 0, 0)
        self.samples_location_label = QLabel("Select a folder")
        self.samples_location_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        files_layout.addWidget(self.samples_location_label)
        self.sample_file_list = QListWidget()
        self.sample_file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.sample_file_list.setFrameShape(QFrame.Shape.NoFrame)
        self.sample_file_list.currentItemChanged.connect(self._sample_selection_changed)
        self.sample_file_list.itemDoubleClicked.connect(lambda _item: self.preview_player._play())
        files_layout.addWidget(self.sample_file_list, 1)

        self.scan_recursive_checkbox = QCheckBox("Scan subfolders")
        self.scan_recursive_checkbox.setChecked(self.sample_library.scan_recursive)
        self.scan_recursive_checkbox.toggled.connect(self.sample_library.set_scan_recursive)
        files_layout.addWidget(self.scan_recursive_checkbox)

        self.actions_one_widget = QWidget()
        actions_one = QHBoxLayout(self.actions_one_widget)
        actions_one.setContentsMargins(0, 0, 0, 0)
        self.add_reference_button = QPushButton("Link to Library")
        self.add_reference_button.clicked.connect(self._add_selected_references)
        self.copy_library_button = QPushButton("Copy to Library")
        self.copy_library_button.clicked.connect(self._copy_selected_to_library)
        actions_one.addWidget(self.add_reference_button)
        actions_one.addWidget(self.copy_library_button)
        self.add_folder_button = QPushButton("Link Folder to Library")
        self.add_folder_button.clicked.connect(self._add_folder_references)
        self.copy_folder_button = QPushButton("Copy Folder to Library")
        self.copy_folder_button.clicked.connect(self._copy_folder_to_library)
        actions_one.addWidget(self.add_folder_button)
        actions_one.addWidget(self.copy_folder_button)
        files_layout.addWidget(self.actions_one_widget)

        self.actions_two_widget = self.actions_one_widget

        self.library_actions_widget = QWidget()
        library_actions = QHBoxLayout(self.library_actions_widget)
        library_actions.setContentsMargins(0, 0, 0, 0)
        self.new_library_folder_button = QPushButton("New Folder")
        self.new_library_folder_button.clicked.connect(self._new_library_folder)
        self.rename_library_folder_button = QPushButton("Rename")
        self.rename_library_folder_button.clicked.connect(self._rename_library_folder)
        self.delete_library_folder_button = QPushButton("Delete")
        self.delete_library_folder_button.clicked.connect(self._delete_library_folder)
        self.refresh_library_button = QPushButton("Refresh")
        self.refresh_library_button.clicked.connect(self._refresh_library)
        for button in (
            self.new_library_folder_button,
            self.rename_library_folder_button,
            self.delete_library_folder_button,
            self.refresh_library_button,
        ):
            library_actions.addWidget(button)
        library_actions.addStretch()
        self.library_actions_widget.setVisible(False)
        files_layout.addWidget(self.library_actions_widget)

        self.samples_status = QLabel()
        files_layout.addWidget(self.samples_status)
        browser.addWidget(files)
        browser.setStretchFactor(0, 0)
        browser.setStretchFactor(1, 1)
        browser.setSizes([280, 780])
        layout.addWidget(browser, 1)

        # The waveform editor is the only visible preview surface. Keep the
        # player as a hidden engine for audio output and transport state.
        self.preview_player = PreviewPlayer(page)
        self.preview_player.setVisible(False)
        self.waveform_editor = WaveformEditor()
        self.waveform_editor.setFixedHeight(280)
        self.waveform_editor.set_preview_callback(self._preview_trim_range)
        self.waveform_editor.playhead_requested.connect(self._seek_preview_from_waveform)
        self.waveform_editor.split_requested.connect(self._split_current_sample)
        self.waveform_editor.working_file_changed.connect(self._working_preview_changed)
        self.waveform_editor.saved.connect(self._trim_saved)
        self.waveform_editor.play_button.clicked.connect(self._play_trim_selection)
        self.waveform_editor.pause_button.clicked.connect(self._pause_trim_playback)
        self.waveform_editor.stop_button.clicked.connect(self._stop_trim_playback)
        self.waveform_editor.rewind_start_button.clicked.connect(self._rewind_trim_start)
        self.waveform_editor.rewind_button.clicked.connect(lambda: self._seek_trim_playback(-5000))
        self.waveform_editor.forward_button.clicked.connect(lambda: self._seek_trim_playback(5000))
        self.waveform_editor.skip_end_button.clicked.connect(self._skip_trim_end)
        self.preview_player.playhead_changed.connect(self.waveform_editor.set_playhead)
        layout.addWidget(self.waveform_editor)

        last_directory = self.sample_library.last_directory
        self._restore_samples_directory(last_directory)
        return page

    def _set_sample_view(self, view: str) -> None:
        showing_explorer = view == "explorer"
        self.explorer_view_button.setChecked(showing_explorer)
        self.library_view_button.setChecked(not showing_explorer)
        self.scan_recursive_checkbox.setVisible(showing_explorer)
        self.actions_one_widget.setVisible(showing_explorer)
        self.actions_two_widget.setVisible(showing_explorer)
        self.library_actions_widget.setVisible(not showing_explorer)
        self.add_reference_button.setVisible(showing_explorer)
        self.copy_library_button.setVisible(showing_explorer)
        self.add_folder_button.setVisible(showing_explorer)
        self.copy_folder_button.setVisible(showing_explorer)
        if showing_explorer:
            self.showing_library = False
            self.samples_location_label.setVisible(True)
            self.folder_tree.setVisible(True)
            self.library_tree.setVisible(False)
            root_index = self.folder_model.setRootPath(self.filesystem_root)
            self.folder_tree.setRootIndex(root_index)
            self._restore_samples_directory(self.last_explorer_directory)
            self._show_current_folder()
        else:
            self.showing_library = True
            self.folder_tree.setVisible(False)
            self.library_tree.setVisible(True)
            self._populate_library_tree()
            self._show_directory(self.sample_library.root)

    def _restore_samples_directory(self, path: Path) -> None:
        index = self.folder_model.index(str(path))
        if index.isValid():
            self.folder_tree.setCurrentIndex(index)
            self.folder_tree.scrollTo(index)
        else:
            self._show_directory(Path.home())

    def _folder_changed(self, current, _previous) -> None:
        path = Path(self.folder_model.filePath(current))
        if path.is_dir():
            self._show_directory(path)

    def _populate_library_tree(self) -> None:
        self.library_tree.clear()

        library_root = QTreeWidgetItem(["Library"])
        library_root.setData(0, Qt.ItemDataRole.UserRole, str(self.sample_library.root))
        self.library_tree.addTopLevelItem(library_root)

        def add_children(parent: QTreeWidgetItem, directory: Path) -> None:
            try:
                directories = sorted(
                    (path for path in directory.iterdir() if path.is_dir()),
                    key=lambda path: path.name.casefold(),
                )
            except OSError:
                return
            for child in directories:
                item = QTreeWidgetItem([child.name])
                item.setData(0, Qt.ItemDataRole.UserRole, str(child))
                parent.addChild(item)
                add_children(item, child)

        add_children(library_root, self.sample_library.root)
        references = QTreeWidgetItem(["References"])
        references.setData(0, Qt.ItemDataRole.UserRole, "references")
        self.library_tree.addTopLevelItem(references)
        self.library_tree.expandItem(library_root)
        self.library_tree.setCurrentItem(library_root)

    def _library_tree_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        value = item.data(0, Qt.ItemDataRole.UserRole)
        if value == "references":
            self._show_references()
        elif value:
            self._show_directory(Path(value))

    def _show_references(self) -> None:
        self.current_samples_directory = None
        self.samples_location_label.clear()
        self.samples_location_label.setVisible(False)
        self.sample_file_list.clear()
        for audio_path in self.sample_library.reference_files():
            list_item = QListWidgetItem(audio_path.name)
            list_item.setData(Qt.ItemDataRole.UserRole, str(audio_path))
            if not audio_path.exists():
                list_item.setEnabled(False)
                list_item.setToolTip("Original file is no longer available")
            self.sample_file_list.addItem(list_item)
        self.samples_status.setText(f"{self.sample_file_list.count()} referenced files")

    def _show_directory(self, path: Path) -> None:
        if not path.is_dir():
            return
        self.current_samples_directory = path
        self.samples_location_label.setVisible(True)
        if not self.showing_library:
            self.last_explorer_directory = path
            self.sample_library.set_last_directory(path)
        self.samples_location_label.setText(str(path))
        self.sample_file_list.clear()
        for audio_path in sorted((item for item in path.iterdir() if is_audio_file(item)), key=lambda item: item.name.casefold()):
            list_item = QListWidgetItem(audio_path.name)
            list_item.setData(Qt.ItemDataRole.UserRole, str(audio_path))
            self.sample_file_list.addItem(list_item)
        self.samples_status.setText(f"{self.sample_file_list.count()} audio files")

    def _show_current_folder(self) -> None:
        self._show_directory(self.last_explorer_directory)

    def _selected_sample_paths(self) -> list[Path]:
        paths: list[Path] = []
        for item in self.sample_file_list.selectedItems():
            value = item.data(Qt.ItemDataRole.UserRole)
            if value:
                paths.append(Path(value))
        return paths

    def _sample_selection_changed(self, current, _previous) -> None:
        if not current:
            self.preview_player.set_file(None)
            self.waveform_editor.set_file(None)
            return
        value = current.data(Qt.ItemDataRole.UserRole)
        path = Path(value) if value else None
        self.preview_player.set_file(path)
        self.waveform_editor.set_file(path)

    def _add_selected_references(self) -> None:
        paths = self._selected_sample_paths()
        count = self.sample_library.add_references(paths)
        self.samples_status.setText(f"Added {count} reference(s)")

    def _copy_selected_to_library(self) -> None:
        paths = self._selected_sample_paths()
        destination = self.current_samples_directory if self.showing_library else None
        copied = self.sample_library.copy_into_library(paths, destination)
        self.samples_status.setText(f"Copied {len(copied)} file(s) into the library")
        if self.showing_library:
            self._refresh_library()

    def _folder_audio_paths(self) -> list[Path]:
        return self.sample_library.scan_folder(
            self.current_samples_directory or self.sample_library.last_directory,
            self.scan_recursive_checkbox.isChecked(),
        )

    def _add_folder_references(self) -> None:
        paths = self._folder_audio_paths()
        count = self.sample_library.add_references(paths)
        self.samples_status.setText(f"Added {count} folder reference(s)")

    def _copy_folder_to_library(self) -> None:
        paths = self._folder_audio_paths()
        copied = self.sample_library.copy_into_library(paths)
        self.samples_status.setText(f"Copied {len(copied)} folder file(s) into the library")
        if self.showing_library:
            self._refresh_library()

    def _selected_library_directory(self) -> Path:
        if self.showing_library and self.current_samples_directory:
            path = self.current_samples_directory
            if path.is_dir() and self.sample_library.is_library_path(path):
                return path
        return self.sample_library.root

    def _library_folder_name(self, title: str, label: str, text: str = "") -> str | None:
        name, accepted = QInputDialog.getText(self, title, label, text=text)
        if not accepted:
            return None
        name = name.strip()
        if not name or name in {".", ".."} or Path(name).name != name:
            QMessageBox.warning(self, title, "Enter a single folder name without path separators.")
            return None
        return name

    def _new_library_folder(self) -> None:
        parent = self._selected_library_directory()
        name = self._library_folder_name("New library folder", "Folder name:")
        if not name:
            return
        destination = parent / name
        if destination.exists():
            QMessageBox.warning(self, "Folder already exists", f"A folder named {name!r} already exists here.")
            return
        try:
            destination.mkdir()
        except OSError as exc:
            QMessageBox.critical(self, "Could not create folder", str(exc))
            return
        self._refresh_library(destination)

    def _rename_library_folder(self) -> None:
        path = self._selected_library_directory()
        name = self._library_folder_name("Rename library folder", "New folder name:", path.name)
        if not name or name == path.name:
            return
        renamed = self.sample_library.rename_library_directory(path, name)
        if renamed is None:
            QMessageBox.warning(
                self,
                "Folder cannot be renamed",
                "Only user-created library folders can be renamed, and the destination name must be unused.",
            )
            return
        self._refresh_library(renamed)

    def _delete_library_folder(self) -> None:
        path = self._selected_library_directory()
        if path.resolve() in self.sample_library.protected_directories() or path.resolve() == self.sample_library.root.resolve():
            QMessageBox.warning(self, "Folder cannot be deleted", "StemKart's root and system folders are protected.")
            return
        answer = QMessageBox.question(
            self,
            "Delete empty folder?",
            f"Delete the empty folder {path.name!r}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self.sample_library.remove_library_directory(path):
            QMessageBox.warning(self, "Folder not deleted", "Only empty library folders can be deleted.")
            return
        self._refresh_library(self.sample_library.root)

    def _refresh_library(self, selected: Path | None = None) -> None:
        if not self.showing_library:
            return
        self.sample_library.sync_legacy_additions()
        selected = selected or self.current_samples_directory or self.sample_library.root
        if not selected.is_dir() or not self.sample_library.is_library_path(selected):
            selected = self.sample_library.root
        self._populate_library_tree()
        self._show_directory(selected)

    def _recording_saved(self, path: Path) -> None:
        self.sample_library.add_recorded(path)
        self.samples_status.setText(f"Saved {path.name}")
        self.preview_player.set_file(path)
        self.waveform_editor.set_file(path)
        if self.showing_library:
            self._populate_library_tree()
            self._show_directory(self.sample_library.recorded_dir)

    def _recording_failed(self, message: str) -> None:
        self.samples_status.setText(message)

    def _preview_trim_range(self, start_ms: int, end_ms: int) -> None:
        self.preview_player.play_selection(start_ms, end_ms)

    def _seek_preview_from_waveform(self, position_ms: int) -> None:
        self.preview_player.seek_absolute(position_ms)

    def _split_current_sample(self, path: Path) -> None:
        if not path.exists():
            return
        self.set_source(path)
        self.tabs.setCurrentIndex(1)
        self.split_track()

    def _sync_trim_playback_range(self) -> None:
        if self.waveform_editor.samples.size:
            start_ms, end_ms = self.waveform_editor._selected_range_ms()
            self.preview_player.set_clip(start_ms, end_ms)

    def _play_trim_selection(self) -> None:
        self._sync_trim_playback_range()
        self.preview_player._play()

    def _pause_trim_playback(self) -> None:
        self.preview_player._pause()

    def _stop_trim_playback(self) -> None:
        self._sync_trim_playback_range()
        self.preview_player._stop()

    def _rewind_trim_start(self) -> None:
        self._sync_trim_playback_range()
        self.preview_player._rewind_to_start()

    def _skip_trim_end(self) -> None:
        self._sync_trim_playback_range()
        self.preview_player._skip_to_end()

    def _seek_trim_playback(self, milliseconds: int) -> None:
        self._sync_trim_playback_range()
        self.preview_player._seek_relative(milliseconds)

    def _working_preview_changed(self, path: Path) -> None:
        self.preview_player.set_file(path)

    def _trim_saved(self, path: Path) -> None:
        self.sample_library.add_trimmed(path)
        self.samples_status.setText(f"Saved {path.name}")
        self.preview_player.set_file(path)
        if self.showing_library:
            self._populate_library_tree()
            self._show_directory(self.sample_library.trimmed_dir)

    def _set_split_progress_visible(self, visible: bool) -> None:
        self.progress_bar.setVisible(visible)
        self.progress_label.setVisible(visible)

    def choose_track(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Choose a music track", "", SUPPORTED_AUDIO)
        if filename:
            self.set_source(Path(filename))

    def set_source(self, path: Path) -> None:
        self.source_path = path
        self.file_label.setText(path.name)
        self.file_label.setToolTip(str(path))
        self.split_button.setEnabled(True)
        self._set_split_progress_visible(False)
        self.statusBar().clearMessage()
        for card in self.cards.values():
            card.reset()
        self.named_export_button.setEnabled(False)
        self.folder_export_button.setEnabled(False)

    def split_track(self) -> None:
        if not self.source_path or not self.source_path.exists():
            return
        if self.split_thread and self.split_thread.isRunning():
            return
        for card in self.cards.values():
            card.reset()
        self.named_export_button.setEnabled(False)
        self.folder_export_button.setEnabled(False)
        self.split_button.setEnabled(False)
        self.choose_button.setEnabled(False)
        self.progress_label.setText("Preparing split...")
        self.progress_bar.setRange(0, 0)
        self._set_split_progress_visible(True)
        output_dir = Path(tempfile.mkdtemp(prefix="demucs-", dir=self.temp_root))
        self.split_thread = SplitThread(self.source_path, output_dir)
        self.split_thread.progress.connect(self._split_progress)
        self.split_thread.succeeded.connect(self._split_succeeded)
        self.split_thread.failed.connect(self._split_failed)
        self.split_thread.finished.connect(self._split_finished)
        self.split_thread.start()

    @pyqtSlot(int, str)
    def _split_progress(self, percent: int, message: str) -> None:
        self._set_split_progress_visible(True)
        if percent < 0:
            self.progress_bar.setRange(0, 0)
        else:
            if self.progress_bar.minimum() != 0 or self.progress_bar.maximum() != 100:
                self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percent)
        self.progress_label.setText(message)

    @pyqtSlot(str)
    def _split_succeeded(self, output_dir: str) -> None:
        found = {path.stem.lower(): path for path in Path(output_dir).rglob("*.wav")}
        for stem, card in self.cards.items():
            path = found.get(stem)
            if path is None:
                card.set_file(None)
            else:
                availability = wav_availability(path)
                if availability == "usable":
                    card.set_file(path)
                elif availability == "empty":
                    card.set_empty()
                else:
                    card.set_file(None)
        loaded = sum(card.file_path is not None for card in self.cards.values())
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self._set_split_progress_visible(False)
        if loaded:
            self.statusBar().clearMessage()
            self.named_export_button.setEnabled(True)
            self.folder_export_button.setEnabled(True)
        else:
            self.statusBar().clearMessage()
            QMessageBox.warning(self, "No stems found", "Demucs finished without producing the expected WAV files.")

    @pyqtSlot(str)
    def _split_failed(self, message: str) -> None:
        self._set_split_progress_visible(False)
        self.statusBar().showMessage("Split failed")
        QMessageBox.critical(self, "Could not split track", message)

    def _split_finished(self) -> None:
        self.split_button.setEnabled(self.source_path is not None)
        self.choose_button.setEnabled(True)

    def closeEvent(self, event) -> None:
        for card in self.cards.values():
            card.close_player()
        self.recording_panel.close_recording()
        self.preview_player.close_player()
        self.waveform_editor.close_editor()
        if self.split_thread and self.split_thread.isRunning():
            self.split_thread.terminate()
            self.split_thread.wait(1500)
        event.accept()

    def export_card(self, card: StemCard) -> None:
        if not card.file_path:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            f"Save {pretty_name(card.stem)} WAV",
            f"{card.stem}.wav",
            "WAV files (*.wav)",
        )
        if destination:
            shutil.copy2(card.file_path, destination)
            self.statusBar().showMessage(f"Saved {Path(destination).name}")

    def _available_files(self) -> list[Path]:
        return [card.file_path for card in self.cards.values() if card.file_path is not None]

    def _copy_all(self, destination: Path) -> int:
        files = self._available_files()
        destination.mkdir(parents=True, exist_ok=True)
        for source in files:
            shutil.copy2(source, destination / source.name)
        return len(files)

    def export_named_folder(self) -> None:
        if not self._available_files():
            return
        parent = QFileDialog.getExistingDirectory(self, "Choose where to create the export folder")
        if not parent:
            return
        default = f"{self.source_path.stem} - stems" if self.source_path else "split-stems"
        name, accepted = QInputDialog.getText(self, "Name the export folder", "Folder name:", text=default)
        if not accepted:
            return
        destination = Path(parent) / safe_folder_name(name)
        self._confirm_and_copy(destination)

    def export_existing_folder(self) -> None:
        if not self._available_files():
            return
        parent = QFileDialog.getExistingDirectory(self, "Choose an existing export folder")
        if parent:
            self._confirm_and_copy(Path(parent))

    def _confirm_and_copy(self, destination: Path) -> None:
        files = self._available_files()
        collisions = [destination / source.name for source in files if (destination / source.name).exists()]
        if collisions:
            answer = QMessageBox.question(
                self,
                "Replace existing WAV files?",
                f"{len(collisions)} file(s) already exist in this folder. Replace them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        count = self._copy_all(destination)
        self.statusBar().showMessage(f"Exported {count} WAV files to {destination.name}")
        QMessageBox.information(self, "Export complete", f"Saved {count} WAV files in:\n{destination}")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = Path(urls[0].toLocalFile())
            if path.is_file():
                self.set_source(path)
                event.acceptProposedAction()


def apply_style(app: QApplication) -> None:
    # Use the platform's ordinary Qt style: no stylesheet, theme, or custom chrome.
    _ = app


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(APP_SLUG)
    apply_style(app)
    window = AudioSplitter()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
