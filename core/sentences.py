"""Turns chunk-level ASR output into one subtitle per sentence, timed from per-character alignment."""
import difflib
import re

from . import models

MAX_CHARS = 28  # a longer sentence is split at a comma, then at its longest pause
PAUSE_BREAK = 1.2  # a comma followed by a pause this long ends a sentence
# A pause without punctuation never does: Qwen punctuates well, and the aligner often leaves false gaps inside words.
GLUE_GAP = 1.0  # an unfinished clause joins the next one across a gap up to this long
SHORT = 3  # spoken characters; a fragment this short joins a neighbour that follows closely
SHORT_GAP = 0.35
SAME_TURN_GAP = 0.6  # a sentence nobody could be matched to joins a neighbour this close
MIN_DURATION = 0.8
TAIL = 0.15  # keeps the subtitle up briefly after the last character

ENDS = "。！？!?…"
CLOSERS = "」』）)】\"'”’"
COMMAS = "，、,;；：:"
SKIP = set(ENDS + CLOSERS + COMMAS + "「『（(【\"'“‘ 　.．~～-—")
KANA = re.compile(r"[぀-ヿ]")
# Words that cannot end a sentence: the speaker paused mid-clause.
OPEN_ZH = ("的", "我们", "你们", "他们", "这个", "那个", "就是", "然后", "所以", "但是", "因为", "如果", "而且", "要去", "去", "在", "从", "把", "给", "跟", "和", "是", "就", "要", "想", "会", "很", "都", "也", "还", "对", "被", "让")
OPEN_JA = ("て", "で", "が", "を", "に", "は", "と", "の", "も", "から", "けど", "ので", "たら", "ば")
FILLERS = set("嗯哎啊哦呃唉诶欸噢呀哈呵额唔哼喔嘿哟呦咦") | set("あえんうおーっ")
FILLER_WORDS = {"えっと", "あの", "その", "まあ"}


def is_filler(text):
    """A sentence made only of interjections, like 嗯 or 哎哎 or えっと."""
    core = "".join(c for c in text if c not in SKIP)
    return bool(core) and (core in FILLER_WORDS or (len(core) <= 3 and all(c in FILLERS for c in core)))


def char_times(text, words):
    """Returns (start, end) per character of text; None where a character has no aligned time."""
    timed = []
    for w in words:
        if w.get("t0", -1) < 0 or w.get("t1", -1) < 0:
            continue
        chars = [c for c in w["text"] if not c.isspace()]
        for n, c in enumerate(chars):
            t0, t1 = w["t0"] / 100, w["t1"] / 100
            step = (t1 - t0) / len(chars)
            timed.append((c, t0 + n * step, t0 + (n + 1) * step))
    times = [None] * len(text)
    j = 0
    for i, c in enumerate(text):
        k = j
        while k < len(timed) and k < j + 8 and timed[k][0] != c:
            k += 1
        if k < len(timed) and timed[k][0] == c:
            times[i] = timed[k][1:]
            j = k + 1
    return times


def _spoken(text):
    return sum(c not in SKIP for c in text)


def _break_long(text, start, end, times):
    """Yields spans of at most MAX_CHARS spoken characters, preferring commas and then pauses."""
    if _spoken(text[start:end]) <= MAX_CHARS:
        return [(start, end)]
    candidates = [i + 1 for i in range(start + 1, end - 1) if text[i] in COMMAS]
    if not candidates:
        gaps = [(times[i + 1][0] - times[i][1], i + 1) for i in range(start + 1, end - 1) if times[i] and times[i + 1]]
        candidates = [max(gaps)[1]] if gaps else []
    if not candidates:
        return [(a, min(a + MAX_CHARS, end)) for a in range(start, end, MAX_CHARS)]
    mid = (start + end) / 2
    cut = min(candidates, key=lambda i: abs(i - mid))
    return _break_long(text, start, cut, times) + _break_long(text, cut, end, times)


def _is_open(sentence):
    core = sentence.rstrip("".join(SKIP))
    if sentence.rstrip()[-1:] in "？?！!":
        return False
    return core.endswith(OPEN_JA if KANA.search(core) else OPEN_ZH)


def _gap(times, a_end, b_start):
    i = a_end - 1
    while i >= 0 and not times[i]:
        i -= 1
    j = b_start
    while j < len(times) and not times[j]:
        j += 1
    return times[j][0] - times[i][1] if i >= 0 and j < len(times) else 99.0


def _glue(text, spans, times):
    """Joins a clause the speaker had not finished, and tiny fragments, with what follows."""
    out = []
    for a, b in spans:
        if out:
            pa, pb = out[-1]
            gap = _gap(times, pb, a)
            fits = _spoken(text[pa:b]) <= MAX_CHARS
            prev_n, cur_n = _spoken(text[pa:pb]), _spoken(text[a:b])
            prev_filler = is_filler(text[pa:pb]) or is_filler(text[a:b])
            if fits and not prev_filler and ((_is_open(text[pa:pb]) and gap < GLUE_GAP) or (min(prev_n, cur_n) <= SHORT and gap < SHORT_GAP)):
                out[-1] = (pa, b)
                continue
        out.append((a, b))
    return out


def _cut_speakers(spans, who):
    """Starts a new span where someone else takes over for at least two characters."""
    out = []
    for a, b in spans:
        start, current = a, None
        for i in range(a, b):
            if not who[i]:
                continue
            ahead = [x for x in who[i:b] if x][:2]
            if current and who[i] != current and len(ahead) == 2 and ahead[1] == who[i]:
                out.append((start, i))
                start = i
                current = who[i]
            current = current or who[i]
        out.append((start, b))
    return out


def split(text, times, who=None):
    """Returns (start, end) character spans, one per sentence. who gives each character's speaker, if known."""
    spans, start, i = [], 0, 0
    while i < len(text):
        c = text[i]
        end_here = c in ENDS
        if c in COMMAS and _spoken(text[start:i]) >= 2:
            end_here = _gap(times, i, i + 1) >= PAUSE_BREAK
        if end_here:
            while i + 1 < len(text) and text[i + 1] in ENDS + CLOSERS:
                i += 1
        if end_here:
            spans.append((start, i + 1))
            start = i + 1
        i += 1
    if text[start:].strip():
        spans.append((start, len(text)))
    spans = _glue(text, spans, times)
    out = []
    for a, b in spans:
        out += _break_long(text, a, b, times)
    # A fragment with no spoken characters belongs to the sentence before it.
    merged = []
    for a, b in out:
        if merged and _spoken(text[a:b]) == 0:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return _cut_speakers(merged, who) if who else merged


def _span_time(times, a, b):
    known = [t for t in times[a:b] if t]
    return (known[0][0], known[-1][1]) if known else None


def finalize(segs):
    """Numbers the sentences and gives each a readable display time without running into the next."""
    for n, s in enumerate(segs, 1):
        s["id"] = n
    for s, nxt in zip(segs, segs[1:] + [None]):
        spoken_end = s.setdefault("spoken_end", s["b"])
        limit = nxt["a"] if nxt else spoken_end + TAIL
        s["b"] = min(max(spoken_end + TAIL, s["a"] + MIN_DURATION), max(limit, spoken_end))
    return segs


def _main_speaker(who):
    known = [x for x in who if x]
    return max(set(known), key=known.count) if known else ""


def _fill_speakers(segs):
    """Gives a sentence with no matched characters the speaker of a neighbour that follows on without a pause."""
    for s, nxt in zip(segs, segs[1:]):
        if not s["speaker"] and nxt["speaker"] and nxt["a"] - s["b"] < SAME_TURN_GAP:
            s["speaker"] = nxt["speaker"]
    for s, nxt in zip(segs, segs[1:]):
        if not nxt["speaker"] and s["speaker"] and nxt["a"] - s["b"] < SAME_TURN_GAP:
            nxt["speaker"] = s["speaker"]


def _estimate(text, times, c0, c1):
    """A time for every character: its aligned time, else shared out between its timed neighbours; punctuation goes with the character before it."""
    n = len(text)
    marks = [(-1, c0)] + [(i, (t[0] + t[1]) / 2) for i, t in enumerate(times) if t] + [(n, c1)]
    est = [0.0] * n
    for (i, x), (j, y) in zip(marks, marks[1:]):
        for k in range(i + 1, j):
            est[k] = x + (y - x) * (k - i) / (j - i)
        if 0 <= i < n:
            est[i] = x
    for k in range(1, n):
        if text[k] in SKIP:
            est[k] = est[k - 1]
    return est


def stitch(slices, texts, words, speakers=None):
    """Joins every chunk's text into one stream of (text, times, speakers, estimated times).

    Each chunk keeps only the characters timed inside the part it owns. Where a chunk starts before its cut, it heard
    the last words of the chunk before in context, so its punctuation at the seam replaces the full stop the model puts
    at the end of every chunk.
    """
    text, times, who, est = [], [], [], []
    for i, (c0, c1, raw) in enumerate(slices):
        t = texts.get(i, "").strip()
        if not t:
            continue
        tm = char_times(t, words.get(i, []))
        e = _estimate(t, tm, c0, c1)
        w = list((speakers or {}).get(i) or [None] * len(t))
        own0, own1 = raw.get("own0", c0), raw.get("own1", c1)
        keep = [own0 <= x < own1 for x in e]
        first = next((k for k in range(len(t)) if keep[k] and t[k] not in SKIP), None)
        if first is None:
            continue
        heard_before = any(t[k] not in SKIP for k in range(first))
        if text and heard_before and own0 > c0 + 0.01:
            while text and text[-1] in ENDS + COMMAS + " ":
                for lst in (text, times, who, est):
                    lst.pop()
            k = first
            while k > 0 and t[k - 1] in SKIP:
                k -= 1
            for m in range(k, first):
                text.append(t[m]); times.append(None); who.append(None); est.append(e[first])
        for m in range(first, len(t)):
            if keep[m]:
                text.append(t[m]); times.append(tm[m]); who.append(w[m]); est.append(e[m])
    return "".join(text), times, who, est


def text_between(stream, t0, t1):
    """The stream's characters timed from t0 to t1, for cutting it at another pass's sentences."""
    text, _, _, est = stream
    return "".join(c for c, x in zip(text, est) if t0 <= x < t1)


def build(stream, lang):
    """Returns the sentence list for the whole recording, from stitch's stream."""
    text, times, who, est = stream
    has_who = any(who)
    segs = []
    for a, b in split(text, times, who if has_who else None):
        raw = text[a:b]
        sentence = raw.strip()
        if not sentence or is_filler(sentence):
            continue
        t = _span_time(times, a, b) or (est[a], max(est[b - 1], est[a] + 0.3))
        lead = len(raw) - len(raw.lstrip())
        seg_lang = lang if lang != "mix" else ("ja" if KANA.search(sentence) else "zh")
        segs.append({
            "id": 0, "a": t[0], "b": t[1], "lang": seg_lang, "qwen": sentence, "text": sentence, **dict.fromkeys(OTHERS, ""),
            "status": "raw", "why": "", "chunk": 0, "span": [0, len(sentence)], "times": times[a + lead:a + lead + len(sentence)], "qwen2": "", "moss": "",
            "speaker": _main_speaker(who[a:b]) if has_who else "",
        })
    _fill_speakers(segs)
    return finalize(segs)


GROUP_SPAN = 30.0  # seconds of neighbouring sentences the backup model hears at once
GROUP_GAP = 3.0


def groups(segs, lang=None):
    """Groups neighbouring sentences for another model to hear at once, so its text can be cut at their edges.

    With lang, only sentences of that language, and a change of language ends a group. Sets each sentence's chunk to
    its group and its span to its place in the group's joined text.
    """
    out = []
    for s in segs:
        last = out[-1] if out else None
        if last and (lang is None or last[-1]["lang"] == s["lang"]) and s["a"] - last[-1]["spoken_end"] < GROUP_GAP and s["spoken_end"] - last[0]["a"] <= GROUP_SPAN:
            last.append(s)
        else:
            out.append([s])
    out = [g for g in out if lang is None or g[0]["lang"] == lang]
    for n, g in enumerate(out):
        pos = 0
        for s in g:
            s["chunk"], s["span"] = n, [pos, pos + len(s["qwen"])]
            pos += len(s["qwen"])
    return out


def _anchors(primary, backup):
    """Matching runs between the two texts; single characters only count when nothing longer exists."""
    blocks = [b for b in difflib.SequenceMatcher(None, primary, backup, autojunk=False).get_matching_blocks() if b.size]
    return [b for b in blocks if b.size >= 2] or blocks


OTHERS = ("qwen2", "moss") + models.BACKUPS  # other transcripts kept per sentence: Qwen's second pass, MOSS, each backup model


def cut_at(spans, primary, other):
    """Cuts another transcript of the same audio at the sentence spans of the primary text, using their shared runs as anchors."""
    anchors = _anchors(primary, other)
    floor, out = 0, []
    for a, b in spans:
        pieces = [(max(a, k.a), min(b, k.a + k.size), k.b - k.a) for k in anchors if k.a < b and k.a + k.size > a]
        if not pieces:
            out.append("")
            continue
        first, last = pieces[0], pieces[-1]
        # Unmatched characters at either edge are likely misheard words, so keep as many from the other text.
        # Punctuation does not count: a transcript without any (Whisper) would otherwise shift a character per edge.
        lead = sum(c not in SKIP for c in primary[a:first[0]])
        trail = sum(c not in SKIP for c in primary[last[1]:b])
        # Never reach into text that matches a neighbouring sentence.
        before = max((k.b + k.size for k in anchors if k.a + k.size <= a), default=0)
        after = min((k.b for k in anchors if k.a >= b), default=len(other))
        start = max(floor, before, first[0] + first[2] - lead)
        end = min(after, last[1] + last[2] + trail)
        while end < len(other) and other[end] in ENDS + CLOSERS + COMMAS:
            end += 1
        floor = max(floor, end)
        out.append(other[start:end].strip())
    return out


def attach(group, other, field):
    """Stores, in field, the piece of another transcript of the group's audio that matches each sentence."""
    spans, primary = [], ""
    for s in group:
        spans.append((len(primary), len(primary) + len(s["qwen"])))
        primary += s["qwen"]
    for s, piece in zip(group, cut_at(spans, primary, other.strip())):
        s[field] = piece


def attach_backup(chunk_segs, primary, backup, langs, field):
    """Cuts a backup model's text at the sentences of the primary text and stores it, in field, on those in langs."""
    for s, piece in zip(chunk_segs, cut_at([tuple(s["span"]) for s in chunk_segs], primary, backup)):
        if piece and s["lang"] in langs:
            s[field] = piece


def backups_agree(s):
    """True when some backup model heard the sentence exactly as Qwen did."""
    plain = lambda t: "".join(c for c in t if c not in SKIP)
    return any(s.get(k) and plain(s[k]) == plain(s["qwen"]) for k in models.BACKUPS)


def apply_edit(group, text):
    """Replaces adjacent sentences with DeepSeek's lines, retiming each from the original characters.

    Returns the new sentences, or None when the edit lost too much of the speech to be trusted.
    """
    q = "".join(s["qwen"] for s in group)
    times = [t for s in group for t in (s["times"] + [None] * len(s["qwen"]))[:len(s["qwen"])]]
    owner = [s for s in group for _ in s["qwen"]]
    lines = [x.strip() for x in text.split("\n") if x.strip()]
    joined = "".join(lines)
    blocks = [b for b in difflib.SequenceMatcher(None, q, joined, autojunk=False).get_matching_blocks() if b.size]
    spoken = sum(c not in SKIP for c in q) or 1
    kept = sum(c not in SKIP for b in blocks for c in q[b.a:b.a + b.size])
    if kept / spoken < 0.5:
        return None
    out, pos, floor = [], 0, 0
    for n, line in enumerate(lines):
        lo, hi = pos, pos + len(line)
        pos = hi
        hits = [(b.a + max(lo, b.b) - b.b, b.a + min(hi, b.b + b.size) - b.b) for b in blocks if b.b < hi and b.b + b.size > lo]
        q0 = hits[0][0] if hits else floor
        q1 = hits[-1][1] if hits else floor
        if n == 0:
            q0 = 0  # corrected words at the edges have no match but still belong to this line
        if n == len(lines) - 1:
            q1 = len(q)
        q0, q1 = max(q0, floor), max(q1, q0)
        floor = q1
        known = [t for t in times[q0:q1] if t]
        sources = list(dict.fromkeys(id(s) for s in owner[q0:q1]))
        first = next((s for s in owner[q0:q1]), group[0])
        speaker = next((s["speaker"] for s in group if s.get("speaker")), "")
        a, b = (known[0][0], known[-1][1]) if known else (first["a"], first["b"])
        out.append({
            "id": 0, "a": a, "b": b, "lang": first["lang"], "qwen": q[q0:q1].strip(),
            "text": line, "status": "raw", "why": "", "chunk": first["chunk"], "span": [q0, q1], "times": [], "speaker": speaker,
            **dict.fromkeys(OTHERS, ""),
        })
        if len(group) > 1:
            for f in OTHERS:
                out[-1][f] = "".join(s.get(f, "") for s in group if id(s) in sources)
    if len(group) == 1:
        for f in OTHERS:
            if group[0].get(f):
                for s, piece in zip(out, cut_at([tuple(s["span"]) for s in out], q, group[0][f])):
                    s[f] = piece
    return out
