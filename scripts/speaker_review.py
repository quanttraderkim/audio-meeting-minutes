#!/usr/bin/env python3
"""Prepare local speaker review cards and retain explicitly confirmed clips."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import html
import json
from pathlib import Path

import numpy as np
import sherpa_onnx as so
import soundfile as sf

from transcribe import digest, stamp, write_json

MODEL_DIR = Path.home() / ".cache/codex-meeting-audio-models"
PROFILE_DIR = Path.home() / ".local/share/audio-meeting-minutes/speaker-samples"
RATE = 16000
MAX_CLIP = 14
MIN_CLIP = 6


@contextmanager
def locked(path):
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def embedding_config(model):
    return so.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=4)


def model_path(models, filename):
    path = models / filename
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path}. See references/speaker-review.md for setup.")
    return path


def load_audio(folder):
    audio, rate = sf.read(folder / "audio-16k.wav", dtype="float32")
    if rate != RATE or audio.ndim != 1:
        raise ValueError("Expected 16 kHz mono audio-16k.wav")
    return audio


def diarize(folder, audio, models, audio_sha, model_sha):
    path = folder / "speaker-diarization.json"
    seg = model_path(models, "segmentation.onnx")
    identity = dict(audio_sha256=audio_sha, embedding_model_sha256=model_sha,
                    segmentation_model_sha256=digest(seg))
    cached = load_json(path, {})
    if all(cached.get(key) == val for key, val in identity.items()):
        return cached["turns"]
    config = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(seg), window_shift_ratio=0.5), num_threads=4),
        embedding=embedding_config(models / "wespeaker.onnx"),
        clustering=so.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5,
    )
    if not config.validate():
        raise ValueError("Check segmentation.onnx and wespeaker.onnx")
    engine = so.OfflineSpeakerDiarization(config)
    length = len(audio) / RATE
    turns = [{"start": float(turn.start), "end": min(length, float(turn.end)),
              "speaker": int(turn.speaker)}
             for turn in engine.process(audio).sort_by_start_time()
             if 0 <= turn.start < min(turn.end, length)]
    write_json(path, dict(identity, turns=turns))
    return turns


def clean_candidates(turns, start, end, minimum=MIN_CLIP):
    candidates = []
    for turn in turns:
        first = max(turn["start"], start)
        last = min(turn["end"], end)
        if last - first < minimum:
            continue
        others = sorted((max(first, other["start"]), min(last, other["end"]))
                        for other in turns if other["speaker"] != turn["speaker"]
                        and other["start"] < last and other["end"] > first)
        cursor = first
        for left, right in others + [(last, last)]:
            clip_start, clip_end = cursor + 0.2, left - 0.2
            if clip_end - clip_start >= minimum:
                candidates.append((clip_start, min(clip_end, clip_start + MAX_CLIP),
                                   turn["speaker"]))
            cursor = max(cursor, right)
    return candidates


def excerpt(segments, start, end):
    words = [word["word"] for segment in segments
             if segment["start"] < end and segment["end"] > start
             for word in segment.get("words", [])
             if start <= (word["start"] + word["end"]) / 2 < end]
    if words:
        return "".join(words).strip()
    return " ".join(segment["text"].strip() for segment in segments
                    if start <= segment["start"] and segment["end"] <= end)


def vector(extractor, audio, card):
    stream = extractor.create_stream()
    stream.accept_waveform(RATE, audio[round(card["start"] * RATE):round(card["end"] * RATE)])
    stream.input_finished()
    if not extractor.is_ready(stream):
        raise ValueError("This clip has too little usable speech")
    values = np.asarray(extractor.compute(stream), dtype=np.float32)
    norm = np.linalg.norm(values)
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("The clip produced an invalid speaker embedding")
    return values / norm


def suggest(values, samples, roster):
    scores = {}
    for sample in samples:
        name = sample["name"]
        reference = np.asarray(sample["vector"], dtype=np.float32)
        if sample.get("person", name) in roster and reference.shape == values.shape:
            scores[name] = max(scores.get(name, -1), float(values @ reference))
    return [{"name": name, "similarity": round(score, 4)}
            for name, score in sorted(scores.items(), key=lambda item: -item[1])[:2]]


def render(folder, cards, profiles):
    rows = []
    for key, card in cards.items():
        review = card.get("review", {})
        options = card.get("suggestions", [])
        status = review.get("name") or review.get("status") or "확인 대기"
        sample = load_json(profiles / f"{key}.json", {})
        if sample.get("test_only"):
            status += " · 짧은 구간의 음성 특징을 로컬 테스트용으로 보관"
        elif sample.get("reuse_confirmed"):
            status += " · 다음 회의에 쓸 음성 특징 등록됨"
        suggested = " · ".join(html.escape(o["name"]) for o in options)
        suggestion = f"<p>비교 후보 {suggested}</p>" if suggested else ""
        rows.append(f'<article id="{key}"><h2>{stamp(round(card["start"]))}–{stamp(round(card["end"]))}'
                    f' · 화자 {card["cluster"]}</h2><p>{html.escape(card["excerpt"])}</p>'
                    f'<button type="button" onclick="playClip({card["start"]},{card["end"]})">'
                    f'이 구간 듣기</button><p>{html.escape(status)}</p>{suggestion}</article>')
    css = (Path(__file__).parent / "preview.css").read_text()
    page = ('<!doctype html><html lang="ko"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>화자 확인</title><style>{css}</style><main><h1>화자 확인</h1>'
            '<p>구간을 들은 뒤 채팅의 확인 카드에서 답을 선택합니다.</p>'
            '<nav><a href="index.html">회의록</a></nav>'
            '<audio id="audio" controls preload="metadata" src="audio-16k.wav"></audio>'
            + "".join(rows) + """</main><script>
const audio = document.querySelector('#audio');
let stopAt;
function playClip(start, end) {
  stopAt = end;
  const startPlaying = () => { audio.currentTime = start; audio.play().catch(() => {}); };
  if (audio.readyState >= 1) startPlaying();
  else audio.addEventListener('loadedmetadata', startPlaying, {once: true});
}
audio.addEventListener('timeupdate', () => {
  if (stopAt !== undefined && audio.currentTime >= stopAt) {
    audio.pause(); stopAt = undefined;
  }
});
</script></html>""")
    (folder / "speaker-review.html").write_text(page, encoding="utf-8")


def prepare(folder, state, args, audio_sha, source):
    audio = load_audio(folder)
    model = model_path(args.models, "wespeaker.onnx")
    model_sha = digest(model)
    turns = diarize(folder, audio, args.models, audio_sha, model_sha)
    asr = load_json(folder / "transcription-reviewed.json", {})["segments"]
    roster = {item["name"] if isinstance(item, dict) else item for item in (source.get("attendees") or [])}
    samples = [load_json(p, {}) for p in args.profiles.glob("*.json")]
    samples = [sample for sample in samples if sample.get("reuse_confirmed")
               and not sample.get("test_only")
               and sample.get("model_sha256") == model_sha
               and sample.get("source_sha256") != source["source_sha256"]
               and sample.get("person", sample.get("name")) in roster]
    extractor = so.SpeakerEmbeddingExtractor(embedding_config(model)) if samples else None
    for interval in args.ranges:
        start, end = map(float, interval.split(":"))
        if not 0 <= start < end <= len(audio) / RATE:
            raise ValueError(f"Range outside the recording: {interval}")
        choices = clean_candidates(turns, start, end, args.min_seconds)
        if not choices:
            print(f"{interval}에는 겹침 없이 {args.min_seconds:g}초 이상 이어진 후보가 없습니다.")
            continue
        start, end, cluster = max(choices, key=lambda x: x[1] - x[0])
        key = hashlib.sha256(f"{audio_sha}:{start:.3f}:{end:.3f}:{cluster}".encode()).hexdigest()[:16]
        card = state["cards"].get(key, {"start": round(start, 3), "end": round(end, 3),
                                        "cluster": cluster, "model_sha256": model_sha,
                                        "excerpt": excerpt(asr, start, end)})
        card["suggestions"] = (
            suggest(vector(extractor, audio, card), samples, roster) if extractor else []
        )
        state["cards"][key] = card
        print(json.dumps(dict(card, card=key), ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--models", type=Path, default=MODEL_DIR)
    parser.add_argument("--profiles", type=Path, default=PROFILE_DIR)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("ranges", nargs="+", metavar="START:END")
    prep.add_argument("--min-seconds", type=float, default=MIN_CLIP)
    confirm = sub.add_parser("confirm")
    confirm.add_argument("card")
    answer = confirm.add_mutually_exclusive_group(required=True)
    answer.add_argument("--name")
    answer.add_argument("--status", choices=["모르겠음", "겹침"])
    enroll = sub.add_parser("enroll")
    enroll.add_argument("card")
    storage = enroll.add_mutually_exclusive_group(required=True)
    storage.add_argument("--allow-reuse", action="store_true")
    storage.add_argument("--test-only", action="store_true", help="Store a local test reference only")
    forget = sub.add_parser("forget")
    forget.add_argument("card")
    args = parser.parse_args()
    if args.action == "prepare" and not 1.5 <= args.min_seconds <= MAX_CLIP:
        parser.error(f"--min-seconds must be between 1.5 and {MAX_CLIP}")
    folder = args.output.expanduser().resolve()
    args.models = args.models.expanduser().resolve()
    args.profiles = args.profiles.expanduser().resolve()
    if args.profiles.is_relative_to(folder) or args.profiles.is_relative_to(Path(__file__).parents[1]):
        parser.error("The profile directory must be outside the meeting output and skill")
    audio_sha = digest(folder / "audio-16k.wav")
    source = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    if source.get("audio_sha256", audio_sha) != audio_sha:
        raise ValueError("The prepared audio does not match source.json")
    state_path = folder / "speaker-review.json"
    with locked(folder / ".speaker-review.lock"):
        state = load_json(state_path, {"audio_sha256": audio_sha, "cards": {}})
        if state["audio_sha256"] != audio_sha:
            raise ValueError("The prepared audio changed")
        if args.action == "prepare":
            prepare(folder, state, args, audio_sha, source)
        else:
            card = state["cards"][args.card]
            if args.action == "confirm":
                name = args.name.strip() if args.name else ""
                if args.name and not name:
                    raise ValueError("The confirmed name is empty")
                updated = {"status": "확인" if name else args.status, "name": name}
                if "review" in card and card["review"] != updated:
                    (args.profiles / f"{args.card}.json").unlink(missing_ok=True)
                card["review"] = updated
            elif args.action == "enroll":
                name = card.get("review", {}).get("name")
                if not name:
                    raise ValueError("Confirm a single speaker first")
                if args.allow_reuse and card["end"] - card["start"] < MIN_CLIP:
                    raise ValueError(f"Reuse requires a clip of at least {MIN_CLIP} seconds")
                model = model_path(args.models, "wespeaker.onnx")
                model_sha = digest(model)
                if model_sha != card["model_sha256"]:
                    raise ValueError("The embedding model changed")
                extractor = so.SpeakerEmbeddingExtractor(embedding_config(model))
                features = vector(extractor, load_audio(folder), card)
                args.profiles.mkdir(parents=True, exist_ok=True, mode=0o700)
                person = name
                for attendee in source.get("attendees") or []:
                    if isinstance(attendee, dict):
                        display = f'{attendee["name"]} {attendee.get("role", "")}'.strip()
                        if display == name:
                            person = attendee["name"]
                            break
                write_json(args.profiles / f"{args.card}.json",
                           {"name": name, "person": person, "reuse_confirmed": args.allow_reuse,
                            "test_only": args.test_only, "source_sha256": source["source_sha256"],
                            "start": card["start"], "end": card["end"],
                            "model_sha256": model_sha, "vector": features.tolist(),
                            "registered_at": datetime.now(timezone.utc).isoformat()})
                print(f"등록 완료 {args.card}")
            else:
                (args.profiles / f"{args.card}.json").unlink(missing_ok=True)
        write_json(state_path, state)
        render(folder, state["cards"], args.profiles)


if __name__ == "__main__":
    main()
