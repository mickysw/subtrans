"""② 받아쓰기 — 단어 하나하나의 시작·끝 시각을 뽑는다.

자막 싱크는 전부 여기서 나온다. word_timestamps=True 가 핵심.
③에서 자막 한 장의 시각을 "첫 단어 시작 ~ 마지막 단어 끝"으로 못 박기 때문에,
이 단계만 정확하면 자막이 어긋날 수가 없다.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent


def main(workdir: Path, force: bool = False) -> dict:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["asr"]
    out = workdir / "words.json"
    if out.exists() and not force:
        print(f"[asr] 이미 받아쓴 결과를 씁니다: {out.name}")
        return json.loads(out.read_text(encoding="utf-8"))

    meta = json.loads((workdir / "fetch.json").read_text(encoding="utf-8"))
    audio = meta["audio"]
    duration = float(meta.get("duration") or 0)

    from faster_whisper import WhisperModel

    print(f"[asr] 모델 로딩: {cfg['model']} ({cfg['device']}/{cfg['compute_type']})")
    print("      첫 실행이면 모델을 내려받습니다 (약 1.5GB, 한 번만).")
    t_load = time.perf_counter()
    model = WhisperModel(
        cfg["model"],
        device=cfg["device"],
        compute_type=cfg["compute_type"],
        cpu_threads=cfg.get("cpu_threads", 0),
    )
    load_s = time.perf_counter() - t_load
    print(f"[asr] 모델 준비 완료 ({load_s:.1f}초)")

    print(f"[asr] 받아쓰는 중... (오디오 {duration:.0f}초)")
    t0 = time.perf_counter()
    segments, info = model.transcribe(
        audio,
        language=cfg.get("language") or None,
        beam_size=cfg.get("beam_size", 5),
        word_timestamps=True,
        vad_filter=cfg.get("vad_filter", True),
    )

    words: list[dict] = []
    segs: list[dict] = []
    last_report = t0
    for seg in segments:  # 제너레이터라 여기서 실제 연산이 돈다
        segs.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
        for w in (seg.words or []):
            words.append({
                "w": w.word.strip(),
                "s": round(w.start, 3),
                "e": round(w.end, 3),
                "p": round(w.probability, 3),
            })
        now = time.perf_counter()
        if now - last_report > 15:
            done = seg.end
            pct = (done / duration * 100) if duration else 0
            print(f"      {done:6.0f}초 / {duration:.0f}초  ({pct:4.1f}%)  "
                  f"경과 {now - t0:.0f}초")
            last_report = now

    elapsed = time.perf_counter() - t0
    audio_s = duration or (segs[-1]["end"] if segs else 0)
    rtf = elapsed / audio_s if audio_s else 0

    result = {
        "language": info.language,
        "audio_seconds": round(audio_s, 1),
        "elapsed_seconds": round(elapsed, 1),
        "rtf": round(rtf, 3),
        "model": cfg["model"],
        "word_count": len(words),
        "segments": segs,
        "words": words,
    }
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    print()
    print("─" * 52)
    print(f"  오디오 길이   {audio_s:8.0f}초")
    print(f"  걸린 시간     {elapsed:8.0f}초")
    print(f"  실시간 대비   {rtf:8.2f}배   (1.0보다 작으면 실제 재생보다 빠름)")
    print(f"  단어 수       {len(words):8,}개")
    if rtf:
        print(f"  → 20분 영상 환산 약 {20 * 60 * rtf / 60:.1f}분")
    print("─" * 52)
    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("사용법: python steps/asr.py <work폴더명> [--force]")
        raise SystemExit(1)
    wd = ROOT / "work" / args[0]
    if not wd.exists():
        raise SystemExit(f"작업 폴더가 없습니다: {wd}")
    main(wd, force="--force" in args)
