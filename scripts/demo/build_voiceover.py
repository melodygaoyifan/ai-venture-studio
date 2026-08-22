#!/usr/bin/env python3
"""Narration-driven re-timing of the demo, plus a macOS TTS scratch track.

Timing is derived from the narration, not guessed: each segment is held for
at least as long as its line takes to speak (plus lead-in and tail), and
never shorter than the silent cut's duration. Hard cap enforced at 180s.

Outputs:
  $AVS_DEMO_WORK/narration.txt   script with real timecodes, to record against
  $AVS_DEMO_WORK/audio/full.wav  assembled scratch narration
  $AVS_DEMO_OUT                  video + scratch narration
"""
import json
import os
import pathlib
import subprocess
import sys

# Must match build_demo.py's default exactly — this reads what that writes.
WORK = pathlib.Path(
    os.environ.get("AVS_DEMO_WORK", "~/.cache/avs-demo")
).expanduser()
FRAMES = WORK / "frames"
AUDIO = WORK / "audio"
AUDIO.mkdir(parents=True, exist_ok=True)
VIDEO_OUT = pathlib.Path(
    os.environ.get("AVS_DEMO_OUT", "~/Downloads/avs-yc-demo-vo.mp4")
).expanduser()

VOICE = "Samantha"
RATE = 172          # words per minute for `say`
LEAD = 0.6          # silence before the line starts
TAIL = 0.9          # silence after it ends
HARD_CAP = 180.0    # the 3:00 ceiling

# (frame file, minimum seconds from the silent cut, narration)
SEGMENTS = [
    ("00-title.png", 6, "AI Venture Studio takes one plain-language document, "
     "and returns a planned, built, tested, and reviewed product."),
    ("cap-avs-frame-01-editor.png", 14, "Everything starts here. Six questions, "
     "answered in your own words. No technical terms, no spec, no diagrams. And "
     "if your answers are unclear, it asks you follow-up questions instead of "
     "guessing."),
    ("cap-avs-frame-02-fdr.png", 20, "This is one real answer: a shared task list "
     "for a two-person studio that keeps losing track of work in chat messages. "
     "Notice question four, what it should not build. Scope you exclude is "
     "enforced, not just advice."),
    ("cap-avs-frame-03-confirm.png", 26, "Before any code is written, it restates "
     "your intent: what will be built, what is deliberately out of this version, "
     "and how you will know it worked, with a real deadline attached. It also sets "
     "expectations honestly: roughly four to seven modules, ten to thirty minutes "
     "each. Nothing is built until you confirm."),
    ("cap-avs-frame-04-building-1.png", 10, "Then it plans. No progress bar, and no "
     "estimated finish time, because it does not yet know whether the next attempt "
     "will be the last one."),
    ("cap-avs-frame-05-building-2.png", 16, "The plan becomes seven named modules. "
     "Each one gets a specification, an implementation, its own tests, and an "
     "independent code review. Progress updates live as each finishes."),
    ("cap-avs-frame-07-report.png", 26, "And here is the part most demos leave out. "
     "This report says the product is partly built. Some pieces work. One needs "
     "changes before you rely on it. One could not be completed at all. Each is "
     "named, in plain language, with every failed attempt preserved so you can "
     "retry just those. A demo you can only pass is marketing. This one can fail "
     "in public."),
    ("cap-avs-frame-08-product.png", 16, "And this is the product that run actually "
     "produced. Running in the browser, task list working. Not a mockup, and not a "
     "rendering."),
    ("99-end.png", 8, "You can run the review pipeline yourself, offline, with no "
     "API key."),
]


def run(argv, **kw):
    return subprocess.run(argv, check=True, capture_output=True, text=True,
                          timeout=300, **kw)


def duration(path: pathlib.Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "json", str(path)]).stdout
    return float(json.loads(out)["format"]["duration"])


plan = []
for index, (frame, floor_secs, line) in enumerate(SEGMENTS):
    if not (FRAMES / frame).exists():
        sys.exit(f"missing composed frame: {FRAMES / frame}")
    raw = AUDIO / f"{index:02d}-raw.aiff"
    run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(raw), line])
    spoken = duration(raw)
    held = max(float(floor_secs), spoken + LEAD + TAIL)
    plan.append({"frame": frame, "raw": raw, "line": line,
                 "spoken": spoken, "held": held})

total = sum(item["held"] for item in plan)
if total > HARD_CAP:
    sys.exit(f"narration needs {total:.1f}s, over the {HARD_CAP:.0f}s cap — "
             "shorten the longest lines")

# Per-segment audio padded to exactly the held duration, so audio and video
# stay aligned without a global offset accumulating across nine cuts.
for item in plan:
    padded = item["raw"].with_name(item["raw"].stem.replace("-raw", "-pad") + ".wav")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(item["raw"]),
         "-af", f"adelay={int(LEAD * 1000)}|{int(LEAD * 1000)},apad",
         "-t", f"{item['held']:.3f}", "-ar", "44100", "-ac", "2",
         str(padded)])
    item["padded"] = padded

audio_list = AUDIO / "concat.txt"
audio_list.write_text(
    "\n".join(f"file '{item['padded']}'" for item in plan) + "\n", encoding="utf-8")
run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat",
     "-safe", "0", "-i", str(audio_list), "-c", "copy", str(AUDIO / "full.wav")])

video_list = WORK / "concat-vo.txt"
lines = []
for item in plan:
    lines.append(f"file '{FRAMES / item['frame']}'")
    lines.append(f"duration {item['held']:.3f}")
lines.append(f"file '{FRAMES / plan[-1]['frame']}'")
video_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

fade_out = max(0.0, total - 0.8)
run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
     "-f", "concat", "-safe", "0", "-i", str(video_list),
     "-i", str(AUDIO / "full.wav"),
     "-t", f"{total:.3f}",
     "-vf", f"fps=30,format=yuv420p,fade=t=in:st=0:d=0.5,"
            f"fade=t=out:st={fade_out:.2f}:d=0.8",
     "-c:v", "libx264", "-preset", "slow", "-crf", "21",
     "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
     str(VIDEO_OUT)])

# The script a human records against, with the timings that actually shipped.
clock = 0.0
rows = []
for item in plan:
    minutes, seconds = divmod(int(clock), 60)
    rows.append(f"[{minutes}:{seconds:02d}]  ({item['held']:.1f}s on screen, "
                f"{item['spoken']:.1f}s of speech)\n{item['line']}\n")
    clock += item["held"]
(WORK / "narration.txt").write_text(
    f"AI Venture Studio — demo narration\n"
    f"Total runtime {total:.1f}s ({int(total // 60)}m{int(total % 60):02d}s). "
    f"Read at a normal pace; each block has ~1.5s of padding.\n\n"
    + "\n".join(rows), encoding="utf-8")

print(f"segments: {len(plan)}")
print(f"speech:   {sum(i['spoken'] for i in plan):.1f}s")
print(f"runtime:  {total:.1f}s ({int(total // 60)}m{int(total % 60):02d}s) "
      f"of {HARD_CAP:.0f}s cap")
for item in plan:
    print(f"  {item['frame']:<34} hold {item['held']:5.1f}s  "
          f"speech {item['spoken']:5.1f}s")
