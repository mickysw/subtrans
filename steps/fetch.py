"""① 받기 — 유튜브 링크(또는 로컬 파일)에서 영상과 16kHz 오디오를 확보한다.

화질 원칙: 컨테이너를 mp4로 강제하지 않는다. 유튜브의 mp4(AVC) 스트림은
1080p가 상한이고 같은 해상도에서도 VP9/AV1보다 화질이 낮기 때문이다.
mp4는 마지막 굽기(⑦)에서 만든다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
YTDLP = ROOT / ".venv" / "Scripts" / "yt-dlp.exe"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """자식 프로세스 출력을 UTF-8로 강제한다.

    윈도우에서 yt-dlp는 콘솔 기본 인코딩(cp949 등)으로 찍는데 그걸 UTF-8로 읽으면
    제목의 특수 문자가 깨진다. 실제로 영상 제목의 작은따옴표가 깨져 파일 이름에
    U+FFFD 가 박히는 사고가 났다. PYTHONIOENCODING 으로 출력 쪽을 맞춘다.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(cmd, text=True, encoding="utf-8", errors="replace",
                          env=env, **kw)


def probe(path: Path) -> dict:
    """ffprobe로 영상 정보를 읽는다."""
    cp = run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True,
    )
    if cp.returncode != 0:
        raise RuntimeError(f"ffprobe 실패: {cp.stderr.strip()}")
    data = json.loads(cp.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        raise RuntimeError("영상 스트림이 없습니다.")
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": video.get("r_frame_rate", "?"),
        "vcodec": video.get("codec_name"),
        "acodec": audio.get("codec_name") if audio else None,
        "duration": float(data["format"].get("duration", 0)),
    }


def video_id(source: str) -> str:
    """작업 폴더 이름으로 쓸 식별자."""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})", source)
    if m:
        return m.group(1)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(source).stem)[:40]
    return stem or "local"


def video_title(source: str, workdir: Path) -> str:
    """받는 시점의 제목을 잡는다. 유튜브는 제목을 수시로 바꾸므로
    나중에 다시 물어보면 다른 답이 온다 — 실제로 이 영상이 그랬다."""
    if Path(source).exists():
        return Path(source).stem
    cp = run([str(YTDLP), source, "--print", "%(title)s",
              "--skip-download", "--no-warnings", "--no-playlist"],
             capture_output=True)
    title = (cp.stdout or "").strip().splitlines()
    return title[0] if title and title[0] else workdir.name


def download(url: str, workdir: Path, cfg: dict, clip: str | None = None) -> Path:
    """yt-dlp로 최고 화질 영상을 받는다. 봇 차단 시 브라우저 쿠키로 재시도."""
    out_tpl = str(workdir / "video.%(ext)s")
    base = [
        str(YTDLP), url,
        "-f", cfg["format"],
        "--merge-output-format", cfg["container"],
        "-o", out_tpl,
        "--no-playlist",
        "--newline",
    ]
    if clip:
        # --force-keyframes-at-cuts 는 재인코딩을 유발해 화질을 깎는다. 쓰지 않는다.
        base += ["--download-sections", f"*{clip}"]

    attempts = [base, base + ["--cookies-from-browser", "chrome"]]
    last_err = ""
    for i, cmd in enumerate(attempts, 1):
        if i > 1:
            print(f"[fetch] 봇 차단으로 보입니다. 브라우저 쿠키로 재시도합니다...")
        cp = run(cmd, capture_output=True)
        if cp.returncode == 0:
            break
        last_err = (cp.stderr or cp.stdout or "").strip()
    else:
        raise RuntimeError(
            "영상을 받지 못했습니다.\n"
            f"{last_err[-800:]}\n\n"
            "→ 브라우저에서 유튜브에 로그인한 뒤 다시 시도하거나,\n"
            "   영상을 직접 받아 로컬 파일 경로를 넣어 주세요."
        )

    found = sorted(workdir.glob("video.*"))
    found = [p for p in found if p.suffix.lower() not in (".part", ".ytdl")]
    if not found:
        raise RuntimeError("받은 파일을 찾지 못했습니다.")
    return found[0]


def extract_audio(video: Path, workdir: Path) -> Path:
    """받아쓰기 모델이 요구하는 16kHz 모노 wav."""
    audio = workdir / "audio.wav"
    cp = run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)],
        capture_output=True,
    )
    if cp.returncode != 0:
        raise RuntimeError(f"오디오 추출 실패: {cp.stderr.strip()}")
    return audio


def main(source: str, clip: str | None = None, force: bool = False) -> dict:
    cfg_all = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    cfg = cfg_all["fetch"]

    vid = video_id(source)
    workdir = ROOT / "work" / vid
    workdir.mkdir(parents=True, exist_ok=True)

    is_local = Path(source).exists()
    existing = [p for p in workdir.glob("video.*") if p.suffix.lower() not in (".part", ".ytdl")]

    if is_local:
        video = Path(source).resolve()
    elif existing and not force:
        video = existing[0]
        print(f"[fetch] 이미 받아둔 영상을 씁니다: {video.name}")
    else:
        print(f"[fetch] 받는 중... ({'클립 ' + clip if clip else '전체'})")
        video = download(source, workdir, cfg, clip)

    info = probe(video)
    print(f"[fetch] {info['width']}x{info['height']} / {info['vcodec']} / "
          f"{info['duration']:.0f}초 / 오디오 {info['acodec']}")

    if info["acodec"] not in ("aac", "mp3"):
        print(f"[fetch] 참고: 오디오가 {info['acodec']} 입니다. mp4에 그대로 복사할 수 "
              f"없어 ⑦단계에서 AAC로 한 번 변환됩니다(소리 손실은 아주 작음).")

    if info["height"] < cfg["min_height"]:
        raise SystemExit(
            f"\n⚠️ 해상도가 {info['height']}p 입니다. 최소 {cfg['min_height']}p가 필요합니다.\n"
            "   이대로 진행하면 자막을 다 만든 뒤에 화질 때문에 처음부터 다시 해야 합니다.\n"
            "   원본이 1080p를 제공하지 않는 영상일 수 있습니다."
        )

    audio = workdir / "audio.wav"
    if not audio.exists() or force:
        print("[fetch] 오디오 추출 중...")
        audio = extract_audio(video, workdir)

    prev = {}
    if (workdir / "fetch.json").exists():
        try:
            prev = json.loads((workdir / "fetch.json").read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    title = prev.get("title") or video_title(source, workdir)
    print(f"[fetch] 제목: {title}")

    meta = {"source": source, "title": title,
            "video": str(video), "audio": str(audio), **info}
    (workdir / "fetch.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[fetch] 완료 → {workdir}")
    return meta


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("사용법: python steps/fetch.py <유튜브URL|파일경로> [--clip 0:00-5:00]")
        raise SystemExit(1)
    clip = None
    if "--clip" in args:
        i = args.index("--clip")
        clip = args[i + 1]
        args = args[:i] + args[i + 2:]
    main(args[0], clip=clip, force="--force" in args)
