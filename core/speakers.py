"""Decides who said each character. MOSS tells speakers apart and Qwen supplies the words, so the two are joined by text, because MOSS's clock drifts."""
import collections
import difflib

DETAIL = 30.0  # short windows keep quick replies, but MOSS restarts its numbering in each one
WIDE = 300.0  # long windows lose some quick replies, but keep one numbering for minutes at a time
OVERLAP = 60.0  # wide windows share this much audio so their numberings can be joined
MIN_RUN = 2  # characters that must match in a row; a lone match is usually a coincidence
MIN_SHARED = 6  # shared characters needed to call two numbers the same person


def windows(slices, span, overlap=0.0):
    """Groups neighbouring chunks into windows of about span seconds; returns lists of chunk indices."""
    out, i = [], 0
    while i < len(slices):
        j = i
        while j + 1 < len(slices) and slices[j + 1][1] - slices[i][0] <= span:
            j += 1
        out.append(list(range(i, j + 1)))
        if j + 1 == len(slices):
            break
        nxt = j + 1
        while nxt - 1 > i and slices[j][1] - slices[nxt - 1][0] <= overlap:
            nxt -= 1
        i = nxt
    return out


def slice_of(slices, chunks):
    """The stretch of audio a window covers, in the format CrispASR imports."""
    first, last = slices[chunks[0]][2], slices[chunks[-1]][2]
    return {"start": first["start"], "end": last["end"], "t0_cs": first["t0_cs"], "t1_cs": last["t1_cs"]}


def turns_by_window(transcription):
    out = collections.defaultdict(list)
    for r in transcription:
        out[r.get("chunk_id", 0)].append((r.get("speaker", "").strip(), r["text"]))
    return out


def match(texts, chunks, turns):
    """Labels the characters of these chunks that MOSS also heard: {(chunk, position): MOSS speaker}."""
    where = [(i, n) for i in chunks for n, c in enumerate(texts.get(i, "").strip()) if c.isalnum()]
    ours = "".join(texts[i].strip()[n] for i, n in where)
    said = [(c, who) for who, text in turns for c in text if c.isalnum()]
    theirs = "".join(c for c, _ in said)
    labels = {}
    for b in difflib.SequenceMatcher(None, ours, theirs, autojunk=False).get_matching_blocks():
        if b.size >= MIN_RUN:
            for k in range(b.size):
                labels[where[b.a + k]] = said[b.b + k][1]
    return labels


def _join(passes):
    """Gives wide windows one numbering, using the characters neighbouring windows both labelled."""
    names, count = {}, 0
    for w, labels in enumerate(passes):
        shared = collections.Counter()
        if w:
            before = passes[w - 1]
            for key, who in labels.items():
                if key in before:
                    shared[who, names[w - 1, before[key]]] += 1
        taken = set()
        for (who, person), n in shared.most_common():
            if n >= MIN_SHARED and (w, who) not in names and person not in taken:
                names[w, who] = person
                taken.add(person)
        for who in sorted(set(labels.values())):
            if (w, who) not in names:
                count += 1
                names[w, who] = count
    merged = {}
    for w, labels in enumerate(passes):
        for key, who in labels.items():
            merged.setdefault(key, names[w, who])
    return merged, count


def _place(passes, wide, count):
    """Moves each short window's speakers onto the wide numbering; one window never holds the same person twice."""
    out, seen = {}, []  # seen: people, most recently heard last
    for labels in passes:
        votes = collections.Counter((who, wide[key]) for key, who in labels.items() if key in wide)
        local, taken = {}, set()
        for (who, person), _ in votes.most_common():
            if who not in local and person not in taken:
                local[who] = person
                taken.add(person)
        for who in dict.fromkeys(labels.values()):
            if who not in local:
                # A quick reply the wide window missed: most likely someone recently in the conversation.
                free = [p for p in reversed(seen) if p not in taken] + [p for p in range(1, count + 1) if p not in taken]
                if free:
                    local[who] = free[0]
                else:
                    count += 1
                    local[who] = count
                taken.add(local[who])
        for key, who in labels.items():
            out[key] = local[who]
        for person in dict.fromkeys(local[who] for who in labels.values()):
            if person in seen:
                seen.remove(person)
            seen.append(person)
    return out


def number(segs):
    """Renumbers the speakers left in the final sentences, in order of appearance; returns their default names."""
    order = {}
    for s in segs:
        if s.get("speaker"):
            s["speaker"] = order.setdefault(s["speaker"], str(len(order) + 1))
    return {n: f"说话人{n}" for n in order.values()}


def resolve(texts, detail, wide):
    """detail and wide are (chunks, turns) pairs. Returns {chunk: speaker per character}, numbered by who speaks first."""
    short = [match(texts, chunks, turns) for chunks, turns in detail]
    long_ = [match(texts, chunks, turns) for chunks, turns in wide]
    labels = _place(short, *_join(long_))
    order = {}
    result = {}
    for i in sorted(texts):
        text = texts[i].strip()
        who = [labels.get((i, n)) for n in range(len(text))]
        for p in who:
            if p is not None:
                order.setdefault(p, str(len(order) + 1))
        result[i] = [order.get(p) for p in who]
    return result
