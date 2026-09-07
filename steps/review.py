"""⑥ 검수 — 브라우저에서 자막을 눈으로 확인하고 문구만 고치는 화면을 만든다.

타임코드는 건드리지 않는다. 자막 줄의 시각을 누르면 영상이 그 시점으로 점프하므로
싱크와 오역을 한 자리에서 볼 수 있다. 저장하면 ko.edited.srt 를 내려받는다.

브라우저가 원본 mkv(VP9)를 못 여는 경우가 많아 검수용 저용량 mp4를 따로 만든다.
탐색이 빨라서 검수 자체도 훨씬 수월하다.
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
from srtlib import read_srt  # noqa: E402


def make_preview(video: Path, out: Path) -> Path:
    if out.exists():
        print(f"[review] 미리보기 영상 재사용: {out.name}")
        return out
    print("[review] 검수용 미리보기 영상을 만듭니다 (저화질, 탐색용)...")
    cp = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(video),
         "-vf", "scale=640:-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
         "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if cp.returncode != 0:
        raise RuntimeError(f"미리보기 생성 실패: {cp.stderr[-500:]}")
    return out


def main(workdir: Path, ko_name: str = "ko.srt") -> Path:
    meta = json.loads((workdir / "fetch.json").read_text(encoding="utf-8"))
    ko = read_srt(workdir / ko_name)
    en = read_srt(workdir / "en.srt") if (workdir / "en.srt").exists() else []

    preview = make_preview(Path(meta["video"]), workdir / "preview.mp4")

    rows = []
    for i, c in enumerate(ko):
        rows.append({
            "s": round(c["start"], 3),
            "e": round(c["end"], 3),
            "ko": c["text"],
            "en": en[i]["text"] if i < len(en) else "",
        })

    html = (HTML
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__VIDEO__", preview.name)
            .replace("__TITLE__", workdir.name))
    out = workdir / "review.html"
    out.write_text(html, encoding="utf-8")
    print(f"[review] 완료 → {out}")
    print("        브라우저로 이 파일을 열어 검수하세요.")
    print("        고친 뒤 [저장]을 누르면 ko.edited.srt 가 내려받아집니다.")
    print(f"        그 파일을 {workdir} 안에 넣고 ⑦을 돌리면 됩니다.")
    return out


HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>자막 검수 — __TITLE__</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#15161a;color:#e8e8ea;font:14px/1.6 "Malgun Gothic","맑은 고딕",system-ui,sans-serif;height:100vh;display:flex;flex-direction:column}
header{padding:10px 16px;background:#1d1f25;border-bottom:1px solid #2c2f38;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header b{font-size:15px}
.stat{color:#9aa0ad;font-size:13px}
button{background:#3a6df0;color:#fff;border:0;border-radius:6px;padding:8px 16px;font-size:14px;cursor:pointer;font-family:inherit}
button.ghost{background:#2c2f38}
button:hover{filter:brightness(1.15)}
main{flex:1;display:flex;min-height:0}
.left{width:46%;padding:16px;display:flex;flex-direction:column;gap:12px;border-right:1px solid #2c2f38}
video{width:100%;background:#000;border-radius:8px}
.sub-preview{background:#000;border-radius:8px;padding:14px;text-align:center;min-height:86px;display:flex;align-items:center;justify-content:center}
.sub-preview span{font-weight:700;font-size:23px;line-height:1.35;color:#fff;text-shadow:-2px -2px 0 #000,2px -2px 0 #000,-2px 2px 0 #000,2px 2px 0 #000;white-space:pre-line}
.right{flex:1;overflow-y:auto;padding:8px 12px}
.cue{display:grid;grid-template-columns:64px 1fr;gap:10px;padding:8px;border-radius:8px;border:1px solid transparent}
.cue:hover{background:#1c1e24}
.cue.on{background:#1e2740;border-color:#3a6df0}
.t{color:#7d8494;font-size:12px;cursor:pointer;padding-top:6px;font-variant-numeric:tabular-nums}
.t:hover{color:#8ab4ff;text-decoration:underline}
.en{color:#767c8a;font-size:12.5px;margin-bottom:4px}
textarea{width:100%;background:#22252c;color:#e8e8ea;border:1px solid #343845;border-radius:6px;padding:7px 9px;font:inherit;resize:vertical;min-height:38px;overflow:hidden}
textarea:focus{outline:0;border-color:#3a6df0}
.meta{font-size:11.5px;color:#6d7382;margin-top:3px}
.warn{color:#f0a63a}
.bad{color:#f0603a}
</style></head><body>
<header>
  <b>자막 검수</b>
  <span class="stat" id="stat"></span>
  <span style="flex:1"></span>
  <button class="ghost" onclick="jumpNext()">다음 경고로 ↓</button>
  <button onclick="save()">저장 (ko.edited.srt)</button>
</header>
<main>
  <div class="left">
    <video id="v" src="__VIDEO__" controls preload="metadata"></video>
    <div class="sub-preview"><span id="sp"></span></div>
    <div class="stat">자막 줄의 시각을 누르면 그 시점으로 이동합니다. 글자만 고치세요 — 타이밍은 자동입니다.</div>
  </div>
  <div class="right" id="list"></div>
</main>
<script>
const D = __DATA__;
const v = document.getElementById('v'), list = document.getElementById('list'), sp = document.getElementById('sp');
const SPEED = 7.0, MAXLINE = 16;

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function ts(x){const m=Math.floor(x/60),s=Math.floor(x%60);return String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')}
function chars(t){return t.replace(/\s/g,'').length}
function grade(i){
  const d=D[i], dur=d.e-d.s, c=chars(d.ko), lines=d.ko.split('\n');
  if(lines.length>2 || !d.ko.trim()) return 'bad';
  if(c/dur>SPEED || lines.some(l=>l.length>MAXLINE+4)) return 'warn';
  return '';
}
function render(){
  list.innerHTML='';
  D.forEach((d,i)=>{
    const el=document.createElement('div');
    el.className='cue'; el.id='c'+i;
    el.innerHTML='<div class="t" data-i="'+i+'">'+ts(d.s)+'<br>'+(d.e-d.s).toFixed(1)+'초</div>'+
      '<div><div class="en">'+esc(d.en)+'</div>'+
      '<textarea rows="1" data-i="'+i+'">'+esc(d.ko)+'</textarea>'+
      '<div class="meta" id="m'+i+'"></div></div>';
    list.appendChild(el);
    meta(i);
  });
  stat();
  document.querySelectorAll('textarea').forEach(fit);
}
function fit(t){t.style.height='auto';t.style.height=(t.scrollHeight+2)+'px'}
function meta(i){
  const d=D[i], dur=d.e-d.s, c=chars(d.ko), g=grade(i), lines=d.ko.split('\n');
  const el=document.getElementById('m'+i); if(!el) return;
  el.className='meta '+g;
  el.textContent = c+'자 / 초당 '+(c/dur).toFixed(1)+'자'
    + (lines.length>1 ? ' / '+lines.length+'줄' : '')
    + (g==='warn' ? '   읽기 빠듯' : '')
    + (g==='bad' ? '   확인 필요' : '');
}
function stat(){
  let w=0,b=0;
  D.forEach((_,i)=>{const g=grade(i); if(g==='warn')w++; if(g==='bad')b++});
  document.getElementById('stat').textContent = D.length+'장 · 경고 '+w+' · 확인필요 '+b;
}
list.addEventListener('click',e=>{
  const t=e.target.closest('.t'); if(!t) return;
  v.currentTime=Math.max(0,D[+t.dataset.i].s-0.3); v.play();
});
list.addEventListener('input',e=>{
  const ta=e.target.closest('textarea'); if(!ta) return;
  const i=+ta.dataset.i;
  D[i].ko=ta.value; meta(i); stat(); fit(ta);
});
let cur=-1;
v.addEventListener('timeupdate',()=>{
  const t=v.currentTime;
  let i=-1;
  for(let k=0;k<D.length;k++){ if(t>=D[k].s && t<=D[k].e){ i=k; break } }
  sp.textContent = i>=0 ? D[i].ko : '';
  if(i!==cur){
    if(cur>=0){const p=document.getElementById('c'+cur); if(p)p.classList.remove('on')}
    if(i>=0){const el=document.getElementById('c'+i); if(el){el.classList.add('on');
      el.scrollIntoView({block:'center',behavior:'smooth'})}}
    cur=i;
  }
});
function jumpNext(){
  let i=-1;
  for(let k=cur+1;k<D.length;k++){ if(grade(k)!==''){ i=k; break } }
  if(i<0){ for(let k=0;k<D.length;k++){ if(grade(k)!==''){ i=k; break } } }
  if(i<0){ alert('경고가 없습니다.'); return }
  document.getElementById('c'+i).scrollIntoView({block:'center'});
  v.currentTime=Math.max(0,D[i].s-0.3);
  cur=i;
}
function pad(n,w){return String(n).padStart(w,'0')}
function srtTs(x){
  const ms=Math.round(x*1000);
  return pad(Math.floor(ms/3600000),2)+':'+pad(Math.floor(ms/60000)%60,2)+':'
       + pad(Math.floor(ms/1000)%60,2)+','+pad(ms%1000,3);
}
function save(){
  const out=D.map((d,i)=>(i+1)+'\n'+srtTs(d.s)+' --> '+srtTs(d.e)+'\n'+d.ko.trim()+'\n').join('\n');
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([out],{type:'text/plain;charset=utf-8'}));
  a.download='ko.edited.srt'; a.click();
}
render();
</script></body></html>
"""

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python steps/review.py <work폴더명> [ko.srt]")
        raise SystemExit(1)
    main(ROOT / "work" / sys.argv[1],
         sys.argv[2] if len(sys.argv) > 2 else "ko.srt")
