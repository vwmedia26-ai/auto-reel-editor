#!/usr/bin/env python3
"""Auto Reel Editor self-test: builds a 6 s synthetic clip, renders captions and every pop-up
type, and runs the objective QA. Needs no camera footage and no transcription."""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    d = tempfile.mkdtemp(prefix="are_selftest_")
    clip = os.path.join(d, "clip.mp4")
    # gradient "sky" over a dark "subject" block, with a speech-like tone burst pattern
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "gradients=s=1080x1920:c0=0x2a6fd6:c1=0x6aa8f0:d=6:speed=0.00001,format=yuv420p",
                    "-f", "lavfi", "-i", "color=c=0x332a26:s=520x1300:d=6",
                    "-f", "lavfi", "-i", "sine=f=220:d=6,volume='0.25*(0.6+0.4*sin(2*PI*3*t))':eval=frame",
                    "-filter_complex", "[0:v][1:v]overlay=280:620,fps=30[v]", "-map", "[v]", "-map", "2:a",
                    "-t", "6", "-ac", "2", "-ar", "48000", "-c:v", "libx264", "-crf", "16", "-c:a", "aac", clip], check=True)
    subprocess.run([sys.executable, os.path.join(HERE, "prep.py"), os.path.join(d, "prepped.mov"), clip, "--no-trim"], check=True)
    text = "Here is a quick self test of captions and every pop up type working end to end okay"
    words, t = [], 0.2
    for w in text.split():
        words.append({"w": w, "s": round(t, 2), "e": round(t + 0.28, 2), "p": 1.0})
        t += 0.34
    json.dump(words, open(os.path.join(d, "words.json"), "w"))
    spec = {"topic": "selftest", "master": "prepped.mov", "fixes": {"replace": {}, "groups": ["pop up type"]},
            "popups": [
                {"type": "flowArrow", "a": "Prompt", "b": "Result", "at": 0.2, "tB": 0.6, "until": 1.0},
                {"type": "checklist", "title": "Checklist", "sub": "two row", "at": 1.25, "until": 2.0,
                 "items": [{"label": "One", "at": 1.4}, {"label": "Two", "at": 1.6}, {"label": "Three", "at": 1.8}]},
                {"type": "splitBars", "rows": [{"app": "chatgpt", "name": "ChatGPT", "pct": 80},
                                               {"app": "claude", "name": "Claude", "pct": 20}], "at": 2.25, "until": 3.0},
                {"type": "raceBars", "winner": {"app": "claude", "name": "Claude"},
                 "loser": {"app": "chatgpt", "name": "ChatGPT"}, "at": 3.25, "until": 4.0},
                {"type": "typingPrompt", "line": "Summarize this in three bullets.", "t0": 4.3, "t1": 4.8,
                 "at": 4.25, "until": 5.0},
                {"type": "notification", "app": "gmail", "title": "New lead", "body": "Hey, are you free to talk today?",
                 "at": 5.25, "until": 5.8}]}
    json.dump(spec, open(os.path.join(d, "reel.json"), "w"), indent=1)
    subprocess.run([sys.executable, os.path.join(HERE, "render.py"), os.path.join(d, "reel.json")], check=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, "qa.py"), os.path.join(d, "reel.json")])
    out = os.path.join(d, "selftest-v1.mp4")
    keep = os.path.join(os.getcwd(), "auto-reel-editor-selftest.mp4")
    shutil.copy(out, keep)
    print("\nself-test video:", keep)
    if r.returncode != 0:
        sys.exit(1)
    q = json.load(open(os.path.join(d, "build", "qa.json")))
    # loudness on a pure test tone is not meaningful; every other check must pass
    must = [k for k in ("safe_zone", "av_sync", "captions", "em_dash") if not q[k]["pass"]]
    if must:
        print("SELF-TEST FAILED:", must)
        sys.exit(1)
    print("SELF-TEST PASSED")


if __name__ == "__main__":
    main()
