"""Cuts the audio into the chunks every model hears.

Done the way Qwen3-ASR's own toolkit and WhisperX do it: chunks of about 30 seconds, cut at the quietest moment near
that length, and nothing dropped but long stretches of plain noise. The voice detector misses quiet speech in noisy
rooms, so its result is only one hint: anything clearly louder than the room's own noise is kept too.

Qwen tends to skip what is said in the first moments of a chunk, so each chunk starts a few seconds before its cut and
"owns" only the audio from the cut on; whatever it hears earlier was already heard by the chunk before.
"""
import array
import wave

RATE = 16000
FRAME = 1600  # 100 ms
FLOOR_PCT = 0.2  # the room's noise level: the level 20% of the recording stays under
LOUD = 1.6  # a frame this many times the noise level may be speech
REACH = 5  # frames kept either side of a loud frame
DROP_GAP = 3.0  # seconds; only quiet stretches this long are left out
TARGET = 30.0  # seconds per chunk
SEARCH = 5.0  # the cut lands within this many seconds of TARGET, at the quietest 300 ms
OVERLAP = 3.0  # seconds a chunk starts before its cut
END_PAD = 0.5  # seconds a chunk runs past its cut
PAD = 0.25  # seconds kept either side of speech next to a dropped stretch


def levels(path):
    """Peak level of every 100 ms frame, and the sample count."""
    out = []
    with wave.open(str(path)) as w:
        total = w.getnframes()
        while True:
            buf = w.readframes(FRAME * 600)
            if not buf:
                break
            a = array.array("h", buf)
            for i in range(0, len(a), FRAME):
                part = a[i:i + FRAME]
                out.append(max(max(part), -min(part)))
    return out, total


def noise_floor(lv):
    return max(1, sorted(lv)[int(len(lv) * FLOOR_PCT)]) if lv else 1


def regions(vad, lv):
    """Frame spans that may hold speech: what the detector found, plus whatever stands out from the noise."""
    n = len(lv)
    keep = bytearray(n)
    for s in vad:
        for f in range(max(0, s["start"] // FRAME), min(n, -(-s["end"] // FRAME))):
            keep[f] = 1
    loud = LOUD * noise_floor(lv)
    for f, v in enumerate(lv):
        if v >= loud:
            for g in range(max(0, f - REACH), min(n, f + REACH + 1)):
                keep[g] = 1
    spans, f = [], 0
    while f < n:
        if keep[f]:
            g = f
            while g < n and keep[g]:
                g += 1
            if spans and (f - spans[-1][1]) * FRAME / RATE < DROP_GAP:
                spans[-1][1] = g
            else:
                spans.append([f, g])
            f = g
        else:
            f += 1
    return [tuple(s) for s in spans]


def _cut(a, b, lv, first=None):
    """Cuts frames a..b into pieces of about TARGET seconds, each ending at the quietest 300 ms in reach.

    The length is evened out over what is left, so no short scrap is left at the end: Qwen hears little in a few
    seconds of audio. `first` sets the first piece's length instead (the second pass moves its cuts by half a chunk).
    """
    per = RATE / FRAME
    out, n = [], 0
    while True:
        rem = (b - a) / per
        if first:
            target, first = first, None
            if rem <= target + SEARCH:
                break
        else:
            if not n:  # pieces still to make, planned once so the quiet-spot cuts cannot add a scrap
                n = max(1, round(rem / TARGET))
                n += rem / n > TARGET + SEARCH
            if n == 1:
                break
            target = rem / n
            n -= 1
        reach = min(SEARCH, target / 4)
        lo, hi = int((target - reach) * per), int((target + reach) * per)
        mid = a + target * per
        best = min(range(a + lo, a + hi), key=lambda f: (lv[f - 1] + lv[f] + lv[f + 1], abs(f - mid)))
        out.append((a, best))
        a = best
    out.append((a, b))
    return out


def make(path, vad, first=None):
    """Returns chunks in the slice format CrispASR imports. own0 and own1 (seconds) mark the part each chunk speaks for."""
    lv, total = levels(path)
    pieces = []
    for a, b in regions(vad, lv):
        cut = _cut(a, b, lv, first)
        pieces += [(x, y, k > 0, k < len(cut) - 1) for k, (x, y) in enumerate(cut)]
    out = []
    for a, b, after_cut, before_cut in pieces:
        own0, own1 = a * FRAME, min(total, b * FRAME)
        start = max(0, own0 - int((OVERLAP if after_cut else PAD) * RATE))
        end = min(total, own1 + int((END_PAD if before_cut else PAD) * RATE))
        if not after_cut:
            own0 = start
        if not before_cut:
            own1 = end
        out.append({"start": start, "end": end, "t0_cs": start // 160, "t1_cs": end // 160, "own0": own0 / RATE, "own1": own1 / RATE})
    return out
