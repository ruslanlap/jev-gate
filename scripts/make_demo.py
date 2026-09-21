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
    assert any("certificate" in l for l in out), "verdict signal missing from real output"
    return out


def render(lines):
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    height = PAD * 2 + LINE_H * (len(lines) + 1)
    img = Image.new("RGB", (WIDTH, height), BG)
    d = ImageDraw.Draw(img)
    # title bar
    d.rounded_rectangle([PAD, PAD, WIDTH - PAD, PAD + LINE_H], 6, fill=(33, 38, 45))
    d.text((PAD + 10, PAD + 3), "jev-gate — typed decision model for PRs", font=font, fill=DIM)
    y = PAD + LINE_H + 8
    for l in lines:
        color = FG
        if "NOT READY" in l or "certificate" in l:
            color = ACCENT
        elif "$" in l or "cost" in l:
            color = GREEN if "cost" in l else FG
        d.text((PAD + 10, y), l, font=font, fill=color)
        y += LINE_H
    return img


def main():
    lines = ["$ jev-gate " + PR] + capture()
    img = render(lines)
    out = REPO / "docs" / "demo.gif"
    out.parent.mkdir(exist_ok=True)
    img.save(out)  # single-frame gif: quiet, honest, tiny
    print(f"{out} ({out.stat().st_size} bytes, {img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
