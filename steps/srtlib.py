"""SRT 자막 파일 읽기/쓰기 — ③~⑦이 공용으로 쓴다."""
from __future__ import annotations

import re
from pathlib import Path


def fmt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_ts(text: str) -> float:
    h, m, rest = text.strip().split(":")
    s, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def write_srt(cues: list[dict], path: Path) -> Path:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(str(i))
        out.append(f"{fmt_ts(c['start'])} --> {fmt_ts(c['end'])}")
        out.append(c["text"].strip())
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")
    return path


_BLOCK = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n(.*?)(?=\n\s*\n|\Z)",
    re.S,
)


def read_srt(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig")
    cues = []
    for m in _BLOCK.finditer(raw):
        cues.append({
            "index": int(m.group(1)),
            "start": parse_ts(m.group(2)),
            "end": parse_ts(m.group(3)),
            "text": m.group(4).strip(),
        })
    return cues
