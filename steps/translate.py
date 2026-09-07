"""④ 번역 — Claude Code를 헤드리스로 불러 자막을 한국어로 옮긴다.

타이밍은 절대 건드리지 않는다. 글자만 바꾼다.
품질 장치 3개: 앞뒤 문맥 창 / 고유명사 용어집 / 장당 글자수 상한.
"""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
import urllib.error
import urllib.request
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
from srtlib import write_srt  # noqa: E402

BATCH_SIZE = 60          # 호출 1번의 고정비가 약 4만 토큰(시스템 프롬프트·도구 목록).
                         # 묶음을 키워도 입력은 2천 토큰만 느니 호출 수를 줄이는 게 이득이다.
                         # 60장은 실측에서 번호 누락 0개로 확인됨.
CONTEXT = 12
CHARS_PER_SEC = 5.0      # 한국어 자막이 편하게 읽히는 속도
CHARS_MIN, CHARS_MAX = 8, 32


DEPENDENT_NOUNS = ("때", "것", "거", "수", "데", "줄", "뿐", "만큼",
                   "대로", "채", "등", "지", "바", "터", "쪽", "편")


def wrap_ko(text: str, max_len: int) -> str:
    """한 줄이 길면 어절 단위로 두 줄로 나눈다.
    LLM에게 맡기면 들쭉날쭉해서 여기서 규칙으로 정한다.
    두 줄 길이가 비슷하게 갈리는 자리를 고른다(한쪽만 길면 보기 나쁘다)."""
    text = " ".join(text.split(chr(10))).strip()
    if len(text) <= max_len:
        return text
    words = text.split(" ")
    if len(words) < 2:
        return text
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = max(len(a), len(b)) + abs(len(a) - len(b)) * 0.35
        # 의존명사는 앞말에 붙어야 읽힌다. "시작할 / 때" 처럼 끊기면 어색하다.
        head = words[i].split("(")[0]
        if any(head.startswith(x) for x in DEPENDENT_NOUNS):
            score += 8
        if len(words[i - 1]) <= 1:        # 한 글자만 남기고 넘기지 않는다
            score += 4
        if best is None or score < best[0]:
            best = (score, a + chr(10) + b)
    return best[1]


def max_chars(cue: dict) -> int:
    n = round((cue["end"] - cue["start"]) * CHARS_PER_SEC)
    return max(CHARS_MIN, min(CHARS_MAX, int(n)))


def flatten_glossary(g: dict) -> str:
    lines = []
    for section, items in g.items():
        if section.startswith("_") or not isinstance(items, dict):
            continue
        pairs = ", ".join(f"{k} → {v}" for k, v in items.items())
        lines.append(f"- **{section}**: {pairs}")
    return "\n".join(lines)


# ─────────────────── 번역 제공자 ───────────────────

def load_key() -> str | None:
    """환경변수 GEMINI_API_KEY → 이 폴더의 .env 순으로 찾는다.
    키는 어떤 경우에도 화면이나 로그에 출력하지 않는다."""
    k = os.environ.get("GEMINI_API_KEY")
    if k and k.strip():
        return k.strip()
    env = ROOT / ".env"
    if env.exists():
        m = re.search(r"^GEMINI_API_KEY=(.+)$",
                      env.read_text(encoding="utf-8", errors="replace"), re.M)
        if m and m.group(1).strip():
            return m.group(1).strip().strip('"').strip("'")
    return None


def call_gemini(prompt: str, model: str, key: str, timeout: int = 240) -> str:
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
    }).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    cands = d.get("candidates") or []
    if not cands:
        raise RuntimeError(f"Gemini 응답이 비었습니다: {str(d)[:200]}")
    return cands[0]["content"]["parts"][0]["text"]


def claude_cmd(model: str) -> list[str]:
    """윈도우의 claude는 .CMD 래퍼라 파이썬이 직접 실행하지 못한다. cmd /c 로 감싼다."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude 명령을 찾지 못했습니다. Claude Code가 설치돼 있는지 확인하세요.")
    head = ["cmd", "/c", exe] if exe.lower().endswith((".cmd", ".bat")) else [exe]
    return head + ["-p", "--model", model]


def call_claude(prompt: str, model: str = "opus", timeout: int = 600) -> str:
    cp = subprocess.run(claude_cmd(model), input=prompt, capture_output=True,
                        text=True, encoding="utf-8", errors="replace",
                        timeout=timeout, shell=False)
    if cp.returncode != 0:
        raise RuntimeError(f"claude 호출 실패({cp.returncode}): {(cp.stderr or '')[-400:]}")
    return cp.stdout.strip()


class Translator:
    """Gemini(무료)를 기본으로 쓰고, 막히면 Claude Code로 넘어간다.

    호출 1번당 입력 토큰: Gemini 약 2,400 / Claude Code 약 44,000.
    Claude Code는 매번 시스템 프롬프트와 도구 목록을 통째로 싣기 때문이다.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.provider = cfg.get("provider", "gemini")
        self.key = load_key() if self.provider == "gemini" else None
        if self.provider == "gemini" and not self.key:
            print("[translate] Gemini 키가 없어 Claude Code로 진행합니다.")
            print("           .env.example 을 .env 로 복사하고 키를 넣으면 무료로 씁니다.")
            self.provider = "claude"
        print(f"[translate] 번역 엔진: "
              + (f"Gemini {cfg['gemini_model']} (무료)" if self.provider == "gemini"
                 else f"Claude Code {cfg['claude_model']} (구독 사용량 소모)"))

    def __call__(self, prompt: str) -> str:
        if self.provider == "gemini":
            try:
                return call_gemini(prompt, self.cfg["gemini_model"], self.key)
            except Exception as e:
                msg = str(e)[:160]
                if not self.cfg.get("fallback_to_claude", True):
                    raise
                print(f"      Gemini 실패({msg}) → Claude Code로 전환합니다.")
                self.provider = "claude"
        return call_claude(prompt, self.cfg.get("claude_model", "opus"))


def clean(text: str) -> str:
    """LLM 응답에 이따금 깨진 문자가 섞여 온다. 그대로 파일에 쓰면
    저장이 통째로 실패하므로 여기서 걷어낸다."""
    return text.encode("utf-8", "ignore").decode("utf-8", "ignore")


def parse_json_array(text: str) -> list[dict]:
    """코드펜스나 앞뒤 설명이 섞여 와도 배열만 건져낸다."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean(text).strip(), flags=re.M)
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("JSON 배열을 찾지 못했습니다.")
    return json.loads(t[start:end + 1])


def render_prompt(tpl: str, glossary: str, cues: list[dict],
                  batch: list[int], ko: dict[int, str]) -> str:
    lo, hi = batch[0], batch[-1]
    before = []
    for i in range(max(0, lo - CONTEXT), lo):
        got = ko.get(i)
        before.append(f"{cues[i]['text']}" + (f"  →  {got}" if got else ""))
    after = [cues[i]["text"] for i in range(hi + 1, min(len(cues), hi + 1 + CONTEXT))]

    body = []
    for i in batch:
        c = cues[i]
        body.append(f"[{i + 1}] (최대 {max_chars(c)}자) {c['text']}")

    return (tpl
            .replace("{GLOSSARY}", glossary)
            .replace("{CONTEXT_BEFORE}", "\n".join(before) or "(영상 시작)")
            .replace("{BATCH}", "\n".join(body))
            .replace("{CONTEXT_AFTER}", "\n".join(after) or "(영상 끝)"))


def translate_batch(send, tpl, glossary, cues, batch, ko) -> dict[int, str]:
    """한 묶음을 번역한다. 실패하면 한 번 더, 그래도 실패하면 반으로 쪼갠다."""
    want = {i + 1 for i in batch}
    prompt = render_prompt(tpl, glossary, cues, batch, ko)

    for attempt in (1, 2):
        try:
            raw = send(prompt if attempt == 1 else prompt +
                              "\n\n⚠️ 반드시 JSON 배열만, 위 번호 전부를 정확히 한 번씩 포함해 다시 출력하세요.")
            items = parse_json_array(raw)
            got = {int(it["i"]): clean(str(it["ko"])).strip()
                   for it in items if "i" in it and "ko" in it}
            missing = want - set(got)
            if not missing:
                return {i - 1: got[i] for i in want}
            print(f"      번호 {len(missing)}개 빠짐 → 재시도 {attempt}")
        except Exception as e:
            print(f"      실패({e}) → 재시도 {attempt}")
        time.sleep(2)

    if len(batch) <= 2:
        raise RuntimeError(f"자막 {batch[0]+1}~{batch[-1]+1} 번역 실패")
    mid = len(batch) // 2
    print(f"      묶음을 반으로 쪼개 다시 시도합니다.")
    out = translate_batch(send, tpl, glossary, cues, batch[:mid], ko)
    ko2 = {**ko, **out}
    out.update(translate_batch(send, tpl, glossary, cues, batch[mid:], ko2))
    return out


def main(workdir: Path, force: bool = False) -> list[dict]:
    cfg_all = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    send = Translator(cfg_all.get("translate", {"provider": "claude", "claude_model": "opus"}))
    tpl = (ROOT / "prompts" / "translate.md").read_text(encoding="utf-8")
    glossary = flatten_glossary(json.loads((ROOT / "glossary.json").read_text(encoding="utf-8")))
    cues = json.loads((workdir / "cues.json").read_text(encoding="utf-8"))

    partial = workdir / "ko_partial.json"
    ko: dict[int, str] = {}
    if partial.exists() and not force:
        ko = {int(k): v for k, v in json.loads(partial.read_text(encoding="utf-8")).items()}
        if ko:
            print(f"[translate] 이어서 진행합니다 ({len(ko)}/{len(cues)}장 완료)")

    todo = [i for i in range(len(cues)) if i not in ko]
    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    print(f"[translate] 자막 {len(cues)}장 중 {len(todo)}장을 {len(batches)}묶음으로 번역합니다.")

    t0 = time.perf_counter()
    for n, batch in enumerate(batches, 1):
        print(f"  [{n}/{len(batches)}] {batch[0]+1}~{batch[-1]+1}번...", flush=True)
        ko.update(translate_batch(send, tpl, glossary, cues, batch, ko))
        partial.write_text(json.dumps({str(k): v for k, v in ko.items()},
                                      ensure_ascii=False, indent=1), encoding="utf-8")

    max_line = json.loads((ROOT / "config.json").read_text(encoding="utf-8")
                          )["segment"]["max_chars_per_line"]
    out_cues = [{"start": c["start"], "end": c["end"],
                 "text": wrap_ko(ko[i], max_line)}
                for i, c in enumerate(cues)]
    write_srt(out_cues, workdir / "ko.srt")
    print(f"[translate] 완료 ({time.perf_counter()-t0:.0f}초) → {workdir / 'ko.srt'}")
    return out_cues


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python steps/translate.py <work폴더명> [--force]")
        raise SystemExit(1)
    main(ROOT / "work" / sys.argv[1], force="--force" in sys.argv)
