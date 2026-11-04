#!/usr/bin/env python3
"""Local, checkpointed Korean transcription on Apple Silicon."""
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import wave
from datetime import date
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def stamp(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def metrics(path):
    import numpy as np
    import soundfile as sf
    audio, rate = sf.read(path, dtype="float32")
    if rate != 16000 or audio.ndim != 1 or len(audio) == 0:
        raise ValueError("Expected nonempty 16 kHz mono audio")
    nonzero = np.flatnonzero(audio)
    silent = []
    for offset in range(0, len(audio), rate * 10):
        block = audio[offset:offset + rate * 10]
        if len(block) == rate * 10 and not np.count_nonzero(block):
            silent.append(offset / rate)
    return {
        "duration": len(audio) / rate,
        "peak": float(np.max(np.abs(audio))),
        "rms": float(np.sqrt(np.mean(audio * audio))),
        "signal_end": (int(nonzero[-1]) + 1) / rate if len(nonzero) else 0,
        "zero_10second_windows": silent,
    }


def prepare(source, output, args):
    import av
    import soundfile as sf

    state_path = output / "source.json"
    audio_path = output / "audio-16k.wav"
    before = source.stat()
    source_hash = digest(source)
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["source_sha256"] != source_hash:
            raise ValueError("This output folder belongs to another recording. Use a new folder.")
        if not audio_path.exists() or digest(audio_path) != state["audio_sha256"]:
            raise ValueError("Prepared audio changed or is missing. Use a new output folder.")
    else:
        if audio_path.exists():
            raise ValueError("Untracked audio exists in the output folder. Use a new folder.")
        with av.open(str(source)) as container:
            metadata = dict(container.metadata)
            converter = av.AudioResampler(format="s16", layout="mono", rate=16000)
            with wave.open(str(audio_path), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(16000)
                for frame in container.decode(audio=0):
                    for converted in converter.resample(frame):
                        target.writeframes(converted.to_ndarray().astype("<i2").tobytes())
                for converted in converter.resample(None):
                    target.writeframes(converted.to_ndarray().astype("<i2").tobytes())
        primary = metrics(audio_path)
        verification = {"primary_decoder": primary}
        if shutil.which("afconvert"):
            independent = output / "audio-native-check.wav"
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                            str(source), str(independent)], check=True)
            secondary = metrics(independent)
            verification["independent_decoder"] = secondary
            difference = abs(primary["duration"] - secondary["duration"])
            verification["duration_difference_seconds"] = difference
            if difference > 0.25:
                raise ValueError("Decoder durations differ. Inspect the source before transcription.")
        if primary["signal_end"] == 0:
            raise ValueError("The recording is silent.")
        state = {
            "source_path": str(source), "source_name": source.name,
            "source_sha256": source_hash, "source_mtime_ns": before.st_mtime_ns,
            "source_metadata": metadata, "audio_sha256": digest(audio_path),
            "duration": primary["duration"], "meeting_date": None,
            "attendees": [], "speaker_mapping_verified": False,
        }
        write_json(output / "audio-verification.json", verification)
    if args.attendee:
        state["attendees"] = args.attendee
    if args.meeting_date:
        date.fromisoformat(args.meeting_date)
        state["meeting_date"] = args.meeting_date
    state["source_path"] = str(source)
    state["source_mtime_ns"] = before.st_mtime_ns
    if source.stat().st_mtime_ns != before.st_mtime_ns or digest(source) != source_hash:
        raise ValueError("The original changed during processing.")
    write_json(state_path, state)
    audio, rate = sf.read(audio_path, dtype="float32")
    return state, audio, rate


def resolve_model(model):
    candidate = Path(model).expanduser()
    if candidate.is_dir():
        return str(candidate.resolve())
    from huggingface_hub import snapshot_download
    return snapshot_download(repo_id=model)


def recognize(audio, model):
    import mlx_whisper
    return mlx_whisper.transcribe(
        audio, path_or_hf_repo=model, language="ko", task="transcribe",
        word_timestamps=True, verbose=False, condition_on_previous_text=False,
        initial_prompt="한국어로 진행된 회의입니다.",
    )


def offset_segments(result, offset):
    segments = copy.deepcopy(result.get("segments", []))
    for segment in segments:
        segment["start"] += offset
        segment["end"] += offset
        for word in segment.get("words", []):
            word["start"] += offset
            word["end"] += offset
    return segments


def keep_core(segments, core_start, core_end, duration):
    kept = []
    for segment in segments:
        words = [word for word in segment.get("words", [])
                 if core_start <= (word["start"] + word["end"]) / 2 < core_end
                 and word["word"].strip()]
        if segment.get("words"):
            if not words:
                continue
            segment.update(words=words, start=words[0]["start"], end=words[-1]["end"],
                           text="".join(word["word"] for word in words))
        elif not core_start <= (segment["start"] + segment["end"]) / 2 < core_end:
            continue
        segment["start"] = max(0, min(duration, segment["start"]))
        segment["end"] = max(segment["start"], min(duration, segment["end"]))
        kept.append(segment)
    return kept


def review(raw, audio, rate, output):
    import numpy as np
    nonzero = np.flatnonzero(audio)
    signal_end = (int(nonzero[-1]) + 1) / rate
    kept, rejected, uncertain = [], [], []
    for original in raw["segments"]:
        segment = copy.deepcopy(original)
        start, end = segment["start"], segment["end"]
        block = audio[int(start * rate):max(int(end * rate), int(start * rate) + 1)]
        if start >= signal_end or not np.count_nonzero(block):
            rejected.append(segment)
            continue
        segment["end"] = min(end, signal_end)
        reasons = []
        if segment.get("avg_logprob", 0) < -1.1:
            reasons.append("음성 인식 확도가 낮음")
        if re.search(r"(\S{1,8}\s+)(?:\1){7,}", segment["text"]):
            reasons.append("자동전사 반복")
        if reasons:
            uncertain.append({"start": start, "end": end, "text": segment["text"],
                              "reasons": reasons})
            segment["text"] = "[인식 불확실] " + segment["text"].strip()
        segment["id"] = len(kept)
        kept.append(segment)
    if raw["duration"] - signal_end >= 1:
        kept.append({"id": len(kept), "start": signal_end, "end": raw["duration"],
                     "text": "[무음 구간]", "annotation": True})
    reviewed = dict(raw, segments=kept, signal_end_seconds=signal_end,
                    speaker_mapping_verified=False)
    write_json(output / "transcription-reviewed.json", reviewed)
    write_json(output / "recognition-uncertainties.json", uncertain)
    write_json(output / "rejected-silence-asr.json", rejected)
    (output / "transcript.txt").write_text(
        "한국어 자동전사입니다. 주요 구간은 원음과 대조해야 합니다.\n\n" +
        "\n".join(f'[{stamp(s["start"])}–{stamp(s["end"])}] {s["text"].strip()}'
                  for s in kept), encoding="utf-8")


def transcribe(state, audio, rate, output, args):
    duration = len(audio) / rate
    segments = []
    for index, core_start in enumerate(range(0, int(duration) + 1, args.chunk_seconds)):
        if core_start >= duration:
            break
        core_end = min(core_start + args.chunk_seconds, duration)
        start, end = max(0, core_start - 8), min(duration, core_end + 8)
        identity = {"source_sha256": state["source_sha256"], "model": args.model,
                    "core_start": core_start, "core_end": core_end,
                    "offset": start, "pipeline_version": 1}
        checkpoint = output / f"asr-chunk-{index + 1:03d}.json"
        if checkpoint.exists():
            cached = json.loads(checkpoint.read_text())
            if cached["identity"] != identity:
                raise ValueError("Checkpoint settings differ. Use a new output folder.")
            result = cached["result"]
        else:
            result = recognize(audio[int(start * rate):int(end * rate)], args.model)
            write_json(checkpoint, {"identity": identity, "result": result})
        segments.extend(keep_core(offset_segments(result, start),
                                  core_start, core_end, duration))
        for number, segment in enumerate(segments):
            segment["id"] = number
        raw = {"language": "ko", "model": args.model, "duration": duration,
               "completed_until": core_end, "segments": segments}
        write_json(output / "transcription-raw.json", raw)
        print(f"{stamp(core_end)}/{stamp(duration)} transcribed", flush=True)
    review(raw, audio, rate, output)


def recheck(state, audio, rate, output, args):
    duration = len(audio) / rate
    for value in args.recheck:
        start, end = map(float, value.split(":"))
        if not 0 <= start < end <= duration:
            raise ValueError(f"Recheck range is outside the audio: {value}")
        result = recognize(audio[int(start * rate):int(end * rate)], args.model)
        result["segments"] = offset_segments(result, start)
        model_tag = hashlib.sha256(args.model.encode()).hexdigest()[:8]
        path = output / f"recheck-{start:g}-{end:g}-{model_tag}.json"
        write_json(path, {"source_sha256": state["source_sha256"],
                          "start": start, "end": end, "model": args.model,
                          "accept_for_transcript": False, "result": result})
        print(result.get("text", ""), flush=True)


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attendee", action="append", default=[])
    parser.add_argument("--meeting-date")
    parser.add_argument("--chunk-seconds", type=int, default=360)
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-turbo")
    parser.add_argument("--recheck", nargs="+")
    args = parser.parse_args()
    if args.chunk_seconds < 30:
        parser.error("--chunk-seconds must be at least 30")
    source = args.source.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    if source.parent == output:
        parser.error("Use a dedicated output folder, separate from the original.")
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".transcribe.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state, audio, rate = prepare(source, output, args)
        args.model = resolve_model(args.model)
        if args.recheck:
            recheck(state, audio, rate, output, args)
        else:
            transcribe(state, audio, rate, output, args)
        if digest(source) != state["source_sha256"] or source.stat().st_mtime_ns != state["source_mtime_ns"]:
            raise ValueError("The original changed during transcription.")
    print(str(output / "transcript.txt"), flush=True)


if __name__ == "__main__":
    main()
