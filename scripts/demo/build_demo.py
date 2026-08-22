#!/usr/bin/env python3
"""Compose the AI Venture Studio demo: real captured frames + caption bar.

Captions describe ONLY what is visible in each frame. Frames 04 and 05 both
show zero modules built, so neither is captioned as mid-build progress, and
no frame is captioned as a retry -- none shows one.
"""
import os
import pathlib
import textwrap

from PIL import Image, ImageDraw, ImageFont

# Paths are inputs, not constants: this used to live in /tmp with the
# author's home directory baked in, which is a script that runs exactly
# once on exactly one machine.
#   AVS_DEMO_SRC  screenshots captured from a real run (required)
#   AVS_DEMO_WORK where composed frames and ffmpeg lists are written
SRC = pathlib.Path(
    os.environ.get("AVS_DEMO_SRC", "~/Downloads/autoproduct-demo-frames")
).expanduser()
# Default under the user's own cache, not /tmp. `build_voiceover.py` reads
# what this writes, so the two must agree on a deterministic path and a
# `mkdtemp` cannot be used — which leaves the world-writable predictable name
# as the thing to remove, rather than the predictability itself.
WORK = pathlib.Path(
    os.environ.get("AVS_DEMO_WORK", "~/.cache/avs-demo")
).expanduser()
OUT = WORK / "frames"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1280, 900
BAR = 110
CANVAS = (W, H + BAR)

FONT_DIR = pathlib.Path("/System/Library/Fonts/Supplemental")
REG = FONT_DIR / "Arial.ttf"
BOLD = FONT_DIR / "Arial Bold.ttf"
MONO = pathlib.Path("/System/Library/Fonts/SFNSMono.ttf")


def font(path, size):
    for candidate in (path, REG):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


CAP = font(REG, 29)
TITLE = font(BOLD, 54)
SUB = font(REG, 30)
SMALL = font(REG, 25)
CODE = font(MONO, 30)

INK = (26, 26, 26)
MUTED = (120, 120, 120)
RULE = (223, 223, 223)
GREEN = (7, 193, 96)

# (source frame, seconds, caption)
SEQUENCE = [
    ("avs-frame-01-editor.png", 14,
     "Six questions, in your own words. No technical terms required."),
    ("avs-frame-02-fdr.png", 20,
     "One real answer: a shared task list for a two-person studio."),
    ("avs-frame-03-confirm.png", 26,
     "It restates your intent — and what it will NOT build — then waits "
     "for you to confirm."),
    ("avs-frame-04-building-1.png", 10,
     "Planning. No percentage bar, no invented ETA."),
    ("avs-frame-05-building-2.png", 16,
     "The plan becomes seven named modules. Progress updates live."),
    ("avs-frame-07-report.png", 26,
     "The report says “partly built” — and names the pieces that "
     "failed, in plain language."),
    ("avs-frame-08-product.png", 16,
     "The product that run produced, running in the browser."),
]

TITLE_SECS, END_SECS = 6, 8


def captioned(src: pathlib.Path, caption: str) -> Image.Image:
    canvas = Image.new("RGB", CANVAS, "white")
    canvas.paste(Image.open(src).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.line([(0, H), (W, H)], fill=RULE, width=1)
    lines = textwrap.wrap(caption, width=76)[:2]
    y = H + (BAR - len(lines) * 36) // 2
    for line in lines:
        draw.text((44, y), line, font=CAP, fill=INK)
        y += 36
    return canvas


def card(rows) -> Image.Image:
    canvas = Image.new("RGB", CANVAS, "white")
    draw = ImageDraw.Draw(canvas)
    heights = [f.getbbox("Ag")[3] + gap for _, f, _, gap in rows]
    y = (CANVAS[1] - sum(heights)) // 2
    for text, fnt, color, gap in rows:
        width = draw.textlength(text, font=fnt)
        draw.text(((W - width) / 2, y), text, font=fnt, fill=color)
        y += fnt.getbbox("Ag")[3] + gap
    return canvas


written = []

card([
    ("AI Venture Studio", TITLE, INK, 34),
    ("One plain-language document in.", SUB, INK, 8),
    ("A planned, built, tested, reviewed product out.", SUB, INK, 44),
    ("Real screens from real runs of the Studio — no mockups, no compositing.",
     SMALL, MUTED, 0),
]).save(OUT / "00-title.png")
written.append(("00-title.png", TITLE_SECS))

for name, secs, caption in SEQUENCE:
    src = SRC / name
    if not src.exists():
        raise SystemExit(f"missing frame: {src}")
    out_name = f"cap-{name}"
    captioned(src, caption).save(OUT / out_name)
    written.append((out_name, secs))

card([
    ("Run the review pipeline yourself — offline, no API key:", SUB, INK, 40),
    ("uvx --from ai-venture-studio avs replay --demo", CODE, GREEN, 40),
    ("github.com/melodygaoyifan/ai-venture-studio", SMALL, MUTED, 0),
]).save(OUT / "99-end.png")
written.append(("99-end.png", END_SECS))

concat = WORK / "concat.txt"
lines = []
for name, secs in written:
    lines.append(f"file '{OUT / name}'")
    lines.append(f"duration {secs}")
lines.append(f"file '{OUT / written[-1][0]}'")  # last frame needs a repeat
concat.write_text("\n".join(lines) + "\n", encoding="utf-8")

total = sum(secs for _, secs in written)
print(f"frames: {len(written)}  total: {total}s ({total // 60}m{total % 60:02d}s)")
print(f"concat list: {concat}")
