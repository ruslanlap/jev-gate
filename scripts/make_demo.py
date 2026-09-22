#!/usr/bin/env python3
"""Render docs/demo.gif from the REAL captured CLI run (Pillow, no recorder).

Captures actual `jev-gate <PR>` output via subprocess, asserts the verdict
signal appears, then renders a dark-theme terminal animation from those lines.
Re-run any time the CLI changes — the gif regenerates itself.
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
PR = "https://github.com/microsoft/winget-pkgs/pull/431811"

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
BG, FG, ACCENT, GREEN, DIM = (13, 17, 23), (230, 237, 243), (88, 166, 255), (63, 185, 80), (139, 148, 158)
FONT_SIZE, LINE_H, PAD = 15, 22, 18
WIDTH = 880

# ponytail: fixed palette no terminal emulator — replace with vhs if motion matters later


def capture():
    r = subprocess.run([sys.executable, str(REPO / "jev_gate.py"), PR],
                       capture_output=True, text=True, timeout=120)
    out = r.stdout.strip().splitlines()
    assert any("certificate" in ln for ln in out), "verdict signal missing from real output"
    return out


def render_frames(lines):
    """One frame per output line — looks like the command typing itself out."""
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    height = PAD * 2 + LINE_H * (len(lines) + 1)
    frames = []
    for n in range(1, len(lines) + 1):
        img = Image.new("RGB", (WIDTH, height), BG)
        d = ImageDraw.Draw(img)
        # title bar
        d.rounded_rectangle([PAD, PAD, WIDTH - PAD, PAD + LINE_H], 6, fill=(33, 38, 45))
        d.text((PAD + 10, PAD + 3), "jev-gate — typed decision model for PRs", font=font, fill=DIM)
        y = PAD + LINE_H + 8
        for ln in lines[:n]:
            color = FG
            if "NOT READY" in ln or "certificate" in ln:
                color = ACCENT
            elif "$" in ln or "cost" in ln:
                color = GREEN if "cost" in ln else FG
            d.text((PAD + 10, y), ln, font=font, fill=color)
            y += LINE_H
        frames.append(img.quantize(colors=32))
    return frames


def main():
    lines = ["$ jev-gate " + PR] + capture()
    frames = render_frames(lines)
    out = REPO / "docs" / "demo.gif"
    # hold the final verdict frame long enough to read
    durations = [110] * (len(frames) - 1) + [2500]
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)

    check = Image.open(out)
    assert getattr(check, "n_frames", 1) > 1, "demo.gif must be animated"
    print(f"{out} ({out.stat().st_size} bytes, {frames[0].size[0]}x{frames[0].size[1]}, {check.n_frames} frames)")


if __name__ == "__main__":
    main()
