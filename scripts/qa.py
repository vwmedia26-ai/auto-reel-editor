#!/usr/bin/env python3
"""Auto Reel Editor: objective QA for a rendered reel.

Usage: qa.py <reel.json> [output.mp4]
Checks (all must pass):
  safe zones  every frame, two ways: element boxes reported by the renderer (mid-animation
              included) AND the actual pixels each overlay changed in that frame
  a/v sync    stream starts and lengths match, and the final audio lines up with the master
              audio by cross-correlation (lag must be 0 ms)
  loudness    -14 LUFS +/- 1 integrated, true peak <= -1 dBTP
  captions    each chunk on screen within 50 ms of its first word; no chunk ends on a weak word
  text        no em-dash anywhere on screen
"""
import json, os, re, subprocess, sys
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import captions as CAP
from render import BAND_Y, CAP_Y, SAFE, FPS

DIFF = 10        # a pixel counts as "changed by an overlay" above this difference (0-255)


def violates(x0, y0, x1, y1):
    v = []
    if y0 < SAFE["top"]:
        v.append("top")
    if y1 > SAFE["bottom"]:
        v.append("bottom")
    if x1 > SAFE["right_x"] and y1 > SAFE["right_from_y"]:
        v.append("right rail")
    return v


def pixel_extent(changed, y_offset):
    ys, xs = np.nonzero(changed)
    if not len(ys):
        return None
    return int(xs.min()), int(ys.min()) + y_offset, int(xs.max()) + 1, int(ys.max()) + 1 + y_offset


def mono_env(path, rate=1000):
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path, "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    a = np.abs(np.frombuffer(raw, np.float32))
    k = 16000 // rate
    a = a[: len(a) // k * k].reshape(-1, k).mean(axis=1)
    return (a - a.mean()) / (a.std() + 1e-9)


def main():
    spec_path = os.path.abspath(sys.argv[1])
    base = os.path.dirname(spec_path)
    build = os.path.join(base, "build")
    out = sys.argv[2] if len(sys.argv) > 2 else json.load(open(os.path.join(build, "last.json")))["output"]
    plan = json.load(open(os.path.join(build, "plan.json")))
    boxes = json.load(open(os.path.join(build, "boxes.json")))
    spec = json.load(open(spec_path))
    master = os.path.join(base, spec["master"])
    res = {"output": os.path.basename(out)}

    # ---------------- safe zones, geometry
    geo = []
    for f, bl in boxes["popup_boxes"].items():
        for b in bl:
            v = violates(b["x0"], b["y0"], b["x1"], b["y1"])
            if v:
                geo.append({"frame": int(f), "id": b["id"], "zones": v})
    for k, b in boxes["caption_boxes"].items():
        v = violates(b["x0"], b["y0"], b["x1"], b["y1"])
        if v:
            geo.append({"caption": int(k), "zones": v})

    # ---------------- safe zones, pixels (every frame)
    pix = []
    band, band_bg = os.path.join(build, "band"), os.path.join(build, "band_bg")
    frames = sorted(os.listdir(band))
    checked = 0
    for name in frames:
        a = os.path.join(band, name)
        b = os.path.join(band_bg, name)
        if os.stat(a).st_ino == os.stat(b).st_ino:      # untouched frame (hard link)
            continue
        A = np.asarray(Image.open(a).convert("RGB"), np.int16)
        B = np.asarray(Image.open(b).convert("RGB"), np.int16)
        ext = pixel_extent(np.abs(A - B).max(axis=2) > DIFF, BAND_Y)
        checked += 1
        if ext and violates(*ext):
            pix.append({"frame": int(name[:5]), "extent": ext, "zones": violates(*ext)})
    capdir = os.path.join(build, "cap")
    for name in sorted(os.listdir(capdir)):
        alpha = np.asarray(Image.open(os.path.join(capdir, name)).convert("RGBA"))[:, :, 3]
        ext = pixel_extent(alpha > 8, CAP_Y)
        if ext and violates(*ext):
            pix.append({"caption_png": name, "extent": ext, "zones": violates(*ext)})
    res["safe_zone"] = {"pass": not geo and not pix, "violations": len(geo) + len(pix),
                        "popup_frames_pixel_checked": checked, "caption_images_checked": len(os.listdir(capdir)) - 1,
                        "detail": (geo + pix)[:20]}

    # ---------------- A/V sync
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time,duration",
                                    "-of", "json", out], capture_output=True, text=True).stdout)["streams"]
    v = next(s for s in pr if s["codec_type"] == "video")
    a = next(s for s in pr if s["codec_type"] == "audio")
    e1, e2 = mono_env(master), mono_env(out)
    n = min(len(e1), len(e2))
    win = 200                                             # search +/- 200 ms
    lags = range(-win, win + 1)
    score = [np.dot(e1[max(0, L):n + min(0, L)], e2[max(0, -L):n - max(0, L)]) for L in lags]
    lag = list(lags)[int(np.argmax(score))]
    res["av_sync"] = {"pass": lag == 0 and abs(float(v["start_time"]) - float(a["start_time"])) < 0.001
                      and abs(float(v["duration"]) - float(a["duration"])) <= 1.0 / FPS + 0.025,
                      "audio_lag_ms": lag, "video_start": float(v["start_time"]), "audio_start": float(a["start_time"]),
                      "video_dur": float(v["duration"]), "audio_dur": float(a["duration"])}

    # ---------------- loudness
    log = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", out, "-vn", "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    summ = log[log.rindex("Summary:"):]
    I = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summ).group(1))
    TP = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summ).group(1))
    res["loudness"] = {"pass": abs(I + 14) <= 1.0 and TP <= -1.0, "integrated_lufs": I, "true_peak_dbtp": TP}

    # ---------------- captions
    chunks = plan["chunks"]
    early_late = [round((c["on"] / FPS - c["s"]) * 1000) for c in chunks]
    weak = []
    words = json.load(open(os.path.join(build, "words.fixed.json")))
    toks = [CAP.norm(w["w"]) for w in words]
    for k, c in enumerate(chunks):
        last = c["last"]
        nxt = chunks[k + 1]["text"].split()[0].lower() if k + 1 < len(chunks) else ""
        elided = last == "to" and CAP.norm(nxt) in CAP.PREPS
        closed = "j" in c and bool(re.search(r"[.,!?;:]$", words[c["j"] - 1]["w"]))
        if last in CAP.WEAK_END and not elided and not closed:
            weak.append(c["text"])
    res["captions"] = {"pass": max(abs(x) for x in early_late) <= 50 and not weak,
                       "max_offset_ms": max(early_late, key=abs), "chunks": len(chunks), "weak_endings": weak}

    # ---------------- on-screen text
    texts = [c["text"] for c in chunks]
    for p in plan["popups"]:
        for key in ("a", "b", "title", "sub", "line", "body"):
            if key in p:
                texts.append(str(p[key]))
        texts += [it["label"] for it in p.get("items", [])]
    dash = [t for t in texts if "\u2014" in t]
    res["em_dash"] = {"pass": not dash, "found": dash}

    res["all_pass"] = all(res[k]["pass"] for k in ("safe_zone", "av_sync", "loudness", "captions", "em_dash"))
    json.dump(res, open(os.path.join(build, "qa.json"), "w"), indent=1)
    for k in ("safe_zone", "av_sync", "loudness", "captions", "em_dash"):
        r = {x: y for x, y in res[k].items() if x != "detail"}
        print(("PASS " if res[k]["pass"] else "FAIL ") + k, r)
    print("ALL PASS" if res["all_pass"] else "SOME CHECKS FAILED")


if __name__ == "__main__":
    main()
