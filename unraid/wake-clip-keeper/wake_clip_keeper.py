#!/usr/bin/env python3
"""Turn Home Assistant Assist debug recordings into small clips, automatically.

With `assist_pipeline: debug_recording_dir` set and the wake word running in Home
Assistant, every pipeline run writes a folder with a wake word WAV that holds
*everything* streamed while waiting for the wake word (often hundreds of MB), plus a
small speech-to-text WAV.

This loop watches that directory. When a run folder has stopped changing, it saves:
  - the last CLIP_SECONDS of each wake word WAV (the audio that triggered it),
  - each other WAV (speech-to-text) as is, capped at MAX_OTHER_SECONDS,
into CLIPS_DIR, then deletes the run folder. Clips older than KEEP_DAYS are removed,
and the oldest go first if CLIPS_DIR grows past MAX_CLIPS_MB.

Standard library only. Configuration is through environment variables (see below).
"""

import os
import shutil
import sys
import time
import wave
from pathlib import Path

RECORDINGS_DIR = Path(os.environ.get("RECORDINGS_DIR", "/recordings"))
CLIPS_DIR = Path(os.environ.get("CLIPS_DIR", "/clips"))
CLIP_SECONDS = float(os.environ.get("CLIP_SECONDS", "6"))
MAX_OTHER_SECONDS = float(os.environ.get("MAX_OTHER_SECONDS", "30"))
KEEP_DAYS = float(os.environ.get("KEEP_DAYS", "14"))
MAX_CLIPS_MB = float(os.environ.get("MAX_CLIPS_MB", "500"))
SETTLE_SECONDS = float(os.environ.get("SETTLE_SECONDS", "120"))
SCAN_INTERVAL = float(os.environ.get("SCAN_INTERVAL", "60"))


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def wav_tail(src: Path, dst: Path, seconds: float) -> float:
    """Copy the last `seconds` of a WAV. Returns the clip length in seconds.

    Reads the sample format from the header but takes the audio length from the file
    size, so files whose header was never finalised (HA restarted mid-run) still work.
    """
    with open(src, "rb") as f:
        head = f.read(4096)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError("not a WAV file")
        fmt = head.find(b"fmt ")
        data = head.find(b"data")
        if fmt < 0 or data < 0:
            raise ValueError("missing fmt/data chunk")
        channels = int.from_bytes(head[fmt + 10 : fmt + 12], "little")
        rate = int.from_bytes(head[fmt + 12 : fmt + 16], "little")
        width = int.from_bytes(head[fmt + 22 : fmt + 24], "little") // 8
        frame = channels * width
        data_start = data + 8
        data_len = (src.stat().st_size - data_start) // frame * frame
        want = min(data_len, int(seconds * rate) * frame)
        f.seek(data_start + data_len - want)
        audio = f.read(want)

    tmp = dst.with_suffix(".part")
    with wave.open(str(tmp), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(audio)
    tmp.rename(dst)
    return len(audio) / frame / rate


def match_owner(path: Path, like: Path) -> None:
    try:
        st = like.stat()
        os.chown(path, st.st_uid, st.st_gid)
        os.chmod(path, 0o664)
    except OSError:
        pass


def run_dirs():
    """Folders that directly contain WAV files (one per pipeline run)."""
    seen = set()
    for wav in RECORDINGS_DIR.rglob("*.wav"):
        if CLIPS_DIR in wav.parents or wav.parent == RECORDINGS_DIR:
            continue  # never delete the clips or the recordings root itself
        if wav.parent not in seen:
            seen.add(wav.parent)
            yield wav.parent


def process_run(run_dir: Path) -> None:
    wavs = sorted(run_dir.glob("*.wav"))
    newest = max(w.stat().st_mtime for w in wavs)
    if time.time() - newest < SETTLE_SECONDS:
        return  # still being written (a wake run grows until something triggers)

    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(newest))
    rel = run_dir.relative_to(RECORDINGS_DIR)
    # Device folder names are long ids; keep a short, stable piece for grouping.
    device = rel.parts[0][:8] if len(rel.parts) > 1 else "run"
    saved = []
    for wav in wavs:
        # HA names them like 00_wake-wake_word.openwakeword.wav, 01_stt-stt.onnx_asr.wav
        kind = wav.stem.split("-")[0].lstrip("0123456789_") or "audio"
        is_wake = kind == "wake"
        dst = CLIPS_DIR / f"{stamp}_{device}_{kind}.wav"
        n = 1
        while dst.exists():
            dst = CLIPS_DIR / f"{stamp}_{device}_{kind}_{n}.wav"
            n += 1
        try:
            secs = wav_tail(wav, dst, CLIP_SECONDS if is_wake else MAX_OTHER_SECONDS)
        except (OSError, ValueError) as err:
            log(f"skip {wav}: {err}")
            continue
        match_owner(dst, wav)
        saved.append(f"{dst.name} ({secs:.1f}s, from {wav.stat().st_size / 1e6:.1f} MB)")

    shutil.rmtree(run_dir, ignore_errors=True)
    log(f"{rel}: " + (", ".join(saved) if saved else "nothing usable") + "; original deleted")


def prune_clips() -> None:
    clips = sorted(CLIPS_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    cutoff = time.time() - KEEP_DAYS * 86400
    total = sum(p.stat().st_size for p in clips)
    for clip in clips:
        too_old = clip.stat().st_mtime < cutoff
        too_big = total > MAX_CLIPS_MB * 1e6
        if not (too_old or too_big):
            break
        total -= clip.stat().st_size
        clip.unlink(missing_ok=True)
    for part in CLIPS_DIR.glob("*.part"):
        if time.time() - part.stat().st_mtime > 3600:
            part.unlink(missing_ok=True)


def main() -> None:
    if not RECORDINGS_DIR.is_dir():
        sys.exit(f"RECORDINGS_DIR {RECORDINGS_DIR} does not exist (check the volume mapping)")
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    log(
        f"watching {RECORDINGS_DIR} -> {CLIPS_DIR}: wake clips {CLIP_SECONDS:g}s, "
        f"keep {KEEP_DAYS:g} days / {MAX_CLIPS_MB:g} MB"
    )
    while True:
        try:
            for run_dir in list(run_dirs()):
                process_run(run_dir)
            prune_clips()
        except Exception as err:  # keep the loop alive; report and retry next scan
            log(f"error: {err!r}")
        if os.environ.get("RUN_ONCE"):
            return
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
