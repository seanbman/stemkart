# 5. Effects

[← Stem Splitter](04-stem-splitter.md) · [Contents](README.md) · [Next: Recording →](06-recording.md)

## 5.1 Select a source

Select an audio file in Samples. StemKart loads the current selection into Effects, where the waveform previews the processed result without changing the source file.

## 5.2 Pitch and time

The controls above the effect chain are physical-style dials:

- **Octave**: coarse pitch shift from -4 to +4 octaves.
- **Key**: fine pitch shift from -12 to +12 semitones.
- **Speed**: time-stretch multiplier from 0.25× to 2.00× without changing pitch.
- **Preserve formants**: optional formant preservation during pitch shifting.

Octave and Key combine into one total semitone shift. Rubber Band performs the decoupled pitch and speed processing.

Click a dial and use the mouse or arrow keys for adjustment. Dials stop at their minimum and maximum values.

## 5.3 Effect chain

The default order is:

1. High-Pass EQ
2. Low-Pass / Band-Pass Filter
3. Compression
4. Transient Shaping
5. Saturation / Distortion
6. Chorus / Phaser
7. Delay
8. Reverb

Every effect starts at `0`, which bypasses it. Use the mode selector where an effect has variants, then adjust its amount. **Move Up** and **Move Down** reorder the selected effect in the chain.

## 5.4 Presets and export

Enter a name and choose **Save** to store the complete chain, pitch, speed, and formant settings. Choose a saved preset and press **Load** to apply it to the active sample. **Delete** removes the selected preset.

Use **Save effected WAV** to render the current sample selection and save a new WAV. The exported file is registered in the managed `trimmed` library folder when you save it there.

[← Stem Splitter](04-stem-splitter.md) · [Contents](README.md) · [Next: Recording →](06-recording.md)
