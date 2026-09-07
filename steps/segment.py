"""③ 조각내기 — 단어 타임스탬프를 자막 한 장 단위로 쪼갠다.

싱크의 승부처. 자막 한 장의 시각을
  시작 = 그 장 첫 단어의 시작 시각
  끝   = 그 장 마지막 단어의 끝 시각
으로 못 박는다. 그래서 어긋날 수가 없다.

끊는 자리는 규칙으로 정한다: 문장부호 > 긴 무음 > 접속사 앞 > 길이 강제.
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
from srtlib import write_srt  # noqa: E402

# 이 단어들 앞은 의미가 새로 시작하는 자리라 끊기 좋다
CONJ = {
    "and", "but", "so", "because", "when", "while", "if", "though", "although",
    "that", "which", "who", "then", "or", "just", "like", "cause", "coz",
}
STRONG_END = re.compile(r"[.!?]$")
MAX_CHARS_PER_SEC = 22.0   # 영어 기준. 이보다 빽빽하면 합치지 않는다
MIN_ON_SCREEN = 0.75       # 이보다 짧으면 깜빡이기만 하고 안 읽힌다
SOFT_END = re.compile(r"[,;:]$")


def boundary_score(prev: dict, nxt: dict) -> float:
    """prev 다음에서 끊을 때의 점수. 높을수록 끊기 좋은 자리."""
    gap = max(0.0, nxt["s"] - prev["e"])
    score = gap * 40.0
    w = prev["w"]
    if STRONG_END.search(w):
        score += 100.0
    elif SOFT_END.search(w):
        score += 40.0
    if nxt["w"].strip(",.!?'\"").lower() in CONJ:
        score += 25.0
    return score


def split_run(words: list[dict], cfg: dict) -> list[list[dict]]:
    """길이 제약을 지키면서 가장 좋은 자리에서 재귀적으로 쪼갠다."""
    max_dur = cfg["max_duration"]
    max_chars = cfg["max_chars_per_line"] * cfg["max_lines"] * 2.6  # 영어는 한글보다 글자가 많다

    dur = words[-1]["e"] - words[0]["s"]
    chars = sum(len(w["w"]) + 1 for w in words)
    if len(words) < 4 or (dur <= max_dur and chars <= max_chars):
        return [words]

    # 가운데 60% 구간에서 가장 점수 높은 자리를 고른다 (양 끝에 붙으면 조각이 너무 짧아짐)
    lo, hi = max(1, int(len(words) * 0.2)), min(len(words) - 1, int(len(words) * 0.8) + 1)
    if lo >= hi:
        lo, hi = 1, len(words)
    best_i = max(range(lo, hi), key=lambda i: boundary_score(words[i - 1], words[i]))
    return split_run(words[:best_i], cfg) + split_run(words[best_i:], cfg)


def build_cues(words: list[dict], cfg: dict) -> list[dict]:
    gap_th = cfg["gap_threshold"]
    min_dur = cfg["min_duration"]
    max_dur = cfg["max_duration"]

    # 1차: 긴 무음에서 자른다
    runs: list[list[dict]] = []
    cur = [words[0]]
    for prev, w in zip(words, words[1:]):
        if w["s"] - prev["e"] >= gap_th or STRONG_END.search(prev["w"]):
            runs.append(cur)
            cur = [w]
        else:
            cur.append(w)
    runs.append(cur)

    # 2차: 너무 긴 덩어리를 의미 자리에서 쪼갠다
    pieces: list[list[dict]] = []
    for r in runs:
        pieces.extend(split_run(r, cfg))

    # 3차: 너무 짧은 조각을 이웃과 합친다.
    # 단, 문장이 끝난 자리는 절대 넘지 않는다 — 화자가 바뀌는 자리이기도 해서
    # 붙이면 서로 다른 사람 대사가 한 장에 들어간다.
    merged: list[list[dict]] = []
    for p in pieces:
        if merged:
            prev = merged[-1]
            joint_dur = p[-1]["e"] - prev[0]["s"]
            p_dur = p[-1]["e"] - p[0]["s"]
            gap = p[0]["s"] - prev[-1]["e"]
            joint_chars = sum(len(w["w"]) + 1 for w in prev) + sum(len(w["w"]) + 1 for w in p)
            sentence_ended = bool(STRONG_END.search(prev[-1]["w"]))
            if (p_dur < min_dur and joint_dur <= max_dur and gap < gap_th * 2
                    and not sentence_ended
                    and joint_chars / joint_dur <= MAX_CHARS_PER_SEC):
                prev.extend(p)
                continue
        merged.append(p)

    cues = []
    for i, p in enumerate(merged):
        start = p[0]["s"]
        end = p[-1]["e"]
        # 다음 자막을 침범하지 않는 선에서 아주 살짝 늘려 읽을 시간을 준다
        # 뒤에 여유가 있으면 조금 늘려 읽을 시간을 준다.
        # 다음 자막의 시작은 어떤 경우에도 침범하지 않는다(겹치면 자막이 깨진다).
        # 다음 자막 시작이 절대 상한이다. 늘리는 계산을 먼저 하고 마지막에 잘라낸다
        # (순서가 바뀌면 상한을 덮어써서 자막이 겹친다).
        limit = merged[i + 1][0]["s"] - 0.02 if i + 1 < len(merged) else end + 1.0
        desired = max(end + 0.5, start + MIN_ON_SCREEN, p[-1]["e"])
        end = min(desired, limit)
        if end <= start:                    # 말이 겹쳐 들어온 드문 경우
            end = max(limit, start + 0.05)
        cues.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": " ".join(w["w"] for w in p).strip(),
            "words": len(p),
        })
    return cues


def main(workdir: Path) -> list[dict]:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["segment"]
    data = json.loads((workdir / "words.json").read_text(encoding="utf-8"))
    words = data["words"]
    if not words:
        raise SystemExit("받아쓴 단어가 없습니다. ②를 먼저 돌리세요.")

    cues = build_cues(words, cfg)
    write_srt(cues, workdir / "en.srt")
    (workdir / "cues.json").write_text(
        json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")

    durs = [c["end"] - c["start"] for c in cues]
    total = data.get("audio_seconds") or (cues[-1]["end"] if cues else 0)
    print(f"[segment] 자막 {len(cues)}장  "
          f"(평균 {sum(durs)/len(durs):.1f}초, 최장 {max(durs):.1f}초, 최단 {min(durs):.1f}초)")
    print(f"[segment] 1분당 약 {len(cues)/(total/60):.0f}장")
    over = [c for c in cues if c["end"] - c["start"] > cfg["max_duration"] + 0.3]
    if over:
        print(f"[segment] 참고: {cfg['max_duration']}초를 넘는 장이 {len(over)}개 있습니다.")
    print(f"[segment] 완료 → {workdir / 'en.srt'}")
    return cues


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python steps/segment.py <work폴더명>")
        raise SystemExit(1)
    main(ROOT / "work" / sys.argv[1])
