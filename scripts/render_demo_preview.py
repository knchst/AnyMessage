#!/usr/bin/env python3
"""Render an edited replay of actual synthetic MCP output for the README."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".github/assets/demo.gif"))
    parser.add_argument("--font", default="/System/Library/Fonts/Menlo.ttc")
    parser.add_argument("--video", action="store_true", help="Also create an MP4 using ffmpeg")
    args = parser.parse_args()
    result = subprocess.run([sys.executable, "-m", "anymessage.server", "demo"], check=True, capture_output=True, text=True, timeout=30)
    lines = result.stdout.splitlines()
    proposal = lines.index(next(line for line in lines if line.startswith("message/message_0.db:2 |")))
    attachment = lines.index("Verified local attachment from message/message_0.db:4:")
    missing = lines.index(next(line for line in lines if line.startswith("Unavailable drawing:")))
    search = lines.index(next(line for line in lines if line.startswith("Bounded search for")))
    scenes = [
        ("Ask your chats. Get answers with sources.", ["$ anymessage demo", "", *lines[:3], "", "What changed in Atlas?", "What still needs confirmation?"], 5000),
        ("Separate a proposal from an approval.", lines[proposal:proposal + 4], 7000),
        ("Read the specification attached to that conversation.", lines[attachment:missing - 1], 8000),
        ("See what the evidence cannot establish.", [*lines[missing:missing + 1], "", *lines[search:search + 2]], 7000),
        ("Try it with your own AI.", ["$ uvx --from git+https://github.com/knchst/AnyMessage anymessage demo --mcp", "", "Connect as a separate anymessage-demo server.", "", "Ask for approved changes, proposals, and open questions.", "Cite the sender, timestamp, and message ID."], 3000),
    ]
    title_font = ImageFont.truetype(args.font, 27)
    body_font = ImageFont.truetype(args.font, 22)
    small_font = ImageFont.truetype(args.font, 16)
    frames = []
    durations = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for index, (title, content, duration) in enumerate(scenes, 1):
        frame = Image.new("RGB", (1200, 720), "#101820")
        draw = ImageDraw.Draw(frame)
        draw.rounded_rectangle((28, 24, 1172, 690), radius=18, fill="#17242d", outline="#31454f", width=2)
        draw.text((56, 48), "AnyMessage", font=title_font, fill="#69dfb4")
        draw.text((860, 55), f"Fictional data  {index}/5", font=small_font, fill="#a1b4be")
        draw.text((56, 119), title, font=title_font, fill="#f0f6f8")
        y = 190
        for line in content:
            for wrapped in textwrap.wrap(line, width=79, replace_whitespace=False) or [""]:
                draw.text((56, y), wrapped, font=body_font, fill="#d3e2e8")
                y += 32
        if y > 625:
            raise ValueError("Preview text exceeds the frame. Shorten the scene before publishing.")
        draw.text((56, 650), "Replay of actual MCP output. Timing edited. No AI model called in this replay.", font=small_font, fill="#a1b4be")
        frames.append(frame)
        durations.append(duration)
    frames[0].save(args.output, save_all=True, append_images=frames[1:], duration=durations, loop=0)
    if args.video:
        subprocess.run(["ffmpeg", "-y", "-i", str(args.output), "-t", "30", "-r", "10", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(args.output.with_suffix(".mp4"))], check=True, capture_output=True)
    print(f"Rendered {sum(durations) / 1000:g}s replay to {args.output}.")


if __name__ == "__main__":
    main()
