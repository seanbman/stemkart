from __future__ import annotations

import unittest
import shutil

import numpy as np

from effects import EffectState, chain_from_dicts, default_chain, process_chain, process_time_pitch


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


if __name__ == "__main__":
    unittest.main()
