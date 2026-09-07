"""영어 유튜브 영상 → 한국어 자막 박힌 mp4.

  python run.py <유튜브주소>            ① 받기 ~ ⑥ 검수화면까지
  python run.py <유튜브주소> --burn     검수를 마쳤으면 ⑦ 굽기

중간 결과를 work/<영상id>/ 에 남기므로 끊긴 지점부터 다시 돌릴 수 있다.
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "steps"))

import fetch as S_fetch      # noqa: E402
import asr as S_asr          # noqa: E402
import segment as S_segment  # noqa: E402
import translate as S_translate  # noqa: E402
import check as S_check      # noqa: E402
import review as S_review    # noqa: E402
import burn as S_burn        # noqa: E402

USAGE = """사용법
  python run.py <유튜브주소>            받기 → 받아쓰기 → 번역 → 검수화면
  python run.py <유튜브주소> --burn     검수를 마친 뒤 영상 굽기

옵션
  --clip 0:00-5:00   앞부분만 잘라서 시험할 때 (처음엔 이걸로 해보세요)
  --redo asr         특정 단계를 다시 (fetch/asr/segment/translate)
"""


def hr(title: str) -> None:
    print()
    print(f"── {title} " + "─" * max(0, 56 - len(title)))


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(USAGE)
        return 1
    source = args[0]

    clip = None
    if "--clip" in argv:
        clip = argv[argv.index("--clip") + 1]
    redo = argv[argv.index("--redo") + 1] if "--redo" in argv else ""

    workdir = ROOT / "work" / S_fetch.video_id(source)
    t_all = time.perf_counter()

    # ⑦만 실행 (검수 후)
    if "--burn" in argv:
        hr("⑦ 액자 + 굽기")
        out = S_burn.main(workdir)
        print()
        print(f"완성됐습니다 → {out}")
        print("유튜브에 먼저 '비공개'로 올려 1080p로 재생해 확인해 보세요.")
        return 0

    hr("① 받기")
    S_fetch.main(source, clip=clip, force=(redo == "fetch"))

    hr("② 받아쓰기 (단어 단위 시각)")
    S_asr.main(workdir, force=(redo in ("asr", "fetch")))

    hr("③ 조각내기 (자막 한 장 단위)")
    S_segment.main(workdir)

    hr("④ 번역")
    S_translate.main(workdir, force=(redo == "translate"))

    hr("⑤ 검사")
    S_check.main(workdir)

    hr("⑥ 검수 화면")
    html = S_review.main(workdir)

    print()
    print("─" * 60)
    print(f"여기까지 {(time.perf_counter()-t_all)/60:.1f}분 걸렸습니다.")
    print()
    print("다음 순서:")
    print(f"  1. 이 파일을 브라우저로 여세요:")
    print(f"     {html}")
    print("  2. 오역만 고치고 [저장]을 누르면 ko.edited.srt 가 내려받아집니다.")
    print(f"  3. 그 파일을 이 폴더에 넣으세요:")
    print(f"     {workdir}")
    print("  4. 마지막으로 이 명령:")
    print(f"     python run.py \"{source}\" --burn")
    print("─" * 60)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n중단했습니다. 같은 명령을 다시 실행하면 이어서 진행합니다.")
        raise SystemExit(130)
    except Exception as e:
        print(f"\n오류: {e}\n")
        traceback.print_exc()
        raise SystemExit(1)
