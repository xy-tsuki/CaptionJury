"""Character error rate against a hand-corrected reference.

    python tests/cer.py 参考稿.txt result.srt|job.json [more ...]

The reference has one sentence per line; [mm:ss] prefixes, lines starting with # and punctuation are ignored.
A job JSON is scored once per transcript it holds (qwen, qwen2, alt, moss, final text).
"""
import json
import re
import sys
import unicodedata

CN_DIGITS = dict(zip("〇零一二三四五六七八九", "00123456789"))
FIELDS = ("qwen", "qwen2", "alt", "moss", "text")


def norm(text):
    """Compares only what is said: no punctuation, spaces or case, and M九 the same as M9."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"(?<=[a-z])([〇零一二三四五六七八九])", lambda m: CN_DIGITS[m.group(1)], text)
    return "".join(c for c in text if unicodedata.category(c)[0] in "LN")


def read_ref(path):
    lines = []
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(re.sub(r"^\[\d+:\d+\]\s*", "", line).replace("？？", ""))
    return norm("".join(lines))


def read_srt(path):
    out = []
    for block in open(path, encoding="utf-8-sig").read().split("\n\n"):
        rows = block.strip().splitlines()
        if len(rows) >= 3:
            out += [re.sub(r"^\[[^\]]*\]\s*|^说话人\d+[:：]\s*", "", r) for r in rows[2:]]
    return norm("".join(out))


def read_job(path):
    job = json.load(open(path, encoding="utf-8"))
    segs = job["segments"]
    return {f: norm("".join(s.get(f) or "" for s in segs)) for f in FIELDS if any(s.get(f) for s in segs)}


def align(ref, hyp):
    """Edit counts (substitutions, deletions, insertions) and the aligned pairs."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    i, j, s, dl, ins, pairs = n, m, 0, 0, 0, []
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            s += ref[i - 1] != hyp[j - 1]
            pairs.append((ref[i - 1], hyp[j - 1]))
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            dl += 1
            pairs.append((ref[i - 1], ""))
            i -= 1
        else:
            ins += 1
            pairs.append(("", hyp[j - 1]))
            j -= 1
    return s, dl, ins, pairs[::-1]


def errors(pairs, ctx=3):
    """The wrong stretches, as 'reference→hypothesis' with a little context."""
    out, k = [], 0
    while k < len(pairs):
        if pairs[k][0] == pairs[k][1]:
            k += 1
            continue
        a = k
        while k < len(pairs) and (pairs[k][0] != pairs[k][1] or (k + 1 < len(pairs) and pairs[k + 1][0] != pairs[k + 1][1] and k + 1 - a < 40)):
            k += 1
        left = "".join(p[0] for p in pairs[max(0, a - ctx):a])
        out.append(f"{left}[{''.join(p[0] for p in pairs[a:k])}→{''.join(p[1] for p in pairs[a:k])}]")
    return out


def score(ref, hyp):
    s, dl, ins, pairs = align(ref, hyp)
    return (s + dl + ins) / max(1, len(ref)), (s, dl, ins), pairs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    verbose = "-v" in sys.argv
    args = [a for a in sys.argv[1:] if a != "-v"]
    ref = read_ref(args[0])
    print(f"reference {len(ref)} chars")
    for path in args[1:]:
        hyps = read_job(path) if path.endswith(".json") else {"srt": read_srt(path)}
        for name, hyp in hyps.items():
            cer, (s, dl, ins), pairs = score(ref, hyp)
            print(f"{cer * 100:6.2f}%  S{s:3} D{dl:3} I{ins:3}  {name:5} {path}")
            if verbose:
                print("        " + "  ".join(errors(pairs)))
