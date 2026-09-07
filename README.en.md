# subtrans

Burns Korean subtitles into English videos. Give it a YouTube link, get an mp4 with
subtitles rendered into the frame.

```
python run.py "https://www.youtube.com/watch?v=..."          # fetch -> transcribe -> translate -> review
python run.py "https://www.youtube.com/watch?v=..." --burn   # after review, render
```

> **Only use this on videos you own or have permission to use.**
> Redistributing someone else's video may infringe copyright.

[한국어 README](README.md)

---

## The hard part

Automatic subtitling has to satisfy two things at once:

1. Subtitles must land exactly when the words are spoken.
2. Broken, spoken English must be translated for **intent**, not word-for-word.

These need different tools, and **mixing them breaks both.**

Handing a whole video to an LLM and asking for a subtitle file is convenient, but
timestamps drift on long audio ([reported case](https://github.com/jianchang512/pyvideotrans/issues/624)).
So here, **the ASR engine owns time; the LLM only translates.**

---

## Three design decisions

### 1. Subtitle timing is observed, not computed

`faster-whisper` with `word_timestamps=True` gives the start and end of every word.
Each cue's timing is pinned to those observations:

```
start = start of the first word in the cue
end   = end of the last word in the cue
```

No estimation is involved, so there is nothing to drift.

### 2. Cue boundaries follow rules, not vibes

Candidates are scored: sentence punctuation > long silence > conjunction > length cap.
**A boundary never crosses the end of a sentence** — that is also where speakers change,
so crossing it puts two people's lines in one subtitle.

### 3. The frame grows the canvas instead of shrinking the video

Putting a video inside a frame usually means scaling it down, turning 1080p into an
effective 918p. Instead the canvas grows:

```
canvas  2560 x 1440
video   2176 x 1224   <- source 1920x1080, scaled UP, 85% of width
```

There is a bonus: YouTube assigns a better codec and higher bitrate to 1440p uploads,
so **even viewers watching at 1080p see a sharper picture.** The frame improves quality
rather than costing it.

---

## Engineering log

### Defects found by measurement

Everything looked fine by eye. These only surfaced once measured.

| Defect | Before | After |
|---|---|---|
| Two speakers' lines merged into one cue | 19 cues | **0** |
| Minimum-duration logic overran the next cue | 2 overlaps | **0** |
| Missing line breaks pushed text off screen | 21 lines | **0** |
| Break before a dependent noun (bad Korean typography) | many | **1** |
| Quality measurement time (20 min video) | 1 hr+ | **~5 min** |

### The measurement was wrong — how that surfaced

The longest investigation, and the one whose conclusion reversed.

**1. Symptom** — output measured VMAF 92, below the 95 target. One segment scored 85.

**2. First attempt** — increase quality (CRF 17 to 14).
Result: **+0.38 points**, file 46% larger (236MB to 345MB). The lever did nothing.

**3. Splitting causes** — a dead lever means the wrong cause. Each was isolated:

| Measured | VMAF | Verdict |
|---|---|---|
| Upscale/downscale round trip, no compression | 98.4 | not resampling |
| Frame without upscaling | 98.7 | upscale costs 0.3 |
| Excluding the subtitle region | 85.4 | not subtitles |

All three hypotheses died. Loss was supposedly from compression, yet reducing compression
did nothing — a contradiction.

**4. Remaining hypothesis** — what if the problem is the *measurement*, not the output?

Comparison used `-ss` to seek both files to the same timestamp. The two files have
different keyframe layouts, so they can land **one frame apart**, and VMAF treats
misalignment as severe damage. That also explains why only high-motion segments collapsed.

**5. Verification** — decode from the start instead of seeking, compare the same segment:

```
with -ss seek:        85.1
decoded from start:   97.4    <- same segment, same file
```

The ruler was broken. Replaced seeking with a `select` filter and reverted CRF to 17.
**Final measured score: 97.12.**

> Takeaway: when a lever produces no response, suspect the **ruler**, not the lever.
> Otherwise this would have shipped a 46% larger file and a "quality failed" conclusion.

### Cutting translation cost 18x

The first version called Claude Code headless (`claude -p`). Measured:

```
actual translation prompt     ~ 2,000 tokens
actual input per call         ~44,000 tokens   <- 22x
```

The gap is Claude Code loading its system prompt, MCP tool definitions and skill list on
every invocation. `--strict-mcp-config` only removed 2,300 tokens.

Two fixes:

1. **Call the API directly** — the fixed overhead disappears (44,000 to **2,389 tokens**)
2. **Batch 30 to 60 cues** — since overhead is fixed per call, doubling the batch adds only
   about 2,000 input tokens

| | via Claude Code | direct Gemini |
|---|---|---|
| Input tokens (5 min of video) | ~220,000 | **~7,000** |
| Wall time | 90 s | **79 s** |
| Subtitle gate warnings | 18 | **4** |
| Cost | subscription usage | **free tier** |

Quality did not drop; line-break rules were actually followed more consistently.

### Korean subtitle typography

Korean subtitles carry constraints English does not:

- 16 characters per line, 2 lines maximum
- The character budget is derived from how long the cue stays on screen and **passed into
  the prompt** (a 2.4 s cue allows 12 characters) — telling the translator the size of the page
- Line breaks must not separate a dependent noun from the word it attaches to

---

## Pipeline

Every stage writes to `work/<video id>/`, so a run resumes where it stopped.

| | Stage | Tool | Output |
|---|---|---|---|
| 1 | Fetch | yt-dlp | `video.mkv`, `audio.wav` |
| 2 | Transcribe | faster-whisper | `words.json` |
| 3 | Segment | rule-based | `en.srt`, `cues.json` |
| 4 | Translate | Gemini (or Claude) | `ko.srt` |
| 5 | Gate | custom checks | pass / reject list |
| 6 | Review | browser page | `review.html` |
| 7 | Frame + burn | ffmpeg | `out.mp4` |

**Stage 5** catches what a human would miss: cue count drift (the LLM merging or dropping
lines), reading speed too fast, overlapping timings, untranslated English left behind.

**Stage 6** is a single HTML file, no server. Clicking a cue's timestamp seeks the video
there, so sync and mistranslation are checked in one place. Only text is editable;
timecodes are never touched.

---

## Performance (measured, i5-12400 6-core, no GPU)

| Stage | 5 min clip | 20 min video |
|---|---|---|
| Transcribe | 2.8 min (0.57x realtime) | ~11 min |
| Translate | 79 s | ~5 min |
| Burn (1440p) | 2.7 min | ~11 min |
| Quality check | 2 min | ~5 min |

CPU only. First run downloads a 1.5GB ASR model.

## Setup

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv faster-whisper yt-dlp
cp .env.example .env        # then add a Gemini key
```

ffmpeg must be on PATH. Get a free Gemini key at
[Google AI Studio](https://aistudio.google.com/apikey). Without a key it falls back to
Claude Code if installed.

## Configuration

| What | Where |
|---|---|
| Subtitle size / position / colour, frame margins | `style.json` |
| Proper nouns (names, teams) | `glossary.json` |
| Translation tone and rules | `prompts/translate.md` |
| Cue length, characters per line, engine choice | `config.json` |

Mistranslated a name? Add it to `glossary.json` and re-run translation only —
transcription is reused.

```bash
python run.py "url" --redo translate
```

## License

MIT
