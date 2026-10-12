#!/usr/bin/env python3
"""Trim every WAV in a folder to its last N seconds, in place.

For wake word recordings the end of the file is what triggered the detection, so
the tail is the part worth reviewing and feeding back into training.

    python3 trim_clips.py [SECONDS] [FOLDER]     # defaults: 30, the script's own folder

Files already that short are left alone. Each file is rewritten through a temporary
file and only replaced once the trimmed copy is complete. Works on files whose WAV
header was never finalised. Standard library only.
"""

import sys
import wave
from pathlib import Path


def trim(src: Path, seconds: float) -> str:
    with open(src, "rb") as f:
        head = f.read(4096)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return "skipped (not a WAV)"
        fmt, data = head.find(b"fmt "), head.find(b"data")
        if fmt < 0 or data < 0:
            return "skipped (no fmt/data chunk)"
        channels = int.from_bytes(head[fmt + 10 : fmt + 12], "little")
        rate = int.from_bytes(head[fmt + 12 : fmt + 16], "little")
        width = int.from_bytes(head[fmt + 22 : fmt + 24], "little") // 8
        frame = channels * width
        data_start = data + 8
        data_len = (src.stat().st_size - data_start) // frame * frame
        keep = int(seconds * rate) * frame
        if data_len <= keep:
            return f"kept ({data_len / frame / rate:.1f}s)"
        f.seek(data_start + data_len - keep)
        audio = f.read(keep)

    tmp = src.with_name(src.name + ".trimming")
    with wave.open(str(tmp), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(audio)
    tmp.replace(src)
    return f"trimmed {data_len / frame / rate:.0f}s -> {seconds:g}s"


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    folder = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent
    wavs = sorted(folder.glob("*.wav"))
    if not wavs:
        sys.exit(f"no .wav files in {folder}")
    before = sum(w.stat().st_size for w in wavs)
    for wav in wavs:
        try:
            print(f"{wav.name}: {trim(wav, seconds)}", flush=True)
        except OSError as err:
            print(f"{wav.name}: error {err}", flush=True)
    after = sum(w.stat().st_size for w in wavs if w.exists())
    print(f"\n{len(wavs)} files, {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
