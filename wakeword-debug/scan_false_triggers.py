#!/usr/bin/env python3
"""Scan long recordings for wake word detections, offline.

Runs a microWakeWord model (the same .tflite + .json you flash to the
ESP32-S3) and/or an openWakeWord model over WAV files and reports every
detection with a timestamp, the peak score, and a clip of the audio around it.
Feed it hours of normal household audio (TV, conversation, cooking) that never
contains the wake word: every detection is a false accept you can listen to.

It also sweeps cutoffs so you can see false accepts per hour at each threshold
before changing probability_cutoff in the model JSON or the oWW threshold.

Input: 16 kHz, 16-bit, mono WAV. Convert anything else first, e.g.
    ffmpeg -i in.mp3 -ar 16000 -ac 1 -sample_fmt s16 out.wav

Setup:
    python3 -m venv venv && venv/bin/pip install pymicro-wakeword openwakeword

Examples:
    venv/bin/python scan_false_triggers.py --mww-config my_word.json tv_*.wav
    venv/bin/python scan_false_triggers.py --oww-model my_word.onnx \\
        --oww-threshold 0.5 --clips-dir clips/ recordings/
"""

import argparse
import sys
import wave
from pathlib import Path
from typing import Callable, Iterable, List, Tuple

import numpy as np

SAMPLE_RATE = 16000
REFRACTORY_SEC = 2.0  # ignore re-triggers this soon after a detection
CLIP_BEFORE_SEC = 2.5
CLIP_AFTER_SEC = 1.0

# (time in seconds, score)
Scores = List[Tuple[float, float]]


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav_file:
        if (
            wav_file.getframerate() != SAMPLE_RATE
            or wav_file.getsampwidth() != 2
            or wav_file.getnchannels() != 1
        ):
            raise ValueError(f"{path}: need 16 kHz 16-bit mono (see --help)")
        return wav_file.readframes(wav_file.getnframes())


def mww_scorer(config_path: str) -> Tuple[str, float, Callable[[bytes], Scores]]:
    from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures

    mww = MicroWakeWord.from_config(config_path)

    def score(audio: bytes) -> Scores:
        mww.reset()
        features = MicroWakeWordFeatures()
        scores: Scores = []
        for i, frame in enumerate(features.process_streaming(audio)):
            # One feature frame per 10 ms; the model runs once per stride.
            prob = mww.process_streaming_prob(frame)
            if (prob is not None) and ((i + 1) % mww.stride == 0):
                scores.append(((i + 1) * 0.01, prob))
        return scores

    return f"microWakeWord:{mww.wake_word}", mww.probability_cutoff, score


def oww_scorer(
    model_path: str, threshold: float, vad_threshold: float
) -> Tuple[str, float, Callable[[bytes], Scores]]:
    from openwakeword.model import Model

    try:  # openwakeword >= 0.5
        model = Model(
            wakeword_models=[model_path],
            inference_framework="onnx" if model_path.endswith(".onnx") else "tflite",
            vad_threshold=vad_threshold,
        )
    except TypeError:  # openwakeword 0.4
        model = Model(wakeword_model_paths=[model_path], vad_threshold=vad_threshold)

    name = next(iter(model.models))
    chunk_samples = 1280  # 80 ms, what openWakeWord expects per predict()

    def score(audio: bytes) -> Scores:
        model.reset()
        samples = np.frombuffer(audio, dtype=np.int16)
        scores: Scores = []
        for start in range(0, len(samples) - chunk_samples + 1, chunk_samples):
            result = model.predict(samples[start : start + chunk_samples])
            scores.append(((start + chunk_samples) / SAMPLE_RATE, float(result[name])))
        return scores

    return f"openWakeWord:{name}", threshold, score


def detections(scores: Scores, cutoff: float) -> Scores:
    """Collapse runs of above-cutoff scores into single detections."""
    found: Scores = []
    for t, s in scores:
        if s <= cutoff:
            continue
        if found and (t - found[-1][0]) < REFRACTORY_SEC:
            if s > found[-1][1]:
                found[-1] = (found[-1][0], s)  # keep first time, peak score
            continue
        found.append((t, s))
    return found


def save_clip(audio: bytes, t: float, out_path: Path) -> None:
    start = max(0, int((t - CLIP_BEFORE_SEC) * SAMPLE_RATE)) * 2
    end = min(len(audio), int((t + CLIP_AFTER_SEC) * SAMPLE_RATE) * 2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.setsampwidth(2)
        wav_file.setnchannels(1)
        wav_file.writeframes(audio[start:end])


def iter_wavs(paths: Iterable[str]) -> Iterable[Path]:
    for p in map(Path, paths):
        if p.is_dir():
            yield from sorted(p.rglob("*.wav"))
        else:
            yield p


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("inputs", nargs="+", help="WAV files or directories")
    parser.add_argument("--mww-config", help="microWakeWord model JSON")
    parser.add_argument("--oww-model", help="openWakeWord .onnx/.tflite model")
    parser.add_argument("--oww-threshold", type=float, default=0.5)
    parser.add_argument(
        "--oww-vad-threshold",
        type=float,
        default=0.0,
        help="Silero VAD gate for openWakeWord (0 = off, try 0.5)",
    )
    parser.add_argument(
        "--cutoff", type=float, help="Override the detection cutoff/threshold"
    )
    parser.add_argument(
        "--sweep",
        default="0.5,0.6,0.7,0.8,0.9,0.95,0.97,0.99",
        help="Comma-separated cutoffs to report false accepts/hour for",
    )
    parser.add_argument("--clips-dir", help="Save a WAV clip around each detection")
    args = parser.parse_args()

    scorers = []
    if args.mww_config:
        scorers.append(mww_scorer(args.mww_config))
    if args.oww_model:
        scorers.append(
            oww_scorer(args.oww_model, args.oww_threshold, args.oww_vad_threshold)
        )
    if not scorers:
        parser.error("give --mww-config and/or --oww-model")

    sweep = [float(c) for c in args.sweep.split(",") if c]

    for label, default_cutoff, score in scorers:
        cutoff = default_cutoff if args.cutoff is None else args.cutoff
        total_sec = 0.0
        sweep_counts = {c: 0 for c in sweep}
        print(f"=== {label} (cutoff {cutoff:.3f})")

        for wav_path in iter_wavs(args.inputs):
            try:
                audio = read_wav(wav_path)
            except (ValueError, wave.Error) as err:
                print(f"skip: {err}", file=sys.stderr)
                continue

            total_sec += len(audio) / 2 / SAMPLE_RATE
            scores = score(audio)
            for c in sweep:
                sweep_counts[c] += len(detections(scores, c))

            for t, s in detections(scores, cutoff):
                print(f"{wav_path}  {t // 60:4.0f}:{t % 60:05.2f}  score={s:.3f}")
                if args.clips_dir:
                    clip_name = f"{wav_path.stem}_{t:08.2f}s_{s:.3f}.wav"
                    save_clip(
                        audio, t, Path(args.clips_dir) / label.replace(":", "_") / clip_name
                    )

        hours = total_sec / 3600
        print(f"--- scanned {hours:.2f} h")
        if hours > 0:
            for c in sweep:
                print(
                    f"  cutoff {c:.2f}: {sweep_counts[c]:4d} detections"
                    f"  ({sweep_counts[c] / hours:.2f}/h)"
                )
        print()


if __name__ == "__main__":
    main()
