from __future__ import annotations

import re
import os
import selectors
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

from PyQt6.QtCore import QThread, QUrl, Qt, QSize, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
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
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer


STEMS = ("vocals", "drums", "bass", "other")
SUPPORTED_AUDIO = "Audio files (*.mp3 *.wav *.flac *.m4a *.ogg *.aac *.opus);;All files (*)"


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
        self.play_button.clicked.connect(self._toggle_play_pause)
        self.stop_button = self._icon_button("Stop", QStyle.StandardPixmap.SP_MediaStop)
        self.stop_button.clicked.connect(self._stop)
        self.rewind_start_button = self._icon_button(
            "Rewind to beginning", QStyle.StandardPixmap.SP_MediaSeekBackward
        )
        self.rewind_start_button.clicked.connect(self._rewind_to_start)
        self.rewind_button = self._icon_button("Rewind 5 seconds", QStyle.StandardPixmap.SP_MediaSkipBackward)
        self.rewind_button.clicked.connect(lambda: self._seek_relative(-5000))
        self.forward_button = self._icon_button("Fast-forward 5 seconds", QStyle.StandardPixmap.SP_MediaSkipForward)
        self.forward_button.clicked.connect(lambda: self._seek_relative(5000))
        self.export_button = QPushButton("Save WAV")
        self.export_button.clicked.connect(lambda: self.export_requested.emit(self))
        for button in (
            self.play_button,
            self.stop_button,
            self.rewind_start_button,
            self.rewind_button,
            self.forward_button,
        ):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(self.export_button)
        layout.addLayout(buttons)
        self._set_controls_enabled(False)

    def _icon_button(self, name: str, standard_icon: QStyle.StandardPixmap) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(30, 30)
        button.setIcon(self.style().standardIcon(standard_icon))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(name)
        button.setAccessibleName(name)
        return button

    def _set_controls_enabled(self, enabled: bool) -> None:
        for button in (
            self.play_button,
            self.stop_button,
            self.rewind_start_button,
            self.rewind_button,
            self.forward_button,
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

    def _toggle_play_pause(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.player.position() >= self.player.duration() > 0:
                self.player.setPosition(0)
            self.player.play()

    def _stop(self) -> None:
        self.player.stop()
        self.player.setPosition(0)

    def _rewind_to_start(self) -> None:
        self.player.setPosition(0)

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

    def _playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _playback_error(self, _error: QMediaPlayer.Error) -> None:
        if self.player.errorString():
            self.status_message.emit(f"{pretty_name(self.stem)} playback error: {self.player.errorString()}")

    def close_player(self) -> None:
        self.player.stop()


class AudioSplitter(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.source_path: Path | None = None
        self.split_thread: SplitThread | None = None
        self.temp_root = Path(tempfile.mkdtemp(prefix="audio-splitter-"))
        self.cards: dict[str, StemCard] = {}

        self.setWindowTitle("Stem Splitter")
        self.setFixedSize(1120, 760)
        self.setAcceptDrops(True)

        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(18, 18, 18, 18)
        page.setSpacing(10)

        heading = QHBoxLayout()
        title_column = QVBoxLayout()
        title = QLabel("Stem Splitter")
        title_font = QFont(title.font())
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title_column.addWidget(title)
        heading.addLayout(title_column)
        heading.addStretch()
        page.addLayout(heading)

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
        page.addWidget(source)

        self.split_button = QPushButton("Split track")
        self.split_button.setMinimumHeight(36)
        self.split_button.setEnabled(False)
        self.split_button.clicked.connect(self.split_track)
        page.addWidget(self.split_button)

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
        self.progress_panel.setVisible(False)
        page.addWidget(self.progress_panel)

        stem_header = QHBoxLayout()
        stem_title = QLabel("Stems")
        stem_header.addWidget(stem_title)
        page.addLayout(stem_header)

        stem_grid = QGridLayout()
        stem_grid.setHorizontalSpacing(10)
        stem_grid.setVerticalSpacing(8)
        for index, stem in enumerate(STEMS):
            card = StemCard(stem)
            card.status_message.connect(self.statusBar().showMessage)
            card.export_requested.connect(self.export_card)
            self.cards[stem] = card
            stem_grid.addWidget(card, index // 2, index % 2)
        page.addLayout(stem_grid)

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
        page.addWidget(export_panel)

    def choose_track(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Choose a music track", "", SUPPORTED_AUDIO)
        if filename:
            self.set_source(Path(filename))

    def set_source(self, path: Path) -> None:
        self.source_path = path
        self.file_label.setText(path.name)
        self.file_label.setToolTip(str(path))
        self.split_button.setEnabled(True)
        self.progress_panel.setVisible(False)
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
        self.progress_panel.setVisible(True)
        output_dir = Path(tempfile.mkdtemp(prefix="demucs-", dir=self.temp_root))
        self.split_thread = SplitThread(self.source_path, output_dir)
        self.split_thread.progress.connect(self._split_progress)
        self.split_thread.succeeded.connect(self._split_succeeded)
        self.split_thread.failed.connect(self._split_failed)
        self.split_thread.finished.connect(self._split_finished)
        self.split_thread.start()

    @pyqtSlot(int, str)
    def _split_progress(self, percent: int, message: str) -> None:
        self.progress_panel.setVisible(True)
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
        self.progress_panel.setVisible(False)
        if loaded:
            self.statusBar().clearMessage()
            self.named_export_button.setEnabled(True)
            self.folder_export_button.setEnabled(True)
        else:
            self.statusBar().clearMessage()
            QMessageBox.warning(self, "No stems found", "Demucs finished without producing the expected WAV files.")

    @pyqtSlot(str)
    def _split_failed(self, message: str) -> None:
        self.progress_panel.setVisible(False)
        self.statusBar().showMessage("Split failed")
        QMessageBox.critical(self, "Could not split track", message)

    def _split_finished(self) -> None:
        self.split_button.setEnabled(self.source_path is not None)
        self.choose_button.setEnabled(True)

    def closeEvent(self, event) -> None:
        for card in self.cards.values():
            card.close_player()
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
    apply_style(app)
    window = AudioSplitter()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
