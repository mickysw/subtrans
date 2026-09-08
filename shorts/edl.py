"""② 편집 결정 목록(EDL) — "원본의 어느 조각을 어떤 순서로 이어 붙일 것인가".

세 가지를 한다.
  a. 후킹 문장을 맨 앞으로 (LLM 1회, 후보당)
  b. 말 사이 빈 공간 제거 (코드만)
  c. 군말 제거 (코드만)

원칙
  - **단어 안쪽은 절대 자르지 않는다.** 자르는 자리는 항상 단어와 단어 사이다.
  - 잘라낸 자리에 약간의 숨은 남긴다. 완전히 붙이면 말이 쫓기듯 들린다.
  - 남기는 숨은 원본에 실제로 있는 무음에서 가져온다(만들어 넣지 않는다).
    그래서 여유는 "그 자리에 있는 무음의 절반"을 넘지 않는다 — 넘으면 옆 단어를 삼킨다.
  - LLM에게는 "어느 문장을 앞으로 뺄까" 하나만 묻는다. 순서 전체를 맡기지 않는다.

cue 번호는 1부터 센다(ko.srt 와 같게). 배열 색인은 -1.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
from translate import Translator, parse_json_array  # noqa: E402

# ── 편집 상수 (초) ───────────────────────────────────────────────
EDGE_PAD = 0.12       # 클립 맨 앞뒤 여유
SILENCE_GAP = 0.35    # 단어 사이가 이보다 벌어지면 잘라낸다
KEEP_WORD = 0.15      # 문장 안에서 잘라낸 자리에 남길 숨
CUE_GAP = 0.60        # 문장 사이가 이보다 벌어지면 잘라낸다
KEEP_CUE = 0.25       # 문장 사이에 남길 숨
KEEP_SEAM = 0.30      # 후킹 뒤 이음새에 남길 숨 (조금 더 준다)

FILLERS = {"um", "uh", "uhm", "hmm", "erm", "mm", "mhm", "uhh", "umm"}

TARGET_MIN, TARGET_MAX = 25.0, 60.0
MIN_SPAN = 0.4        # 이보다 짧은 조각은 만들지 않는다
MAX_REMOVED_PCT = 40.0  # 이보다 많이 잘려나가면 뭔가 잘못된 것


def norm(word: str) -> str:
    return word.strip().strip(".,!?\"'").lower()


def index_words(cues: list[dict], words: list[dict]) -> list[list[int]]:
    """각 cue에 속한 단어의 전역 색인 목록. cue는 단어에서 만들어졌으므로 시간순이 보장된다."""
    buckets: list[list[int]] = [[] for _ in cues]
    k = 0
    for i, w in enumerate(words):
        while k + 1 < len(cues) and w["s"] >= cues[k + 1]["start"]:
            k += 1
        buckets[k].append(i)
    return buckets


def gaps_around(words: list[dict]) -> tuple[list[float], list[float]]:
    """각 단어 앞/뒤에 실제로 있는 무음의 길이. 여유를 얼마나 줄 수 있는지의 상한."""
    n = len(words)
    before = [9.9] * n
    after = [9.9] * n
    for i in range(1, n):
        g = max(0.0, words[i]["s"] - words[i - 1]["e"])
        before[i] = g
        after[i - 1] = g
    return before, after


# ── a. 후킹 재배치 ───────────────────────────────────────────────

def pick_hook(send, cues: list[dict], ko: list[dict], cand: dict) -> tuple[int | None, str]:
    """맨 앞으로 뺄 cue 번호. 마땅한 게 없으면 None (그러면 원래 순서를 쓴다).

    ⚠️ 판단은 **한국어 자막 기준**이다. 영어 원문으로만 보면 대화체 문장이 대부분
    and/but/so 로 시작해 전부 탈락한다(실제로 4개 중 4개가 거절됐다). 한국어에서는
    그 접속사가 사라지고 주어도 생략돼 훨씬 자립적이다 — 시청자가 읽는 것도 그쪽이다.
    """
    tpl = (ROOT / "prompts" / "shorts_hook.md").read_text(encoding="utf-8")
    lines = "\n".join(
        f"[{i}] KO: {ko[i - 1]['text'].replace(chr(10), ' ')}\n"
        f"      EN: {cues[i - 1]['text']}"
        for i in range(cand["start_cue"], cand["end_cue"] + 1))
    prompt = tpl.replace("{LINES}", lines)

    for _ in (1, 2):
        try:
            items = parse_json_array(send(prompt))
            if not items:
                continue
            h = items[0].get("hook")
            why = str(items[0].get("why", "")).strip()
            if h is None:
                return None, why or "마땅한 문장 없음"
            h = int(h)
            if cand["start_cue"] <= h <= cand["end_cue"]:
                return h, why
            return None, f"구간 밖 번호({h})를 골라서 무시"
        except Exception as e:
            last = str(e)[:80]
    return None, f"응답을 읽지 못함({last})"


# ── b·c. 무음·군말 제거하며 조각 만들기 ──────────────────────────

def build_spans(cue_seq: list[int], buckets: list[list[int]],
                words: list[dict], seam_at: int | None) -> dict:
    """cue 순서대로 원본 조각을 만든다.

    seam_at: 이 위치(cue_seq의 색인) 앞에 재배치 이음새가 있다 — 숨을 조금 더 준다.
    """
    before, after = gaps_around(words)

    spans: list[dict] = []
    words_out: list[dict] = []
    cue_spans: list[dict] = []
    out_t = 0.0

    for pos, cue_no in enumerate(cue_seq):
        idxs = [i for i in buckets[cue_no - 1] if norm(words[i]["w"]) not in FILLERS]
        if not idxs:
            continue

        # 문장 안에서 무음이 큰 자리를 끊어 여러 조각(run)으로 나눈다
        runs: list[list[int]] = [[idxs[0]]]
        for a, b in zip(idxs, idxs[1:]):
            if words[b]["s"] - words[a]["e"] > SILENCE_GAP:
                runs.append([b])
            else:
                runs[-1].append(b)

        cue_out_start = out_t
        for r_i, run in enumerate(runs):
            first, last = run[0], run[-1]

            if pos == 0 and r_i == 0:
                want_start = EDGE_PAD
            elif r_i == 0:                       # 문장 첫머리
                want_start = (KEEP_SEAM if pos == seam_at else KEEP_CUE) / 2
            else:                                # 문장 안에서 끊긴 자리
                want_start = KEEP_WORD / 2

            is_last_run_of_last_cue = (pos == len(cue_seq) - 1 and r_i == len(runs) - 1)
            if is_last_run_of_last_cue:
                want_end = EDGE_PAD
            elif r_i == len(runs) - 1:           # 문장 끝
                nxt = seam_at is not None and pos + 1 == seam_at
                want_end = (KEEP_SEAM if nxt else KEEP_CUE) / 2
            else:
                want_end = KEEP_WORD / 2

            # 여유는 그 자리에 실제로 있는 무음의 절반을 넘지 않는다 (옆 단어를 삼키지 않게)
            s = words[first]["s"] - min(want_start, before[first] * 0.5)
            e = words[last]["e"] + min(want_end, after[last] * 0.5)
            if e - s < MIN_SPAN:                 # 너무 짧으면 가운데를 기준으로 늘린다
                mid = (s + e) / 2
                s, e = mid - MIN_SPAN / 2, mid + MIN_SPAN / 2
            s = max(0.0, s)

            for i in run:
                words_out.append({
                    "w": words[i]["w"],
                    "out_s": round(out_t + (words[i]["s"] - s), 3),
                    "out_e": round(out_t + (words[i]["e"] - s), 3),
                    "cue": cue_no,
                })
            spans.append({"s": round(s, 3), "e": round(e, 3), "cue": cue_no})
            out_t += e - s

        cue_spans.append({"cue": cue_no,
                          "out_s": round(cue_out_start, 3),
                          "out_e": round(out_t, 3)})

    return {"spans": spans, "words_out": words_out,
            "cue_spans": cue_spans, "duration": round(out_t, 3)}


# ── d. 길이 맞추기 ───────────────────────────────────────────────

def fit_length(cue_seq, buckets, words, seam_at):
    """60초를 넘으면 끝 문장부터 뺀다. 후킹(맨 앞)은 절대 빼지 않는다."""
    seq = list(cue_seq)
    dropped: list[int] = []
    built = build_spans(seq, buckets, words, seam_at)
    while built["duration"] > TARGET_MAX and len(seq) > 2:
        dropped.append(seq.pop())
        built = build_spans(seq, buckets, words, seam_at)
    return seq, built, dropped


# ── 후보 하나 처리 ───────────────────────────────────────────────

def make_edl(send, cand: dict, cues: list[dict], ko: list[dict], words: list[dict],
             buckets: list[list[int]]) -> dict:
    s_cue, e_cue = cand["start_cue"], cand["end_cue"]
    original = list(range(s_cue, e_cue + 1))
    warnings: list[str] = []

    hook, why = pick_hook(send, cues, ko, cand)
    if hook is None:
        seq, seam_at = original, None
        warnings.append(f"재배치 안 함: {why}")
    else:
        seq = [hook] + [c for c in original if c != hook]
        seam_at = 1                      # seq[1] 앞이 이음새

    seq, built, dropped = fit_length(seq, buckets, words, seam_at)

    src_dur = cues[e_cue - 1]["end"] - cues[s_cue - 1]["start"]
    removed = src_dur - built["duration"]
    removed_pct = removed / src_dur * 100 if src_dur else 0.0

    if built["duration"] < TARGET_MIN:
        warnings.append(f"편집 후 {built['duration']:.0f}초로 너무 짧음")
    if removed_pct > MAX_REMOVED_PCT:
        warnings.append(f"원본의 {removed_pct:.0f}%가 잘려나감 — 규칙 확인 필요")
    total = sum(sp["e"] - sp["s"] for sp in built["spans"])
    if abs(total - built["duration"]) > 0.05:
        warnings.append(f"조각 합계({total:.2f})와 길이({built['duration']:.2f})가 어긋남")
    short = [sp for sp in built["spans"] if sp["e"] - sp["s"] < MIN_SPAN - 0.01]
    if short:
        warnings.append(f"{MIN_SPAN}초 미만 조각 {len(short)}개")

    return {
        "n": cand["n"], "title": cand["title"],
        "start_cue": s_cue, "end_cue": e_cue,
        "hook_cue": hook, "hook_why": why, "reordered": hook is not None,
        "cues_out": seq, "dropped_cues": dropped,
        "duration": built["duration"],
        "source_duration": round(src_dur, 2),
        "removed_s": round(removed, 2), "removed_pct": round(removed_pct, 1),
        "spans": built["spans"], "cue_spans": built["cue_spans"],
        "words_out": built["words_out"],
        "warnings": warnings,
        "usable": built["duration"] >= TARGET_MIN and not short,
    }


def main(workdir: Path, force: bool = False) -> list[dict]:
    sd = workdir / "shorts"
    cand_file = sd / "candidates.json"
    if not cand_file.exists():
        raise SystemExit("먼저 shorts/candidates.py 를 돌리세요.")

    data = json.loads(cand_file.read_text(encoding="utf-8"))
    cues = json.loads((workdir / "cues.json").read_text(encoding="utf-8"))
    words = json.loads((workdir / "words.json").read_text(encoding="utf-8"))["words"]
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    buckets = index_words(cues, words)

    # 검수를 마친 자막이 있으면 그것을 쓴다 (사람이 고친 문장이 더 정확하다)
    from srtlib import read_srt
    ko_file = workdir / ("ko.edited.srt" if (workdir / "ko.edited.srt").exists() else "ko.srt")
    ko = read_srt(ko_file)
    if len(ko) != len(cues):
        raise SystemExit(f"자막 장수({len(ko)})와 cue 수({len(cues)})가 다릅니다: {ko_file.name}")
    print(f"[edl] 자막: {ko_file.name}")

    send = Translator(cfg.get("translate", {"provider": "gemini",
                                            "gemini_model": "gemini-3.8-flash",
                                            "claude_model": "opus"}))

    out: list[dict] = []
    for cand in data["candidates"]:
        f = sd / f"edl_{cand['n']}.json"
        if f.exists() and not force:
            print(f"[edl] {f.name} 재사용")
            out.append(json.loads(f.read_text(encoding="utf-8")))
            continue

        print(f"[edl] {cand['n']}번 \"{cand['title']}\" 처리 중...")
        e = make_edl(send, cand, cues, ko, words, buckets)
        f.write_text(json.dumps(e, ensure_ascii=False, indent=1), encoding="utf-8")
        out.append(e)

        mark = "재배치" if e["reordered"] else "원래 순서"
        print(f"      {e['source_duration']:.0f}초 → {e['duration']:.0f}초 "
              f"({e['removed_pct']:.0f}% 덜어냄) · {mark} · 조각 {len(e['spans'])}개")
        for w in e["warnings"]:
            print(f"      ⚠️ {w}")

    ok = sum(1 for e in out if e["usable"])
    print(f"\n[edl] {len(out)}개 중 쓸 수 있는 것 {ok}개 → {sd}")
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python shorts/edl.py <work폴더명> [--force]")
        raise SystemExit(1)
    main(ROOT / "work" / sys.argv[1], force="--force" in sys.argv)
