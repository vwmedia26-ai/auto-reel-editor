#!/usr/bin/env python3
"""Auto Reel Editor: prep takes into one clean 1080x1920 / 30 fps / 48 kHz master.

Usage: prep.py <out.mov> <take1> [take2 ...] [--no-trim]
- converts HLG / PQ HDR to SDR BT.709
- normalizes size, frame rate, pixel format and audio
- trims dead air at each take's head and tail with 100 ms RMS blocks (+0.1 s pad)
- short audio fades at every cut, then joins the takes in the order given
Writes <out.mov> plus <out>.prep.json describing what was kept.
"""
import json, os, subprocess, sys, tempfile, wave
import numpy as np

HDR_TRC = {"arib-std-b67": "arib-std-b67", "smpte2084": "smpte2084"}
BLOCK = 0.10
PAD = 0.10
FADE = 0.015
# HDR reference white in nits. 108 matched the editor Preston already approved within 0.3 levels
# per channel on the parity clip (100 rendered ~2-5 levels brighter and bluer).
NPL = 108


def sh(args):
    subprocess.run(args, check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,color_transfer,r_frame_rate:format=duration",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    return {"w": v["width"], "h": v["height"], "trc": v.get("color_transfer", ""),
            "fps": v.get("r_frame_rate"), "dur": float(j["format"]["duration"])}


def video_filter(trc):
    chain = []
    if trc in HDR_TRC:
        chain.append(f"zscale=tin={HDR_TRC[trc]}:min=bt2020nc:pin=bt2020:rin=tv:t=linear:npl={NPL},"
                     "format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                     "zscale=t=bt709:m=bt709:r=tv")
    # cover-fit to 9:16 then exact size; constant 30 fps
    chain.append("scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1,fps=30,format=yuv420p")
    return ",".join(chain)


def rms_blocks(path):
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    sh(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp])
    with wave.open(tmp) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    os.unlink(tmp)
    n = int(16000 * BLOCK)
    k = len(a) // n
    r = np.sqrt(np.mean(a[:k * n].reshape(k, n) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(r)


def speech_span(db, dur):
    """First and last 100 ms block that holds speech. Threshold adapts to the take:
    25 dB under the loud end of the take, never below -50 dBFS. A short block run near the
    end (a quiet sign-off after a pause) is kept because we only look for the LAST active block."""
    loud = np.percentile(db, 95)
    thr = max(loud - 25.0, -50.0)
    active = np.where(db > thr)[0]
    # ignore isolated single-block clicks at the very edges
    if len(active) > 2:
        if active[1] - active[0] > 3:
            active = active[1:]
        if active[-1] - active[-2] > 8 and db[active[-1]] < thr + 3:
            active = active[:-1]
    if not len(active):
        return 0.0, dur
    start = max(0.0, active[0] * BLOCK - PAD)
    end = min(dur, (active[-1] + 1) * BLOCK + PAD)
    return round(start, 3), round(end, 3)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    trim = "--no-trim" not in sys.argv
    out, takes = args[0], args[1:]
    work = tempfile.mkdtemp(prefix="are_prep_")
    parts, report = [], []
    for i, src in enumerate(takes):
        info = probe(src)
        norm = os.path.join(work, f"norm{i}.mov")
        sh(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
            "-vf", video_filter(info["trc"]), "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-af", "aresample=48000:async=1:first_pts=0", "-ac", "2", "-c:a", "pcm_s16le", norm])
        s, e = speech_span(rms_blocks(norm), info["dur"]) if trim else (0.0, info["dur"])
        cut = os.path.join(work, f"cut{i}.mov")
        d = e - s
        sh(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{s:.3f}", "-i", norm,
            "-t", f"{d:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-af", f"afade=t=in:d={FADE},afade=t=out:st={max(0, d - FADE):.3f}:d={FADE}",
            "-c:a", "pcm_s16le", cut])
        parts.append(cut)
        report.append({"src": os.path.basename(src), "hdr": info["trc"] in HDR_TRC, "in": info,
                       "keep": [s, e]})
        print(f"take {i + 1}: {os.path.basename(src)} hdr={info['trc'] in HDR_TRC} keep {s:.2f}-{e:.2f} of {info['dur']:.2f}s")
    if len(parts) == 1:
        os.replace(parts[0], out)
    else:
        lst = os.path.join(work, "list.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{p}'\n" for p in parts)
        sh(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", lst, "-c", "copy", out])
    json.dump({"takes": report, "duration": probe(out)["dur"]}, open(os.path.splitext(out)[0] + ".prep.json", "w"), indent=1)
    print("prepped:", out, f"{probe(out)['dur']:.2f}s")


if __name__ == "__main__":
    main()
