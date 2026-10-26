#!/usr/bin/env python3
"""Auto Reel Editor: word-level transcription.

Usage: transcribe.py <video_or_audio> <out_words.json> [--model DIR] [--vocab "Term one, Term two"]
Reads audio through ffmpeg into a 16 kHz mono float32 array (avoids the
faster-whisper 1.2.x + PyAV 19 file-path crash), then writes a word list:
[{"w": "text", "s": start, "e": end, "p": probability}, ...]
"""
import json, os, re, subprocess, sys, tempfile, wave
import numpy as np

BRAND_PROMPT = "ChatGPT, GPT-6, Claude, Claude Fable, Gemini, GoHighLevel, AI."
FIXES = {"clawed": "Claude", "clogged": "Claude", "claude": "Claude", "gpt": "GPT",
         "chatgpt": "ChatGPT", "ai": "AI", "i": "I", "gemini": "Gemini"}
LOW_CONF = 0.55


def load_audio(path):
    """16 kHz mono float32, loudness-normalized so quiet indoor takes transcribe as well as loud ones."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", path, "-vn", "-af", "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp],
                   check=True)
    with wave.open(tmp) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    os.unlink(tmp)
    return a


def merge_tokens(words):
    """Join split hyphen tokens ("GPT" + "-6", "80" + "-20", "in" + "-depth")."""
    out = []
    for w in words:
        t = w["w"]
        if out and t.startswith("-") and len(t) > 1:
            out[-1]["w"] += t
            out[-1]["e"] = w["e"]
            out[-1]["p"] = min(out[-1]["p"], w["p"])
            continue
        out.append(dict(w))
    for w in out:
        w["w"] = re.sub(r"^(\d+)-(\d+)$", r"\1/\2", w["w"])  # 80-20 -> 80/20
    return out


def fix_case(word):
    core = re.sub(r"[^\w'/-]", "", word)
    rep = FIXES.get(core.lower())
    return word.replace(core, rep) if rep and core else word


def main():
    src, dst = sys.argv[1], sys.argv[2]
    model_dir = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else \
        os.environ.get("ARE_MODELS", os.path.expanduser("~/.cache/auto-reel-editor/models"))
    prompt = BRAND_PROMPT
    if "--vocab" in sys.argv:
        prompt = BRAND_PROMPT[:-1] + ", " + sys.argv[sys.argv.index("--vocab") + 1].strip(" .") + "."
    from faster_whisper import WhisperModel
    model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8",
                         download_root=model_dir, cpu_threads=os.cpu_count())
    segs, _ = model.transcribe(load_audio(src), language="en", word_timestamps=True,
                               initial_prompt=prompt, vad_filter=False,
                               condition_on_previous_text=False)
    words = []
    for s in segs:
        for w in s.words:
            words.append({"w": w.word.strip(), "s": round(w.start, 3),
                          "e": round(w.end, 3), "p": round(w.probability, 3)})
    words = merge_tokens(words)
    for w in words:
        w["w"] = fix_case(w["w"])
    json.dump(words, open(dst, "w"), indent=1)
    low = [f'{w["w"]}@{w["s"]:.2f}' for w in words if w["p"] < LOW_CONF]
    print(" ".join(w["w"] for w in words))
    print("LOW_CONFIDENCE:", ", ".join(low) if low else "none")


if __name__ == "__main__":
    main()
