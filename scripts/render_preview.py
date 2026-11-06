#!/usr/bin/env python3
"""Render reviewed audio transcripts and authored Markdown as a local preview."""
import argparse
import html
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import markdown

from transcribe import digest, stamp, write_json


def validate_links(markdown_text, duration):
    import re
    for target in re.findall(r"\]\(([^)]+)\)", markdown_text):
        url = urlparse(target)
        if url.scheme not in ("", "http", "https", "mailto"):
            raise ValueError(f"Unsupported link scheme: {target}")
        query = parse_qs(url.query)
        if url.path == "transcript.html" and "t" in query:
            if not 0 <= float(query["t"][0]) <= duration:
                raise ValueError(f"Time link is outside the recording: {target}")


def group_segments(segments):
    turns = []
    for segment in segments:
        if turns and segment["start"] - turns[-1]["end"] < 2 and segment["end"] - turns[-1]["start"] <= 35:
            turns[-1]["text"] += " " + segment["text"].strip()
            turns[-1]["end"] = max(turns[-1]["end"], segment["end"])
        else:
            turns.append({"start": segment["start"], "end": segment["end"],
                          "text": segment["text"].strip()})
    return turns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--title", default="회의록")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    source = json.loads((output / "source.json").read_text())
    reviewed = json.loads((output / "transcription-reviewed.json").read_text())
    if reviewed["completed_until"] != reviewed["duration"]:
        raise ValueError("Transcription is incomplete.")
    if digest(Path(source["source_path"])) != source["source_sha256"]:
        raise ValueError("The original recording changed.")
    duration = reviewed["duration"]
    for segment in reviewed["segments"]:
        if not 0 <= segment["start"] <= segment["end"] <= duration:
            raise ValueError("Transcript timestamps are outside the recording.")
    bodies = {}
    for filename in ["shared.md", "details.md"]:
        text = (output / filename).read_text()
        if text.startswith("# "):
            text = text.split("\n", 1)[1] if "\n" in text else ""
        validate_links(text, duration)
        bodies[filename] = markdown.markdown(html.escape(text),
                                            extensions=["tables", "fenced_code", "sane_lists"])
    css = (Path(__file__).parent / "preview.css").read_text()
    title = html.escape(args.title)
    attendees = html.escape(" · ".join(source.get("attendees", [])) or "미확인")
    meeting_date = html.escape(source.get("meeting_date") or "미확인")
    recorded = html.escape(source.get("source_metadata", {}).get("creation_time", "미확인"))
    note = "자동전사 자료입니다. 인식이 불분명한 표현과 개별 발언자의 실명은 확인이 필요합니다."
    turns = group_segments(reviewed["segments"])
    write_json(output / "transcript-with-times.json", turns)
    transcript_text = args.title + "\n" + note + "\n\n" + "\n\n".join(
        f'[{stamp(t["start"])}–{stamp(t["end"])}] {t["text"]}' for t in turns)
    (output / "transcript.txt").write_text(transcript_text, encoding="utf-8")
    articles = "".join(
        f'<article data-start="{t["start"]}"><button type="button" onclick="seek({t["start"]})">'
        f'{stamp(t["start"])}</button><span class="muted">–{stamp(t["end"])}</span>'
        f'<p>{html.escape(t["text"])}</p></article>' for t in turns)
    head = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{title}</title><style>{css}</style></head><body><main>')
    transcript = head + f'<h1>{title} 전사와 원음</h1><p>{note}</p>' + (
        '<div class="audio-header"><nav><a href="index.html">회의록</a>'
        '<a href="transcript.txt">전사 텍스트</a></nav>'
        '<audio id="recording" controls preload="metadata" src="audio-16k.wav"></audio>'
        '<label for="search">전사 검색</label> <input id="search" type="search"></div>') + articles
    transcript += """
<script>
const player=document.getElementById('recording');
const rows=[...document.querySelectorAll('article')];
function seek(t){player.currentTime=t;player.play().catch(()=>{});}
document.getElementById('search').addEventListener('input',event=>{
  const value=event.target.value.trim().toLowerCase();
  rows.forEach(row=>{row.hidden=!!value&&!row.textContent.toLowerCase().includes(value);});
});
const query=new URLSearchParams(location.search);
if(query.has('t')){
  const time=Number(query.get('t'));
  if(Number.isFinite(time)&&time>=0){
    const jump=()=>{if(time<=player.duration)player.currentTime=time;};
    if(player.readyState>=1)jump();
    else player.addEventListener('loadedmetadata',jump,{once:true});
    const candidates=rows.filter(row=>Number(row.dataset.start)<=time);
    const near=candidates[candidates.length-1];
    if(near){near.classList.add('selected');near.scrollIntoView();}
  }
}
</script></main></body></html>"""
    (output / "transcript.html").write_text(transcript, encoding="utf-8")
    review_link = '<a href="speaker-review.html">화자 확인</a>' if (output / "speaker-review.html").is_file() else ""
    page = head + (
        f'<header><h1>{title}</h1><p class="meta">회의일 {meeting_date} · 참석자 {attendees}</p>'
        f'<p class="meta">녹음에 기록된 생성시각 {recorded} · {stamp(duration)}</p>'
        '<nav><a href="shared.md">요약 Markdown</a><a href="details.md">상세 Markdown</a>'
        '<a href="transcript.html">시간별 전사와 원음</a>'
        f'{review_link}</nav></header>'
        f'<section>{bodies["shared.md"]}</section><details open><summary>상세 회의록</summary>'
        f'<div class="detail-content">{bodies["details.md"]}</div></details>'
        f'<footer>{note}</footer></main></body></html>')
    (output / "index.html").write_text(page, encoding="utf-8")
    verification = {
        "source_sha256_unchanged": True, "duration": duration,
        "completed_until": reviewed["completed_until"],
        "segments": len(reviewed["segments"]), "transcript_blocks": len(turns),
        "time_links_in_range": True, "speaker_mapping_verified": False,
    }
    write_json(output / "minutes-verification.json", verification)
    print(str(output / "index.html"))


if __name__ == "__main__":
    main()
