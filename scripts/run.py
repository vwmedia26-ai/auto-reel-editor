#!/usr/bin/env python3
"""Auto Reel Editor: one entry point.

  run.py init  <folder> <take1> [take2 ...] [--vocab "names, terms"]   prep the takes, transcribe, write <folder>/reel.json
                                              (no pop-ups yet) and print the transcript with any
                                              low-confidence words to double-check
  run.py stills <folder>                      render pop-ups/captions and a contact sheet only
  run.py render <folder> [--phone]            full render + QA  ->  <topic>-vN.mp4
  run.py qa     <folder>                      re-run the objective checks on the last render

Takes are joined in the order given. If they are named IMG_1234 and no order is given,
sort them by number first (init does this when every name has a number).
"""
import json, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def step(*args):
    print("\n>>", " ".join(os.path.basename(a) if os.path.isabs(a) else a for a in args), flush=True)
    subprocess.run([PY, *args], check=True)


def init(folder, takes):
    os.makedirs(folder, exist_ok=True)
    nums = [re.search(r"(\d+)", os.path.basename(t)) for t in takes]
    if len(takes) > 1 and all(nums) and "--keep-order" not in sys.argv:
        takes = [t for _, t in sorted(zip([int(n.group(1)) for n in nums], takes))]
    master = os.path.join(folder, "prepped.mov")
    step(os.path.join(HERE, "prep.py"), master, *takes)
    vocab = ["--vocab", sys.argv[sys.argv.index("--vocab") + 1]] if "--vocab" in sys.argv else []
    step(os.path.join(HERE, "transcribe.py"), master, os.path.join(folder, "words.json"), *vocab)
    spec_path = os.path.join(folder, "reel.json")
    if not os.path.exists(spec_path):
        topic = re.sub(r"[^a-z0-9]+", "-", os.path.basename(os.path.abspath(folder)).lower()).strip("-") or "reel"
        json.dump({"topic": topic, "master": "prepped.mov", "takes": [os.path.abspath(t) for t in takes],
                   "fixes": {"replace": {}, "groups": []}, "popups": []},
                  open(spec_path, "w"), indent=2)
    print("\nwrote", spec_path, "- add pop-ups and fixes, then: run.py stills", folder)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd, folder = sys.argv[1], os.path.abspath(sys.argv[2])
    spec = os.path.join(folder, "reel.json")
    if cmd == "init":
        args = sys.argv[3:]
        if "--vocab" in args:
            k = args.index("--vocab")
            del args[k:k + 2]
        init(folder, [os.path.abspath(t) for t in args if not t.startswith("--")])
    elif cmd == "stills":
        step(os.path.join(HERE, "render.py"), spec, "--stills-only")
    elif cmd == "render":
        extra = ["--phone"] if "--phone" in sys.argv else []
        step(os.path.join(HERE, "render.py"), spec, *extra)
        step(os.path.join(HERE, "qa.py"), spec)
    elif cmd == "qa":
        step(os.path.join(HERE, "qa.py"), spec)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
