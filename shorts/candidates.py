"""① 후보 선정 — 롱폼 대본에서 쇼츠로 만들 구간을 고른다.

입력은 subtrans 롱폼 결과(`work/<id>/cues.json`)뿐이다. 영상을 다시 받거나
받아쓰지 않는다. LLM 호출은 영상당 1회.

⚠️ cue 번호는 이 도구 전체에서 **1부터 센다**(ko.srt 와 같게). 배열 색인은 -1.
   섞이면 자막이 한 칸씩 밀리므로 경계에서만 변환한다.
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
from emphasis import valid_emphasis  # noqa: E402

MIN_SEC, MAX_SEC = 35.0, 85.0     # 후보 구간 길이 (편집 후 30~60초가 되게 넉넉히)
OVERLAP_LIMIT = 0.5               # 이 이상 겹치면 점수 낮은 쪽을 버린다
PER_MINUTES = 6                   # 영상 몇 분당 쇼츠 1개
MIN_OUT, MAX_OUT = 2, 6


def target_count(duration_s: float) -> int:
    return max(MIN_OUT, min(MAX_OUT, round(duration_s / 60 / PER_MINUTES)))


def mmss(sec: float) -> str:
    return f"{int(sec // 60):d}:{int(sec % 60):02d}"


def build_transcript(cues: list[dict]) -> str:
    """[번호] 분:초 문장 — LLM이 번호로 구간을 지정할 수 있게."""
    return "\n".join(
        f"[{i + 1}] {mmss(c['start'])} {c['text']}" for i, c in enumerate(cues))


def validate(items: list[dict], cues: list[dict]) -> tuple[list[dict], list[str]]:
    """LLM 응답을 걸러낸다. 통과한 것만 남기고 버린 이유를 함께 돌려준다."""
    n = len(cues)
    ok: list[dict] = []
    dropped: list[str] = []

    for it in items:
        try:
            s = int(it["start_cue"]); e = int(it["end_cue"]); h = int(it["hook_cue"])
            title = str(it.get("title", "")).strip()
            score = float(it.get("score", 0.5))
        except (KeyError, TypeError, ValueError):
            dropped.append(f"형식 오류: {str(it)[:60]}")
            continue

        if not (1 <= s <= e <= n):
            dropped.append(f"{s}~{e}: 번호가 범위 밖 (전체 1~{n})")
            continue
        if not (s <= h <= e):
            dropped.append(f"{s}~{e}: 후킹 {h}가 구간 밖")
            continue
        if not title:
            dropped.append(f"{s}~{e}: 제목 없음")
            continue

        start, end = cues[s - 1]["start"], cues[e - 1]["end"]
        dur = end - start
        if not (MIN_SEC <= dur <= MAX_SEC):
            dropped.append(f"{s}~{e}: {dur:.0f}초 (허용 {MIN_SEC:.0f}~{MAX_SEC:.0f})")
            continue

        ok.append({
            "start_cue": s, "end_cue": e, "hook_cue": h,
            "title": title, "why": str(it.get("why", "")).strip(),
            "score": score,
            "emphasis": valid_emphasis(title, it.get("emphasis")),
            "start": round(start, 2), "end": round(end, 2), "duration": round(dur, 1),
        })

    return ok, dropped


def drop_overlaps(cands: list[dict], seed: list[dict] | None = None) -> tuple[list[dict], list[str]]:
    """점수 높은 것부터 담되, 이미 담은 것과 많이 겹치면 버린다.

    seed: 이미 확정된 후보들. 새로 뽑을 때 이것들과도 겹치면 안 된다.
    """
    kept: list[dict] = list(seed or [])
    seed_n = len(kept)
    dropped: list[str] = []
    for c in sorted(cands, key=lambda x: -x["score"]):
        clash = None
        for k in kept:
            lo = max(c["start"], k["start"])
            hi = min(c["end"], k["end"])
            if hi <= lo:
                continue
            if (hi - lo) / min(c["duration"], k["duration"]) > OVERLAP_LIMIT:
                clash = k
                break
        if clash:
            dropped.append(f"{c['start_cue']}~{c['end_cue']} \"{c['title'][:18]}\" "
                           f"→ \"{clash['title'][:18]}\"과 겹침")
        else:
            kept.append(c)
    return kept[seed_n:], dropped      # 새로 담은 것만 돌려준다


def main(workdir: Path, force: bool = False, add: int = 0) -> dict:
    """add > 0 이면 기존 후보는 그대로 두고 겹치지 않는 것만 더 뽑는다.

    이미 사람이 검수한 후보를 다시 뽑으면 바뀌어 버린다. 더 필요할 때는
    새로 뽑지 말고 덧붙인다.
    """
    out_dir = workdir / "shorts"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "candidates.json"
    existing: list[dict] = []
    if out.exists():
        if add > 0:
            existing = json.loads(out.read_text(encoding="utf-8"))["candidates"]
            print(f"[candidates] 기존 {len(existing)}개는 그대로 두고 {add}개를 더 찾습니다.")
        elif not force:
            print(f"[candidates] 이미 있는 결과를 씁니다: {out.name}")
            return json.loads(out.read_text(encoding="utf-8"))

    cues = json.loads((workdir / "cues.json").read_text(encoding="utf-8"))
    meta = json.loads((workdir / "fetch.json").read_text(encoding="utf-8"))
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    tpl = (ROOT / "prompts" / "shorts_candidates.md").read_text(encoding="utf-8")

    duration = float(meta.get("duration") or cues[-1]["end"])
    target = add if add > 0 else target_count(duration)
    ask = target * 2

    print(f"[candidates] 영상 {duration/60:.1f}분 · 자막 {len(cues)}장 "
          f"→ 목표 {target}개 (넉넉히 {ask}개 요청)")

    taken = ""
    if existing:
        rows = "\n".join(
            f"- [{c['start_cue']}]~[{c['end_cue']}] \"{c['title']}\"" for c in existing)
        taken = ("## 이미 고른 구간 — 여기와 겹치지 않는 곳에서 찾으세요\n\n"
                 + rows + "\n\n이 구간들은 이미 쓰기로 했습니다. 다른 곳을 보세요.")

    send = Translator(cfg.get("translate", {"provider": "gemini",
                                            "gemini_model": "gemini-3.8-flash",
                                            "claude_model": "opus"}))
    prompt = (tpl.replace("{TRANSCRIPT}", build_transcript(cues))
                 .replace("{COUNT}", str(ask))
                 .replace("{TAKEN}", taken))

    items = None
    for attempt in (1, 2):
        try:
            items = parse_json_array(send(prompt))
            break
        except Exception as e:
            print(f"      실패({str(e)[:100]}) → 재시도 {attempt}")
    if items is None:
        raise SystemExit("[candidates] LLM이 쓸 수 있는 답을 주지 않았습니다.")

    ok, bad = validate(items, cues)
    kept, overlapped = drop_overlaps(ok, seed=existing)
    fresh = kept[:target]
    final = existing + fresh          # 기존 것이 앞이라 n 번호가 유지된다

    print(f"[candidates] 받은 {len(items)}개 → 검증 통과 {len(ok)} → "
          f"겹침 제거 {len(kept)} → 새로 담은 것 {len(fresh)}개 "
          f"(전체 {len(final)}개)")
    for msg in (bad + overlapped)[:8]:
        print(f"      버림: {msg}")

    if len(fresh) < target:
        print(f"[candidates] ⚠️ {target}개를 원했지만 {len(fresh)}개만 남았습니다. "
              "남은 구간에 쓸 만한 게 그만큼인 듯합니다.")

    for i, c in enumerate(final, 1):
        c["n"] = i

    result = {
        "video_id": workdir.name,
        "title": meta.get("title", ""),
        "target": target,
        "candidates": final,
    }
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print()
    show = fresh if existing else final
    for c in show:
        print(f"  [{c['n']}] {mmss(c['start'])}~{mmss(c['end'])} ({c['duration']:.0f}초) "
              f"점수 {c['score']:.2f}  {c['title']}")
        print(f"      {c['why'][:74]}")
    print(f"\n[candidates] 완료 → {out}")
    return result


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("사용법: python shorts/candidates.py <work폴더명> [--force] [--add N]")
        print("  --add N : 기존 후보는 그대로 두고 겹치지 않는 것만 N개 더 찾는다")
        raise SystemExit(1)
    add = 0
    if "--add" in sys.argv:
        i = sys.argv.index("--add")
        add = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 2
    main(ROOT / "work" / args[0], force="--force" in sys.argv, add=add)
