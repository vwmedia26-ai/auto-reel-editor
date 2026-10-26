# Auto Reel Editor

Turns selfie-style talking-head takes into a finished 1080x1920, 30 fps reel: clean cuts, word-timed captions, animated glass pop-ups, loudness-matched voice, and an automatic quality check. Built to be driven by Claude as a skill, but every step is a plain command.

## Setup (one time)

```
bash install.sh
```

Installs the Python packages, checks ffmpeg and a headless Chromium, downloads the Whisper speech model (about 1.6 GB, needs network access to huggingface.co), then renders a short self-test. Plan on 15 to 30 minutes the first time.

## Editing a reel

```
python3 scripts/run.py init   my-reel  IMG_0101.mov IMG_0102.mov   # prep + transcribe, writes my-reel/reel.json
# add pop-ups and caption fixes to my-reel/reel.json (see below)
python3 scripts/run.py stills my-reel                                # quick preview sheet, no encode
python3 scripts/run.py render my-reel --phone                        # final video + QA
```

Output: `my-reel/<topic>-v1.mp4` (v2, v3 on later renders), an optional smaller `-phone` copy, a contact sheet of every pop-up moment, and `my-reel/build/qa.json`.

### What happens to the footage
1. **Prep**: HDR (HLG or PQ) is converted to standard video, every take is scaled to 1080x1920 at 30 fps with 48 kHz audio, dead air at the start and end of each take is trimmed by measuring loudness in 100 ms blocks, and the takes are joined with short audio fades.
2. **Transcribe**: Whisper large-v3-turbo with word timestamps. Words it is unsure of are listed so they can be checked.
3. **Captions**: 2 to 3 words per caption, cut where the speaker naturally pauses, never ending on words like "a", "the", "to", "and" or "of", and never splitting a describing word from what it describes. Hard cuts, no animation.
4. **Pop-ups**: dark frosted-glass cards drawn by a headless browser over the real video, so the blur is genuine. Each pop-up is anchored to spoken words, not timestamps.
5. **Audio**: voice normalized to -14 LUFS with true peak at or below -1 dBTP.
6. **QA**: checks every frame for anything inside the TikTok/Reels UI zones, verifies audio sync, loudness and caption timing, and scans all on-screen text for em-dashes.

## reel.json

```json
{
  "topic": "my-topic",
  "master": "prepped.mov",
  "fixes": {
    "replace": {"clawed": "Claude"},
    "groups": ["light thinking", "keep in mind"]
  },
  "popups": [
    {"type": "flowArrow", "a": "Prompt quality", "b": "Response quality",
     "at": "quality of the prompt", "tB": "quality of the response", "until": {"before": "when it comes"}},
    {"type": "checklist", "title": "Light thinking", "sub": "is enough for", "color": "blue",
     "at": "for everyday", "until": {"before": "where higher"},
     "items": [{"label": "Emails", "at": "emails"}, {"label": "Brainstorming", "at": "brainstorming"}]}
  ]
}
```

**Anchors** (`at`, `until`, `tB`, `t0`, `t1`, item `at`):
- `"some words"`: when that phrase starts (first time it is spoken)
- `{"word": "some words", "n": 2}`: the second time it is spoken; `"after": 12.5` skips earlier matches
- `{"word": "some words", "edge": "end"}`: when that phrase finishes; `"offset": 0.1` shifts it
- `{"before": "some words", "pad": 0.04}`: just before that phrase starts
- a plain number: seconds

**Fixes**: `replace` swaps misheard words (whole words, case taken from what you write). `groups` forces phrases to stay together on one caption, for lists and key lines.

### Pop-up types
| type | what it shows | fields |
|---|---|---|
| `checklist` | title + grey subtitle, then chips that tick at their word (`"layout": "slim"` for one row, no title) | `title`, `sub`, `color` (`blue`, `orange` or a hex), `items[{label, at}]`, optional `app` |
| `flowArrow` | "A", then an arrow and "B" slide in | `a`, `b`, `tB` |
| `splitBars` | two apps with bars and percentages counting up | `rows[{app, name, pct, color?}]` |
| `raceBars` | winner fills to 100% and shows "Done", loser crawls to about 55% | `winner{app, name}`, `loser{app, name}` |
| `typingPrompt` | chat box typing a line, send button lights up | `line`, `t0`, `t1`, optional `placeholder` |
| `notification` | phone-style banner | `app`, `title`, `body`, optional `when` |

Apps with logos: `chatgpt` / `openai`, `claude`, `gemini`, `gmail`, `instagram`.

**Layouts**: SLIM cards are 76 px tall at y 204; two-row cards are 146 px tall at y 200; both 940 px wide. The render reports, for every pop-up, how close the top of the speaker's head gets to the card and suggests SLIM when a two-row card would cover hair.

## Rules the QA enforces
- Nothing in the top 192 px, the bottom 384 px, or the right rail (x over 900, below y 960), checked on every frame including mid-animation
- Audio lag 0 ms, matching stream lengths
- -14 LUFS plus or minus 1, true peak at or below -1 dBTP
- Each caption on screen within 50 ms of its first word; no caption ending on a weak word
- No em-dash characters on screen

## Running on a small cloud machine
Long steps (prep, render) can take several minutes on 2 CPUs. Run them in the background and check back rather than waiting on one long command. If the container runs as root, Chromium is started with `--no-sandbox` automatically.

## Credits and licenses
- Auto Reel Editor code: MIT, see `LICENSE`
- TikTok Sans font: SIL Open Font License 1.1, see `engine/assets/fonts/TikTokSans-OFL.txt`
- App logos: Simple Icons, CC0 1.0, see `engine/assets/icons/SimpleIcons-LICENSE-CC0.md`. Logos remain trademarks of their owners.
- Uses ffmpeg, faster-whisper (MIT), Playwright (Apache-2.0), NumPy and Pillow, installed separately
