"""⑦ 액자 + 굽기 — 한 번의 ffmpeg 실행으로 액자 합성과 자막 굽기를 같이 한다.

두 번 인코딩하면 그만큼 화질이 깎이므로 반드시 한 번에 처리한다.

화질 원칙
  - 영상을 축소하지 않는다. 캔버스를 키워서 여백을 만든다.
    (1080p 캔버스에 85%로 축소해 넣으면 그 영상은 실질 918p가 된다)
  - 1440p로 올리면 유튜브가 더 좋은 코덱과 비트레이트를 배정한다. 액자가 화질을 올려준다.
  - 오디오는 AAC면 그대로 복사한다. 소리는 손대지 않는다.
  - 굽고 나서 VMAF로 실제 화질을 잰다. 주장하지 않고 측정한다.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
from srtlib import read_srt, fmt_ts  # noqa: E402


# ─────────────────────────────── 배치 계산 ───────────────────────────────

def even(n: float) -> int:
    """짝수로 맞춘다. h264는 홀수 해상도를 싫어한다."""
    return int(round(n / 2)) * 2


def layout(src_w: int, src_h: int, f: dict) -> dict:
    """액자 배치를 계산한다. 어떤 경우에도 영상을 축소하지 않는다."""
    ratio = f["video_width_ratio"]
    canvas_w = f["canvas_width"]

    # 원본이 커서 축소가 생길 상황이면 캔버스를 키운다 (다운스케일 금지)
    need_w = src_w / ratio
    if need_w > canvas_w:
        canvas_w = even(need_w)
    canvas_h = even(canvas_w * f["canvas_height"] / f["canvas_width"])

    vid_w = even(canvas_w * ratio)
    vid_h = even(vid_w * src_h / src_w)
    if vid_h > canvas_h:                      # 세로가 넘치면 세로에 맞춘다
        vid_h = even(canvas_h * ratio)
        vid_w = even(vid_h * src_w / src_h)

    pad_x = even((canvas_w - vid_w) / 2)
    pad_y = even((canvas_h - vid_h) * f["video_top_ratio"])
    return {
        "canvas_w": canvas_w, "canvas_h": canvas_h,
        "vid_w": vid_w, "vid_h": vid_h,
        "pad_x": pad_x, "pad_y": pad_y,
        "scale_pct": vid_w / src_w * 100,
        "band_bottom": canvas_h - (pad_y + vid_h),
    }


# ─────────────────────────────── ASS 자막 ───────────────────────────────

def ass_color(hex_color: str) -> str:
    """#RRGGBB → ASS의 &HAABBGGRR (알파 00 = 불투명)"""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def ass_time(sec: float) -> str:
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(cues: list[dict], lay: dict, st: dict) -> str:
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {lay['canvas_w']}
PlayResY: {lay['canvas_h']}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: KR,{st['font']},{st['size']},{ass_color(st['color'])},{ass_color(st['color'])},{ass_color(st['outline_color'])},&H80000000,{-1 if st['bold'] else 0},0,0,0,100,100,{st['line_spacing']},0,1,{st['outline']},{st['shadow']},2,{st['margin_lr']},{st['margin_lr']},{st['margin_bottom']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues:
        text = c["text"].strip().replace("\n", r"\N")
        text = re.sub(r"\{|\}", "", text)          # ASS 태그 오인 방지
        lines.append(f"Dialogue: 0,{ass_time(c['start'])},{ass_time(c['end'])},KR,,0,0,0,,{text}")
    return head + "\n".join(lines) + "\n"


# ─────────────────────────────── 실행 ───────────────────────────────

def run_ffmpeg(cmd: list[str], cwd: Path, label: str) -> str:
    cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        raise RuntimeError(f"{label} 실패:\n{(cp.stderr or '')[-1500:]}")
    return cp.stderr or ""


VMAF_WINDOWS = (0.12, 0.45, 0.78)   # 영상의 앞/중간/뒤 지점
VMAF_WINDOW_SEC = 6


def measure_vmaf(out_mp4: Path, src: Path, lay: dict, src_w: int, src_h: int,
                 workdir: Path, duration: float) -> float | None:
    """액자에서 영상 영역만 잘라 원본 크기로 되돌린 뒤 원본과 비교한다.

    ⚠️ -ss 로 탐색해서 비교하면 안 된다. 두 파일의 키프레임 위치가 달라
       1프레임씩 어긋나고, 움직임 많은 장면에서 점수가 무너진다.
       실측: 같은 구간이 탐색하면 85.1, 처음부터 디코딩하면 97.4.
       그래서 처음부터 디코딩하고 select 필터로 필요한 구간만 고른다.
       (디코딩은 전 구간, libvmaf 계산은 표본 구간만 → 충분히 빠르다)

    여백은 원본에 없으니 잘라내고 비교한다. 자막이 들어간 면적은
    점수에 거의 영향이 없다(실측 0.3점 차이).
    """
    wins = []
    for frac in VMAF_WINDOWS:
        t = duration * frac
        if t + 1 < duration:
            wins.append((t, min(t + VMAF_WINDOW_SEC, duration)))
    if not wins:
        wins = [(0.0, min(VMAF_WINDOW_SEC, duration))]
    sel = "+".join(f"between(t\,{a:.2f}\,{b:.2f})" for a, b in wins)

    vf = (f"[0:v]crop={lay['vid_w']}:{lay['vid_h']}:{lay['pad_x']}:{lay['pad_y']},"
          f"scale={src_w}:{src_h}:flags=lanczos,"
          f"select='{sel}',setpts=N/FRAME_RATE/TB[dist];"
          f"[1:v]select='{sel}',setpts=N/FRAME_RATE/TB[ref];"
          f"[dist][ref]libvmaf=n_threads=12")

    span = ", ".join(f"{a:.0f}~{b:.0f}초" for a, b in wins)
    print(f"       비교 구간: {span}")
    try:
        err = run_ffmpeg(["ffmpeg", "-v", "info", "-i", out_mp4.name, "-i", str(src),
                          "-lavfi", vf, "-f", "null", "-"], workdir, "VMAF 측정")
    except RuntimeError as e:
        print(f"[burn] 화질 측정을 건너뜁니다: {str(e)[:160]}")
        return None
    m = re.search(r"VMAF score:\s*([\d.]+)", err)
    return float(m.group(1)) if m else None


def report_vmaf(score: float | None, style: dict, enc: dict) -> None:
    gate = style["quality_gate"]["vmaf_min"]
    if score is None:
        print("[burn] 화질 점수를 얻지 못했습니다.")
    elif score >= gate:
        print(f"[burn] ✓ VMAF {score:.2f} — {gate} 이상이라 사람 눈으로 구분 불가입니다.")
    else:
        print(f"[burn] ⚠️ VMAF {score:.2f} — 기준 {gate}에 못 미칩니다. "
              f"style.json 의 crf 를 낮춰(예: {enc['crf']-3}) 다시 구우세요.")


def main(workdir: Path, srt_name: str | None = None, vmaf: bool = True,
         vmaf_only: bool = False) -> Path:
    style = json.loads((ROOT / "style.json").read_text(encoding="utf-8"))
    meta = json.loads((workdir / "fetch.json").read_text(encoding="utf-8"))
    f, st, enc = style["frame"], style["subtitle"], style["encode"]

    # 검수를 마친 파일이 있으면 그걸 우선한다
    if srt_name is None:
        srt_name = "ko.edited.srt" if (workdir / "ko.edited.srt").exists() else "ko.srt"
    srt = workdir / srt_name
    if not srt.exists():
        raise SystemExit(f"자막 파일이 없습니다: {srt}")
    print(f"[burn] 자막: {srt.name}")

    src = Path(meta["video"])
    lay = layout(meta["width"], meta["height"], f)
    cues = read_srt(srt)

    (workdir / "subs.ass").write_text(build_ass(cues, lay, st), encoding="utf-8")

    print(f"[burn] 캔버스 {lay['canvas_w']}x{lay['canvas_h']} / "
          f"영상 {lay['vid_w']}x{lay['vid_h']} (원본의 {lay['scale_pct']:.0f}%)")
    print(f"[burn] 여백 좌우 {lay['pad_x']}px / 위 {lay['pad_y']}px / 아래 {lay['band_bottom']}px")
    if lay["scale_pct"] < 99.9:
        print("[burn] ⚠️ 영상이 축소됩니다. style.json 의 canvas_width 를 키우세요.")

    audio_aac = (meta.get("acodec") or "").lower() in ("aac", "mp4a")
    acodec = ["-c:a", "copy"] if audio_aac else ["-c:a", "aac", "-b:a", enc["audio_bitrate"]]
    print(f"[burn] 오디오: {'원본 그대로 복사' if audio_aac else 'AAC로 변환 ' + enc['audio_bitrate']}")

    bg = f.get("background_color", "#141414").lstrip("#")
    vf = (f"scale={lay['vid_w']}:{lay['vid_h']}:flags=lanczos,"
          f"pad={lay['canvas_w']}:{lay['canvas_h']}:{lay['pad_x']}:{lay['pad_y']}:color=0x{bg},"
          f"ass=subs.ass")

    out = workdir / "out.mp4"
    if vmaf_only and out.exists():
        print("[burn] 이미 구운 영상으로 화질만 다시 잽니다.")
        score = measure_vmaf(out, src, lay, meta["width"], meta["height"],
                             workdir, float(meta.get("duration") or 0))
        report_vmaf(score, style, enc)
        return out

    print(f"[burn] 굽는 중... (preset {enc['preset']}, crf {enc['crf']})")
    t0 = time.perf_counter()
    run_ffmpeg(["ffmpeg", "-y", "-v", "error", "-stats", "-i", str(src),
                "-vf", vf,
                "-c:v", "libx264", "-preset", enc["preset"], "-crf", str(enc["crf"]),
                "-pix_fmt", "yuv420p", *acodec,
                "-movflags", "+faststart", out.name],
               workdir, "굽기")
    took = time.perf_counter() - t0
    size_mb = out.stat().st_size / 1024 / 1024
    print(f"[burn] 완료 ({took/60:.1f}분, {size_mb:.0f}MB) → {out}")

    if vmaf:
        print("[burn] 화질 측정 중 (VMAF, 앞·중간·뒤 표본)...")
        score = measure_vmaf(out, src, lay, meta["width"], meta["height"],
                             workdir, float(meta.get("duration") or 0))
        report_vmaf(score, style, enc)
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("사용법: python steps/burn.py <work폴더명> [자막파일명] [--no-vmaf] [--vmaf-only]")
        raise SystemExit(1)
    main(ROOT / "work" / args[0],
         args[1] if len(args) > 1 else None,
         vmaf="--no-vmaf" not in sys.argv,
         vmaf_only="--vmaf-only" in sys.argv)
