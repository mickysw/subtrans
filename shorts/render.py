"""④ 렌더 — 편집 결정(EDL)대로 잘라 붙이고 세로 화면으로 굽는다.

두 단계로 나눈다. 한 번에 하면 재배치·크롭·자막 시각이 서로 얽혀 원인을 못 찾는다.

  4-a  spans 를 이어 붙여 clip_<n>_raw.mp4 (거의 무손실)
       → 이 파일의 타임라인이 최종 타임라인이 되어 이후 계산이 단순해진다
  4-b  1080x1080 크롭 → 1080x1920 액자 → 자막 → h264

화질 원칙: 원본이 1920x1080이고 쇼츠 폭이 1080이다. 세로를 통째로 쓰고 가로만
잘라내면 **리샘플링이 0**이다. 이 숫자를 바꾸면 그 이점이 사라진다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
from srtlib import read_srt          # noqa: E402
from burn import safe_filename, ass_color, ass_time  # noqa: E402
from translate import wrap_ko        # noqa: E402

DEFAULTS = {
    "canvas_w": 1080, "canvas_h": 1920,
    "title_band": 430, "video_size": 1080,
    "background": "#141414",
    "title": {"font": "Malgun Gothic", "size": 76, "color": "#FFFFFF",
              "outline_color": "#000000", "outline": 5.0, "shadow": 1.5,
              "margin_top": 120, "margin_lr": 60, "max_chars": 12},
    "sub": {"font": "Malgun Gothic", "size": 58, "color": "#FFFFFF",
            "outline_color": "#000000", "outline": 4.5, "shadow": 1.5,
            "margin_bottom": 125, "margin_lr": 50},
    "brand": {"font": "Malgun Gothic", "size": 30, "color": "#B8BCC8",
              "outline_color": "#000000", "outline": 2.0,
              "margin_bottom": 42, "margin_lr": 40, "text": ""},
    "encode": {"crf": 18, "preset": "medium", "raw_crf": 10},
}


def cfg_shorts(style: dict) -> dict:
    """style.json 의 shorts 절을 기본값 위에 덮어쓴다."""
    out = json.loads(json.dumps(DEFAULTS))
    for k, v in (style.get("shorts") or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def run(cmd: list[str], cwd: Path, label: str) -> str:
    cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        raise RuntimeError(f"{label} 실패:\n{(cp.stderr or '')[-1500:]}")
    return cp.stderr or ""


def probe(path: Path) -> dict:
    cp = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    d = json.loads(cp.stdout)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    return {"w": int(v["width"]), "h": int(v["height"]),
            "acodec": a["codec_name"] if a else None,
            "duration": float(d["format"]["duration"])}


# ── 4-a. 조각 이어 붙이기 ────────────────────────────────────────

def concat_spans(src: Path, spans: list[dict], out: Path, workdir: Path,
                 raw_crf: int) -> None:
    """EDL 의 조각들을 순서대로 이어 붙인다. 영상과 소리를 함께 자른다.

    필터가 조각 수만큼 길어져 명령줄 길이 제한에 걸릴 수 있으므로 파일로 넘긴다.
    """
    parts, labels = [], []
    for i, sp in enumerate(spans):
        parts.append(f"[0:v]trim=start={sp['s']:.3f}:end={sp['e']:.3f},"
                     f"setpts=PTS-STARTPTS[v{i}];")
        parts.append(f"[0:a]atrim=start={sp['s']:.3f}:end={sp['e']:.3f},"
                     f"asetpts=PTS-STARTPTS[a{i}];")
        labels.append(f"[v{i}][a{i}]")
    parts.append("".join(labels) + f"concat=n={len(spans)}:v=1:a=1[v][a]")

    script = workdir / f"{out.stem}_filter.txt"
    script.write_text("".join(parts), encoding="utf-8")

    run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-filter_complex_script", script.name,
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", str(raw_crf),
         "-c:a", "aac", "-b:a", "192k", out.name],
        workdir, "조각 이어 붙이기")
    script.unlink(missing_ok=True)


# ── 4-b. 세로 화면 + 자막 ────────────────────────────────────────

def build_ass(title: str, lines: list[dict], s: dict, brand: str) -> str:
    """제목(고정) · 대사 자막 · 채널명(고정) 세 가지를 한 파일에."""
    t, sub, br = s["title"], s["sub"], s["brand"]
    end = max((l["out_e"] for l in lines), default=1.0) + 1.0

    def style_row(name, d, align, mv):
        return (f"Style: {name},{d['font']},{d['size']},"
                f"{ass_color(d['color'])},{ass_color(d['color'])},"
                f"{ass_color(d['outline_color'])},&H80000000,"
                f"-1,0,0,0,100,100,0,0,1,{d['outline']},{d.get('shadow', 0)},"
                f"{align},{d['margin_lr']},{d['margin_lr']},{mv},1")

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {s['canvas_w']}
PlayResY: {s['canvas_h']}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_row('Title', t, 8, t['margin_top'])}
{style_row('Sub', sub, 2, sub['margin_bottom'])}
{style_row('Brand', br, 3, br['margin_bottom'])}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = [f"Dialogue: 0,{ass_time(0)},{ass_time(end)},Title,,0,0,0,,"
          + wrap_ko(title, t["max_chars"]).replace("\n", r"\N")]
    if brand:
        ev.append(f"Dialogue: 0,{ass_time(0)},{ass_time(end)},Brand,,0,0,0,,{brand}")
    for l in lines:
        txt = l["ko"].strip().replace("\n", r"\N").replace("{", "").replace("}", "")
        ev.append(f"Dialogue: 1,{ass_time(l['out_s'])},{ass_time(l['out_e'])},"
                  f"Sub,,0,0,0,,{txt}")
    return head + "\n".join(ev) + "\n"


def vertical(raw: Path, ass_name: str, out: Path, workdir: Path,
             src_w: int, src_h: int, s: dict, audio_copy: bool) -> None:
    size = min(src_w, src_h)                 # 정사각형으로 자른다
    x = max(0, (src_w - size) // 2)          # 가로 가운데
    vid = s["video_size"]
    scale = "" if size == vid else f",scale={vid}:{vid}:flags=lanczos"
    bg = s["background"].lstrip("#")

    vf = (f"crop={size}:{size}:{x}:{max(0, (src_h - size) // 2)}{scale},"
          f"pad={s['canvas_w']}:{s['canvas_h']}:0:{s['title_band']}:color=0x{bg},"
          f"ass={ass_name}")
    acodec = ["-c:a", "copy"] if audio_copy else ["-c:a", "aac", "-b:a", "192k"]

    run(["ffmpeg", "-y", "-v", "error", "-i", raw.name, "-vf", vf,
         "-c:v", "libx264", "-preset", s["encode"]["preset"],
         "-crf", str(s["encode"]["crf"]), "-pix_fmt", "yuv420p", *acodec,
         "-movflags", "+faststart", out.name],
        workdir, "세로 변환")


# ── 설명란 ───────────────────────────────────────────────────────

def description(edl: dict, hook_ko: str, meta: dict) -> str:
    src = meta.get("uploader") or meta.get("title", "")
    url = meta.get("source", "")
    return "\n".join([
        f"[제목] {edl['title']}",
        "",
        hook_ko.replace("\n", " "),
        "",
        f"원본: {src}" + (f"\n{url}" if url.startswith("http") else ""),
        "전체 인터뷰 한국어 자막본: (롱폼 링크를 여기에)",
        "",
        "#쇼츠 #인터뷰 #한글자막",
    ])


# ── 실행 ─────────────────────────────────────────────────────────

def main(workdir: Path, only: list[int] | None = None, force: bool = False) -> list[Path]:
    sd = workdir / "shorts"
    style = json.loads((ROOT / "style.json").read_text(encoding="utf-8"))
    s = cfg_shorts(style)
    meta = json.loads((workdir / "fetch.json").read_text(encoding="utf-8"))
    src = Path(meta["video"])
    info = probe(src)

    ko_file = workdir / ("ko.edited.srt" if (workdir / "ko.edited.srt").exists() else "ko.srt")
    ko = read_srt(ko_file)

    if only is None:
        sel = sd / "selected.json"
        only = (json.loads(sel.read_text(encoding="utf-8"))["selected"]
                if sel.exists() else None)
    if only:
        print(f"[render] 고른 것만 굽습니다: {only}")

    edls = sorted(sd.glob("edl_*.json"), key=lambda p: int(p.stem.split("_")[1]))
    made: list[Path] = []

    for f in edls:
        e = json.loads(f.read_text(encoding="utf-8"))
        n = e["n"]
        if only and n not in only:
            continue

        name = safe_filename(f"{n}_{e['title']}", 70)
        out = sd / f"{name}.mp4"
        if out.exists() and not force:
            print(f"[render] {out.name} 재사용")
            made.append(out)
            continue

        print(f"[render] {n}번 \"{e['title']}\" ({e['duration']:.0f}초, 조각 {len(e['spans'])}개)")

        raw = sd / f"clip_{n}_raw.mp4"
        if not raw.exists() or force:
            print("         조각 이어 붙이는 중...")
            concat_spans(src, e["spans"], raw, sd, s["encode"]["raw_crf"])
        ri = probe(raw)
        if abs(ri["duration"] - e["duration"]) > 0.25:
            print(f"         ⚠️ 이어 붙인 길이({ri['duration']:.2f}초)가 "
                  f"EDL({e['duration']:.2f}초)과 다릅니다.")

        spans_map = {c["cue"]: c for c in e["cue_spans"]}
        lines = [{"ko": ko[c - 1]["text"],
                  "out_s": spans_map[c]["out_s"], "out_e": spans_map[c]["out_e"]}
                 for c in e["cues_out"] if c in spans_map]

        ass_name = f"shorts_{n}.ass"
        (sd / ass_name).write_text(
            build_ass(e["title"], lines, s, s["brand"].get("text", "")),
            encoding="utf-8")

        print("         세로 화면으로 굽는 중...")
        vertical(raw, ass_name, out, sd, ri["w"], ri["h"], s,
                 audio_copy=(ri["acodec"] == "aac"))

        oi = probe(out)
        ok = (oi["w"], oi["h"]) == (s["canvas_w"], s["canvas_h"])
        print(f"         {oi['w']}x{oi['h']} · {oi['duration']:.1f}초 · "
              f"{out.stat().st_size/1024/1024:.0f}MB "
              + ("✓" if ok else "⚠️ 크기가 의도와 다릅니다"))

        hook_ko = ko[e["cues_out"][0] - 1]["text"]
        (sd / f"{name}.txt").write_text(description(e, hook_ko, meta), encoding="utf-8")
        made.append(out)

    print(f"\n[render] {len(made)}개 완료 → {sd}")
    return made


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("사용법: python shorts/render.py <work폴더명> [번호...] [--force]")
        raise SystemExit(1)
    nums = [int(a) for a in args[1:]] or None
    main(ROOT / "work" / args[0], only=nums, force="--force" in sys.argv)
