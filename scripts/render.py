#!/usr/bin/env python3
"""Auto Reel Editor: render a reel from a prepped master, a word list and a reel spec.

Usage: render.py <reel.json> [--words words.json] [--stills-only] [--phone]

reel.json (paths relative to the spec file):
{
  "topic": "ai-thinking-levels",
  "master": "prepped.mov",                 # output of prep.py
  "fixes": {"replace": {"trend": "train"}, "groups": ["light thinking"]},
  "popups": [ {"type": "checklist", "at": "for everyday", "until": {"before": "where higher"}, ...} ]
}
Anchors: a number (seconds), a phrase string (start of its first spoken match), or
{"word": phrase, "n": 2, "after": 12.0, "edge": "start"|"end", "offset": 0.1} or
{"before": phrase, "pad": 0.04}  (= phrase start minus pad).
"""
import json, os, re, shutil, subprocess, sys, time
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import captions as CAP

FPS = 30
W, H = 1080, 1920
BAND_Y, BAND_H = 150, 340          # pop-ups live inside this strip; everything else is untouched video
CAP_Y, CAP_H = 955, 200            # caption strip, text centered on y = 1055
OUT_FADE = 0.18
# explicit BT.709 conversions both ways; untagged PNGs so the browser does no color management
TO_RGB = ("scale=in_color_matrix=bt709:in_range=tv:out_range=pc:flags=accurate_rnd+full_chroma_int+bitexact,"
          "format=rgb24,setparams=color_trc=unknown:color_primaries=unknown:colorspace=unknown")
TO_YUV = "scale=in_range=pc:out_range=tv:out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int+bitexact"
SAFE = {"top": 192, "bottom": 1536, "right_x": 900, "right_from_y": 960}


def sh(args, **kw):
    return subprocess.run(args, check=True, **kw)


def ffprobe_json(path, entries):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


# ---------------------------------------------------------------- anchors
class Words:
    def __init__(self, words):
        self.w = words
        self.t = [CAP.norm(x["w"]) for x in words]

    def find(self, phrase, n=1, after=None):
        pw = [CAP.norm(x) for x in phrase.split()]
        hits = [i for i in range(len(self.t) - len(pw) + 1)
                if self.t[i:i + len(pw)] == pw and (after is None or self.w[i]["s"] >= after)]
        if len(hits) < n:
            raise SystemExit(f"anchor not found in transcript: '{phrase}' (n={n}, after={after})")
        i = hits[n - 1]
        return i, i + len(pw) - 1

    def time(self, a):
        if isinstance(a, (int, float)):
            return float(a)
        if isinstance(a, str):
            a = {"word": a}
        if "before" in a:
            i, _ = self.find(a["before"], a.get("n", 1), a.get("after"))
            return self.w[i]["s"] - a.get("pad", 0.04)
        i, j = self.find(a["word"], a.get("n", 1), a.get("after"))
        t = self.w[j]["e"] if a.get("edge") == "end" else self.w[i]["s"]
        return t + a.get("offset", 0.0)


def resolve_popups(spec_popups, words):
    out = []
    for k, p in enumerate(spec_popups):
        q = {key: v for key, v in p.items()}
        q["id"] = p.get("id", f"{p['type']}{k + 1}")
        q["at"] = round(words.time(p["at"]), 3)
        q["until"] = round(words.time(p["until"]), 3)
        for key in ("tB", "t0", "t1"):
            if key in p:
                q[key] = round(words.time(p[key]), 3)
        if "items" in p:
            q["items"] = [{**it, "t": round(words.time(it.get("at", it["label"])), 3)} for it in p["items"]]
        if q["until"] <= q["at"]:
            raise SystemExit(f"pop-up {q['id']}: until ({q['until']}) is not after at ({q['at']})")
        out.append(q)
    out.sort(key=lambda q: q["at"])
    for a, b in zip(out, out[1:]):       # back-to-back cards hand over cleanly
        if a["until"] + OUT_FADE > b["at"]:
            a["until"] = round(b["at"] - OUT_FADE, 3)
    return out


# ---------------------------------------------------------------- head-room guard
def head_top(rgb, cx0=240, cx1=840, run=6, blob=90):
    """Top of the subject's head: first row below the 192 px safe line where the centre span
    holds a solid run of at least `blob` px that differ from that column's own background
    (sampled from the rows above), for `run` rows in a row. Per-column backgrounds with a
    threshold that loosens slowly with depth ignore sky gradients; the solid-run test ignores
    thin clutter like branches, wires and flagpoles. Returns None if nothing is found."""
    a = rgb.astype(np.int16)
    ref = np.median(a[100:190, cx0:cx1], axis=0)
    region = a[192:900, cx0:cx1]
    dist = np.abs(region - ref[None]).sum(axis=2)
    thr = 120 + np.arange(region.shape[0])[:, None] * 0.08
    mask = dist > thr
    # longest horizontal run of masked pixels per row
    m = mask.astype(np.int32)
    d = np.diff(np.pad(m, ((0, 0), (1, 1))), axis=1)
    longest = np.zeros(len(m), int)
    for r in range(len(m)):
        st, en = np.where(d[r] == 1)[0], np.where(d[r] == -1)[0]
        if len(st):
            longest[r] = (en - st).max()
    ok = longest >= blob
    for r in range(len(ok) - run):
        if ok[r:r + run].all():
            return 192 + r
    return None


def window_frames(path, t0, t1, fps=4):
    """Decode a time window once at a low frame rate (one pass, no per-sample seeking)."""
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{t0:.3f}",
                          "-t", f"{max(0.1, t1 - t0):.3f}", "-i", path, "-vf", f"fps={fps},format=rgb24",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    n = len(raw) // (W * H * 3)
    fr = np.frombuffer(raw[: n * W * H * 3], np.uint8).reshape(n, H, W, 3)
    return [(t0 + k / fps, fr[k]) for k in range(n)]


def headroom_report(master, popups, outdir):
    rep = []
    for p in popups:
        box_h = 76 if p.get("layout") == "slim" or p["type"] in ("flowArrow", "typingPrompt") else 146
        top = 204 if box_h == 76 else 200
        if p["type"] == "notification":
            box_h, top = 104, 204
        bottom = top + box_h
        tops, at = [], []
        for t, img in window_frames(master, p["at"], p["until"]):
            ht = head_top(img)
            if ht is not None:
                tops.append(ht)
                at.append(round(t, 2))
        worst = min(tops) if tops else None
        ok = worst is None or worst >= bottom + 6
        advice = None
        if not ok:
            over = bottom + 6 - worst
            if box_h == 146 and worst >= 204 + 76 + 6:
                advice = f"two-row card overlaps the top of the head by {over}px: switch to layout slim"
            elif over <= 60:
                advice = f"card overlaps hair tips by up to {over}px (face clear); fine unless it looks crowded"
            else:
                advice = f"card may cover the forehead or face (overlap {over}px): move or shorten this pop-up"
        rep.append({"id": p["id"], "card_bottom": bottom, "head_top_min": worst, "clear": ok,
                    "worst_at": at[tops.index(worst)] if tops else None, "advice": advice})
    return rep


def grab(path, t, crop=None):
    vf = ["format=rgb24"]
    if crop:
        vf.insert(0, crop)
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
                          "-frames:v", "1", "-vf", ",".join(vf), "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H if not crop else -1, W, 3)


# ---------------------------------------------------------------- browser rendering
def icons():
    d = os.path.join(KIT, "engine", "assets", "icons")
    return {os.path.splitext(f)[0]: open(os.path.join(d, f)).read() for f in os.listdir(d) if f.endswith(".svg")}


def launch():
    from playwright.sync_api import sync_playwright
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    pw = sync_playwright().start()
    args = ["--font-render-hinting=none", "--disable-lcd-text", "--force-color-profile=srgb"]
    if os.geteuid() == 0:
        args.append("--no-sandbox")     # only needed when the container runs as root
    b = pw.chromium.launch(args=args)
    pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    pg.goto("file://" + os.path.join(KIT, "engine", "overlay.html"))
    return pw, b, pg


def render_overlays(master, popups, chunks, work, nframes):
    band_bg = os.path.join(work, "band_bg")
    band = os.path.join(work, "band")
    capdir = os.path.join(work, "cap")
    for d in (band_bg, band, capdir):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
    sh(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", master, "-vf",
        f"crop={W}:{BAND_H}:0:{BAND_Y},{TO_RGB}", "-frames:v", str(nframes), "-start_number", "0",
        os.path.join(band_bg, "%05d.png")])
    nb = len(os.listdir(band_bg))

    pw, b, pg = launch()
    try:
        cap_text = " ".join(c["text"] for c in chunks)
        pg.evaluate("s => ARE.setup(s)", {"popups": popups, "icons": icons(), "captionText": cap_text})
        # captions: one transparent strip per chunk
        pg.evaluate("ARE.hideStage(true)")
        cap_boxes = {}
        for k, c in enumerate(chunks):
            r = pg.evaluate("t => ARE.caption(t)", c["text"])
            cap_boxes[k] = r
            pg.screenshot(path=os.path.join(capdir, f"c{k:03d}.png"), omit_background=True,
                          clip={"x": 0, "y": 0, "width": W, "height": CAP_H})
        pg.evaluate("ARE.caption('')")
        pg.evaluate("ARE.hideStage(false)")
        # move caption element back to its true place for box reporting
        cap_boxes = {k: {**v, "y0": v["y0"] + CAP_Y, "y1": v["y1"] + CAP_Y} for k, v in cap_boxes.items()}

        # pop-up band, every frame where any card is visible, rendered over the real video pixels
        windows = [(p["at"], p["until"] + OUT_FADE) for p in popups]
        boxes = {}
        rendered = 0
        t0 = time.time()
        for i in range(nb):
            t = i / FPS
            src = os.path.join(band_bg, f"{i:05d}.png")
            dst = os.path.join(band, f"{i:05d}.png")
            if not any(a <= t <= e for a, e in windows):
                os.link(src, dst)
                continue
            pg.evaluate("([u, y]) => ARE.setBackground(u, y)", ["file://" + src, BAND_Y])
            bx = pg.evaluate("t => ARE.frame(t)", t)
            if bx:
                boxes[i] = bx
            pg.screenshot(path=dst, clip={"x": 0, "y": BAND_Y, "width": W, "height": BAND_H})
            rendered += 1
        print(f"rendered {rendered} pop-up frames in {time.time() - t0:.0f}s")
    finally:
        b.close()
        pw.stop()

    # caption track: one RGBA frame per video frame (hard links, so it costs no disk)
    blank = os.path.join(capdir, "blank.png")
    Image.new("RGBA", (W, CAP_H), (0, 0, 0, 0)).save(blank)
    track = os.path.join(work, "captrack")
    shutil.rmtree(track, ignore_errors=True)
    os.makedirs(track)
    owner = [None] * nb
    for k, c in enumerate(chunks):
        for f in range(c["on"], min(c["off"], nb)):
            owner[f] = k
    for f in range(nb):
        src = blank if owner[f] is None else os.path.join(capdir, f"c{owner[f]:03d}.png")
        os.link(src, os.path.join(track, f"{f:05d}.png"))
    fw, cur = [], None
    for i in range(nb):
        act = any(a <= i / FPS <= e for a, e in windows)
        if act and cur is None:
            cur = i
        if not act and cur is not None:
            fw.append((cur, i - 1)); cur = None
    if cur is not None:
        fw.append((cur, nb - 1))
    return {"band": band, "band_bg": band_bg, "captrack": track, "nframes": nb, "windows": fw,
            "popup_boxes": boxes, "caption_boxes": cap_boxes, "caption_owner": owner}


# ---------------------------------------------------------------- audio
def loudnorm(master, out_wav, target=-14.0, tp=-1.0):
    first = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", master, "-vn", "-af",
                            f"loudnorm=I={target}:TP={tp - 0.5}:LRA=11:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    m = json.loads(first[first.rindex("{"):first.rindex("}") + 1])
    af = (f"loudnorm=I={target}:TP={tp - 0.5}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
          f"linear=true:print_format=json,aresample=48000")
    sh(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", master, "-vn", "-af", af,
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", out_wav])
    return float(m["input_i"])


# ---------------------------------------------------------------- composite + export
def export(master, ov, audio_wav, out_mp4, crf=17, preset="slow"):
    n = ov["nframes"]
    win = " + ".join(f"between(n,{a},{b})" for a, b in ov["windows"]) or "0"
    fc = (f"[0:v]trim=end_frame={n},setpts=PTS-STARTPTS[base];"
          f"[1:v]{TO_YUV},format=yuv444p[band];[2:v]{TO_YUV},format=yuva444p[cap];"
          f"[base][band]overlay=0:{BAND_Y}:eof_action=pass:format=yuv444:enable='{win}'[v1];"
          f"[v1][cap]overlay=0:{CAP_Y}:eof_action=pass:format=yuv444,format=yuv420p[v]")
    sh(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", master,
        "-framerate", str(FPS), "-start_number", "0", "-i", os.path.join(ov["band"], "%05d.png"),
        "-framerate", str(FPS), "-start_number", "0", "-i", os.path.join(ov["captrack"], "%05d.png"),
        "-i", audio_wav,
        "-filter_complex", fc, "-map", "[v]", "-map", "3:a",
        "-c:v", "libx264", "-profile:v", "high", "-preset", preset, "-tune", "film", "-crf", str(crf), "-r", str(FPS),
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{n / FPS:.4f}", "-movflags", "+faststart", out_mp4])


def contact_sheet(video, times, labels, out_png):
    tiles = []
    for t in times:
        img = Image.fromarray(grab(video, t)).resize((270, 480))
        tiles.append(img)
    cols = min(5, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 270, rows * 500), (16, 16, 18))
    from PIL import ImageDraw
    d = ImageDraw.Draw(sheet)
    for k, (img, lab) in enumerate(zip(tiles, labels)):
        x, y = (k % cols) * 270, (k // cols) * 500
        sheet.paste(img, (x, y))
        d.text((x + 6, y + 483), lab, fill=(230, 230, 230))
    sheet.save(out_png)


def preview_stills(master, ov, popups, out_png, grid=True):
    """Composite the exact rendered overlay frames onto the master at each pop-up beat,
    without encoding a video. Yellow lines mark the 192 px safe-zone grid."""
    from PIL import ImageDraw
    beats = []
    for p in popups:
        beats.append((p["at"] + 0.6, f"{p['id']} in"))
        beats += [(it["t"] + 0.4, it["label"]) for it in p.get("items", [])]
        if "tB" in p:
            beats.append((p["tB"] + 0.5, f"{p['id']} B"))
    tiles = []
    for t, lab in beats:
        f = min(int(round(t * FPS)), ov["nframes"] - 1)
        base = Image.fromarray(grab(master, f / FPS)).convert("RGBA")
        base.paste(Image.open(os.path.join(ov["band"], f"{f:05d}.png")).convert("RGBA"), (0, BAND_Y))
        cap = Image.open(os.path.join(ov["captrack"], f"{f:05d}.png")).convert("RGBA")
        base.alpha_composite(cap, (0, CAP_Y))
        d = ImageDraw.Draw(base)
        if grid:
            for y in range(192, H, 192):
                d.line([(0, y), (W, y)], fill=(255, 220, 0, 110), width=2)
            d.rectangle([0, 0, W, SAFE["top"]], outline=(255, 60, 60, 200), width=3)
            d.rectangle([0, SAFE["bottom"], W, H], outline=(255, 60, 60, 200), width=3)
            d.rectangle([SAFE["right_x"], SAFE["right_from_y"], W, H], outline=(255, 60, 60, 200), width=3)
        tiles.append((base.convert("RGB").resize((360, 640)), f"{lab} @{f / FPS:.2f}s"))
    if not tiles:
        return None
    cols = min(4, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 372, rows * 676), (14, 14, 16))
    d = ImageDraw.Draw(sheet)
    for k, (im, lab) in enumerate(tiles):
        x, y = (k % cols) * 372 + 6, (k // cols) * 676 + 6
        sheet.paste(im, (x, y))
        d.text((x + 4, y + 646), lab, fill=(235, 235, 235))
    sheet.save(out_png)
    return out_png


def main():
    spec_path = os.path.abspath(sys.argv[1])
    base = os.path.dirname(spec_path)
    spec = json.load(open(spec_path))
    words_path = sys.argv[sys.argv.index("--words") + 1] if "--words" in sys.argv else os.path.join(base, "words.json")
    master = os.path.join(base, spec["master"])
    work = os.path.join(base, "build")
    os.makedirs(work, exist_ok=True)

    raw_words = json.load(open(words_path))
    words = CAP.apply_fixes(CAP.drop_fillers(raw_words), spec.get("fixes", {}).get("replace"))
    W_ = Words(words)
    popups = resolve_popups(spec.get("popups", []), W_)

    dur = float(ffprobe_json(master, "format=duration")["format"]["duration"])
    vinfo = ffprobe_json(master, "stream=nb_frames,codec_type")
    nframes = int(next(s for s in vinfo["streams"] if s["codec_type"] == "video").get("nb_frames") or round(dur * FPS))
    chunks = CAP.timeline(CAP.chunk(words, spec.get("fixes", {}).get("groups")), nframes / FPS)

    plan = {"popups": popups, "chunks": [{k: c[k] for k in ("text", "s", "on", "off", "last", "i", "j")} for c in chunks]}
    json.dump(plan, open(os.path.join(work, "plan.json"), "w"), indent=1)
    json.dump(words, open(os.path.join(work, "words.fixed.json"), "w"), indent=1)
    print(f"{len(chunks)} caption chunks, {len(popups)} pop-ups, {nframes} frames")

    guard = headroom_report(master, popups, work)
    json.dump(guard, open(os.path.join(work, "headroom.json"), "w"), indent=1)
    for g in guard:
        print("headroom", g)

    ov = render_overlays(master, popups, chunks, work, nframes)
    json.dump({"popup_boxes": ov["popup_boxes"], "caption_boxes": ov["caption_boxes"]},
              open(os.path.join(work, "boxes.json"), "w"))

    topic = spec.get("topic", "reel")
    v = 1
    while os.path.exists(os.path.join(base, f"{topic}-v{v}.mp4")):
        v += 1
    out = os.path.join(base, f"{topic}-v{v}.mp4")
    if "--stills-only" in sys.argv:
        sheet = preview_stills(master, ov, popups, os.path.join(base, f"{topic}-preview.png"))
        print("preview stills:", sheet or "no pop-ups to preview")
        return
    input_lufs = loudnorm(master, os.path.join(work, "voice.wav"))
    print(f"input loudness {input_lufs:.1f} LUFS")
    export(master, ov, os.path.join(work, "voice.wav"), out)
    print("exported", out)
    if "--phone" in sys.argv:
        export(master, ov, os.path.join(work, "voice.wav"), out.replace(".mp4", "-phone.mp4"), crf=21)

    times, labels = [], []
    for p in popups:
        for t, lab in [(p["at"] + 0.6, f"{p['id']} in")] + [(it["t"] + 0.4, it["label"]) for it in p.get("items", [])] + \
                      ([(p["tB"] + 0.5, f"{p['id']} B")] if "tB" in p else []):
            times.append(t)
            labels.append(f"{lab} @{t:.2f}s")
    if times:
        contact_sheet(out, times, labels, out.replace(".mp4", "-sheet.png"))
    json.dump({"output": out, "version": v}, open(os.path.join(work, "last.json"), "w"))


if __name__ == "__main__":
    main()
