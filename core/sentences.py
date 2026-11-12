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
FRAGMENT_GAP = 0.3  # 前一句没有句末标点、停顿不到这么久就接着说的，算同一句
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


REPEAT = re.compile(r"(.{2,20}?)\1{3,}")  # 同一段（2 到 20 个字）连续出现 4 次以上


def squash_repeats(text):
    """模型偶尔会卡住，把同一段话重复几十上百遍（比如"2月1日2月1日…"），只留一遍。"""
    return REPEAT.sub(r"\1", text)


def is_filler(text):
    """A sentence made only of interjections, like 嗯 or 哎哎 or えっと."""
    core = "".join(c for c in text if c not in SKIP)
    return bool(core) and (core in FILLER_WORDS or (len(core) <= 3 and all(c in FILLERS for c in core)))


COLLAPSED = 3  # 连续这么多个长度为 0、时间相同的词，是对齐失败堆在一处的，当作没有时间
LOST = 10  # 堆在一处的字有这么多，多半是 Qwen 漏听了一段、对齐整段失败，值得提醒用户检查（短的只是语速快）
CHAR_STEP = 0.2  # 没有时间的字按每字这么多秒排在有时间的字旁边


def lost_alignment(words):
    """这一块里有没有一大串字堆在同一点上（见 LOST）。"""
    bad = sorted(_collapsed(words))
    run = best = 0
    for k, prev in zip(bad, [None] + bad):
        run = run + 1 if prev is not None and k == prev + 1 else 1
        best = max(best, run)
    return best >= LOST


def _collapsed(words):
    """对齐模型对不上音频时（比如 Qwen 漏听了一段），会把一串字的时间都堆在同一点上（常是块的开头）。返回这些词的序号。"""
    bad, run = set(), []
    for k, w in enumerate(words + [None]):
        if w is not None and w.get("t1", -1) == w.get("t0", -1) and run and words[run[-1]]["t0"] == w["t0"]:
            run.append(k)
            continue
        if len(run) >= COLLAPSED:
            bad.update(run)
        run = [k] if w is not None and w.get("t1", -1) == w.get("t0", -1) else []
    return bad


def char_times(text, words):
    """Returns (start, end) per character of text; None where a character has no aligned time."""
    timed = []
    bad = _collapsed(words)
    for k, w in enumerate(words):
        if w.get("t0", -1) < 0 or w.get("t1", -1) < 0:
            continue
        chars = [c for c in w["text"] if not c.isspace()]
        for n, c in enumerate(chars):
            if k in bad:  # 留着占位，字和词才对得上，只是没有时间
                timed.append((c, None))
                continue
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
            times[i] = timed[k][1:] if timed[k][1] is not None else None
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


def _span_time(text, times, est, a, b):
    """一句的起止时间：取第一个和最后一个有效字的时间，没有对齐时间的字用估算的时间。"""
    spoken = [k for k in range(a, b) if text[k] not in SKIP and not text[k].isspace()] or list(range(a, b))
    i, j = spoken[0], spoken[-1]
    start = times[i][0] if times[i] else est[i]
    nxt = est[b] if b < len(est) else float("inf")  # 估算的时间不越过下一句的第一个字
    end = times[j][1] if times[j] else min(est[j] + CHAR_STEP, nxt)
    return start, max(end, min(start + 0.3, nxt))


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
    marks = [(i, (t[0] + t[1]) / 2) for i, t in enumerate(times) if t]
    # 块开头和结尾没有时间的字，紧挨着最近一个有时间的字、按正常语速排，而不是摊满整段（中间可能是没识别出的话或静音）
    if marks:
        lead = _spoken(text[:marks[0][0]])
        tail = _spoken(text[marks[-1][0] + 1:])
        c0 = max(c0, marks[0][1] - (lead + 1) * CHAR_STEP)
        c1 = min(c1, marks[-1][1] + (tail + 1) * CHAR_STEP)
    marks = [(-1, c0)] + marks + [(n, c1)]
    est = [0.0] * n
    for (i, x), (j, y) in zip(marks, marks[1:]):
        if 0 <= i and j < n:  # 中间空得远超正常语速时（中间是静音或没识别出的话），紧跟在前一个字后面排
            y = min(y, x + 2 * (j - i) * CHAR_STEP)
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
        t = _span_time(text, times, est, a, b)
        lead = len(raw) - len(raw.lstrip())
        seg_lang = lang if lang != "mix" else ("ja" if KANA.search(sentence) else "zh")
        segs.append({
            "id": 0, "a": t[0], "b": t[1], "lang": seg_lang, "qwen": sentence, "text": sentence, **dict.fromkeys(OTHERS, ""),
            "status": "raw", "why": "", "chunk": 0, "span": [0, len(sentence)], "times": times[a + lead:a + lead + len(sentence)], "qwen2": "", "moss": "",
            "speaker": _main_speaker(who[a:b]) if has_who else "",
        })
    _fill_speakers(segs)
    return finalize(merge_fragments(segs))


def merge_fragments(segs):
    """前一句没有句末标点、同一个人几乎不停顿就接着说下去的，合成一句（合起来不超过 MAX_CHARS 个字）。

    这类切口多半来自说话人编号短暂跳动，并不是真的换了一句；不开 AI 校对时也能少掉一批半句话的碎片。
    """
    out = []
    for s in segs:
        p = out[-1] if out else None
        last = p["qwen"].rstrip(CLOSERS)[-1:] if p else ""
        if (p and last and last not in ENDS and p["speaker"] == s["speaker"] and s["a"] - p["b"] < FRAGMENT_GAP
                and len(p["qwen"]) + len(s["qwen"]) <= MAX_CHARS):
            p["times"] = (p["times"] + [None] * len(p["qwen"]))[:len(p["qwen"])] + s["times"]
            p["qwen"] += s["qwen"]
            p["text"] = p["qwen"]
            p["b"] = s["b"]
            p["span"] = [0, len(p["qwen"])]
            continue
        out.append(s)
    return out


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
    return [other[c[0]:c[1]].strip() if c else "" for c in _cuts(spans, primary, other)]


def _cuts(spans, primary, other):
    """cut_at 的位置版：每句在 other 里的 (起, 止)，对不上任何字的句子是 None。"""
    anchors = _anchors(primary, other)
    floor, out = 0, []
    for a, b in spans:
        pieces = [(max(a, k.a), min(b, k.a + k.size), k.b - k.a) for k in anchors if k.a < b and k.a + k.size > a]
        if not pieces:
            out.append(None)
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
        out.append((start, end))
    return out


MIN_EXTRA = 2  # 对不上 Qwen 的文字至少有这么多个有效字（不算标点和语气词）才单独展示


def worth_showing(text):
    core = "".join(c for c in text if c not in SKIP)
    return len(core) >= MIN_EXTRA and not is_filler(text)


def leftovers(cuts, other):
    """切分后没分到任何句子的文字，也就是 Qwen 没识别出的部分。返回 [(它前面那句的序号，没有就是 -1, 文字)]。"""
    out, pos, prev = [], 0, -1
    for k, c in enumerate(cuts + [(len(other), len(other))]):
        if c is None:
            continue
        if c[0] > pos:
            text = other[pos:c[0]].strip().lstrip("".join(ENDS + CLOSERS + COMMAS))
            if worth_showing(text):
                out.append((prev, text))
        pos, prev = max(pos, c[1]), k
    return out


def _found(group, cuts, other):
    """把 leftovers 的序号换成句子：[(前一句或 None, 后一句或 None, 文字)]，前后指组内的句子。"""
    return [(group[p] if p >= 0 else None, group[p + 1] if p + 1 < len(group) else None, t) for p, t in leftovers(cuts, other)]


def attach(group, other, field):
    """Stores, in field, the piece of another transcript of the group's audio that matches each sentence.

    返回对不上任何句子的文字（格式见 _found），由调用方决定要不要单独展示。
    """
    spans, primary = [], ""
    for s in group:
        spans.append((len(primary), len(primary) + len(s["qwen"])))
        primary += s["qwen"]
    other = other.strip()
    cuts = _cuts(spans, primary, other)
    for s, c in zip(group, cuts):
        s[field] = other[c[0]:c[1]].strip() if c else ""
    return _found(group, cuts, other)


def attach_backup(chunk_segs, primary, backup, langs, field):
    """Cuts a backup model's text at the sentences of the primary text and stores it, in field, on those in langs.

    返回对不上任何句子的文字（格式见 _found）。
    """
    cuts = _cuts([tuple(s["span"]) for s in chunk_segs], primary, backup)
    for s, c in zip(chunk_segs, cuts):
        piece = backup[c[0]:c[1]].strip() if c else ""
        if piece and s["lang"] in langs:
            s[field] = piece
    return _found(chunk_segs, cuts, backup)


def spread(text, t0, t1):
    """没有逐字时间的一段文字：按句末标点拆开，t0 到 t1 的时间按字数分给各句。返回 [(起, 止, 句子)]。"""
    pieces = [p.strip() for p in re.findall(r"[^%s]+[%s%s]*" % (ENDS, ENDS, CLOSERS), text)]
    pieces = [p for p in pieces if worth_showing(p)]
    total = sum(_spoken(p) for p in pieces) or 1
    out, t = [], t0
    for p in pieces:
        dt = (t1 - t0) * _spoken(p) / total
        out.append((t, t + dt, p))
        t += dt
    return out


EXTRA_GAP = 0.5  # 前后两句之间的空隙比这短，多出来的文字多半是对齐误差，不单独展示
EXTRA_MIN_LEN = 0.3  # 插进去以后至少要有这么长
EXTRA_MAX_LEN = 6.0  # 最多这么长：没有逐字时间的一段按块铺开时，可能铺满几十秒的静音
EXTRA_DUP = 0.6  # 这么多的字已经在前后几句里出现过时，当作重复（MOSS 会把同一段听写两遍）
EXTRA_NEAR = 2  # 查重时看前后各几句
WHISPER_JUNK = ("ありがとうございました", "ご視聴", "チャンネル登録")  # Whisper 在没人说话时爱编的句子
EXTRA_WHY = "Qwen 没有识别出这段，这是其他模型听到的写法。用不上可以删掉。"
EXTRA_ORDER = ("qwen2",) + models.BACKUPS + ("moss",)  # 几份写法都不一致时，默认文字按这个顺序取


def extra_spot(segs, before, after, text):
    """给一段 Qwen 没识别出的文字估一个时间：紧跟在前一句后面，或者紧挨着后一句前面，长度按字数估。

    before / after 是它在组内的前后句；组的边界上缺的那一边从全文 segs 里找。返回 (起, 止, 语言)，放不下返回 None。
    """
    pos = {id(s): i for i, s in enumerate(segs)}
    i = pos[id(before)] + 1 if before is not None else pos[id(after)]
    prev = segs[i - 1] if i > 0 else None
    nxt = segs[i] if i < len(segs) else None
    lo = prev.get("spoken_end", prev["b"]) if prev else 0.0
    hi = nxt["a"] if nxt else lo + 30
    if hi - lo < EXTRA_GAP:
        return None
    dur = min(6.0, max(1.0, 0.25 * sum(c not in SKIP for c in text)))
    a, b = (lo, min(hi, lo + dur)) if before is not None else (max(lo, hi - dur), hi)
    return a, b, (before or after)["lang"]


def _pick(texts):
    """几份写法里选默认文字：和别的写法一致的最多者优先，一样多时按 EXTRA_ORDER。"""
    plain = {f: "".join(c for c in t if c not in SKIP) for f, t in texts.items()}
    order = [f for f in EXTRA_ORDER if f in texts]
    return texts[max(order, key=lambda f: (sum(plain[g] == plain[f] for g in order), -order.index(f)))]


def _plain(x):
    return "".join(c for c in (x or "") if c not in SKIP and not c.isspace())


def _matched(p, hay):
    """p 里有多少字能在 hay 里按顺序对上。只算连着 2 个字以上的匹配：零散的单字在几百字里总能对上。"""
    m = difflib.SequenceMatcher(None, p, hay, autojunk=False)
    return sum(b.size for b in m.get_matching_blocks() if b.size >= 2)


def _seg_text(s):
    return "".join(_plain(s.get(f)) for f in ("text", "qwen") + OTHERS)


def repeat_of(texts, segs, t):
    """几份写法大部分都已经出现在时间 t 前后几句里时，返回其中对得最多的那句，否则 None。

    这种多半是别的模型在切块交界处把旁边的话又听写了一遍，只是时间和 Qwen 对不齐（MOSS 还会把同一段听写两遍）。
    """
    i = next((k for k, s in enumerate(segs) if s["a"] > t), len(segs))
    near = segs[max(0, i - EXTRA_NEAR):i + EXTRA_NEAR]
    hay = "".join(_seg_text(s) for s in near)
    ps = [p for p in (_plain(x) for x in texts) if p]
    if not hay or not ps or any(_matched(p, hay) < len(p) * EXTRA_DUP for p in ps):
        return None
    return max(near, key=lambda s: sum(_matched(p, _seg_text(s)) for p in ps))


def _clusters(extras):
    """时间重叠的补插段合成一组：{"a", "b", "lang", "texts": {字段: [文字]}, "items": [原来的补插段]}。"""
    out = []
    for e in sorted(extras, key=lambda e: e["a"]):
        c = out[-1] if out else None
        if c and e["a"] < c["b"]:
            c["b"] = max(c["b"], e["b"])
            c["texts"].setdefault(e["field"], []).append(e["text"])
            c["items"].append(e)
        else:
            out.append({"a": e["a"], "b": e["b"], "lang": e["lang"], "texts": {e["field"]: [e["text"]]}, "items": [e]})
    return out


def _junk(c, texts):
    """不值得给用户看的一组：只有在这种语言上不可靠的模型（models 里的 weak）听到的，或者是 Whisper 常编的客套话。"""
    if all(c["lang"] in models.MODELS.get(f, {}).get("weak", ()) for f in c["texts"]):
        return True
    return list(texts) == ["whisper"] and any(j in texts["whisper"] for j in WHISPER_JUNK)


def absorb_repeats(segs, extras):
    """开了 AI 校对时，在校对前调用：和前后句重复的补插段，并到对得最多的那句 Qwen 句子上，
    记在它的 alts 里（[{"field", "text"}]），校对时作为这一句的另一份写法；返回剩下的补插段。
    """
    segs = sorted(segs, key=lambda s: s["a"])
    rest = []
    for c in _clusters(extras):
        texts = {f: "".join(ts) for f, ts in c["texts"].items()}
        if _junk(c, texts):
            continue
        target = repeat_of(texts.values(), segs, c["a"])
        if target is None:
            rest += c["items"]
            continue
        for f, t in texts.items():
            target.setdefault("alts", []).append({"field": f, "text": t})
    return rest


def place_extras(segs, extras):
    """把 Qwen 没识别出、其他模型听到的段落插成存疑字幕，重新编号后返回全部句子。

    extras 是 {"a", "b", "lang", "field", "text"}；时间重叠的合成一条，每个模型的写法放在各自的字段里。
    这些不插：只有在这种语言上不可靠的模型（models 里的 weak，如日语的 MiMo）听到的、Whisper 常编的客套话、
    大部分字已经在前后几句里出现过的（时间错位的重复；开了 AI 校对时已经由 absorb_repeats 并进句子里）。
    """
    if not extras:
        return segs
    segs = sorted(segs, key=lambda s: s["a"])
    new = []
    for c in _clusters(extras):
        texts = {f: "".join(ts) for f, ts in c["texts"].items()}
        if _junk(c, texts) or repeat_of(texts.values(), segs, c["a"]) is not None:
            continue
        prev = next((s for s in reversed(segs) if s["a"] <= c["a"]), None)
        nxt = next((s for s in segs if s["a"] > c["a"]), None)
        a = max(c["a"], prev.get("spoken_end", prev["b"]) if prev else 0.0)
        b = min(c["b"], nxt["a"], a + EXTRA_MAX_LEN) if nxt else min(c["b"], a + EXTRA_MAX_LEN)
        if b - a < EXTRA_MIN_LEN:
            continue
        new.append({
            "id": 0, "a": a, "b": b, "spoken_end": max(a, b - TAIL), "lang": c["lang"], "qwen": "", "text": _pick(texts),
            "status": "ask", "why": EXTRA_WHY, "chunk": 0, "span": [0, 0], "times": [], "speaker": "", "extra": True,
            **dict.fromkeys(OTHERS, ""), **texts,
        })
    return finalize(sorted(segs + new, key=lambda s: s["a"]))


def backups_agree(s):
    """True when some backup model heard the sentence exactly as Qwen did."""
    plain = lambda t: "".join(c for c in t if c not in SKIP)
    return any(s.get(k) and plain(s[k]) == plain(s["qwen"]) for k in models.BACKUPS)


def apply_edit(group, text):
    """Replaces adjacent sentences with DeepSeek's lines, retiming each from the original characters.

    Returns the new sentences, or None when the edit lost too much of the speech to be trusted.
    """
    # 补插段（extra）没有 Qwen 文字，用它的默认写法占位，时间按字数均摊；这些字不写进新句子的 qwen 字段
    base = [(s, s["qwen"] or (s["text"] if s.get("extra") else "")) for s in group]
    q = "".join(t for _, t in base)
    times = []
    for s, t in base:
        if s["qwen"]:
            times += (s["times"] + [None] * len(t))[:len(t)]
        else:
            end = s.get("spoken_end", s["b"])
            step = (end - s["a"]) / max(1, len(t))
            times += [(s["a"] + i * step, s["a"] + (i + 1) * step) for i in range(len(t))]
    real = [bool(s["qwen"]) for s, t in base for _ in t]
    owner = [s for s, t in base for _ in t]
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
            "id": 0, "a": a, "b": b, "lang": first["lang"], "qwen": "".join(c for c, r in zip(q[q0:q1], real[q0:q1]) if r).strip(),
            "text": line, "status": "raw", "why": "", "chunk": first["chunk"], "span": [q0, q1], "times": [], "speaker": speaker,
            **dict.fromkeys(OTHERS, ""),
        })
        if len(group) > 1:
            for f in OTHERS:
                out[-1][f] = "".join(s.get(f, "") for s in group if id(s) in sources)
        alts = [x for s in group if id(s) in sources for x in s.get("alts") or []]  # “另一遍”的写法跟着原句走
        if alts:
            out[-1]["alts"] = alts
        if not out[-1]["qwen"] and all(s.get("extra") for s in group):
            out[-1]["extra"] = True  # 仍然只有其他模型听到，Qwen 一栏为空
    if len(group) == 1:
        for f in OTHERS:
            if group[0].get(f):
                for s, piece in zip(out, cut_at([tuple(s["span"]) for s in out], q, group[0][f])):
                    s[f] = piece
    return out
