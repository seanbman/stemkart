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
from typing import Callable

import numpy as np

from PyQt6.QtCore import QFile, QDir, QEvent, QIODevice, QFileInfo, QFileSystemWatcher, QSignalBlocker, QThread, QTimer, QUrl, Qt, QSize, QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFileSystemModel,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFileIconProvider,
    QGridLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProxyStyle,
    QPushButton,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyleOption,
    QStyleOptionButton,
    QSizePolicy,
    QSizeGrip,
    QTabWidget,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QToolButton,
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
    roots = []
    if frozen_root:
        roots.append(Path(frozen_root))
    roots.extend(
        (
            Path(__file__).resolve().parent,
            Path(sys.prefix) / "share" / APP_SLUG,
        )
    )
    for root in roots:
        candidate = root.joinpath(*parts)
        if candidate.exists():
            return candidate
    return roots[0].joinpath(*parts)


def _xdg_base(environment_name: str, fallback: str) -> Path:
    configured = os.environ.get(environment_name)
    return Path(configured).expanduser() if configured else Path.home() / fallback


def app_data_dir() -> Path:
    return _xdg_base("XDG_DATA_HOME", ".local/share") / APP_SLUG


def app_config_dir() -> Path:
    return _xdg_base("XDG_CONFIG_HOME", ".config") / APP_SLUG


def app_settings_path() -> Path:
    return app_config_dir() / "settings.json"


def load_app_settings() -> dict[str, object]:
    try:
        settings = json.loads(app_settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return settings if isinstance(settings, dict) else {}


def save_app_setting(name: str, value: object) -> None:
    path = app_settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        settings = load_app_settings()
        settings[name] = value
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    except OSError:
        # The UI should remain usable if the settings location is read-only.
        pass


def app_cache_dir() -> Path:
    return _xdg_base("XDG_CACHE_HOME", ".cache") / APP_SLUG


def bundled_icon(name: str) -> QIcon:
    return QIcon(str(resource_path("icons", name)))


def tinted_icon(icon: QIcon, color: str, size: int = 16) -> QIcon:
    """Keep an existing icon shape while replacing its visible pixels with one color."""
    pixmap = icon.pixmap(QSize(size, size))
    if pixmap.isNull():
        return icon
    monochrome = QPixmap(pixmap.size())
    monochrome.fill(Qt.GlobalColor.transparent)
    painter = QPainter(monochrome)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.fillRect(monochrome.rect(), QColor(color))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    tinted = QIcon()
    for mode in (
        QIcon.Mode.Normal,
        QIcon.Mode.Disabled,
        QIcon.Mode.Active,
        QIcon.Mode.Selected,
    ):
        tinted.addPixmap(monochrome, mode)
    return tinted


class ThemedIconButton(QPushButton):
    """An icon button whose glyph follows the button's hover/pressed state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("transportButton")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setProperty("stemkart_active", False)
        self._stemkart_base_icon = QIcon()
        self.toggled.connect(self._update_icon_color)

    def setIcon(self, icon: QIcon) -> None:
        self._stemkart_base_icon = icon
        self._update_icon_color()

    def _update_icon_color(self) -> None:
        if self._stemkart_base_icon.isNull():
            super().setIcon(QIcon())
            return
        application = QApplication.instance()
        light_mode = bool(getattr(application, "_stemkart_light_mode", False))
        normal_color = "#000000" if light_mode else "#ffffff"
        highlighted_color = "#ffffff" if light_mode else "#000000"
        active = self.underMouse() or self.isDown() or self.isChecked()
        if self.property("stemkart_active") != active:
            self.setProperty("stemkart_active", active)
            if self.style() is not None:
                self.style().unpolish(self)
                self.style().polish(self)
        color = highlighted_color if active else normal_color
        size = max(16, self.iconSize().width(), self.iconSize().height())
        super().setIcon(tinted_icon(self._stemkart_base_icon, color, size=size))

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._update_icon_color()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._update_icon_color()

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self._update_icon_color()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._update_icon_color()


class ThemedTextButton(QPushButton):
    """A text button with the same explicit retro hover state as icon buttons."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("markerButton")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setProperty("stemkart_active", False)
        self.toggled.connect(self._update_active_state)

    def _update_active_state(self) -> None:
        active = self.underMouse() or self.isDown() or self.isChecked()
        if self.property("stemkart_active") == active:
            return
        self.setProperty("stemkart_active", active)
        if self.style() is not None:
            self.style().unpolish(self)
            self.style().polish(self)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._update_active_state()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._update_active_state()

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self._update_active_state()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self._update_active_state()


class OrangeCheckBox(QCheckBox):
    """Stock checkbox geometry with an orange check glyph when enabled."""

    def paintEvent(self, _event) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QPainter(self)
        indicator = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator,
            option,
            self,
        )
        background = self.palette().color(QPalette.ColorRole.Base)
        border = self.palette().color(QPalette.ColorRole.Text)
        painter.fillRect(indicator, background)
        painter.setPen(QPen(border, 1))
        painter.drawRect(indicator.adjusted(0, 0, -1, -1))
        if self.isChecked():
            painter.setPen(QPen(QColor("#ff7a00"), 2))
            painter.drawLine(
                indicator.left() + 3,
                indicator.top() + indicator.height() // 2,
                indicator.left() + indicator.width() // 2 - 1,
                indicator.bottom() - 3,
            )
            painter.drawLine(
                indicator.left() + indicator.width() // 2 - 1,
                indicator.bottom() - 3,
                indicator.right() - 3,
                indicator.top() + 3,
            )
        text_rect = option.rect
        text_rect.setLeft(indicator.right() + 6)
        self.style().drawItemText(
            painter,
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.palette(),
            self.isEnabled(),
            self.text(),
            QPalette.ColorRole.Text,
        )


class BarberPoleProgressBar(QProgressBar):
    """Orange progress fill with a quiet animated white barber-pole pattern."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stripe_offset = 0
        self._stripe_timer = QTimer(self)
        self._stripe_timer.setInterval(55)
        self._stripe_timer.timeout.connect(self._advance_stripes)

    def _advance_stripes(self) -> None:
        self._stripe_offset = (self._stripe_offset + 1) % 16
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._stripe_timer.start()

    def hideEvent(self, event) -> None:
        self._stripe_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        border = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(border, QColor("#000000"))
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawRect(border)

        inner = border.adjusted(1, 1, -1, -1)
        if self.maximum() == self.minimum():
            chunk = inner
        else:
            span = self.maximum() - self.minimum()
            progress = max(0.0, min(1.0, (self.value() - self.minimum()) / span))
            chunk = inner.adjusted(0, 0, round(-inner.width() * (1.0 - progress)), 0)

        if chunk.width() > 0:
            painter.fillRect(chunk, QColor("#ff7a00"))
            painter.save()
            painter.setClipRect(chunk)
            stripe_pen = QPen(QColor(255, 255, 255, 105), 1)
            painter.setPen(stripe_pen)
            step = 16
            start = inner.left() - inner.height() - step + self._stripe_offset
            end = inner.right() + inner.height() + step
            for x in range(start, end, step):
                painter.drawLine(x, inner.bottom(), x + inner.height(), inner.top())
            painter.restore()

        if self.isTextVisible() and self.text():
            painter.setPen(QColor("#ffffff"))
            painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, self.text())


class StemKartSizeGrip(QSizeGrip):
    """A minimal resize affordance without Qt's filled corner square."""

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(self.palette().text().color())
        pen.setWidth(1)
        painter.setPen(pen)
        edge = min(self.width(), self.height()) - 1
        for offset in (4, 8, 12):
            painter.drawLine(edge - offset, edge, edge, edge - offset)


class MonochromeIconProvider(QFileIconProvider):
    """Keep filesystem folder and file icons consistent with StemKart's UI."""

    def __init__(self, color: str = "#ffffff") -> None:
        super().__init__()
        self.folder_icon = QIcon(str(resource_path("icons", "folder.svg")))
        self.file_icon = QIcon(str(resource_path("icons", "file.svg")))
        if color.casefold() != "#ffffff":
            self.folder_icon = tinted_icon(self.folder_icon, color)
            self.file_icon = tinted_icon(self.file_icon, color)

    def icon(self, value):
        if value == QFileIconProvider.IconType.Folder:
            return self.folder_icon
        if value == QFileIconProvider.IconType.File:
            return self.file_icon
        if isinstance(value, QFileInfo) and value.isDir():
            return self.folder_icon
        if isinstance(value, QFileInfo):
            return self.file_icon
        if isinstance(value, QFileIconProvider.IconType):
            return self.file_icon
        return super().icon(value)


class OrangeBranchStyle(QProxyStyle):
    """Use the orange accent for every folder-tree expand arrow."""

    def __init__(self) -> None:
        super().__init__("Fusion")

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if element == QStyle.PrimitiveElement.PE_IndicatorBranch:
            selected_option = QStyleOption(option)
            for role in (
                QPalette.ColorRole.Text,
                QPalette.ColorRole.WindowText,
                QPalette.ColorRole.ButtonText,
                QPalette.ColorRole.HighlightedText,
                QPalette.ColorRole.Dark,
                QPalette.ColorRole.Mid,
            ):
                selected_option.palette.setColor(role, QColor("#ff7a00"))
            option = selected_option
        super().drawPrimitive(element, option, painter, widget)


def load_bundled_font() -> str:
    """Load the bundled IBM Plex Sans faces and return their family name."""
    family = "IBM Plex Sans"
    for weight in ("Regular", "Medium", "Bold"):
        font_id = QFontDatabase.addApplicationFont(
            str(resource_path("fonts", f"IBMPlexSans-{weight}.ttf"))
        )
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                family = families[0]
    return family


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
    return (
        path.is_file()
        and not path.name.startswith("._")
        and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


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
            "reference_roots": [],
        }
        self._reference_cache_dirty = False
        self._load()
        self._infer_reference_roots()
        self.register_reference_root(self.legacy_root, recursive=True)

    def _migrate_legacy_library(self) -> None:
        """Create the private metadata root without copying source audio files."""
        self.root.parent.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)

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

    def reference_roots(self) -> list[tuple[Path, bool]]:
        roots = self.data.get("reference_roots", [])
        if not isinstance(roots, list):
            return []
        result: list[tuple[Path, bool]] = []
        for record in roots:
            if not isinstance(record, dict):
                continue
            value = record.get("path")
            if isinstance(value, str) and value:
                result.append((Path(value), bool(record.get("recursive", False))))
        return result

    def _infer_reference_roots(self) -> None:
        """Turn groups of file references into directory roots for live updates."""
        parent_counts: dict[Path, int] = {}
        for record in self.entries.values():
            if not isinstance(record, dict) or record.get("kind") != "reference":
                continue
            if record.get("reference_root"):
                continue
            path = Path(record.get("path", ""))
            if path.is_file():
                parent = path.parent.resolve()
                parent_counts[parent] = parent_counts.get(parent, 0) + 1
        for parent, count in parent_counts.items():
            if count >= 2:
                self.register_reference_root(parent, recursive=False)

    def register_reference_root(self, path: Path, recursive: bool) -> None:
        resolved = path.resolve()
        if not resolved.is_dir() or resolved == self.root.resolve():
            return
        roots = self.data.setdefault("reference_roots", [])
        if not isinstance(roots, list):
            roots = []
            self.data["reference_roots"] = roots
        existing = next(
            (
                item
                for item in roots
                if isinstance(item, dict) and item.get("path") == str(resolved)
            ),
            None,
        )
        if isinstance(existing, dict):
            changed = existing.get("recursive") != bool(recursive)
            existing["recursive"] = bool(recursive)
            if "complete" not in existing:
                existing["complete"] = True
                changed = True
            elif changed:
                existing["complete"] = False
            if changed:
                self.save()
            return
        roots.append({"path": str(resolved), "recursive": bool(recursive), "complete": False})
        self.save()

    def mark_reference_root_complete(self, root: Path) -> None:
        resolved = str(root.resolve())
        changed = False
        for record in self.data.get("reference_roots", []):
            if isinstance(record, dict) and record.get("path") == resolved and not record.get("complete", True):
                record["complete"] = True
                changed = True
        if changed:
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

    def _record(
        self,
        path: Path,
        kind: str,
        source: Path | None = None,
        reference_root: Path | None = None,
    ) -> None:
        resolved = path.resolve()
        record: dict[str, str] = {
            "path": str(resolved),
            "name": resolved.name,
            "kind": kind,
            "source": str(source.resolve()) if source else "",
        }
        if reference_root is not None:
            record["reference_root"] = str(reference_root.resolve())
        self.entries[str(resolved)] = record

    def add_references(
        self,
        paths: list[Path],
        reference_root: Path | None = None,
        recursive: bool = False,
    ) -> int:
        if reference_root is not None:
            self.register_reference_root(reference_root, recursive)
        count = 0
        for path in paths:
            if is_audio_file(path):
                key = str(path.resolve())
                if key not in self.entries:
                    count += 1
                self._record(path, "reference", reference_root=reference_root)
        self.save()
        return count

    def reconcile_references(
        self,
        scans: list[tuple[Path, bool, list[Path]]],
    ) -> bool:
        """Merge background scans into metadata without copying source files."""
        scanned_roots = {str(root.resolve()) for root, _recursive, _paths in scans}
        found_by_root = {
            str(root.resolve()): {path.resolve() for path in paths}
            for root, _recursive, paths in scans
        }
        changed = False
        for root, _recursive, paths in scans:
            resolved_root = root.resolve()
            for path in paths:
                if not is_audio_file(path):
                    continue
                key = str(path.resolve())
                record = self.entries.get(key)
                if (
                    not isinstance(record, dict)
                    or record.get("kind") != "reference"
                    or record.get("reference_root") != str(resolved_root)
                ):
                    self._record(path, "reference", reference_root=resolved_root)
                    changed = True

        for key, record in list(self.entries.items()):
            if not isinstance(record, dict) or record.get("kind") != "reference":
                continue
            reference_root = record.get("reference_root")
            path = Path(record.get("path", key))
            if reference_root in scanned_roots:
                if path.resolve() not in found_by_root[reference_root]:
                    self.entries.pop(key, None)
                    changed = True
            elif not reference_root and not path.is_file():
                self.entries.pop(key, None)
                changed = True
        if changed:
            self.save()
        return changed

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

    def remove_library_directory(self, path: Path, recursive: bool = False) -> bool:
        resolved = path.resolve()
        if (
            not path.is_dir()
            or not self.is_library_path(path)
            or resolved == self.root.resolve()
            or resolved in self.protected_directories()
        ):
            return False
        try:
            if recursive:
                shutil.rmtree(path)
            else:
                path.rmdir()
        except OSError:
            return False
        removed_prefix = resolved
        for key, record in list(self.entries.items()):
            if not isinstance(record, dict):
                continue
            value = record.get("path")
            if not isinstance(value, str):
                continue
            try:
                Path(value).resolve().relative_to(removed_prefix)
            except ValueError:
                continue
            self.entries.pop(key, None)
        self.save()
        return True

    def remove_library_files(self, paths: list[Path]) -> list[Path]:
        deleted: list[Path] = []
        for path in paths:
            resolved = path.resolve()
            if not path.is_file() or not self.is_library_path(path):
                continue
            try:
                path.unlink()
            except OSError:
                continue
            deleted.append(resolved)
            self.entries.pop(str(resolved), None)
        if deleted:
            self.save()
        return deleted

    def add_trimmed(self, path: Path) -> None:
        self._record(path, "managed")
        self.save()

    def add_recorded(self, path: Path) -> None:
        self._record(path, "recorded")
        self.save()

    def scan_folder(
        self,
        folder: Path,
        recursive: bool,
        should_stop: Callable[[], bool] | None = None,
        on_batch: Callable[[list[Path]], None] | None = None,
        on_directory: Callable[[Path], None] | None = None,
    ) -> list[Path]:
        if not folder.is_dir():
            return []
        try:
            if not recursive:
                if on_directory:
                    on_directory(folder)
                paths = sorted(
                    (path for path in folder.iterdir() if is_audio_file(path)),
                    key=lambda path: path.name.casefold(),
                )
                if on_batch and paths:
                    on_batch(paths)
                return paths
            audio_paths: list[Path] = []
            batch: list[Path] = []
            for root, _directories, filenames in os.walk(folder, followlinks=False):
                if should_stop and should_stop():
                    return []
                root_path = Path(root)
                if on_directory:
                    on_directory(root_path)
                for path in (root_path / name for name in filenames):
                    if not is_audio_file(path):
                        continue
                    audio_paths.append(path)
                    batch.append(path)
                    if on_batch and len(batch) >= 128:
                        on_batch(batch)
                        batch = []
            if on_batch and batch:
                on_batch(batch)
            return sorted(audio_paths, key=lambda path: path.name.casefold())
        except OSError:
            return []

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
            if is_audio_file(path):
                paths.append(path)
        return sorted(set(paths), key=lambda path: path.name.casefold())

    def reference_count(self) -> int:
        """Return the cached reference count without stat-ing every source file."""
        return sum(
            1
            for record in self.entries.values()
            if isinstance(record, dict) and record.get("kind") == "reference"
        )

    def cached_files_for_directory(self, folder: Path, recursive: bool) -> list[Path] | None:
        """Return cached reference locations for a directory, or None if uncached."""
        resolved_folder = folder.resolve()
        matching_roots: list[tuple[Path, bool]] = []
        for root, root_recursive in self.reference_roots():
            resolved_root = root.resolve()
            try:
                resolved_folder.relative_to(resolved_root)
            except ValueError:
                continue
            if not root_recursive and resolved_folder != resolved_root:
                continue
            if recursive and not root_recursive:
                continue
            matching_roots.append((resolved_root, root_recursive))
        if not matching_roots:
            return None

        paths: set[Path] = set()
        for path in self.reference_files():
            resolved_path = path.resolve()
            if recursive:
                try:
                    resolved_path.relative_to(resolved_folder)
                except ValueError:
                    continue
                paths.add(resolved_path)
            elif resolved_path.parent == resolved_folder:
                paths.add(resolved_path)
        return sorted(paths, key=lambda path: path.name.casefold())

    def reference_cache_complete_for_directory(self, folder: Path) -> bool:
        resolved_folder = folder.resolve()
        matches: list[bool] = []
        for root, _recursive in self.reference_roots():
            resolved_root = root.resolve()
            try:
                resolved_folder.relative_to(resolved_root)
            except ValueError:
                continue
            record = next(
                (
                    item
                    for item in self.data.get("reference_roots", [])
                    if isinstance(item, dict) and item.get("path") == str(resolved_root)
                ),
                None,
            )
            matches.append(not isinstance(record, dict) or record.get("complete", True))
        return any(matches)

    def reference_root_for_directory(self, folder: Path, recursive: bool) -> Path | None:
        resolved_folder = folder.resolve()
        matches: list[Path] = []
        for root, root_recursive in self.reference_roots():
            resolved_root = root.resolve()
            try:
                resolved_folder.relative_to(resolved_root)
            except ValueError:
                continue
            if not root_recursive and resolved_folder != resolved_root:
                continue
            if recursive and not root_recursive and resolved_folder != resolved_root:
                continue
            matches.append(resolved_root)
        return max(matches, key=lambda path: len(path.parts)) if matches else None

    def cache_reference_paths(self, paths: list[Path], reference_root: Path) -> int:
        changed = 0
        resolved_root = reference_root.resolve()
        for path in paths:
            if not is_audio_file(path):
                continue
            key = str(path.resolve())
            record = self.entries.get(key)
            if (
                not isinstance(record, dict)
                or record.get("kind") != "reference"
                or record.get("reference_root") != str(resolved_root)
            ):
                self._record(path, "reference", reference_root=resolved_root)
                changed += 1
        if changed:
            self._reference_cache_dirty = True
        return changed

    def reconcile_reference_directory(
        self,
        folder: Path,
        paths: list[Path],
        reference_root: Path,
    ) -> bool:
        resolved_folder = folder.resolve()
        resolved_root = reference_root.resolve()
        found = {path.resolve() for path in paths if is_audio_file(path)}
        changed = bool(self.cache_reference_paths(paths, resolved_root))
        for key, record in list(self.entries.items()):
            if not isinstance(record, dict) or record.get("kind") != "reference":
                continue
            if record.get("reference_root") != str(resolved_root):
                continue
            path = Path(record.get("path", key)).resolve()
            try:
                path.relative_to(resolved_folder)
            except ValueError:
                continue
            if path not in found:
                self.entries.pop(key, None)
                changed = True
        if changed:
            self.mark_reference_root_complete(resolved_root)
            self.save()
            self._reference_cache_dirty = False
        elif self._reference_cache_dirty:
            # Batch updates may already have populated the dictionary before
            # the final reconciliation signal arrives.
            self.save()
            self._reference_cache_dirty = False
            self.mark_reference_root_complete(resolved_root)
        else:
            self.mark_reference_root_complete(resolved_root)
        return changed

    def remove_missing_references(self, reference_root: Path) -> bool:
        """Drop dead reference paths without rescanning the source tree."""
        resolved_root = str(reference_root.resolve())
        changed = False
        for key, record in list(self.entries.items()):
            if not isinstance(record, dict) or record.get("kind") != "reference":
                continue
            if record.get("reference_root") != resolved_root:
                continue
            path = Path(record.get("path", key))
            if not path.is_file():
                self.entries.pop(key, None)
                changed = True
        if changed:
            self.save()
        return changed

    def flush_reference_cache(self) -> None:
        """Persist reference batches without touching the source files."""
        if self._reference_cache_dirty:
            self.save()
            self._reference_cache_dirty = False


class FolderScanWorker(QObject):
    completed = pyqtSignal(object, int, bool)
    batch = pyqtSignal(object, int, object)
    directory = pyqtSignal(object, int, object)

    def __init__(
        self,
        library: SampleLibrary,
        folder: Path,
        recursive: bool,
        generation: int,
        reference_root: Path | None = None,
    ) -> None:
        super().__init__()
        self.library = library
        self.folder = folder
        self.recursive = recursive
        self.generation = generation
        self.reference_root = reference_root

    @pyqtSlot()
    def run(self) -> None:
        thread = QThread.currentThread()
        paths = self.library.scan_folder(
            self.folder,
            self.recursive,
            should_stop=lambda: thread.isInterruptionRequested(),
            on_batch=lambda paths: self.batch.emit(paths, self.generation, self.reference_root),
            on_directory=lambda directory: self.directory.emit(
                directory,
                self.generation,
                self.reference_root,
            ),
        )
        self.completed.emit(paths, self.generation, thread.isInterruptionRequested())


class ReferenceDirectorySyncWorker(QObject):
    """Refresh only directories that actually changed on disk."""

    completed = pyqtSignal(object)
    cancelled = pyqtSignal()

    def __init__(self, library: SampleLibrary, changes: list[tuple[Path, Path]]) -> None:
        super().__init__()
        self.library = library
        self.changes = changes

    @pyqtSlot()
    def run(self) -> None:
        results: list[tuple[Path, Path, list[Path]]] = []
        for folder, reference_root in self.changes:
            if QThread.currentThread().isInterruptionRequested():
                self.cancelled.emit()
                return
            paths = self.library.scan_folder(folder, recursive=False) if folder.is_dir() else []
            results.append((folder, reference_root, paths))
        self.completed.emit(results)


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
        button = ThemedIconButton()
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
        availability = wav_availability(path) if path is not None and path.exists() else "unavailable"
        enabled = availability == "usable"
        self.position_slider.setRange(0, 0)
        self.position_label.setText("0:00")
        self.duration_label.setText("0:00")
        if enabled:
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.status.setText(path.name)
        elif availability == "empty":
            self.file_path = None
            self.player.setSource(QUrl())
            self.status.setText("No audible content")
        else:
            self.file_path = None
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
        self._play_when_ready = False

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
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
        button = ThemedIconButton()
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
        self._play_when_ready = False
        self.player.stop()
        self.file_path = path if path and path.exists() else None
        self.clip_start = 0
        self.clip_end = None
        self.position_slider.setRange(0, 0)
        self.position_label.setText("0:00")
        self.duration_label.setText("0:00")
        if self.file_path:
            self.file_label.setText(f"Loading {self.file_path.name}...")
            self.player.setSource(QUrl.fromLocalFile(str(self.file_path)))
        else:
            self.file_label.setText("Select an audio file")
            self.player.setSource(QUrl())
        self.playhead_changed.emit(0, 0)
        self._set_controls_enabled(False)

    def set_clip(self, start_ms: int, end_ms: int | None) -> None:
        self.clip_start = max(0, start_ms)
        self.clip_end = end_ms if end_ms and end_ms > self.clip_start else None

    def _play(self) -> None:
        if not self.file_path:
            return
        if self.player.mediaStatus() not in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.EndOfMedia,
        ):
            self._play_when_ready = True
            return
        end_position = self.clip_end or self.player.duration()
        at_end = (
            self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia
            or (end_position > 0 and self.player.position() >= end_position)
        )
        if at_end:
            self.player.stop()
            self.player.setPosition(self.clip_start)
            QTimer.singleShot(0, self.player.play)
            return
        self.player.play()

    def _pause(self) -> None:
        self.player.pause()

    def play_selection(self, start_ms: int, end_ms: int | None) -> None:
        self.set_clip(start_ms, end_ms)
        self._play()

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

    def _media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if not self.file_path:
            return
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            self.file_label.setText(self.file_path.name)
            self._set_controls_enabled(True)
            if self._play_when_ready:
                self._play_when_ready = False
                self._play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._play_when_ready = False
            self._set_controls_enabled(False)

    def _playback_state_changed(self, _state: QMediaPlayer.PlaybackState) -> None:
        pass

    def _playback_error(self, _error: QMediaPlayer.Error) -> None:
        self._play_when_ready = False
        self._set_controls_enabled(False)
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
        self._process: subprocess.Popen | None = None

    def cancel(self) -> None:
        self.requestInterruption()
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

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
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self._process = process
            while True:
                if self.isInterruptionRequested():
                    self.cancel()
                    return
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except OSError as exc:
            self.failed.emit(f"Could not run FFmpeg: {exc}")
            return
        finally:
            self._process = None
        if self.isInterruptionRequested():
            return
        if process.returncode != 0:
            details = stderr.decode("utf-8", errors="replace").strip()
            self.failed.emit(details or "FFmpeg could not decode this file.")
            return
        samples = np.frombuffer(stdout, dtype=np.float32).copy()
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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        background = self.palette().base().color()
        foreground = self.palette().text().color()
        painter.fillRect(self.rect(), background)
        if not self.samples.size:
            painter.setPen(foreground)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Select an audio file to load its waveform")
            return

        # Keep every painted pixel inside the widget. In particular, drawing a
        # thick line at ``width`` or ``height`` lets Qt's pen extend into the
        # neighboring layout and produces a stray vertical edge.
        plot_rect = self.rect().adjusted(1, 1, -1, -1)
        if plot_rect.width() < 1 or plot_rect.height() < 1:
            return
        painter.save()
        painter.setClipRect(plot_rect)
        width = plot_rect.width()
        height = plot_rect.height()
        view_ratio = max(0.001, self.view_end_ratio - self.view_start_ratio)
        selected_left = round((self.start_ratio - self.view_start_ratio) / view_ratio * width)
        selected_right = round((self.end_ratio - self.view_start_ratio) / view_ratio * width)
        selected_left = max(0, min(width - 1, selected_left))
        selected_right = max(0, min(width - 1, selected_right))
        view_start = round(self.view_start_ratio * len(self.samples))
        view_end = max(view_start + 1, round(self.view_end_ratio * len(self.samples)))
        view_length = view_end - view_start
        bin_size = max(1, view_length // width)
        center = plot_rect.top() + height // 2
        waveform_color = QColor("#ff7a00")
        visible_samples = np.abs(self.samples[view_start:view_end])
        column_count = min(width, len(visible_samples) // bin_size)
        if column_count:
            peaks = visible_samples[: column_count * bin_size].reshape(column_count, bin_size).max(axis=1)
            painter.setPen(QPen(waveform_color))
            for x, peak in enumerate(peaks):
                half_height = max(1, round(float(peak) * (height // 2 - 4)))
                painter.drawLine(
                    plot_rect.left() + x,
                    center - half_height,
                    plot_rect.left() + x,
                    center + half_height,
                )
        painter.setPen(QPen(foreground, 2))
        painter.drawLine(plot_rect.left() + selected_left, plot_rect.top(), plot_rect.left() + selected_left, plot_rect.bottom())
        painter.drawLine(plot_rect.left() + selected_right, plot_rect.top(), plot_rect.left() + selected_right, plot_rect.bottom())
        if self.playhead_ratio is not None and self.view_start_ratio <= self.playhead_ratio <= self.view_end_ratio:
            playhead_x = round(
                (self.playhead_ratio - self.view_start_ratio) / view_ratio * width
            )
            playhead_x = max(0, min(width - 1, playhead_x))
            painter.setPen(QPen(QColor("#ff0000"), 2))
            painter.drawLine(plot_rect.left() + playhead_x, plot_rect.top(), plot_rect.left() + playhead_x, plot_rect.bottom())
        painter.restore()


class WaveformEditor(QGroupBox):
    saved = pyqtSignal(object)
    working_file_changed = pyqtSignal(object)
    playhead_requested = pyqtSignal(int)
    split_requested = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__("")
        self.setObjectName("sampleTrimmingContainer")
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
        self.playback_time_label = QLabel("0:00 - 0:00")
        self.playback_time_label.setFixedWidth(106)
        self.playback_time_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.playback_time_label.setToolTip("Current playback position and total duration")
        controls.addWidget(self.playback_time_label)
        self.set_start_button = ThemedTextButton("Set Start")
        self.set_start_button.setToolTip("Set the start marker to the current playhead position")
        self.set_start_button.clicked.connect(self._set_start_from_playhead)
        controls.addWidget(self.set_start_button)
        self.set_end_button = ThemedTextButton("Set End")
        self.set_end_button.setToolTip("Set the end marker to the current playhead position")
        self.set_end_button.clicked.connect(self._set_end_from_playhead)
        controls.addWidget(self.set_end_button)
        self.range_label = QLabel("Full file")
        self.range_label.setFixedWidth(106)
        self.range_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.range_label.setToolTip("Trim start and end")
        controls.addWidget(self.range_label)
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
        button = ThemedIconButton()
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
        self.playback_time_label.setText(f"{format_time(position_ms)} - {format_time(duration_ms)}")
        self._set_action_state(bool(self.samples.size))

    def _set_start_from_playhead(self) -> None:
        self.waveform.set_marker_from_playhead("start")

    def _set_end_from_playhead(self) -> None:
        self.waveform.set_marker_from_playhead("end")

    def _confirm_split_sample(self) -> None:
        if not self.samples.size:
            return
        start, end = self.waveform.range_samples()
        if ask_confirmation(
            self,
            "Split sample",
            "Split the current sample into stems now?",
            accept_label="Split sample",
            reject_label="Cancel",
        ):
            # Splitting the current selection is also a trim edit from the
            # editor's point of view. Keep the full source available so Undo
            # still works after the split has been launched.
            if (start != 0 or end != len(self.samples)) and self.undo_state is None:
                self.undo_state = (self.samples.copy(), self.sample_rate, self.working_name)
                self._set_action_state(True)
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
            self.decode_thread.cancel()
            self.decode_thread.wait(1500)
        self.file_path = path if path and path.exists() else None
        self.samples = np.array([], dtype=np.float32)
        self.working_name = self.file_path.name if self.file_path else ""
        self.undo_state = None
        self.waveform.set_samples(self.samples, self.sample_rate)
        self.playback_time_label.setText("0:00 - 0:00")
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
        destination = choose_save_path(self, "Save trimmed sample", default, "WAV files (*.wav)")
        if destination is None or not confirm_overwrite(self, Path(destination)):
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
            show_message(self, "Could not save sample", str(exc))
            return
        self.saved.emit(Path(destination))

    def close_editor(self) -> None:
        if self.decode_thread and self.decode_thread.isRunning():
            self.decode_thread.cancel()
            self.decode_thread.wait(1500)
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
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(4)

        self.input_combo = QComboBox()
        self.input_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.input_combo.setFixedHeight(30)
        self.input_combo.setToolTip(
            "Choose a physical input, system output, or active application stream"
        )
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
        self.device_refresh_timer = QTimer(self)
        self.device_refresh_timer.setInterval(2000)
        self.device_refresh_timer.timeout.connect(self._refresh_devices)
        self.device_refresh_timer.start()
        self._refresh_devices()
        self._refresh_controls()

    def _icon_button(self, name: str, icon_name: str) -> QPushButton:
        button = ThemedIconButton()
        button.setFixedSize(30, 30)
        button.setIcon(bundled_icon(icon_name))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(name)
        button.setAccessibleName(name)
        return button

    def _refresh_devices(self) -> None:
        if self.recording_state != "idle":
            return
        selected_label = self.input_combo.currentText()
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
            selected_index = next(
                (
                    index
                    for index in range(self.input_combo.count())
                    if self.input_combo.itemText(index) == selected_label
                ),
                default_target_index,
            )
            self.input_combo.setCurrentIndex(selected_index)
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
        # A sink is not itself a capture source.  Passing its node id to
        # pw-record causes PipeWire to fall back to the default source on
        # some installations.  Create an unlinked recorder stream and link
        # it explicitly to the selected node's output/monitor ports below.
        target_argument = "0"
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

        if target.kind in {"pipewire_app", "pipewire_sink"}:
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


class DarkTitleBar(QWidget):
    """A black client-side title bar with native window actions."""

    def __init__(self, window: QWidget, dialog: bool = False) -> None:
        super().__init__(window)
        self.window = window
        self.dialog = dialog
        self.setObjectName("dialogTitleBar" if dialog else "titleBar")
        self.setFixedHeight(36)

        layout = QGridLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setHorizontalSpacing(2)
        layout.setColumnMinimumWidth(0, 0 if dialog else 86)
        layout.setColumnMinimumWidth(2, 34 if dialog else 116)
        layout.setColumnStretch(1, 1)

        title = QLabel(window.windowTitle() or APP_NAME)
        title.setObjectName("dialogTitleBarTitle" if dialog else "titleBarTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title, 0, 1)

        buttons = QWidget()
        buttons.setObjectName("dialogTitleBarButtons" if dialog else "titleBarButtons")
        buttons_layout = QHBoxLayout(buttons)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(2)
        actions = (
            (("×", "Close", self.window.close),)
            if dialog
            else (
                ("◐", "Toggle light mode", self.window.toggle_theme),
                ("—", "Minimize", self.window.showMinimized),
                ("□", "Maximize", self.window.toggle_maximized),
                ("×", "Close", self.window.close),
            )
        )
        for label, name, callback in actions:
            button = QPushButton(label)
            button.setObjectName("windowButton")
            button.setFixedSize(26, 26)
            button.setToolTip(name)
            button.setAccessibleName(name)
            button.clicked.connect(callback)
            buttons_layout.addWidget(button)
        layout.addWidget(buttons, 0, 2, alignment=Qt.AlignmentFlag.AlignRight)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            window_handle = self.window.windowHandle()
            if window_handle is not None:
                window_handle.startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.dialog:
            self.window.toggle_maximized()
        super().mouseDoubleClickEvent(event)


def show_message(parent: QWidget, title: str, message: str) -> None:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(message)
    dialog.setIcon(QMessageBox.Icon.NoIcon)
    ok_button = dialog.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
    dialog.setDefaultButton(ok_button)
    dialog.exec()


def ask_confirmation(
    parent: QWidget,
    title: str,
    message: str,
    accept_label: str = "Yes",
    reject_label: str = "No",
) -> bool:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setText(message)
    dialog.setIcon(QMessageBox.Icon.NoIcon)
    dialog.addButton(reject_label, QMessageBox.ButtonRole.RejectRole)
    accept_button = dialog.addButton(accept_label, QMessageBox.ButtonRole.AcceptRole)
    dialog.setDefaultButton(accept_button)
    dialog.exec()
    return dialog.clickedButton() is accept_button


def normalize_message_box(dialog: QMessageBox) -> None:
    """Remove platform-specific message and button icons from a message box."""
    dialog.setIcon(QMessageBox.Icon.NoIcon)
    for label in dialog.findChildren(QLabel):
        label.setWordWrap(True)
    for button in dialog.buttons():
        button.setIcon(QIcon())


def finalize_dialog_content_layout(dialog: QDialog) -> None:
    """Keep standard dialog content below the client-side title bar."""
    layout = dialog.layout()
    if layout is None:
        return
    target_top = dialog.property("stemkart_dialog_content_top")
    if not isinstance(target_top, int):
        margins = layout.contentsMargins()
        target_top = margins.top() + 36
        dialog.setProperty("stemkart_dialog_content_top", target_top)
    margins = layout.contentsMargins()
    if margins.top() != target_top:
        layout.setContentsMargins(
            margins.left(),
            target_top,
            margins.right(),
            margins.bottom(),
        )
    layout.activate()
    size_hint = dialog.sizeHint()
    minimum_width = 360 if isinstance(dialog, QInputDialog) else 300
    dialog.resize(
        max(dialog.width(), minimum_width, size_hint.width()),
        max(dialog.height(), size_hint.height()),
    )
    title_bar = getattr(dialog, "_stemkart_title_bar", None)
    if title_bar is not None:
        title_bar.setGeometry(0, 0, dialog.width(), 36)
        title_bar.raise_()


def style_dialog(dialog: QDialog) -> None:
    """Give every Qt dialog the same black client-side chrome as the main window."""
    if getattr(dialog, "_stemkart_title_bar", None) is not None:
        return

    dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowType.FramelessWindowHint)
    application = QApplication.instance()
    theme_stylesheets = getattr(application, "_stemkart_theme_stylesheets", {}) if application else {}
    current_stylesheet = theme_stylesheets.get(bool(getattr(application, "_stemkart_light_mode", False)))
    if current_stylesheet:
        dialog.setStyleSheet(current_stylesheet)
    dialog.setSizeGripEnabled(False)
    minimum_width = 300
    if isinstance(dialog, QMessageBox):
        minimum_width = 420
        normalize_message_box(dialog)
    elif isinstance(dialog, QInputDialog):
        minimum_width = 360
    dialog.setMinimumWidth(max(minimum_width, dialog.minimumWidth()))
    layout = dialog.layout()
    title_bar = DarkTitleBar(dialog, dialog=True)
    dialog._stemkart_title_bar = title_bar
    if isinstance(dialog, QFileDialog):
        foreground = application.palette().text().color().name() if application else "#ffffff"
        icon_provider = MonochromeIconProvider(foreground)
        dialog.setIconProvider(icon_provider)
        dialog._stemkart_file_icon_provider = icon_provider
        for button in dialog.findChildren(QToolButton):
            if not button.icon().isNull():
                button.setIcon(tinted_icon(button.icon(), foreground))
    dialog.adjustSize()
    dialog.resize(max(dialog.width(), minimum_width), dialog.height())
    if layout is not None and not isinstance(dialog, (QInputDialog, QFileDialog)):
        margins = layout.contentsMargins()
        layout.setContentsMargins(
            margins.left(),
            margins.top() + 36,
            margins.right(),
            margins.bottom(),
        )
        layout.activate()
        size_hint = dialog.sizeHint()
        dialog.resize(
            max(dialog.width(), minimum_width, size_hint.width()),
            max(dialog.height(), size_hint.height()),
        )
    title_bar.setGeometry(0, 0, dialog.width(), 36)
    title_bar.raise_()


def choose_save_path(parent: QWidget, title: str, default: Path, file_filter: str) -> str | None:
    """Show a themed save dialog without Qt's platform overwrite prompt."""
    dialog = QFileDialog(parent, title, str(default), file_filter)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    dialog.setOption(QFileDialog.Option.DontConfirmOverwrite, True)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    selected = dialog.selectedFiles()
    return selected[0] if selected else None


def confirm_overwrite(parent: QWidget, path: Path, multiple: bool = False) -> bool:
    """Ask for overwrite confirmation using StemKart's own dialog chrome."""
    if not path.exists():
        return True

    if multiple:
        title = "Replace existing files?"
        message = "One or more files already exist in this folder.\n\nDo you want to replace them?"
    else:
        title = "Replace existing file?"
        message = f"{path.name} already exists.\n\nDo you want to replace it?"
    return ask_confirmation(parent, title, message, accept_label="Replace", reject_label="Cancel")


class DialogThemeFilter(QObject):
    """Apply the custom title bar when Qt creates standard dialogs internally."""

    def eventFilter(self, watched, event) -> bool:
        if isinstance(watched, QDialog):
            # Polish occurs before the platform surface is shown. Changing
            # window flags from a modal Show event can leave exec() stuck in a
            # nested platform loop.
            if event.type() in (QEvent.Type.Polish, QEvent.Type.WindowTitleChange):
                style_dialog(watched)
            elif event.type() == QEvent.Type.Show:
                if isinstance(watched, QMessageBox):
                    normalize_message_box(watched)
                elif isinstance(watched, (QInputDialog, QFileDialog)):
                    finalize_dialog_content_layout(watched)
            elif event.type() == QEvent.Type.Resize:
                minimum_width = 420 if isinstance(watched, QMessageBox) else 360 if isinstance(watched, QInputDialog) else 0
                if minimum_width and watched.width() < minimum_width:
                    watched.setMinimumWidth(minimum_width)
                    watched.resize(minimum_width, watched.height())
                title_bar = getattr(watched, "_stemkart_title_bar", None)
                if title_bar is not None:
                    title_bar.setGeometry(0, 0, watched.width(), 36)
                    title_bar.raise_()
                if isinstance(watched, (QInputDialog, QFileDialog)):
                    finalize_dialog_content_layout(watched)
        return super().eventFilter(watched, event)


class AudioSplitter(QMainWindow):
    def __init__(self, light_mode: bool = False) -> None:
        super().__init__()
        self.source_path: Path | None = None
        self.split_thread: SplitThread | None = None
        self.temp_root = Path(tempfile.mkdtemp(prefix="audio-splitter-"))
        self.cards: dict[str, StemCard] = {}
        self.sample_library = SampleLibrary()
        self.current_samples_directory: Path | None = None
        self.last_explorer_directory = self.sample_library.last_directory
        self.showing_library = False
        self.light_mode = light_mode
        self._folder_scan_generation = 0
        self._folder_scan_threads: list[QThread] = []
        self._folder_scan_workers: list[FolderScanWorker] = []
        self._folder_scan_reference_context: dict[int, tuple[Path, Path | None] | None] = {}
        self._folder_scan_paths: dict[QThread, Path] = {}
        self._reference_sync_thread: QThread | None = None
        self._reference_sync_worker: ReferenceDirectorySyncWorker | None = None
        self._reference_change_queue: dict[str, tuple[Path, Path]] = {}
        self._reference_sync_pending = False
        self._reference_scan_cached_since_save = 0
        self._library_tree_built = False
        self._library_tree_dirty = True
        self._explorer_list_directory: Path | None = None
        self._library_list_directory: Path | None = None
        self._library_list_is_references = False
        self._library_list_dirty = False
        self._explorer_status_text = ""
        self._library_status_text = ""

        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.resize(1120, 760)
        self.setMinimumSize(800, 600)
        self.setAcceptDrops(True)

        self._build_ui()
        self.statusBar().setVisible(False)
        self._reference_watcher = QFileSystemWatcher(self)
        self._reference_watcher.directoryChanged.connect(self._reference_directory_changed)
        self._reference_change_timer = QTimer(self)
        self._reference_change_timer.setSingleShot(True)
        self._reference_change_timer.setInterval(250)
        self._reference_change_timer.timeout.connect(self._start_reference_change_sync)
        self._reference_view_refresh_timer = QTimer(self)
        self._reference_view_refresh_timer.setSingleShot(True)
        self._reference_view_refresh_timer.setInterval(250)
        self._reference_view_refresh_timer.timeout.connect(self._refresh_reference_contents)
        for root, recursive in self.sample_library.reference_roots():
            self._watch_reference_root(root, recursive)
        self._size_grip = StemKartSizeGrip(self)
        self._size_grip.setFixedSize(16, 16)
        self._size_grip.move(self.width() - self._size_grip.width(), self.height() - self._size_grip.height())
        self._size_grip.show()

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def toggle_theme(self) -> None:
        self.light_mode = not self.light_mode
        save_app_setting("light_mode", self.light_mode)
        application = QApplication.instance()
        if application is None:
            return
        apply_style(application, light_mode=self.light_mode)
        theme_stylesheets = getattr(application, "_stemkart_theme_stylesheets", {})
        current_stylesheet = theme_stylesheets.get(self.light_mode)
        if current_stylesheet:
            # Re-polish only StemKart's window subtree. Replacing the
            # application-wide stylesheet repolishes every row in large
            # QListWidgets and causes multi-second theme changes.
            self.main_frame.setStyleSheet(current_stylesheet)
        foreground = application.palette().text().color().name()
        for button in self.findChildren(QPushButton):
            if isinstance(button, ThemedIconButton):
                button._update_icon_color()
            elif not button.icon().isNull():
                button.setIcon(tinted_icon(button.icon(), foreground, size=24))
        self.folder_icon_provider = MonochromeIconProvider("#ff7a00")
        self.folder_model.setIconProvider(self.folder_icon_provider)
        self.folder_tree.viewport().update()
        self._style_tabs()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_size_grip"):
            self._size_grip.move(
                self.width() - self._size_grip.width(),
                self.height() - self._size_grip.height(),
            )

    def _build_ui(self) -> None:
        shell = QWidget()
        shell.setObjectName("retroWindowShell")
        shell.setStyleSheet("QWidget#retroWindowShell { background: transparent; }")
        self.setCentralWidget(shell)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        root = QFrame()
        root.setObjectName("retroWindowFrame")
        self.main_frame = root
        shell_layout.addWidget(root)

        page = QVBoxLayout(root)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        self.title_bar = DarkTitleBar(self)
        page.addWidget(self.title_bar)

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
        self.choose_button.setObjectName("chooseButton")
        self.choose_button.clicked.connect(self.choose_track)
        source_layout.addWidget(self.choose_button)
        splitter_layout.addWidget(source)

        self.split_button = QPushButton("Split track")
        self.split_button.setObjectName("splitButton")
        self.split_button.setMinimumHeight(36)
        self.split_button.setEnabled(False)
        self.split_button.clicked.connect(self.split_track)
        splitter_layout.addWidget(self.split_button)

        self.progress_panel = QWidget()
        progress_layout = QVBoxLayout(self.progress_panel)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(10)
        self.progress_label = QLabel()
        self.progress_bar = BarberPoleProgressBar()
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
        self.named_export_button = ThemedTextButton("Named folder...")
        self.named_export_button.setEnabled(False)
        self.named_export_button.clicked.connect(self.export_named_folder)
        self.folder_export_button = ThemedTextButton("Existing folder...")
        self.folder_export_button.setEnabled(False)
        self.folder_export_button.clicked.connect(self.export_existing_folder)
        export_layout.addWidget(self.named_export_button)
        export_layout.addWidget(self.folder_export_button)
        splitter_layout.addWidget(export_panel)
        self._style_tabs()

    def _style_tabs(self) -> None:
        background = "#ffffff" if self.light_mode else "#000000"
        foreground = "#000000" if self.light_mode else "#ffffff"
        self.tabs.tabBar().setStyleSheet(
            f"""
            QTabBar::tab {{
                background: {background};
                color: {foreground};
                border: 1px solid {foreground};
                border-bottom: none;
                padding: 5px 12px;
                margin-left: 8px;
                margin-right: 2px;
                position: relative;
                top: 1px;
            }}
            QTabBar::tab:selected, QTabBar::tab:hover {{
                background: {foreground};
                color: {background};
            }}
            """
        )

    def _style_main_frame(self) -> None:
        border_color = "#000000" if self.light_mode else "#ffffff"
        self.main_frame.setStyleSheet(
            f"QFrame#retroWindowFrame {{ border: 2px solid {border_color}; }}"
        )

    def _build_samples_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
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
        self.directory_search = QLineEdit()
        self.directory_search.setObjectName("directorySearch")
        self.directory_search.setPlaceholderText("Search current directory")
        self.directory_search.setClearButtonEnabled(True)
        clear_button = self.directory_search.findChild(QToolButton)
        if clear_button is not None:
            clear_button.setIcon(bundled_icon("search-clear.svg"))
            clear_button.setIconSize(QSize(16, 16))
        self.directory_search.setFixedWidth(260)
        self.directory_search.setFixedHeight(self.explorer_view_button.sizeHint().height())
        self.directory_search.textChanged.connect(self._filter_current_directory)
        view_switch.addWidget(self.directory_search)
        view_switch.setContentsMargins(8, 8, 8, 0)
        layout.addLayout(view_switch)

        browser = QSplitter(Qt.Orientation.Horizontal)
        browser.setChildrenCollapsible(False)

        folders = QFrame()
        folders.setObjectName("folderExplorerPane")
        folders_layout = QVBoxLayout(folders)
        folders_layout.setContentsMargins(4, 0, 0, 0)
        self.folder_model = QFileSystemModel(self)
        self.folder_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        self.filesystem_root = Path.home().anchor or "/"
        root_index = self.folder_model.setRootPath(self.filesystem_root)
        self.folder_icon_provider = MonochromeIconProvider("#ff7a00")
        self.folder_model.setIconProvider(self.folder_icon_provider)
        self.folder_tree = QTreeView()
        self.folder_tree.setStyle(OrangeBranchStyle())
        self.folder_tree.setMouseTracking(True)
        self.folder_tree.setModel(self.folder_model)
        self.folder_tree.setRootIndex(root_index)
        self.folder_tree.setFrameShape(QFrame.Shape.NoFrame)
        # The filesystem root's branch connector is rendered as a full-height
        # vertical rule. It serves no navigation purpose here and visually
        # bleeds through the folder pane, so keep child expanders but hide the
        # root decoration.
        self.folder_tree.setRootIsDecorated(False)
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setColumnHidden(1, True)
        self.folder_tree.setColumnHidden(2, True)
        self.folder_tree.setColumnHidden(3, True)
        self.folder_tree.selectionModel().currentChanged.connect(self._folder_changed)
        folders_layout.addWidget(self.folder_tree)

        self.library_tree = QTreeWidget()
        self.library_tree.setStyle(OrangeBranchStyle())
        self.library_tree.setHeaderHidden(True)
        self.library_tree.setFrameShape(QFrame.Shape.NoFrame)
        self.library_tree.itemClicked.connect(self._library_tree_changed)
        self.library_tree.itemExpanded.connect(self._library_tree_expanded)
        self.library_tree.setVisible(False)
        folders_layout.addWidget(self.library_tree)
        browser.addWidget(folders)

        files = QFrame()
        files.setObjectName("fileExplorerPane")
        files_layout = QVBoxLayout(files)
        files_layout.setContentsMargins(8, 8, 8, 8)
        self.samples_location_label = QLabel("Select a folder")
        self.samples_location_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        files_layout.addWidget(self.samples_location_label)
        self.sample_file_lists = QStackedWidget()
        self.explorer_file_list = self._create_sample_file_list()
        self.library_file_list = self._create_sample_file_list()
        self.sample_file_lists.addWidget(self.explorer_file_list)
        self.sample_file_lists.addWidget(self.library_file_list)
        self.sample_file_list = self.explorer_file_list
        files_layout.addWidget(self.sample_file_lists, 1)

        self.scan_recursive_checkbox = OrangeCheckBox("Scan subfolders")
        self.scan_recursive_checkbox.setChecked(self.sample_library.scan_recursive)
        self.scan_recursive_checkbox.toggled.connect(self._scan_recursive_changed)

        self.samples_status = QLabel()
        self.samples_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

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
        self.delete_library_files_button = QPushButton("Delete Files")
        self.delete_library_files_button.clicked.connect(self._delete_library_files)
        self.refresh_library_button = QPushButton("Refresh")
        self.refresh_library_button.clicked.connect(self._refresh_library)
        for button in (
            self.new_library_folder_button,
            self.rename_library_folder_button,
            self.delete_library_folder_button,
            self.delete_library_files_button,
            self.refresh_library_button,
        ):
            library_actions.addWidget(button)
        library_actions.addStretch()
        self.library_actions_widget.setVisible(False)

        samples_metadata = QWidget()
        metadata_layout = QHBoxLayout(samples_metadata)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(8)
        metadata_layout.addWidget(self.scan_recursive_checkbox)
        metadata_layout.addWidget(self.samples_status, 1)

        samples_footer = QWidget()
        footer_layout = QVBoxLayout(samples_footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)
        footer_layout.addWidget(samples_metadata)
        footer_layout.addWidget(self.actions_one_widget)
        footer_layout.addWidget(self.library_actions_widget)
        files_layout.addWidget(samples_footer)
        browser.addWidget(files)
        browser.setStretchFactor(0, 0)
        browser.setStretchFactor(1, 1)
        browser.setSizes([420, 696])
        explorer_content = QVBoxLayout()
        explorer_content.setContentsMargins(0, 0, 0, 0)
        explorer_content.setSpacing(0)
        explorer_content.addWidget(browser, 1)

        # The waveform editor is the only visible preview surface. Keep the
        # player as a hidden engine for audio output and transport state.
        self.preview_player = PreviewPlayer(page)
        self.preview_player.setVisible(False)
        self.waveform_editor = WaveformEditor()
        self.waveform_editor.setFixedHeight(272)
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
        explorer_content.addWidget(self.waveform_editor)
        layout.addLayout(explorer_content, 1)

        last_directory = self.sample_library.last_directory
        self._restore_samples_directory(last_directory)
        self._preload_library_view()
        return page

    def _create_sample_file_list(self) -> QListWidget:
        sample_list = QListWidget()
        sample_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        sample_list.setFrameShape(QFrame.Shape.NoFrame)
        sample_list.currentItemChanged.connect(self._sample_selection_changed)
        sample_list.itemDoubleClicked.connect(lambda _item: self.preview_player._play())
        return sample_list

    def _activate_sample_list(self, showing_library: bool) -> None:
        sample_list = self.library_file_list if showing_library else self.explorer_file_list
        self.sample_file_lists.setCurrentWidget(sample_list)
        self.sample_file_list = sample_list

    def _preload_library_view(self) -> None:
        """Build the hidden Library pane once so switching tabs is only a view change."""
        library_root = self.sample_library.root
        library_paths = self.sample_library.scan_folder(library_root, recursive=False)
        self._populate_sample_file_list(self.library_file_list, library_paths)
        self._library_list_directory = library_root.resolve()
        self._library_list_is_references = False
        self._library_list_dirty = False
        self._library_status_text = f"{len(library_paths)} audio files"
        self._populate_library_tree()

    def _set_sample_view(self, view: str) -> None:
        showing_explorer = view == "explorer"
        if not showing_explorer:
            self._cancel_folder_scans()
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
            self._activate_sample_list(False)
            self.samples_location_label.setVisible(True)
            self.folder_tree.setVisible(True)
            self.library_tree.setVisible(False)
            target = self.last_explorer_directory
            if self._explorer_list_directory != target.resolve():
                self._restore_samples_directory(target)
            else:
                self.current_samples_directory = target
                self.samples_location_label.setText(str(target))
                self.samples_status.setText(self._explorer_status_text)
        else:
            self.showing_library = True
            self._activate_sample_list(True)
            self.folder_tree.setVisible(False)
            self.library_tree.setVisible(True)
            self._populate_library_tree()
            selected_item = self.library_tree.currentItem()
            selected_value = (
                selected_item.data(0, Qt.ItemDataRole.UserRole)
                if selected_item is not None
                else None
            )
            if selected_value == "references":
                if not self._library_list_is_references or self._library_list_dirty:
                    self._show_references()
                else:
                    self.current_samples_directory = None
                    self.samples_location_label.clear()
                    self.samples_location_label.setVisible(False)
                    self.samples_status.setText(self._library_status_text)
            else:
                target = Path(selected_value) if selected_value else self.sample_library.root
                if not target.is_dir() or not self.sample_library.is_library_path(target):
                    target = self.sample_library.root
                if (
                    self._library_list_is_references
                    or self._library_list_directory != target.resolve()
                ):
                    self._show_directory(target)
                else:
                    self.current_samples_directory = target
                    self.samples_location_label.setText(str(target))
                    self.samples_location_label.setVisible(True)
                    self.samples_status.setText(self._library_status_text)

    def _restore_samples_directory(self, path: Path) -> None:
        index = self.folder_model.index(str(path))
        if index.isValid():
            selection_model = self.folder_tree.selectionModel()
            blocker = QSignalBlocker(selection_model)
            self.folder_tree.setCurrentIndex(index)
            del blocker
            self.folder_tree.scrollTo(index)
            self._show_directory(path)
        else:
            self._show_directory(Path.home())

    def _folder_changed(self, current, _previous) -> None:
        path = Path(self.folder_model.filePath(current))
        if path.is_dir():
            self._show_directory(path)

    def _scan_recursive_changed(self, enabled: bool) -> None:
        self.sample_library.set_scan_recursive(enabled)
        if not self.showing_library and self.current_samples_directory:
            self._show_directory(self.current_samples_directory)

    def _cancel_folder_scans(self, except_path: Path | None = None) -> None:
        resolved_except = except_path.resolve() if except_path is not None else None
        for thread, path in list(self._folder_scan_paths.items()):
            if resolved_except is not None and path.resolve() == resolved_except:
                continue
            reference_root = self.sample_library.reference_root_for_directory(path, recursive=True)
            if reference_root is not None and reference_root.resolve() == path.resolve():
                continue
            thread.requestInterruption()

    def _forget_folder_scan_thread(self, thread: QThread, worker: FolderScanWorker) -> None:
        if thread in self._folder_scan_threads:
            self._folder_scan_threads.remove(thread)
        if worker in self._folder_scan_workers:
            self._folder_scan_workers.remove(worker)
        self._folder_scan_paths.pop(thread, None)

    def _watch_reference_root(self, root: Path, recursive: bool) -> None:
        """Watch a registered root without exhausting inotify watches."""
        resolved_root = root.resolve()
        existing = set(self._reference_watcher.directories())
        if resolved_root.is_dir() and str(resolved_root) not in existing:
            self._reference_watcher.addPath(str(resolved_root))

    def _watch_reference_children(self, directory: Path, recursive: bool) -> None:
        """Add watchers for newly created child directories only."""
        if not directory.is_dir():
            return
        existing = set(self._reference_watcher.directories())
        try:
            children = [child for child in directory.iterdir() if child.is_dir()]
        except OSError:
            return
        for child in children:
            if str(child) in existing:
                continue
            self._reference_watcher.addPath(str(child))
            existing.add(str(child))
            if recursive:
                self._watch_reference_root(child, recursive=True)

    def _reference_directory_changed(self, path: str) -> None:
        changed_directory = Path(path).resolve()
        for root, recursive in self.sample_library.reference_roots():
            resolved_root = root.resolve()
            try:
                changed_directory.relative_to(resolved_root)
            except ValueError:
                continue
            if changed_directory != resolved_root and not recursive:
                continue
            self._reference_change_queue[str(changed_directory)] = (changed_directory, resolved_root)
            if recursive:
                self._watch_reference_children(changed_directory, recursive=True)
            if any(thread.isRunning() for thread in self._folder_scan_threads):
                self._reference_sync_pending = True
            else:
                self._reference_change_timer.start()
            break

    def _start_reference_change_sync(self) -> None:
        if not self._reference_change_queue:
            return
        if any(thread.isRunning() for thread in self._folder_scan_threads):
            self._reference_sync_pending = True
            return
        if self._reference_sync_thread and self._reference_sync_thread.isRunning():
            self._reference_sync_pending = True
            return
        changes = list(self._reference_change_queue.values())
        self._reference_change_queue.clear()
        thread = QThread(self)
        worker = ReferenceDirectorySyncWorker(self.sample_library, changes)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._reference_directory_sync_finished)
        worker.completed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._reference_sync_finished)
        self._reference_sync_thread = thread
        self._reference_sync_worker = worker
        thread.start()

    @pyqtSlot(object)
    def _reference_directory_sync_finished(
        self,
        results: list[tuple[Path, Path, list[Path]]],
    ) -> None:
        changed = False
        for folder, reference_root, paths in results:
            changed = self.sample_library.reconcile_reference_directory(
                folder,
                paths,
                reference_root,
            ) or changed
            changed = self.sample_library.remove_missing_references(reference_root) or changed
        if not changed:
            return
        self._library_tree_dirty = True
        self._library_list_dirty = True
        if self.showing_library:
            self._refresh_reference_library_view()
        elif self.current_samples_directory is not None:
            recursive = self.scan_recursive_checkbox.isChecked()
            cached = self.sample_library.cached_files_for_directory(
                self.current_samples_directory,
                recursive,
            )
            if cached is not None:
                self._replace_sample_file_list(cached, f"{len(cached)} audio files")

    @pyqtSlot(object, int, object)
    def _reference_scan_directory(
        self,
        directory: Path,
        generation: int,
        reference_root: Path,
    ) -> None:
        if generation != self._folder_scan_generation:
            return
        resolved_directory = directory.resolve()
        resolved_root = reference_root.resolve()
        if resolved_directory == resolved_root:
            self._watch_reference_root(resolved_root, recursive=False)

    def _start_folder_scan(
        self,
        path: Path,
        generation: int,
        preserve_existing: bool = False,
    ) -> None:
        self._cancel_folder_scans(except_path=path)
        reference_root = self.sample_library.reference_root_for_directory(path, recursive=True)
        if reference_root is None:
            # A recursive Explorer scan is also the reference-location cache
            # for the selected folder. Keep paths in metadata rather than
            # copying audio, so the Library view can reuse this scan.
            reference_root = path.resolve()
            self.sample_library.register_reference_root(reference_root, recursive=True)
            if hasattr(self, "_reference_watcher"):
                self._watch_reference_root(reference_root, recursive=True)
        elif reference_root.resolve() == path.resolve():
            self.sample_library.register_reference_root(reference_root, recursive=True)
        self._folder_scan_reference_context[generation] = (path, reference_root)
        status = "Scanning folder..."
        if reference_root:
            status = "Scanning folder...  Updating references in background..."
        if preserve_existing:
            self.samples_status.setText(status)
        else:
            self._replace_sample_file_list([], status)
        thread = QThread(self)
        worker = FolderScanWorker(
            self.sample_library,
            path,
            recursive=True,
            generation=generation,
            reference_root=reference_root,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        if reference_root:
            worker.batch.connect(self._reference_scan_batch, Qt.ConnectionType.QueuedConnection)
            worker.directory.connect(self._reference_scan_directory, Qt.ConnectionType.QueuedConnection)
        worker.completed.connect(self._folder_scan_finished)
        worker.completed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(
            lambda thread=thread, worker=worker: self._forget_folder_scan_thread(thread, worker)
        )
        self._folder_scan_threads.append(thread)
        self._folder_scan_workers.append(worker)
        self._folder_scan_paths[thread] = path
        thread.start()

    @pyqtSlot(object, int, object)
    def _reference_scan_batch(
        self,
        paths: list[Path],
        generation: int,
        reference_root: Path,
    ) -> None:
        added = self.sample_library.cache_reference_paths(paths, reference_root)
        if added:
            self._library_tree_dirty = True
            self._library_list_dirty = True
            self._reference_scan_cached_since_save += added
            if self._reference_scan_cached_since_save >= 128:
                self.sample_library.flush_reference_cache()
                self._reference_scan_cached_since_save = 0
            if generation == self._folder_scan_generation:
                self.samples_status.setText(
                    f"Scanning folder...  Updating references ({self.sample_library.reference_count()})..."
                )
                if self.showing_library:
                    self._reference_view_refresh_timer.start()

    @pyqtSlot(object, int, bool)
    def _folder_scan_finished(self, paths: list[Path], generation: int, cancelled: bool) -> None:
        reference_context = self._folder_scan_reference_context.pop(generation, None)
        folder = reference_context[0] if reference_context else None
        reference_root = reference_context[1] if reference_context else None
        if folder is not None and reference_root is None:
            reference_root = self.sample_library.reference_root_for_directory(folder, recursive=True)
        if folder is not None and reference_root is not None and not cancelled:
            changed = self.sample_library.reconcile_reference_directory(
                folder,
                paths,
                reference_root,
            )
            if changed:
                self._library_list_dirty = True
        elif cancelled:
            self.sample_library.flush_reference_cache()
        self._reference_scan_cached_since_save = 0
        if self._reference_sync_pending:
            self._reference_sync_pending = False
            self._reference_change_timer.start()
        if generation != self._folder_scan_generation:
            return
        if self.showing_library:
            self._refresh_reference_library_view()
            return
        if self.current_samples_directory is None:
            return
        self._replace_sample_file_list(paths, f"{len(paths)} audio files")

    @pyqtSlot()
    def _reference_sync_finished(self) -> None:
        thread = self._reference_sync_thread
        self._reference_sync_thread = None
        self._reference_sync_worker = None
        if thread is not None:
            thread.deleteLater()
        if self._reference_sync_pending and self._reference_change_queue:
            self._reference_sync_pending = False
            self._reference_change_timer.start()

    def _populate_library_tree(self) -> None:
        if self._library_tree_built and not self._library_tree_dirty:
            return
        selected_value = None
        current_item = self.library_tree.currentItem()
        if current_item is not None:
            selected_value = current_item.data(0, Qt.ItemDataRole.UserRole)
        signals_were_blocked = self.library_tree.blockSignals(True)
        self.library_tree.clear()
        folder_icon = self.folder_icon_provider.folder_icon

        library_root = QTreeWidgetItem(["Library"])
        library_root.setIcon(0, folder_icon)
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
                item.setIcon(0, folder_icon)
                item.setData(0, Qt.ItemDataRole.UserRole, str(child))
                parent.addChild(item)
                add_children(item, child)

        add_children(library_root, self.sample_library.root)
        references = QTreeWidgetItem(["References"])
        references.setIcon(0, folder_icon)
        references.setData(0, Qt.ItemDataRole.UserRole, "references")
        self.library_tree.addTopLevelItem(references)
        for root, _recursive in self.sample_library.reference_roots():
            root_item = QTreeWidgetItem([root.name or str(root)])
            root_item.setIcon(0, folder_icon)
            root_item.setData(0, Qt.ItemDataRole.UserRole, str(root))
            references.addChild(root_item)
            root_item.addChild(QTreeWidgetItem([""]))
        self.library_tree.expandItem(library_root)

        def find_item(parent: QTreeWidgetItem) -> QTreeWidgetItem | None:
            if parent.data(0, Qt.ItemDataRole.UserRole) == selected_value:
                return parent
            for index in range(parent.childCount()):
                match = find_item(parent.child(index))
                if match is not None:
                    return match
            return None

        target = library_root
        if selected_value is not None:
            for index in range(self.library_tree.topLevelItemCount()):
                match = find_item(self.library_tree.topLevelItem(index))
                if match is not None:
                    target = match
                    break
        self.library_tree.setCurrentItem(target)
        self.library_tree.blockSignals(signals_were_blocked)
        self._library_tree_built = True
        self._library_tree_dirty = False

    def _library_tree_expanded(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        if parent is None or parent.data(0, Qt.ItemDataRole.UserRole) != "references":
            return
        if item.childCount() != 1 or item.child(0).text(0) != "":
            return
        item.takeChildren()
        root = Path(item.data(0, Qt.ItemDataRole.UserRole))
        root_resolved = root.resolve()
        directory_items: dict[Path, QTreeWidgetItem] = {root_resolved: item}
        for path in self.sample_library.reference_files():
            try:
                relative_parent = path.resolve().parent.relative_to(root_resolved)
            except ValueError:
                continue
            parent_item = item
            current = root_resolved
            for part in relative_parent.parts:
                current = current / part
                if current not in directory_items:
                    child = QTreeWidgetItem([part])
                    child.setIcon(0, self.folder_icon_provider.folder_icon)
                    child.setData(0, Qt.ItemDataRole.UserRole, str(current))
                    directory_items[current] = child
                    parent_item.addChild(child)
                parent_item = directory_items[current]

    def _refresh_reference_library_view(self) -> None:
        if not self.showing_library:
            return
        selected_item = self.library_tree.currentItem()
        selected_value = (
            selected_item.data(0, Qt.ItemDataRole.UserRole)
            if selected_item is not None
            else None
        )
        selected_directory = self.current_samples_directory
        self._populate_library_tree()
        if selected_value == "references" or selected_directory is None:
            self._show_references()
        elif not self.sample_library.is_library_path(selected_directory):
            self._show_directory(selected_directory)

    def _refresh_reference_contents(self) -> None:
        if not self.showing_library:
            return
        current_item = self.library_tree.currentItem()
        if current_item is not None and current_item.data(0, Qt.ItemDataRole.UserRole) == "references":
            self._show_references()

    def _library_tree_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        value = item.data(0, Qt.ItemDataRole.UserRole)
        if value == "references":
            self._show_references()
        elif value:
            self._show_directory(Path(value))

    def _show_references(self) -> None:
        self.current_samples_directory = None
        self._library_list_directory = None
        self._library_list_is_references = True
        self.samples_location_label.clear()
        self.samples_location_label.setVisible(False)
        for index in range(self.library_tree.topLevelItemCount()):
            item = self.library_tree.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole) == "references":
                signals_were_blocked = self.library_tree.blockSignals(True)
                self.library_tree.setCurrentItem(item)
                self.library_tree.blockSignals(signals_were_blocked)
                break
        paths = self.sample_library.reference_files()
        self._replace_sample_file_list(paths, f"{len(paths)} referenced files")

    def _populate_sample_file_list(self, sample_list: QListWidget, paths: list[Path]) -> None:
        signals_were_blocked = sample_list.blockSignals(True)
        updates_were_enabled = sample_list.updatesEnabled()
        sample_list.setUpdatesEnabled(False)
        try:
            sample_list.clear()
            sample_list.addItems([audio_path.name for audio_path in paths])
            for index, audio_path in enumerate(paths):
                list_item = sample_list.item(index)
                list_item.setData(Qt.ItemDataRole.UserRole, str(audio_path))
                if not audio_path.exists():
                    list_item.setEnabled(False)
                    list_item.setToolTip("Original file is no longer available")
        finally:
            sample_list.setUpdatesEnabled(updates_were_enabled)
            sample_list.blockSignals(signals_were_blocked)

    def _replace_sample_file_list(self, paths: list[Path], status_text: str) -> None:
        self.preview_player.set_file(None)
        self.waveform_editor.set_file(None)
        self._populate_sample_file_list(self.sample_file_list, paths)
        self._filter_current_directory()
        self.samples_status.setText(status_text)
        if self.showing_library:
            self._library_status_text = status_text
            self._library_list_dirty = False
        else:
            self._explorer_status_text = status_text

    def _show_directory(self, path: Path) -> None:
        if not path.is_dir():
            return
        self.current_samples_directory = path
        self.samples_location_label.setVisible(True)
        if not self.showing_library:
            self.last_explorer_directory = path
            self.sample_library.set_last_directory(path)
        self.samples_location_label.setText(str(path))
        recursive = not self.showing_library and self.scan_recursive_checkbox.isChecked()
        if recursive:
            resolved_path = path.resolve()
            if any(
                thread.isRunning()
                and scan_path.resolve() == resolved_path
                for thread, scan_path in self._folder_scan_paths.items()
            ):
                return
        self._cancel_folder_scans(except_path=path if recursive else None)
        # Library folders are managed on disk and must always reflect their
        # actual contents. The reference cache is only for Explorer paths;
        # otherwise a broad recursive reference root such as /home/sean can
        # shadow newly created managed files in the Library.
        cached = (
            None
            if self.showing_library
            else self.sample_library.cached_files_for_directory(path, recursive)
        )
        if cached is not None:
            self._folder_scan_generation += 1
            generation = self._folder_scan_generation
            if self.showing_library:
                self._library_list_directory = path.resolve()
                self._library_list_is_references = False
            else:
                self._explorer_list_directory = path.resolve()
            self._replace_sample_file_list(cached, f"{len(cached)} audio files")
            if recursive and not self.sample_library.reference_cache_complete_for_directory(path):
                self._start_folder_scan(path, generation, preserve_existing=True)
            return
        self._folder_scan_generation += 1
        generation = self._folder_scan_generation
        if recursive:
            if self.showing_library:
                self._library_list_directory = path.resolve()
                self._library_list_is_references = False
            else:
                self._explorer_list_directory = path.resolve()
            self._start_folder_scan(path, generation)
            return
        paths = self.sample_library.scan_folder(path, recursive)
        if self.showing_library:
            self._library_list_directory = path.resolve()
            self._library_list_is_references = False
        else:
            self._explorer_list_directory = path.resolve()
        self._replace_sample_file_list(paths, f"{len(paths)} audio files")

    def _filter_current_directory(self) -> None:
        query = self.directory_search.text().strip().casefold()
        for index in range(self.sample_file_list.count()):
            item = self.sample_file_list.item(index)
            item.setHidden(bool(query) and query not in item.text().casefold())

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
        if path is not None and path.exists():
            self.set_source(path)

    def _add_selected_references(self) -> None:
        paths = self._selected_sample_paths()
        root = self.current_samples_directory if self.current_samples_directory and self.current_samples_directory.is_dir() else None
        count = self.sample_library.add_references(
            paths,
            reference_root=root,
            recursive=self.scan_recursive_checkbox.isChecked() if root else False,
        )
        if root is not None:
            self._watch_reference_root(root, self.scan_recursive_checkbox.isChecked())
        self._library_tree_dirty = True
        self._library_list_dirty = True
        self._refresh_reference_library_view()
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
        root = self.current_samples_directory or self.sample_library.last_directory
        if not root.is_dir():
            self.samples_status.setText("Reference folder is unavailable")
            return
        listed_paths = [
            Path(item.data(Qt.ItemDataRole.UserRole))
            for index in range(self.sample_file_list.count())
            if (item := self.sample_file_list.item(index))
            and item.data(Qt.ItemDataRole.UserRole)
        ]
        self.sample_library.register_reference_root(
            root,
            recursive=self.scan_recursive_checkbox.isChecked(),
        )
        self._library_tree_dirty = True
        self._library_list_dirty = True
        self._watch_reference_root(root, self.scan_recursive_checkbox.isChecked())
        count = self.sample_library.add_references(
            listed_paths,
            reference_root=root,
            recursive=self.scan_recursive_checkbox.isChecked(),
        )
        self.samples_status.setText(
            f"Updating folder references in background ({count} cached immediately)..."
        )
        self._refresh_reference_library_view()

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
            show_message(self, title, "Enter a single folder name without path separators.")
            return None
        return name

    def _new_library_folder(self) -> None:
        parent = self._selected_library_directory()
        name = self._library_folder_name("New library folder", "Folder name:")
        if not name:
            return
        destination = parent / name
        if destination.exists():
            show_message(self, "Folder already exists", f"A folder named {name!r} already exists here.")
            return
        try:
            destination.mkdir()
        except OSError as exc:
            show_message(self, "Could not create folder", str(exc))
            return
        self._refresh_library(destination)

    def _rename_library_folder(self) -> None:
        path = self._selected_library_directory()
        name = self._library_folder_name("Rename library folder", "New folder name:", path.name)
        if not name or name == path.name:
            return
        renamed = self.sample_library.rename_library_directory(path, name)
        if renamed is None:
            show_message(
                self,
                "Folder cannot be renamed",
                "Only user-created library folders can be renamed, and the destination name must be unused.",
            )
            return
        self._refresh_library(renamed)

    def _delete_library_folder(self) -> None:
        path = self._selected_library_directory()
        if path.resolve() in self.sample_library.protected_directories() or path.resolve() == self.sample_library.root.resolve():
            show_message(self, "Folder cannot be deleted", "StemKart's root and system folders are protected.")
            return
        try:
            children = list(path.iterdir())
        except OSError as exc:
            show_message(self, "Folder not deleted", f"StemKart could not inspect this folder:\n{exc}")
            return
        recursive = bool(children)
        if recursive:
            title = "Delete folder and contents?"
            message = f"Delete {path.name!r} and its {len(children)} item(s)? This cannot be undone."
        else:
            title = "Delete empty folder?"
            message = f"Delete the empty folder {path.name!r}?"
        if not ask_confirmation(self, title, message):
            return
        if not self.sample_library.remove_library_directory(path, recursive=recursive):
            show_message(self, "Folder not deleted", "StemKart could not remove that library folder.")
            return
        self._refresh_library(self.sample_library.root)

    def _delete_library_files(self) -> None:
        if not self.showing_library:
            return
        paths = [
            path
            for path in self._selected_sample_paths()
            if path.is_file() and self.sample_library.is_library_path(path)
        ]
        if not paths:
            show_message(self, "Delete library files", "Select one or more files in the library first.")
            return
        names = ", ".join(path.name for path in paths[:3])
        if len(paths) > 3:
            names += f", and {len(paths) - 3} more"
        if not ask_confirmation(
            self,
            "Delete library files?",
            f"Delete {len(paths)} file(s)?\n\n{names}",
        ):
            return
        deleted = self.sample_library.remove_library_files(paths)
        if len(deleted) != len(paths):
            self.samples_status.setText(f"Deleted {len(deleted)} of {len(paths)} selected files")
        else:
            self.samples_status.setText(f"Deleted {len(deleted)} library file(s)")
        self._refresh_library(self.current_samples_directory)

    def _refresh_library(self, selected: Path | None = None) -> None:
        if not self.showing_library:
            return
        self._library_tree_dirty = True
        current_item = self.library_tree.currentItem()
        if current_item is not None and current_item.data(0, Qt.ItemDataRole.UserRole) == "references":
            self._refresh_reference_library_view()
            return
        selected = selected or self.current_samples_directory or self.sample_library.root
        if not selected.is_dir() or not self.sample_library.is_library_path(selected):
            selected = self.sample_library.root
        self._populate_library_tree()
        self._show_directory(selected)

    def _recording_saved(self, path: Path) -> None:
        self.sample_library.add_recorded(path)
        self._library_tree_dirty = True
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
        if self.showing_library:
            self._populate_library_tree()

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
            show_message(self, "No stems found", "Demucs finished without producing the expected WAV files.")

    @pyqtSlot(str)
    def _split_failed(self, message: str) -> None:
        self._set_split_progress_visible(False)
        self.statusBar().showMessage("Split failed")
        show_message(self, "Could not split track", message)

    def _split_finished(self) -> None:
        self.split_button.setEnabled(self.source_path is not None)
        self.choose_button.setEnabled(True)

    def closeEvent(self, event) -> None:
        self._reference_change_timer.stop()
        for thread in list(self._folder_scan_threads):
            thread.requestInterruption()
        for thread in list(self._folder_scan_threads):
            thread.quit()
            thread.wait(1500)
        if self._reference_sync_thread and self._reference_sync_thread.isRunning():
            self._reference_sync_thread.requestInterruption()
            self._reference_sync_thread.quit()
            self._reference_sync_thread.wait(1500)
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
        if not card.file_path or wav_availability(card.file_path) != "usable":
            card.set_empty()
            return
        destination = choose_save_path(
            self,
            f"Save {pretty_name(card.stem)} WAV",
            self.sample_library.root / f"{card.stem}.wav",
            "WAV files (*.wav)",
        )
        if destination and confirm_overwrite(self, Path(destination)):
            shutil.copy2(card.file_path, destination)
            self.statusBar().showMessage(f"Saved {Path(destination).name}")

    def _available_files(self) -> list[Path]:
        available: list[Path] = []
        for card in self.cards.values():
            if card.file_path is not None and wav_availability(card.file_path) == "usable":
                available.append(card.file_path)
            elif card.file_path is not None:
                card.set_empty()
        return available

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
            if not confirm_overwrite(self, destination, multiple=True):
                return
        count = self._copy_all(destination)
        self.statusBar().showMessage(f"Exported {count} WAV files to {destination.name}")
        show_message(self, "Export complete", f"Saved {count} WAV files in:\n{destination}")

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


def apply_style(app: QApplication, light_mode: bool = False) -> None:
    """Apply StemKart's black-and-white theme in dark or inverted light mode."""
    app._stemkart_light_mode = light_mode
    app.styleHints().setColorScheme(Qt.ColorScheme.Light if light_mode else Qt.ColorScheme.Dark)
    if not getattr(app, "_stemkart_style_initialized", False):
        app.setStyle("Fusion")
        app._stemkart_style_initialized = True

    background = QColor(255, 255, 255) if light_mode else QColor(0, 0, 0)
    foreground = QColor(0, 0, 0) if light_mode else QColor(255, 255, 255)
    palette = QPalette()
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase,
        QPalette.ColorRole.Button,
    ):
        palette.setColor(role, background)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.BrightText,
        QPalette.ColorRole.HighlightedText,
        QPalette.ColorRole.Link,
    ):
        palette.setColor(role, foreground)
    palette.setColor(QPalette.ColorRole.Highlight, foreground)
    palette.setColor(QPalette.ColorRole.ToolTipBase, background)
    palette.setColor(QPalette.ColorRole.ToolTipText, foreground)
    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, QPalette.ColorRole.WindowText, foreground)
    palette.setColor(disabled, QPalette.ColorRole.Text, foreground)
    palette.setColor(disabled, QPalette.ColorRole.ButtonText, foreground)
    palette.setColor(disabled, QPalette.ColorRole.Highlight, foreground)
    palette.setColor(disabled, QPalette.ColorRole.HighlightedText, background)
    app.setPalette(palette)

    family = getattr(app, "_stemkart_font_family", None) or load_bundled_font()
    if not getattr(app, "_stemkart_font_initialized", False):
        font = QFont(family, 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        app.setFont(font)
        app._stemkart_font_family = family
        app._stemkart_font_initialized = True

    split_background = "#ff7a00"
    split_foreground = "#000000"
    split_hover_background = "#d96600"
    split_hover_foreground = "#ffffff"
    split_pressed_background = "#bd5900"

    stylesheet = f"""
        QWidget {{
            background: #000000;
            color: #ffffff;
            font-family: "{family}";
            font-size: 12pt;
        }}
        QMainWindow, QDialog, QMenu, QMenuBar, QToolTip {{
            background: #000000;
            color: #ffffff;
        }}
        QDialog {{
            background: #000000;
            color: #ffffff;
            border: 1px solid #ff7a00;
        }}
        QWidget#titleBar {{
            background: #000000;
            border-bottom: 1px solid #ffffff;
        }}
        QLabel#titleBarTitle {{
            background: #000000;
            color: #ffffff;
            font-weight: 600;
        }}
        QWidget#titleBarButtons {{
            background: #000000;
        }}
        QFrame#retroWindowFrame {{
            border: 2px solid #ffffff;
        }}
        QPushButton#windowButton {{
            background: #000000;
            color: #ffffff;
            border: 1px solid #000000;
            padding: 0px;
            font-size: 12pt;
        }}
        QPushButton#windowButton:hover, QPushButton#windowButton:focus {{
            background: #ffffff;
            color: #000000;
            border-color: #ffffff;
        }}
        QPushButton#windowButton:pressed {{
            background: #ffffff;
            color: #000000;
        }}
        QGroupBox {{
            border: 1px solid #ffffff;
            border-radius: 0px;
            margin-top: 10px;
            padding: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px;
            background: #000000;
            color: #ffffff;
        }}
        QFrame#folderExplorerPane {{
            border: none;
        }}
        QFrame#fileExplorerPane {{
            border-top: none;
            border-bottom: none;
            border-left: none;
            border-right: none;
        }}
        QGroupBox#sampleTrimmingContainer {{
            margin-top: 0px;
            border-left: none;
            border-right: none;
            border-bottom: none;
        }}
        QPushButton {{
            background: #000000;
            color: #ffffff;
            border: 1px solid #ffffff;
            border-radius: 0px;
            padding: 4px 8px;
        }}
        QPushButton:hover, QPushButton:pressed {{
            background: #ffffff;
            color: #000000;
        }}
        QPushButton:focus {{
            outline: none;
        }}
        QPushButton:disabled {{
            background: #000000;
            color: #ffffff;
            border-color: #ffffff;
        }}
        QPushButton#splitButton,
        QPushButton#chooseButton {{
            background: {split_background};
            color: {split_foreground};
            border-color: {split_background};
        }}
        QPushButton#splitButton:hover,
        QPushButton#chooseButton:hover {{
            background: {split_hover_background};
            color: {split_hover_foreground};
            border-color: {split_hover_background};
        }}
        QPushButton#splitButton:pressed,
        QPushButton#chooseButton:pressed {{
            background: {split_pressed_background};
            color: {split_hover_foreground};
            border-color: {split_pressed_background};
        }}
        QPushButton#splitButton:disabled,
        QPushButton#chooseButton:disabled {{
            background: #000000;
            color: #ffffff;
            border-color: #ffffff;
        }}
        QLineEdit, QComboBox {{
            background: #000000;
            color: #ffffff;
            border: 1px solid #ffffff;
            border-radius: 0px;
            selection-background-color: #ffffff;
            selection-color: #000000;
        }}
        QLineEdit#directorySearch {{
            padding-left: 4px;
        }}
        QComboBox {{
            padding-left: 4px;
        }}
        QListWidget, QTreeWidget, QTreeView {{
            background: #000000;
            color: #ffffff;
            border: none;
            outline: none;
            selection-background-color: #ffffff;
            selection-color: #000000;
        }}
        QComboBox::drop-down {{
            background: #000000;
            border-left: 1px solid #ffffff;
            width: 22px;
        }}
        QComboBox QAbstractItemView {{
            background: #000000;
            color: #ffffff;
            border: 1px solid #ffffff;
            selection-background-color: #ffffff;
            selection-color: #000000;
        }}
        QAbstractItemView::item:hover, QAbstractItemView::item:selected {{
            background: #ffffff;
            color: #000000;
        }}
        QTabWidget::pane {{
            background: #000000;
            border-top: 1px solid #ffffff;
            border-right: none;
            border-bottom: none;
            border-left: none;
        }}
        QTabBar::tab {{
            background: #000000;
            color: #ffffff;
            border: 1px solid #ffffff;
        }}
        QTabBar::tab {{
            padding: 5px 12px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected, QTabBar::tab:hover {{
            background: #ffffff;
            color: #000000;
        }}
        QProgressBar {{
            background: #000000;
            color: #ffffff;
            border: 1px solid #ffffff;
            text-align: center;
        }}
        QProgressBar::chunk {{
            background: #ff7a00;
        }}
        QSlider::groove:horizontal {{
            background: #000000;
            border: 1px solid #ffffff;
            height: 6px;
        }}
        QSlider::handle:horizontal {{
            background: #ffffff;
            border: 1px solid #000000;
            width: 12px;
            margin: -5px 0;
        }}
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            background: #000000;
            border: 1px solid #ffffff;
        }}
        QCheckBox::indicator:checked {{
            background: #ffffff;
            border: 1px solid #ffffff;
        }}
        QSplitter::handle {{
            background: #ffffff;
        }}
        QSplitter::handle:horizontal {{
            width: 1px;
        }}
        QSplitter::handle:vertical {{
            height: 1px;
        }}
        QScrollBar:vertical {{
            background: #000000;
            border: none;
            width: 14px;
            margin: 0px;
        }}
        QScrollBar:horizontal {{
            background: #000000;
            border: 1px solid #ffffff;
            height: 14px;
            margin: 0px;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background: #ff7a00;
            border: 1px solid #000000;
            min-height: 20px;
            min-width: 20px;
        }}
        QScrollBar::add-line, QScrollBar::sub-line,
        QScrollBar::add-page, QScrollBar::sub-page {{
            background: #000000;
            border: none;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0px;
        }}
        QHeaderView::section {{
            background: #000000;
            color: #ffffff;
            border: 1px solid #ffffff;
        }}
        QToolTip {{
            border: 1px solid #000000;
            background: #ffffff;
            color: #000000;
        }}
        """
    if light_mode:
        stylesheet = stylesheet.replace(
            "#000000", "__STEMKART_BLACK__"
        ).replace("#ffffff", "#000000").replace("__STEMKART_BLACK__", "#ffffff")
        stylesheet += """
        QPushButton#transportButton:hover,
        QPushButton#transportButton:pressed,
        QPushButton#transportButton:checked,
        QPushButton#transportButton[stemkart_active="true"],
        QPushButton#markerButton[stemkart_active="true"],
        QPushButton#markerButton[stemkart_active="true"]:disabled {
            background: #000000;
            color: #ffffff;
            border-color: #000000;
        }
        """
    else:
        stylesheet += """
        QPushButton#transportButton:hover,
        QPushButton#transportButton:pressed,
        QPushButton#transportButton:checked,
        QPushButton#transportButton[stemkart_active="true"],
        QPushButton#markerButton[stemkart_active="true"],
        QPushButton#markerButton[stemkart_active="true"]:disabled {
            background: #ffffff;
            color: #000000;
            border-color: #ffffff;
        }
        """
    theme_stylesheets = getattr(app, "_stemkart_theme_stylesheets", {})
    theme_stylesheets[light_mode] = stylesheet
    app._stemkart_theme_stylesheets = theme_stylesheets
    if not getattr(app, "_stemkart_stylesheet_initialized", False):
        app.setStyleSheet(stylesheet)
        app._stemkart_stylesheet_initialized = True


def main() -> int:
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(APP_SLUG)
    settings = load_app_settings()
    light_mode = settings.get("light_mode") is True
    apply_style(app, light_mode=light_mode)
    dialog_theme_filter = DialogThemeFilter(app)
    app.installEventFilter(dialog_theme_filter)
    app._stemkart_dialog_theme_filter = dialog_theme_filter
    window = AudioSplitter(light_mode=light_mode)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
