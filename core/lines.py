"""校对页按导出的样子拆好字幕条：长句按每条字数拆成几条，各自有文字、时间和各模型的写法。

改了每条字数时，先把同一句拆出来的几条（src 相同）拼回原句，再按新的字数重新拆。
"""
import difflib
import uuid

from . import sentences, subtitles

ORDER = ("ask", "fix", "raw", "keep", "ok")  # 拼回原句时，状态取最需要注意的那个


def _rank(status):
    return ORDER.index(status) if status in ORDER else ORDER.index("raw")


def _ct(s):
    """和 qwen 逐字对应的开始时间，长度不够的补 None。"""
    q = s.get("qwen", "")
    return ((s.get("ct") or []) + [None] * len(q))[:len(q)]


def merge(group):
    """把同一句拆出来的几条拼回去。"""
    s = dict(group[0])
    if len(group) == 1:
        return s
    s["text"] = "".join(x["text"] for x in group)
    s["qwen"] = "".join(x.get("qwen", "") for x in group)
    if any(x.get("ct") for x in group):
        s["ct"] = [t for x in group for t in _ct(x)]
    s["b"] = group[-1]["b"]
    full = group[0].get("full") or {}  # 拆开前各模型的整句写法，拼回时原样还原，不受切分误差影响
    for f in sentences.OTHERS:
        s[f] = full.get(f, "".join(x.get(f, "") for x in group))
    alts = [a for x in group for a in x.get("alts") or []]
    if alts:
        s["alts"] = alts
    s["status"] = min((x.get("status", "raw") for x in group), key=_rank)
    s["why"] = "；".join(dict.fromkeys(x["why"] for x in group if x.get("why")))
    s["extra"] = all(x.get("extra") for x in group)
    if not s["extra"]:
        s.pop("extra")
    return s


def _qpos(text, qwen):
    """文字里每个下标对到 qwen 里的下标（长度 len(text)+1，单调不减）。"""
    pos = [None] * (len(text) + 1)
    for b in difflib.SequenceMatcher(None, qwen, text, autojunk=False).get_matching_blocks():
        for k in range(b.size):
            pos[b.b + k] = b.a + k
    pos[len(text)] = len(qwen)
    for i in range(len(text) - 1, -1, -1):  # 对不上的字跟着后面第一个对得上的字
        if pos[i] is None:
            pos[i] = pos[i + 1]
    pos[0] = 0
    for i in range(1, len(pos)):
        pos[i] = max(pos[i], pos[i - 1])
    return pos


def split(s, max_chars):
    """按每条字数拆开一句；不用拆时原样返回。"""
    parts = subtitles.pieces(s, max_chars)
    if len(parts) <= 1:
        s.pop("full", None)
        s.pop("src", None)
        return [s]
    text, qwen = s["text"], s.get("qwen", "")
    pos = _qpos(text, qwen)
    qspans = [(pos[x], pos[y]) for x, y, _, _ in parts]
    qspans[0] = (0, qspans[0][1])
    qspans[-1] = (qspans[-1][0], len(qwen))
    ct = _ct(s) if s.get("ct") else None
    full = {f: s[f] for f in sentences.OTHERS if s.get(f)}
    cut = {f: sentences.cut_at(qspans, qwen, t) if qwen else [] for f, t in full.items()}
    src = s.get("src") or uuid.uuid4().hex[:8]
    out = []
    for k, ((x, y, t0, t1), (q0, q1)) in enumerate(zip(parts, qspans)):
        p = {key: v for key, v in s.items() if key not in ("alts", "full")}
        p.update(text=text[x:y].strip(), qwen=qwen[q0:q1], a=round(t0, 3), b=round(t1, 3), src=src)
        if ct is not None:
            p["ct"] = ct[q0:q1]
        for f in sentences.OTHERS:
            got = cut.get(f) or []
            # 切不开（比如 Qwen 一栏为空的补插段）时，整句写法放在第一条
            p[f] = got[k] if any(got) else (full.get(f, "") if k == 0 else "")
        if k == 0:
            p["full"] = full
            if s.get("alts"):
                p["alts"] = s["alts"]
        out.append(p)
    return out


def resplit(segs, max_chars):
    """把整个任务按 max_chars 重新拆一遍，返回重新编号的句子。"""
    groups = []
    for s in segs:
        if groups and s.get("src") and groups[-1][-1].get("src") == s["src"]:
            groups[-1].append(s)
        else:
            groups.append([s])
    out = [p for g in groups for p in split(merge(g), max_chars) if p["text"]]
    for n, s in enumerate(out, 1):
        s["id"] = n
    return out


def ensure(job):
    """任务还没按现在的每条字数拆过时拆一遍，返回有没有改动。"""
    kind = job.get("line_max") or "wide"
    if job.get("split") == kind:
        return False
    job["segments"] = resplit(job["segments"], subtitles.line_max(job))
    job["split"] = kind
    return True
