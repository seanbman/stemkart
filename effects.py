"""Small, dependency-free audio effects used by StemKart's Effects tab."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

import numpy as np


DEFAULT_EFFECT_ORDER = (
    "high_pass",
    "filter",
    "compressor",
    "transient",
    "saturation",
    "modulation",
    "delay",
    "reverb",
)

EFFECT_LABELS = {
    "high_pass": "High-Pass EQ",
    "filter": "Low-Pass / Band-Pass Filter",
    "compressor": "Compression",
    "transient": "Transient Shaping",
    "saturation": "Saturation / Distortion",
    "modulation": "Chorus / Phaser",
    "delay": "Delay",
    "reverb": "Reverb",
}


@dataclass
class EffectState:
    effect_id: str
    amount: int = 0
    mode: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def default_chain() -> list[EffectState]:
    return [
        EffectState("high_pass"),
        EffectState("filter", mode="low_pass"),
        EffectState("compressor"),
        EffectState("transient"),
        EffectState("saturation"),
        EffectState("modulation", mode="chorus"),
        EffectState("delay"),
        EffectState("reverb"),
    ]


def chain_from_dicts(records: object) -> list[EffectState]:
    if not isinstance(records, list):
        return default_chain()
    result: list[EffectState] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        effect_id = record.get("effect_id")
        if effect_id not in DEFAULT_EFFECT_ORDER or effect_id in {item.effect_id for item in result}:
            continue
        amount = record.get("amount", 0)
        try:
            amount = max(-100, min(100, int(amount)))
        except (TypeError, ValueError):
            amount = 0
        mode = record.get("mode", "")
        if not isinstance(mode, str):
            mode = ""
        result.append(EffectState(effect_id, amount, mode))
    defaults = {item.effect_id: item for item in default_chain()}
    result.extend(defaults[effect_id] for effect_id in DEFAULT_EFFECT_ORDER if effect_id not in {item.effect_id for item in result})
    return result


def _one_pole_lowpass(samples: np.ndarray, cutoff: float, sample_rate: int) -> np.ndarray:
    if samples.size == 0:
        return samples.copy()
    cutoff = max(1.0, min(float(cutoff), sample_rate * 0.49))
    coefficient = float(np.exp(-2.0 * np.pi * cutoff / sample_rate))
    output = np.empty_like(samples, dtype=np.float32)
    previous = 0.0
    factor = 1.0 - coefficient
    for index, value in enumerate(samples):
        previous = factor * float(value) + coefficient * previous
        output[index] = previous
    return output


def _highpass(samples: np.ndarray, cutoff: float, sample_rate: int) -> np.ndarray:
    return samples - _one_pole_lowpass(samples, cutoff, sample_rate)


def _blend(dry: np.ndarray, wet: np.ndarray, amount: int) -> np.ndarray:
    strength = abs(amount) / 100.0
    if amount > 0:
        return dry * (1.0 - strength) + wet * strength
    if amount < 0:
        # Negative values intentionally reduce the dry contribution for
        # processors that do not have a useful inverse operation.
        return dry * (1.0 - strength)
    return dry.copy()


def _filter_effect(samples: np.ndarray, sample_rate: int, state: EffectState) -> np.ndarray:
    amount = abs(state.amount) / 100.0
    if amount == 0:
        return samples.copy()
    if state.mode == "band_pass":
        low = 80.0 + amount * 900.0
        high = max(low + 80.0, 12000.0 - amount * 10500.0)
        processed = _one_pole_lowpass(_highpass(samples, low, sample_rate), high, sample_rate)
    else:
        cutoff = 20000.0 * ((350.0 / 20000.0) ** amount)
        processed = _one_pole_lowpass(samples, cutoff, sample_rate)
    return _blend(samples, processed, state.amount)


def _compress(samples: np.ndarray, sample_rate: int, amount: int) -> np.ndarray:
    strength = abs(amount) / 100.0
    if strength == 0:
        return samples.copy()
    attack = np.exp(-1.0 / max(1.0, sample_rate * (0.005 - strength * 0.003)))
    release = np.exp(-1.0 / max(1.0, sample_rate * (0.12 - strength * 0.08)))
    envelope = np.empty_like(samples, dtype=np.float32)
    previous = 0.0
    for index, value in enumerate(np.abs(samples)):
        coefficient = attack if value > previous else release
        previous = coefficient * previous + (1.0 - coefficient) * float(value)
        envelope[index] = previous
    threshold = 10.0 ** ((-12.0 - strength * 30.0) / 20.0)
    ratio = 1.0 + strength * 11.0
    over = np.maximum(envelope / max(threshold, 1e-6), 1.0)
    gain = np.ones_like(samples, dtype=np.float32)
    gain[over > 1.0] = over[over > 1.0] ** (1.0 / ratio - 1.0)
    makeup = 1.0 + strength * 0.35
    compressed = samples * gain * makeup
    if amount > 0:
        return compressed
    # The negative half is a gentle upward expansion rather than a clipped
    # inverse compressor, which keeps the centered control useful.
    expanded_gain = np.where(gain > 0, 1.0 / np.maximum(gain, 0.25), 1.0)
    return np.clip(samples * (1.0 + (expanded_gain - 1.0) * strength * 0.35), -1.0, 1.0)


def _transient(samples: np.ndarray, sample_rate: int, amount: int) -> np.ndarray:
    strength = abs(amount) / 100.0
    if strength == 0:
        return samples.copy()
    sustain = _one_pole_lowpass(samples, 180.0, sample_rate)
    attack = samples - sustain
    if amount > 0:
        return np.clip(samples + attack * (strength * 1.8), -1.0, 1.0)
    return np.clip(samples - sustain * (strength * 0.65), -1.0, 1.0)


def _saturate(samples: np.ndarray, amount: int) -> np.ndarray:
    strength = abs(amount) / 100.0
    if strength == 0:
        return samples.copy()
    drive = 1.0 + strength * 14.0
    wet = np.tanh(samples * drive) / np.tanh(drive)
    return _blend(samples, wet, amount)


def _variable_delay(samples: np.ndarray, delay_samples: np.ndarray) -> np.ndarray:
    positions = np.arange(samples.size, dtype=np.float32) - delay_samples
    return np.interp(positions, np.arange(samples.size, dtype=np.float32), samples, left=0.0, right=0.0).astype(np.float32)


def _modulate(samples: np.ndarray, sample_rate: int, state: EffectState) -> np.ndarray:
    strength = abs(state.amount) / 100.0
    if strength == 0:
        return samples.copy()
    time = np.arange(samples.size, dtype=np.float32) / sample_rate
    lfo = (np.sin(2.0 * np.pi * (0.15 + strength * 0.9) * time) + 1.0) / 2.0
    if state.mode == "phaser":
        delayed = _variable_delay(samples, (1.0 + 4.0 * lfo) * sample_rate / 1000.0)
        wet = samples - delayed * (0.55 + strength * 0.25)
    else:
        delay = (12.0 + 18.0 * lfo) * sample_rate / 1000.0
        wet = _variable_delay(samples, delay)
    return _blend(samples, wet, state.amount)


def _delay(samples: np.ndarray, sample_rate: int, amount: int) -> np.ndarray:
    strength = abs(amount) / 100.0
    if strength == 0:
        return samples.copy()
    delay_samples = max(1, round(sample_rate * (0.08 + strength * 0.42)))
    output = samples.astype(np.float32, copy=True)
    feedback = 0.15 + strength * 0.45
    for index in range(delay_samples, samples.size):
        output[index] += samples[index - delay_samples] * (0.22 + strength * 0.35)
        if index >= delay_samples * 2:
            output[index] += output[index - delay_samples] * feedback * 0.35
    return _blend(samples, np.clip(output, -1.0, 1.0), amount)


def _reverb(samples: np.ndarray, sample_rate: int, amount: int) -> np.ndarray:
    strength = abs(amount) / 100.0
    if strength == 0:
        return samples.copy()
    output = samples.astype(np.float32, copy=True)
    for milliseconds, gain in ((31, 0.32), (47, 0.24), (73, 0.18), (109, 0.12)):
        delay = max(1, round(sample_rate * milliseconds / 1000.0))
        if delay < samples.size:
            output[delay:] += samples[:-delay] * gain * strength
    return _blend(samples, np.clip(output, -1.0, 1.0), amount)


def process_chain(samples: np.ndarray, sample_rate: int, chain: list[EffectState]) -> np.ndarray:
    output = np.asarray(samples, dtype=np.float32).reshape(-1).copy()
    for state in chain:
        if state.amount == 0 or not output.size:
            continue
        if state.effect_id == "high_pass":
            cutoff = 20.0 * ((2000.0 / 20.0) ** (abs(state.amount) / 100.0))
            output = _blend(output, _highpass(output, cutoff, sample_rate), state.amount)
        elif state.effect_id == "filter":
            output = _filter_effect(output, sample_rate, state)
        elif state.effect_id == "compressor":
            output = _compress(output, sample_rate, state.amount)
        elif state.effect_id == "transient":
            output = _transient(output, sample_rate, state.amount)
        elif state.effect_id == "saturation":
            output = _saturate(output, state.amount)
        elif state.effect_id == "modulation":
            output = _modulate(output, sample_rate, state)
        elif state.effect_id == "delay":
            output = _delay(output, sample_rate, state.amount)
        elif state.effect_id == "reverb":
            output = _reverb(output, sample_rate, state.amount)
        output = np.nan_to_num(np.clip(output, -1.0, 1.0), copy=False).astype(np.float32, copy=False)
    return output


def _read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    if sample_width == 1:
        values = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 2:
        values = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        values = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported Rubber Band output sample width: {sample_width}")
    if channels > 1:
        values = values.reshape(-1, channels).mean(axis=1)
    return values.astype(np.float32, copy=False), sample_rate


def process_time_pitch(
    samples: np.ndarray,
    sample_rate: int,
    octave: int,
    key: int,
    speed: float,
    formant_preservation: bool,
) -> np.ndarray:
    """Apply independent pitch and duration changes with Rubber Band."""
    total_semitones = int(octave) * 12 + int(key)
    speed = max(0.25, min(2.0, float(speed)))
    if total_semitones == 0 and abs(speed - 1.0) < 1e-6:
        return np.asarray(samples, dtype=np.float32).copy()
    executable = shutil.which("rubberband")
    if executable is None:
        raise RuntimeError("Rubber Band is required for pitch and speed processing")
    with tempfile.TemporaryDirectory(prefix="stemkart-rubberband-") as directory:
        root = Path(directory)
        source = root / "source.wav"
        destination = root / "processed.wav"
        write_wav(source, samples, sample_rate)
        command = [
            executable,
            "--fine",
            "--quiet",
            "--tempo",
            str(speed),
            "--pitch",
            str(total_semitones),
        ]
        if formant_preservation and total_semitones:
            command.append("--formant")
        command.extend((str(source), str(destination)))
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Could not run Rubber Band: {exc}") from exc
        if result.returncode != 0 or not destination.exists():
            message = result.stderr.strip() or result.stdout.strip() or "Rubber Band could not process the sample."
            raise RuntimeError(message)
        processed, output_rate = _read_wav_mono(destination)
        if output_rate != sample_rate:
            raise RuntimeError(f"Rubber Band returned {output_rate} Hz instead of {sample_rate} Hz")
        return processed


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())
