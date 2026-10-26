#!/usr/bin/env python3
"""Auto Reel Editor: side-by-side stills and video for reviewing a new edit against a reference.

Usage:
  compare.py stills <reference.mp4> <new.mp4> <out.png> t1[:label] t2[:label] ...
  compare.py video  <reference.mp4> <new.mp4> <out.mp4>
"""
import subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def grab(path, t):
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
                          "-frames:v", "1", "-vf", "scale=1080:1920,format=rgb24", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return Image.fromarray(np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3))


def font(px):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",):
        try:
            return ImageFont.truetype(f, px)
        except OSError:
            pass
    return ImageFont.load_default()


def stills(ref, new, out, specs):
    tw, th = 405, 720
    pairs = []
    for s in specs:
        t, _, label = s.partition(":")
        pairs.append((float(t), label or f"{float(t):.2f}s"))
    cols = 2
    rows = (len(pairs) + cols - 1) // cols
    gap, head = 24, 54
    W = cols * (2 * tw + gap) + (cols - 1) * gap * 2
    H = rows * (th + head + gap)
    sheet = Image.new("RGB", (W, H), (14, 14, 16))
    d = ImageDraw.Draw(sheet)
    f1, f2 = font(22), font(18)
    for k, (t, label) in enumerate(pairs):
        x = (k % cols) * (2 * tw + gap + gap * 2)
        y = (k // cols) * (th + head + gap)
        d.text((x, y + 6), f"{label}  @ {t:.2f}s", fill=(240, 240, 240), font=f1)
        d.text((x, y + 32), "posted", fill=(150, 150, 155), font=f2)
        d.text((x + tw + gap, y + 32), "Auto Reel Editor", fill=(150, 150, 155), font=f2)
        sheet.paste(grab(ref, t).resize((tw, th), Image.LANCZOS), (x, y + head))
        sheet.paste(grab(new, t).resize((tw, th), Image.LANCZOS), (x + tw + gap, y + head))
    sheet.save(out, optimize=True)


def video(ref, new, out):
    fc = ("[0:v]scale=540:960,setsar=1,drawtext=text='posted':x=16:y=16:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.5[a];"
          "[1:v]scale=540:960,setsar=1,drawtext=text='Auto Reel Editor':x=16:y=16:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.5[b];"
          "[a][b]hstack=inputs=2[v]")
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", ref, "-i", new,
                    "-filter_complex", fc, "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "22", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out], check=True)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "stills":
        stills(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
    else:
        video(sys.argv[2], sys.argv[3], sys.argv[4])
