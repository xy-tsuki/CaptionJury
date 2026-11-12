"""把句子排成字幕条，写出 SRT、ASS、VTT 和 TXT。"""
import os
import re
from pathlib import Path

MAX_CHARS = 28
# 导出时每条字幕的字数上限：横屏、竖屏短视频、不限（只在句子太长时才拆）
LINE_MAX = {"wide": 18, "tall": 12, "full": MAX_CHARS}
ENDS = "。！？!?．…"
COMMAS = "，、,；;：:"
NO_START = set(ENDS + COMMAS + "」』）)】\"'”’ー～~ゃゅょっぁぃぅぇぉゎャュョッァィゥェォヮ")  # 不能放在一条字幕开头的字
MIN_PIECE = 2


def char_times(text, qwen, ct):
    """把 Qwen 每个字的开始时间（ct，和 qwen 逐字对应）对到改过的文字上：对得上的字用它的时间，对不上的是 None。"""
    out = [None] * len(text)
    if not ct or not qwen:
        return out
    import difflib
    for b in difflib.SequenceMatcher(None, qwen, text, autojunk=False).get_matching_blocks():
        for k in range(b.size):
            if b.a + k < len(ct):
                out[b.b + k] = ct[b.a + k]
    return out


JA_PARTICLES = set("はがをにでともへ")  # 名词后面跟这些助词时，助词后面多半是词的分界
JA_AFTER = ("とか", "けど", "から", "まで", "だけ", "って", "けれど", "ので", "のに", "たら", "ても", "でも")  # 这些说法后面适合断开
ZH_AFTER = set("的了吗呢吧啊呀着过")
# 常见的词，不从中间断开
NO_SPLIT = ("我们", "你们", "他们", "她们", "咱们", "什么", "怎么", "这么", "那么", "没有", "可以", "因为", "所以", "但是",
            "如果", "就是", "还是", "已经", "现在", "时候", "的话", "知道", "觉得",
            "いい", "ない", "ます", "です", "ござい", "ちゃん", "っぽい", "ちょっと", "やっぱ", "みたい")  # 中文在这些字后面断开比较自然
ZH_BEFORE = ("但是", "所以", "因为", "然后", "不过", "而且", "如果", "就是", "可是")


def _kind(c):
    if "ぁ" <= c <= "ゟ":
        return "hira"
    if "゠" <= c <= "ヿ":
        return "kata"
    if "一" <= c <= "鿿" or c in "々〆":
        return "han"
    return "other"


def _score(text, times, p, n):
    """在第 p 个字前面断开的好坏：句末标点后最好，其次逗号后，再看词的分界和停顿长短，越靠中间越好。

    没有分词工具，词的分界按字形猜：日语“汉字或片假名 + 助词”之后、平假名转汉字或片假名处；
    片假名词、汉字词中间尽量不断。中文在“的、了”之类后面、“但是、所以”之类前面断开。
    """
    prev, cur = text[p - 1], text[p]
    s = 10 if prev in ENDS else 6 if prev in COMMAS else 0
    kp, kc = _kind(prev), _kind(cur)
    if prev in JA_PARTICLES and p >= 2 and _kind(text[p - 2]) in ("han", "kata"):
        s += 3
    if kp == "hira" and kc in ("han", "kata"):
        s += -3 if prev in "おご" else 2  # お、ご 是接头词，和后面的词连着
    if kp == "han" and kc == "hira":
        s -= 2.5  # 汉字后面的平假名多半是送假名或助词，和前面连着
    if kp == "kata" and kc == "hira":
        s += 1.5  # 片假名词结束
    if any(w in text[max(0, p - k):p - k + len(w)] and text.startswith(w, p - k) for w in JA_AFTER for k in range(1, len(w))):
        s -= 4  # 不在“とか、けど”这类说法中间断开
    if any(text.endswith(w, 0, p) for w in JA_AFTER):
        s += 3
    elif kp == kc == "hira" and prev not in JA_PARTICLES and prev not in "てねよ":
        # 两个平假名中间多半是一个词的中间；前一个是“う、る、た、い”这类词尾、后面又不是助词时，可能是词的分界
        s -= 1 if prev in "うるたい" and cur not in "でてじゃよねなのかけっ" else 2.5
    if cur in "ねよな" and kp == "hira":
        s -= 6 if cur in "ねよ" else 2  # 句末的“ね、よ、な”跟着前面的话，“ね、よ”几乎不会放在条首
    if any(text.startswith(w, p - k) for w in NO_SPLIT for k in range(1, len(w)) if p - k >= 0):
        s -= 4
    if kp == kc == "kata":
        s -= 4
    if kp == kc == "han" and not any(_kind(c) == "hira" for c in text):  # 中文：看虚词
        s += 2.5 if prev in ZH_AFTER and cur != "话" else -3 if prev == "的" else 0  # “的话”连着
        s += 2.5 if text.startswith(ZH_BEFORE, p) else 0
    elif kp == kc == "han":
        s -= 3.5  # 日语里汉字连着的多半是一个词
    t0 = next((times[k] for k in range(p - 1, -1, -1) if times[k] is not None), None)
    t1 = next((times[k] for k in range(p, n) if times[k] is not None), None)
    if t0 is not None and t1 is not None:
        s += min(3.0, max(0.0, t1 - t0 - 0.25) * 4)  # 字和字之间明显的停顿
    return s - abs(p - n / 2) / n * 2


def split_line(text, times, max_chars):
    """把一句切成不超过 max_chars 个字的几条，返回各条的 (起, 止) 下标。

    优先在句末标点后断开，其次逗号后，再其次停顿最长的地方；断点只在中间一段里找，两边都不少于 MIN_PIECE 个字。
    """
    n = len(text)
    if n <= max_chars:
        return [(0, n)]
    lo, hi = max(MIN_PIECE, int(n * 0.25)), min(n - MIN_PIECE, int(n * 0.75) + 1)
    cands = [p for p in range(lo, hi) if text[p] not in NO_START and not text[p].isspace()] or [n // 2]
    p = max(cands, key=lambda p: _score(text, times, p, n))
    left = split_line(text[:p], times[:p], max_chars)
    right = split_line(text[p:], times[p:], max_chars)
    return left + [(a + p, b + p) for a, b in right]


def plain(text):
    """去掉标点：句号全部删掉，逗号和日语顿号换成空格。"""
    text = re.sub(r"[。．]", "", text)
    text = re.sub(r"[，、,]", " ", text)
    return re.sub(r" {2,}", " ", text).strip()


def cues(segments, names=None, no_punct=False, max_chars=MAX_CHARS):
    """把句子排成字幕条。超过 max_chars 个字的句子拆开，按逐字时间（ct）定拆开处的时间，没有逐字时间时按字数分摊。

    names 把说话人编号对应到显示名；no_punct 时去掉标点（见 plain）。
    """
    result = []
    for s in segments:
        raw = s["text"]
        lead = len(raw) - len(raw.lstrip())
        text = raw.strip()
        if not text:
            continue
        times = char_times(raw, s.get("qwen", ""), s.get("ct"))[lead:lead + len(text)]
        spans = split_line(text, times, max_chars)
        a, b = s["a"], s["b"]
        who = (names or {}).get(s.get("speaker"), "")
        starts = [a]
        for x, _ in spans[1:]:
            guess = a + (b - a) * x / len(text)  # 按字数分摊的时间
            known = times[x] if times[x] is not None else guess
            starts.append(min(max(known, starts[-1] + 0.2), b - 0.2) if b - starts[-1] > 0.4 else guess)
        for (x, y), t0, t1 in zip(spans, starts, starts[1:] + [b]):
            piece = text[x:y].strip()
            if piece:
                result.append((t0, t1, plain(piece) if no_punct else piece, who))
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


# 源文件所在的文件夹写不进去时（比如在 Program Files 里、文件夹被删了），改存到这里
FALLBACK = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents" / "听写字幕"


def _free_path(path):
    """不覆盖不是这个任务写的文件。"""
    if not path.exists():
        return path
    n = 2
    while True:
        p = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not p.exists():
            return p
        n += 1


def export(job, formats):
    """写出各格式的字幕，返回写好的路径。优先写在源文件旁边，写不进去就写到 FALLBACK。"""
    src = Path(job["source"])
    c = cues(job["segments"], job.get("speakers") if job.get("speaker_tags") else None, job.get("no_punct", False),
             LINE_MAX.get(job.get("line_max") or "wide", MAX_CHARS))
    known = job.setdefault("exports", {})
    written = []
    for fmt in formats:
        fn, ext = WRITERS[fmt]
        path = Path(known[fmt]) if fmt in known else _free_path(src.with_suffix(ext))
        text, enc = fn(c), "utf-8-sig" if fmt in ("SRT", "ASS") else "utf-8"  # SRT 和 ASS 带 BOM，不少 Windows 播放器没有它会读错
        try:
            path.write_text(text, encoding=enc)
        except OSError:
            FALLBACK.mkdir(parents=True, exist_ok=True)
            path = _free_path(FALLBACK / (src.stem + ext))
            path.write_text(text, encoding=enc)
        known[fmt] = str(path)
        written.append(str(path))
    return written
