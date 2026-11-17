"""Lightweight pitch-class detection for local audio samples."""

from __future__ import annotations

import numpy as np
import aubio


PITCH_CLASS_NAMES = (
    "C",
    "C#",
    "D",
    "D#",
    "E",
    "F",
    "F#",
    "G",
    "G#",
    "A",
    "A#",
    "B",
)

# Krumhansl-Schmuckler profiles. We intentionally compare both modes and
# return only the pitch class because the UI does not claim to identify mode.
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def pitch_class_name(pitch_class: int) -> str:
    return PITCH_CLASS_NAMES[int(pitch_class) % 12]


def detect_pitch_class(
    samples: np.ndarray,
    sample_rate: int,
) -> tuple[int, float] | None:
    """Return ``(pitch_class, confidence)`` or ``None`` for unusable audio.

    The detector aggregates normalized chroma vectors over overlapping frames,
    then scores every root against major and minor key profiles. It is intended
    as a fast, conservative center-key hint for the Effects UI, not as a full
    chord or mode transcription system.
    """
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    values = values[np.isfinite(values)]
    if values.size < 2048 or sample_rate <= 0:
        return None
    values = values - float(np.mean(values))
    peak = float(np.max(np.abs(values), initial=0.0))
    if peak < 1e-4:
        return None
    values = values / peak

    frame_size = 4096 if values.size >= 8192 else 2048
    hop = frame_size // 2
    window = np.hanning(frame_size).astype(np.float32)
    chroma_frames: list[np.ndarray] = []
    for start in range(0, max(1, values.size - frame_size + 1), hop):
        frame = values[start : start + frame_size]
        if frame.size < frame_size:
            frame = np.pad(frame, (0, frame_size - frame.size))
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms < 0.01:
            continue
        spectrum = np.abs(np.fft.rfft(frame * window))
        frequencies = np.fft.rfftfreq(frame_size, 1.0 / sample_rate)
        usable = (frequencies >= 55.0) & (frequencies <= 2200.0)
        if not np.any(usable):
            continue
        frequencies = frequencies[usable]
        magnitudes = spectrum[usable]
        midi = 69.0 + 12.0 * np.log2(frequencies / 440.0)
        pitch_classes = np.mod(np.rint(midi).astype(np.int16), 12)
        chroma = np.bincount(pitch_classes, weights=magnitudes, minlength=12).astype(np.float64)
        total = float(np.sum(chroma))
        if total > 1e-9:
            chroma_frames.append(chroma / total)
    if not chroma_frames:
        return None

    chroma = np.mean(chroma_frames, axis=0)
    concentration = float(np.std(chroma) / (np.mean(chroma) + 1e-9))
    if concentration < 0.35:
        return None
    chroma = (chroma - np.mean(chroma)) / (np.std(chroma) + 1e-9)
    profiles = []
    for profile in (_MAJOR_PROFILE, _MINOR_PROFILE):
        normalized = (profile - np.mean(profile)) / np.std(profile)
        profiles.extend(np.roll(normalized, root) for root in range(12))
    scores = np.array([float(np.dot(chroma, profile)) for profile in profiles])
    root_scores = np.maximum(scores[:12], scores[12:])
    order = np.argsort(root_scores)
    best = int(order[-1])
    second = float(root_scores[order[-2]])
    spread = float(np.std(root_scores))
    confidence = max(0.0, min(1.0, (float(root_scores[best]) - second) / (spread + 1e-9)))
    return best, confidence


def detect_bpm(
    samples: np.ndarray,
    sample_rate: int,
    minimum_bpm: float = 30.0,
    maximum_bpm: float = 300.0,
) -> tuple[float, float] | None:
    """Return ``(bpm, confidence)`` using aubio's beat tracker."""
    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    values = values[np.isfinite(values)]
    if values.size < sample_rate or sample_rate <= 0:
        return None
    values = values - float(np.mean(values))
    peak = float(np.max(np.abs(values), initial=0.0))
    if peak < 1e-4:
        return None
    values = values / peak

    hop_size = 512
    tempo = aubio.tempo("default", 1024, hop_size, sample_rate)
    tempo.set_silence(-70.0)
    beat_times: list[float] = []
    for start in range(0, values.size, hop_size):
        chunk = values[start : start + hop_size]
        if chunk.size < hop_size:
            chunk = np.pad(chunk, (0, hop_size - chunk.size))
        result = tempo(chunk)
        if float(result[0]) > 0.0:
            beat_time = float(tempo.get_last_s())
            if not beat_times or beat_time > beat_times[-1] + 1e-4:
                beat_times.append(beat_time)

    bpm = float(tempo.get_bpm())
    raw_confidence = float(tempo.get_confidence())
    if bpm <= 0.0 or raw_confidence <= 0.0:
        return None
    while bpm < minimum_bpm:
        bpm *= 2.0
    while bpm > maximum_bpm:
        bpm /= 2.0

    if len(beat_times) >= 3:
        intervals = np.diff(np.asarray(beat_times, dtype=np.float64))
        intervals = intervals[intervals > 1e-3]
        if intervals.size:
            interval_bpm = 60.0 / float(np.median(intervals))
            ratio = interval_bpm / max(bpm, 1e-9)
            if minimum_bpm <= interval_bpm <= maximum_bpm and (
                0.85 <= ratio <= 1.15 or 0.45 <= ratio <= 0.55 or 1.8 <= ratio <= 2.2
            ):
                bpm = interval_bpm

    confidence = min(1.0, raw_confidence / 2.0)
    if confidence <= 0.0:
        return None
    return float(bpm), confidence
