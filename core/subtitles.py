"""Turns segments into timed cues and writes SRT, ASS, VTT and TXT."""
import re
from pathlib import Path

MAX_CHARS = 28
_SENT = re.compile(r"[^。！？!?．.…]+[。！？!?．.…]*")
_CLAUSE = re.compile(r"[^，、,；;]+[，、,；;]*")


def _pieces(text):
    """Splits long text at sentence ends, then at commas, keeping each cue readable."""
    if len(text) <= MAX_CHARS:
        return [text]
    out = []
    for sent in _SENT.findall(text):
        if len(sent) <= MAX_CHARS:
            out.append(sent)
            continue
        cur = ""
        for clause in _CLAUSE.findall(sent):
            if cur and len(cur) + len(clause) > MAX_CHARS:
                out.append(cur)
                cur = ""
            cur += clause
        if cur:
            out.append(cur)
    return [p.strip() for p in out if p.strip()] or [text]


def cues(segments, names=None):
    """Long segments share their time span in proportion to character count. names maps speaker ids to display names."""
    result = []
    for s in segments:
        text = s["text"].strip()
        if not text:
            continue
        parts = _pieces(text)
        total = sum(len(p) for p in parts)
        t = s["a"]
        who = (names or {}).get(s.get("speaker"), "")
        for p in parts:
            end = t + (s["b"] - s["a"]) * len(p) / total
            result.append((t, end, p, who))
            t = end
    return result


def _tc(t, sep=","):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02}{sep}{ms:03}"


def _ass_tc(t):
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _tagged(who, t):
    return f"[{who}] {t}" if who else t


def srt(c):
    return "\n".join(f"{i}\n{_tc(a)} --> {_tc(b)}\n{_tagged(w, t)}\n" for i, (a, b, t, w) in enumerate(c, 1))


def vtt(c):
    return "WEBVTT\n\n" + "\n".join(f"{_tc(a, '.')} --> {_tc(b, '.')}\n{f'<v {w}>' if w else ''}{t}\n" for a, b, t, w in c)


def txt(c):
    return "\n".join(f"{w}：{t}" if w else t for _, _, t, w in c) + "\n"


def ass(c):
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,60,&H00FFFFFF,&H000000FF,&H00303030,&H00000000,0,0,0,0,100,100,0,0,1,2.5,0,2,60,60,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{_ass_tc(a)},{_ass_tc(b)},Default,{w},0,0,0,,{_tagged(w, t)}" for a, b, t, w in c]
    return head + "\n".join(lines) + "\n"


WRITERS = {"SRT": (srt, ".srt"), "VTT": (vtt, ".vtt"), "ASS": (ass, ".ass"), "TXT": (txt, ".txt")}


def _free_path(path):
    """Never overwrites a file this job did not write."""
    if not path.exists():
        return path
    n = 2
    while True:
        p = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not p.exists():
            return p
        n += 1


def export(job, formats):
    src = Path(job["source"])
    c = cues(job["segments"], job.get("speakers") if job.get("speaker_tags", True) else None)
    known = job.setdefault("exports", {})
    written = []
    for fmt in formats:
        fn, ext = WRITERS[fmt]
        path = Path(known[fmt]) if fmt in known else _free_path(src.with_suffix(ext))
        # SRT and ASS get a BOM because many Windows players misread them without one.
        path.write_text(fn(c), encoding="utf-8-sig" if fmt in ("SRT", "ASS") else "utf-8")
        known[fmt] = str(path)
        written.append(str(path))
    return written
