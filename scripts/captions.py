"""Auto Reel Editor: word clean-up and 2-3 word caption chunking.

chunk(words, groups) splits the word list by dynamic programming: every way of cutting the
sentence into 1-3 word chunks is scored, the cheapest wins. Rules are costs, so a hard rule
(never end on "the") is just a very large cost and a soft wish (avoid one-word chunks) a small one.
"""
import re

FPS = 30
WEAK_END = {"a", "an", "the", "to", "and", "of", "or", "my", "your", "our", "their", "its", "his", "her"}
# words that usually modify the next word; splitting them from it reads badly
MODIFIERS = {"high", "higher", "low", "lower", "extra", "light", "simple", "complex", "deep", "every",
             "multi-step", "more", "less", "most", "best", "new", "big", "small", "good", "great", "real",
             "whole", "easy", "first", "last", "next",
             "other", "same", "own", "single", "everyday", "common", "free", "full", "open", "top", "few",
             "many", "much", "some", "any", "each", "this", "that", "these", "those", "no"}
PREPS = {"for", "in", "on", "at", "with", "by", "from", "into", "as", "than", "about", "like", "if", "so", "but", "is"}
SWEARS = {"fuck": "f**k", "fucking": "f***ing", "shit": "s**t", "bitch": "b***h", "damn": "d**n", "ass": "a**"}
NUM = re.compile(r"^\$?\d[\d,./%x]*$")
MAX_CHARS = 24          # about 860 px of 56 px TikTok Sans
PAUSE = 0.30            # a gap this long between words is a natural break


def norm(t):
    return re.sub(r"[^\w/'-]", "", t.lower())


FILLERS = {"uh", "um", "uhm", "erm", "er", "ah", "hmm", "mm"}


def drop_fillers(words):
    """Spoken fillers never make it to captions."""
    return [w for w in words if norm(w["w"]) not in FILLERS]


def apply_fixes(words, replace):
    """replace: {"from phrase": "to phrase"} matched on whole normalized words, case kept from target."""
    out = [dict(w) for w in words]
    for src, dst in (replace or {}).items():
        sw = [norm(x) for x in src.split()]
        i = 0
        while i <= len(out) - len(sw):
            if [norm(w["w"]) for w in out[i:i + len(sw)]] == sw:
                trail = re.sub(r"^.*?([.,!?;:]*)$", r"\1", out[i + len(sw) - 1]["w"])
                dws = dst.split()
                s, e = out[i]["s"], out[i + len(sw) - 1]["e"]
                step = (e - s) / max(1, len(dws))
                new = [{"w": d, "s": round(s + k * step, 3), "e": round(s + (k + 1) * step, 3), "p": 1.0}
                       for k, d in enumerate(dws)]
                new[-1]["w"] += trail
                out[i:i + len(sw)] = new
                i += len(new)
            else:
                i += 1
    return out


def display(word):
    w = word.strip()
    w = re.sub(r"[.,;:!]+$", "", w)          # captions drop sentence punctuation, keep "?"
    w = w.replace("—", " ").replace("–", "-")
    core = norm(w)
    if core in SWEARS:
        w = w.lower().replace(core, SWEARS[core])
    return w


def chunk(words, groups=None):
    n = len(words)
    toks = [norm(w["w"]) for w in words]
    # forced groupings: a chunk must start at the phrase's first word and end at its last
    must_cut, no_cut = set(), set()        # cut positions: cut k means a chunk ends after word k-1
    for g in groups or []:
        gw = [norm(x) for x in g.split()]
        for i in range(n - len(gw) + 1):
            if toks[i:i + len(gw)] == gw:
                must_cut.update({i, i + len(gw)})
                no_cut.update(range(i + 1, i + len(gw)))
    forced_len = {}
    for g in groups or []:
        gw = [norm(x) for x in g.split()]
        for i in range(n - len(gw) + 1):
            if toks[i:i + len(gw)] == gw:
                forced_len[i] = len(gw)

    def sentence_end(k):
        return bool(re.search(r"[.!?]$", words[k]["w"]))

    def clause_end(k):
        return bool(re.search(r"[.,!?;:]$", words[k]["w"]))

    def cost(i, j):                      # chunk = words[i:j]
        size = j - i
        if size > 3 and forced_len.get(i) != size:
            return 1e9
        text = " ".join(display(w["w"]) for w in words[i:j])
        c = 0.0
        if len(text) > MAX_CHARS and forced_len.get(i) != size:
            c += 200
        last = toks[j - 1]
        if j < n:
            # "to" closing an elided infinitive ("as it needs to / for the task") is not dangling
            elided = last == "to" and toks[j] in PREPS
            if last in WEAK_END and not clause_end(j - 1) and not elided:
                c += 1000
            if last in PREPS and not clause_end(j - 1):
                c += 14
            if last in MODIFIERS and not clause_end(j - 1) and j not in must_cut:
                c += 30
            if NUM.match(last) and not clause_end(j - 1):
                c += 40
            gap = words[j]["s"] - words[j - 1]["e"]
            if clause_end(j - 1) or gap >= PAUSE:
                c -= 4                   # reward cutting where he breathes
        for k in range(i, j - 1):        # punctuation or a pause inside the chunk
            if sentence_end(k):
                c += 25
            elif clause_end(k):
                c += 8
            if words[k + 1]["s"] - words[k]["e"] >= PAUSE:
                c += 6
        if size == 1 and forced_len.get(i) != 1:
            c += 5 if (j == n or clause_end(j - 1) or (j < n and words[j]["s"] - words[j - 1]["e"] >= PAUSE)) else 9
            if toks[i] in WEAK_END or toks[i] in PREPS:
                c += 25
        if size == 3:
            c += 1.0
        if words[j - 1]["e"] - words[i]["s"] > 1.8:
            c += 3
        return c

    INF = float("inf")
    best = [INF] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0
    for j in range(1, n + 1):
        if j in no_cut:
            continue
        for size in range(1, max(3, max(forced_len.values(), default=3)) + 1):
            i = j - size
            if i < 0 or i in no_cut or best[i] == INF:
                continue
            if any(k in must_cut for k in range(i + 1, j)):
                continue
            c = best[i] + cost(i, j)
            if c < best[j]:
                best[j], back[j] = c, i
    cuts, j = [], n
    while j > 0:
        cuts.append((back[j], j))
        j = back[j]
    cuts.reverse()

    chunks = []
    for i, j in cuts:
        chunks.append({"text": " ".join(display(w["w"]) for w in words[i:j]),
                       "s": words[i]["s"], "e": words[j - 1]["e"], "i": i, "j": j,
                       "last": toks[j - 1]})
    return chunks


def timeline(chunks, duration, hold=0.5):
    """Frame-exact on/off. On = first frame at or just before the first word's start
    (never more than one frame early); off = next chunk's on frame, or hold after the last word."""
    out = []
    total = int(round(duration * FPS))
    for k, c in enumerate(chunks):
        on = int(c["s"] * FPS)                      # floor: at most 33 ms early
        tail = int(round((c["e"] + hold) * FPS))
        nxt = int(chunks[k + 1]["s"] * FPS) if k + 1 < len(chunks) else total
        off = min(nxt, tail, total)
        if off <= on:
            off = on + 1
        out.append({**c, "on": on, "off": off})
    return out
