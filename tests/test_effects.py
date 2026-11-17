from __future__ import annotations

import unittest
import shutil

import numpy as np

from effects import EffectState, chain_from_dicts, default_chain, process_chain, process_time_pitch
from key_detection import detect_bpm, detect_pitch_class
from main import (
    bpm_value_from_detection,
    dial_angle_degrees,
    dial_value_from_angle,
    dial_value_from_horizontal_drag,
    format_time_precise,
    format_trim_range,
)


class EffectsEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        time = np.arange(44100, dtype=np.float32) / 44100.0
        self.samples = (0.35 * np.sin(2.0 * np.pi * 440.0 * time)).astype(np.float32)

    def test_default_chain_is_bypassed(self) -> None:
        output = process_chain(self.samples, 44100, default_chain())
        np.testing.assert_array_equal(output, self.samples)

    def test_each_effect_produces_finite_bounded_output(self) -> None:
        for index, state in enumerate(default_chain()):
            chain = default_chain()
            chain[index] = EffectState(state.effect_id, 65, state.mode)
            output = process_chain(self.samples, 44100, chain)
            self.assertEqual(output.shape, self.samples.shape)
            self.assertTrue(np.isfinite(output).all(), state.effect_id)
            self.assertLessEqual(float(np.max(np.abs(output))), 1.0)

    def test_filter_and_modulation_modes_are_preserved(self) -> None:
        chain = chain_from_dicts(
            [
                {"effect_id": "filter", "amount": 40, "mode": "band_pass"},
                {"effect_id": "modulation", "amount": 40, "mode": "phaser"},
            ]
        )
        self.assertEqual(chain[0].mode, "band_pass")
        self.assertEqual(chain[1].mode, "phaser")

    def test_preset_data_is_clamped_and_completed(self) -> None:
        chain = chain_from_dicts([{"effect_id": "reverb", "amount": 500}])
        self.assertEqual(chain[0].effect_id, "reverb")
        self.assertEqual(chain[0].amount, 100)
        self.assertEqual(len(chain), 8)
        self.assertEqual(len({item.effect_id for item in chain}), 8)

    def test_chain_order_changes_processing_order(self) -> None:
        first = [EffectState("saturation", 70), EffectState("compressor", 70)]
        second = list(reversed(first))
        output_first = process_chain(self.samples, 44100, first)
        output_second = process_chain(self.samples, 44100, second)
        self.assertFalse(np.array_equal(output_first, output_second))

    @unittest.skipUnless(shutil.which("rubberband"), "Rubber Band CLI is not installed")
    def test_rubber_band_decouples_speed_from_pitch_path(self) -> None:
        output = process_time_pitch(self.samples, 44100, 1, -3, 2.0, True)
        self.assertAlmostEqual(len(output) / len(self.samples), 0.5, delta=0.03)
        self.assertTrue(np.isfinite(output).all())

    def test_dial_neutral_values_map_to_zero_degrees(self) -> None:
        self.assertEqual(dial_angle_degrees(0, -4, 4, 0), 90.0)
        self.assertEqual(dial_angle_degrees(0, -12, 12, 0), 90.0)
        self.assertEqual(dial_angle_degrees(100, 25, 200, 100), 90.0)

    def test_dial_values_use_bounded_monotonic_sweep(self) -> None:
        angles = [dial_angle_degrees(value, 25, 200, 100) for value in (25, 75, 100, 150, 200)]
        self.assertEqual(angles[0], 225.0)
        self.assertEqual(angles[2], 90.0)
        self.assertEqual(angles[-1], -45.0)
        self.assertEqual(angles, sorted(angles, reverse=True))

    def test_dial_angles_round_trip_clockwise(self) -> None:
        for value in (25, 75, 100, 150, 200):
            angle = dial_angle_degrees(value, 25, 200, 100)
            self.assertEqual(dial_value_from_angle(angle, 25, 200, 100), value)

    def test_horizontal_dial_drag_is_right_positive_and_clamped(self) -> None:
        self.assertEqual(dial_value_from_horizontal_drag(100, 10, 2, 25, 200), 105)
        self.assertEqual(dial_value_from_horizontal_drag(100, -10, 2, 25, 200), 95)
        self.assertEqual(dial_value_from_horizontal_drag(25, -1000, 2, 25, 200), 25)
        self.assertEqual(dial_value_from_horizontal_drag(200, 1000, 2, 25, 200), 200)

    def test_detected_bpm_is_rounded_and_clamped_to_dial_range(self) -> None:
        self.assertEqual(bpm_value_from_detection(96.4), 96)
        self.assertEqual(bpm_value_from_detection(480), 240)
        self.assertEqual(bpm_value_from_detection(-1), 0)

    def test_precise_playback_and_trim_time_format(self) -> None:
        self.assertEqual(format_time_precise(0), "0:00.000")
        self.assertEqual(format_time_precise(61_234), "1:01.234")
        self.assertEqual(format_trim_range(0, 0), "Trim: 0:00.000-0:00.000")

    def test_pitch_class_detector_finds_c_major_material(self) -> None:
        time = np.arange(44100 * 3, dtype=np.float32) / 44100.0
        frequencies = (261.6256, 329.6276, 391.9954)
        samples = sum(np.sin(2.0 * np.pi * frequency * time) for frequency in frequencies).astype(np.float32)
        result = detect_pitch_class(samples, 44100)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[0], 0)
        self.assertGreater(result[1], 0.08)

    def test_pitch_class_detector_rejects_silence(self) -> None:
        self.assertIsNone(detect_pitch_class(np.zeros(44100, dtype=np.float32), 44100))

    def test_pitch_class_detector_rejects_broadband_noise(self) -> None:
        noise = np.random.default_rng(1).normal(0.0, 0.2, 44100 * 3).astype(np.float32)
        self.assertIsNone(detect_pitch_class(noise, 44100))

    def test_bpm_detector_finds_regular_clicks(self) -> None:
        sample_rate = 44100
        bpm = 120
        samples = np.zeros(sample_rate * 8, dtype=np.float32)
        interval = round(sample_rate * 60 / bpm)
        samples[::interval] = 1.0
        result = detect_bpm(samples, sample_rate)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result[0], bpm, delta=1.0)
        self.assertGreater(result[1], 0.08)

    def test_bpm_detector_tracks_multiple_tempi(self) -> None:
        sample_rate = 44100
        for bpm in (90, 150):
            samples = np.zeros(sample_rate * 8, dtype=np.float32)
            interval = round(sample_rate * 60 / bpm)
            samples[::interval] = 1.0
            result = detect_bpm(samples, sample_rate)
            self.assertIsNotNone(result, bpm)
            assert result is not None
            self.assertAlmostEqual(result[0], bpm, delta=3.0)
            self.assertGreater(result[1], 0.08)

    def test_bpm_detector_rejects_silence(self) -> None:
        self.assertIsNone(detect_bpm(np.zeros(44100, dtype=np.float32), 44100))

    def test_bpm_detector_marks_broadband_noise_low_confidence(self) -> None:
        noise = np.random.default_rng(1).normal(0.0, 0.2, 44100 * 8).astype(np.float32)
        result = detect_bpm(noise, 44100)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertLess(result[1], 0.08)


if __name__ == "__main__":
    unittest.main()
