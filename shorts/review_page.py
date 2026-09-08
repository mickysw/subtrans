"""③ 검수 화면 — 쇼츠 후보를 보고 고르는 화면을 만든다.

서버 없이 브라우저에서 여는 HTML 한 장. 롱폼 검수 화면(`steps/review.py`)과 같은 방식이다.

여기서 사람이 판단할 것은 두 가지다.
  - 후킹이 실제로 궁금증을 만드는가
  - **이음새가 자연스러운가** — 후킹 문장 뒤에 원래 첫 문장이 붙는 자리.
    글로는 알 수 없어서 들어봐야 한다. 그래서 줄마다 원본 시각으로 점프하게 해 둔다.

고른 결과는 `selected.json` 으로 내려받아 ④ 렌더가 읽는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
from srtlib import read_srt  # noqa: E402


def build_data(workdir: Path) -> list[dict]:
    sd = workdir / "shorts"
    cues = json.loads((workdir / "cues.json").read_text(encoding="utf-8"))
    ko_file = workdir / ("ko.edited.srt" if (workdir / "ko.edited.srt").exists() else "ko.srt")
    ko = read_srt(ko_file)

    out = []
    for f in sorted(sd.glob("edl_*.json"), key=lambda p: int(p.stem.split("_")[1])):
        e = json.loads(f.read_text(encoding="utf-8"))
        spans = {c["cue"]: c for c in e["cue_spans"]}
        lines = []
        for i, c in enumerate(e["cues_out"]):
            lines.append({
                "cue": c,
                "ko": ko[c - 1]["text"],
                "src": round(cues[c - 1]["start"], 2),   # 원본에서의 시각 (재생용)
                "out": spans[c]["out_s"],                # 쇼츠에서의 시각
                "hook": i == 0 and e["reordered"],
                "seam": i == 1 and e["reordered"],       # 이음새 — 여기가 관건
            })
        out.append({
            "n": e["n"], "title": e["title"], "duration": e["duration"],
            "reordered": e["reordered"], "why": e["hook_why"],
            "removed_pct": e["removed_pct"], "source_duration": e["source_duration"],
            "dropped": len(e["dropped_cues"]),
            "lines": lines,
        })
    return out


def main(workdir: Path) -> Path:
    data = build_data(workdir)
    if not data:
        raise SystemExit("edl_*.json 이 없습니다. shorts/edl.py 를 먼저 돌리세요.")

    preview = workdir / "preview.mp4"
    if not preview.exists():
        print("[review] 미리보기 영상이 없습니다. steps/review.py 를 먼저 돌리면 생깁니다.")

    html = (HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))
                .replace("__TITLE__", workdir.name))
    out = workdir / "shorts" / "review.html"
    out.write_text(html, encoding="utf-8")

    print(f"[review] 쇼츠 후보 {len(data)}개 → {out}")
    print("        브라우저로 열어 들어보고, 쓸 것만 체크한 뒤 [선택 저장]을 누르세요.")
    print(f"        내려받은 selected.json 을 {workdir / 'shorts'} 에 넣으면 렌더합니다.")
    return out


HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>쇼츠 후보 검수 — __TITLE__</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#15161a;color:#e8e8ea;font:14px/1.65 "Malgun Gothic","맑은 고딕",system-ui,sans-serif;height:100vh;display:flex;flex-direction:column}
header{padding:10px 16px;background:#1d1f25;border-bottom:1px solid #2c2f38;display:flex;gap:14px;align-items:center}
header b{font-size:15px}
.stat{color:#9aa0ad;font-size:13px}
button{background:#3a6df0;color:#fff;border:0;border-radius:6px;padding:8px 16px;font-size:14px;cursor:pointer;font-family:inherit}
button:hover{filter:brightness(1.15)}
main{flex:1;display:flex;min-height:0}
.left{width:38%;padding:16px;border-right:1px solid #2c2f38;display:flex;flex-direction:column;gap:10px}
video{width:100%;background:#000;border-radius:8px}
.hint{color:#7d8494;font-size:12.5px}
.right{flex:1;overflow-y:auto;padding:14px 16px}
.card{background:#1a1c22;border:1px solid #2c2f38;border-radius:10px;padding:14px;margin-bottom:14px}
.card.on{border-color:#3a6df0;background:#1b1f2b}
.top{display:flex;align-items:flex-start;gap:10px}
.top input{width:18px;height:18px;margin-top:3px;cursor:pointer;accent-color:#3a6df0}
.name{font-size:16px;font-weight:700}
.meta{color:#8a90a0;font-size:12.5px;margin-top:2px}
.why{color:#8ab4ff;font-size:12.5px;margin:8px 0 10px;padding-left:10px;border-left:2px solid #3a6df0}
.line{display:grid;grid-template-columns:52px 1fr;gap:8px;padding:2px 0}
.t{color:#6d7382;font-size:11.5px;cursor:pointer;font-variant-numeric:tabular-nums;padding-top:3px}
.t:hover{color:#8ab4ff;text-decoration:underline}
.tx{font-size:14px}
.hook .tx{font-weight:700;color:#ffd479;font-size:15.5px}
.seamline{grid-column:1/-1;color:#f0a63a;font-size:11.5px;margin:6px 0 2px;display:flex;align-items:center;gap:8px}
.seamline::after{content:"";flex:1;height:1px;background:#4a3a22}
.more{color:#6d7382;font-size:12px;margin-top:6px}
</style></head><body>
<header>
  <b>쇼츠 후보 검수</b>
  <span class="stat" id="stat"></span>
  <span style="flex:1"></span>
  <button onclick="save()">선택 저장 (selected.json)</button>
</header>
<main>
  <div class="left">
    <video id="v" src="../preview.mp4" controls preload="metadata"></video>
    <div class="hint">
      대본의 <b>시각을 누르면</b> 원본 영상의 그 대목이 재생됩니다.<br><br>
      가장 중요하게 들어볼 곳은 <b style="color:#f0a63a">이음새</b>입니다 —
      맨 앞으로 당긴 후킹 문장 뒤에 원래 이야기가 붙는 자리예요.
      글로는 자연스러워 보여도 소리로는 어색할 수 있습니다.
      <br><br>어색하면 그 후보의 체크를 빼면 됩니다.
    </div>
  </div>
  <div class="right" id="list"></div>
</main>
<script>
const D = __DATA__;
const v = document.getElementById('v'), list = document.getElementById('list');
const picked = new Set(D.map(d => d.n));

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function mmss(x){const m=Math.floor(x/60),s=Math.floor(x%60);return m+':'+String(s).padStart(2,'0')}

function render(){
  list.innerHTML = '';
  D.forEach(d => {
    const el = document.createElement('div');
    el.className = 'card' + (picked.has(d.n) ? ' on' : '');
    el.id = 'card' + d.n;
    let rows = '';
    d.lines.forEach(l => {
      if (l.seam) rows += '<div class="seamline">여기가 이음새 — 소리로 들어보세요</div>';
      rows += '<div class="line' + (l.hook ? ' hook' : '') + '">'
            + '<div class="t" data-src="' + l.src + '">' + mmss(l.src) + '</div>'
            + '<div class="tx">' + esc(l.ko).replace(/\n/g, ' ') + '</div></div>';
    });
    el.innerHTML =
      '<div class="top"><input type="checkbox" data-n="' + d.n + '"'
      + (picked.has(d.n) ? ' checked' : '') + '>'
      + '<div><div class="name">' + esc(d.title) + '</div>'
      + '<div class="meta">' + d.duration.toFixed(0) + '초 · 원본 '
      + d.source_duration.toFixed(0) + '초에서 ' + d.removed_pct.toFixed(0) + '% 덜어냄'
      + (d.reordered ? ' · 후킹 재배치함' : ' · 원래 순서')
      + (d.dropped ? ' · 끝 ' + d.dropped + '줄 뺌' : '') + '</div></div></div>'
      + (d.why ? '<div class="why">' + esc(d.why) + '</div>' : '')
      + rows;
    list.appendChild(el);
  });
  stat();
}
function stat(){
  document.getElementById('stat').textContent =
    D.length + '개 후보 · ' + picked.size + '개 선택';
}
list.addEventListener('click', e => {
  const t = e.target.closest('.t');
  if (t) { v.currentTime = Math.max(0, parseFloat(t.dataset.src) - 0.4); v.play(); return; }
});
list.addEventListener('change', e => {
  const c = e.target.closest('input[type=checkbox]');
  if (!c) return;
  const n = +c.dataset.n;
  if (c.checked) picked.add(n); else picked.delete(n);
  document.getElementById('card' + n).classList.toggle('on', c.checked);
  stat();
});
function save(){
  const out = JSON.stringify({ selected: [...picked].sort((a,b)=>a-b) }, null, 1);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([out], {type:'application/json'}));
  a.download = 'selected.json'; a.click();
}
render();
</script></body></html>
"""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python shorts/review_page.py <work폴더명>")
        raise SystemExit(1)
    main(ROOT / "work" / sys.argv[1])
