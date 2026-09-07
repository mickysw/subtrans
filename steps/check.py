"""⑤ 검사 — 사람이 보기 전에 기계가 먼저 거른다.

LLM이 줄을 먹거나 합치는 사고, 너무 빨라 못 읽는 자막, 화면 밖으로 나가는
긴 줄을 여기서 잡는다. 반려 항목이 하나라도 있으면 종료 코드 1.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
from srtlib import read_srt, fmt_ts  # noqa: E402

HANGUL = re.compile(r"[가-힣]")
LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
READ_SPEED_WARN = 7.0    # 초당 한글 글자 수. 이보다 빠르면 못 읽는다
SHORT_TEXT = 8           # 이보다 짧으면 한눈에 들어와서 속도 경고 대상이 아니다


def main(workdir: Path, name: str = "ko.srt") -> int:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["segment"]
    ko_path = workdir / name
    if not ko_path.exists():
        raise SystemExit(f"파일이 없습니다: {ko_path}")

    ko = read_srt(ko_path)
    en = read_srt(workdir / "en.srt") if (workdir / "en.srt").exists() else []

    blocking: list[str] = []   # 반려
    warning: list[str] = []    # 경고 (진행은 가능)

    if en and len(en) != len(ko):
        blocking.append(
            f"자막 장수가 다릅니다: 영어 {len(en)}장 vs 한국어 {len(ko)}장. "
            "LLM이 줄을 합치거나 먹은 것이라 타이밍이 전부 밀립니다.")

    for n, c in enumerate(ko, 1):
        where = f"{n}번({fmt_ts(c['start'])})"
        text = c["text"].strip()
        lines = [l for l in text.split("\n")]
        dur = c["end"] - c["start"]
        chars = len(re.sub(r"\s", "", text))

        if not text:
            blocking.append(f"{where} 자막이 비어 있습니다.")
            continue
        if len(lines) > cfg["max_lines"]:
            blocking.append(f"{where} {len(lines)}줄입니다(최대 {cfg['max_lines']}줄).")
        if dur <= 0:
            blocking.append(f"{where} 길이가 0 이하입니다.")
        if not HANGUL.search(text) and LATIN_WORD.search(text):
            blocking.append(f"{where} 번역되지 않은 영어로 보입니다: {text[:40]}")

        over = [l for l in lines if len(l) > cfg["max_chars_per_line"] + 4]
        if over:
            warning.append(f"{where} 한 줄이 {max(len(l) for l in over)}자입니다"
                           f"(권장 {cfg['max_chars_per_line']}자): {over[0][:30]}")
        # 글자가 몇 개 안 되면 순간에 읽힌다. 초당 속도로 재면 오탐이 난다.
        if chars > SHORT_TEXT and dur > 0 and chars / dur > READ_SPEED_WARN:
            warning.append(f"{where} 초당 {chars/dur:.1f}자로 빠릅니다"
                           f"({dur:.1f}초에 {chars}자). 줄이는 게 좋습니다.")
        if 0 < dur < 0.5:
            warning.append(f"{where} {dur:.2f}초로 너무 짧아 눈에 안 들어옵니다.")

    for a, b in zip(ko, ko[1:]):
        if b["start"] < a["end"] - 0.001:
            blocking.append(
                f"{fmt_ts(a['start'])} 자막이 다음 자막과 {a['end']-b['start']:.2f}초 겹칩니다.")

    print(f"[check] {ko_path.name} — 자막 {len(ko)}장 검사")
    for msg in blocking:
        print(f"  ✗ {msg}")
    for msg in warning[:25]:
        print(f"  · {msg}")
    if len(warning) > 25:
        print(f"  · ... 경고 {len(warning)-25}건 더")

    print()
    if blocking:
        print(f"[check] 반려 {len(blocking)}건 / 경고 {len(warning)}건 — 고쳐야 합니다.")
        return 1
    if warning:
        print(f"[check] 통과 (경고 {len(warning)}건 — 검수 화면에서 다듬으면 됩니다.)")
    else:
        print("[check] 통과 — 문제 없습니다.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python steps/check.py <work폴더명> [파일명]")
        raise SystemExit(1)
    fname = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else "ko.srt"
    raise SystemExit(main(ROOT / "work" / sys.argv[1], fname))
