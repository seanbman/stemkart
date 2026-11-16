# 6. Recording

[← Effects](05-effects.md) · [Contents](README.md) · [Next: Troubleshooting →](07-troubleshooting.md)

## 6.1 Recording sources

StemKart can record from a microphone through Qt Multimedia. On Linux with PipeWire it can also discover system-output monitors and application audio targets.

Choose the desired source in the recording panel, then use the record and stop controls. Recordings are written as WAV files in the managed `recorded` library directory.

## 6.2 PipeWire sources

For system or application capture, make sure these commands are installed and available on `PATH`:

```bash
pw-dump
pw-record
pw-link
```

If a source is missing, check the PipeWire graph and confirm that the target is producing audio before recording.

## 6.3 Recording workflow

1. Choose a recording source.
2. Start recording.
3. Stop recording when finished.
4. Select the new WAV in the Library or Explorer.
5. Trim, split, or process it with Effects.

StemKart keeps recording local and does not upload the captured audio.

[← Effects](05-effects.md) · [Contents](README.md) · [Next: Troubleshooting →](07-troubleshooting.md)
