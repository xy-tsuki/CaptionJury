"""按读音算字错率：写法不同、读音相同的不算错（日语的汉字和假名、同音的写法）。可以一次评多段视频。

    PY tests/cer_kana.py 参考稿.txt 结果.srt|job.json [...] [-- 参考稿2.txt 结果2 ...] [-v]

- 每组以参考稿开头，后面跟这段视频的一份或多份结果，组和组之间用 -- 分开。
- 结果是 SRT 时算整份字幕；是 job.json 时，各模型的结果（qwen、qwen2、moss、备用模型）和最终文字（text）各算一遍。
- 参考稿每行一句。空行、以 # 开头的行、以「コメント」或括号开头的行（弹幕、注释）不算；[mm:ss] 前缀去掉。
- 参考稿里有假名时按读音比（日语、中日混说）；没有假名时按字比，和 tests/cer.py 一样。
- 最后按参考稿长度加权，给出所有组合起来的字错率。

需要 pykakasi（只有这个脚本用，不进 requirements.txt）：PY -m pip install pykakasi
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cer import errors, norm, score  # noqa: E402

FIELDS = ("qwen", "qwen2", "moss", "mimo", "whisper", "parakeet", "text")
SKIP_PREFIX = ("#", "コメント", "（", "(")
KANA = re.compile(r"[ぁ-ゟァ-ヿ]")
DIGITS = "〇一二三四五六七八九"

try:
    import pykakasi
    _kks = pykakasi.kakasi()
except ImportError:  # 按字比时用不到
    _kks = None


def _kanji_number(n):
    """把阿拉伯数字写成汉字数字（20 → 二十），让它和参考稿里的汉字数字读音一致。"""
    n = int(n)
    if n == 0:
        return "〇"
    if n >= 10 ** 8:
        return "".join(DIGITS[int(d)] for d in str(n))
    out = ""
    for unit, name in ((10 ** 4, "万"), (1000, "千"), (100, "百"), (10, "十")):
        k, n = divmod(n, unit)
        if k:
            out += (_kanji_number(k) if unit == 10 ** 4 else "" if k == 1 else DIGITS[k]) + name
    return out + (DIGITS[n] if n else "")


def kana(text):
    """只留平假名的读音：汉字换成读音，片假名换成平假名，标点、长音、空格都去掉。"""
    if _kks is None:
        sys.exit("按读音比需要 pykakasi：PY -m pip install pykakasi")
    text = re.sub(r"\d+", lambda m: _kanji_number(m.group()), text)
    hira = "".join(x["hira"] for x in _kks.convert(text))
    return "".join(c for c in hira if "ぁ" <= c <= "ゟ")


def ref_lines(path):
    out = []
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if line and not line.startswith(SKIP_PREFIX):
            out.append(re.sub(r"^\[\d+:\d+\]\s*", "", line))
    return out


def srt_text(path):
    out = []
    for block in open(path, encoding="utf-8-sig").read().split("\n\n"):
        rows = block.strip().splitlines()
        if len(rows) >= 3:
            out += [re.sub(r"^\[[^\]]*\]\s*|^说话人\d+[:：]\s*", "", r) for r in rows[2:]]
    return "".join(out)


def results(path):
    """{名字: 原文}：SRT 一份，job.json 每个有内容的字段一份。"""
    if path.endswith(".json"):
        segs = json.load(open(path, encoding="utf-8"))["segments"]
        return {f: "".join(s.get(f) or "" for s in segs) for f in FIELDS if any(s.get(f) for s in segs)}
    return {"srt": srt_text(path)}


def groups(args):
    out, cur = [], []
    for a in args:
        if a == "--":
            out.append(cur)
            cur = []
        else:
            cur.append(a)
    out.append(cur)
    return [g for g in out if len(g) >= 2]


def main(argv):
    verbose = "-v" in argv
    gs = groups([a for a in argv if a != "-v"])
    if not gs:
        sys.exit(__doc__)
    totals = {}  # 名字 → [错字数, 参考稿长度]
    for ref_path, *paths in gs:
        raw = "".join(ref_lines(ref_path))
        by_kana = bool(KANA.search(raw))
        conv = kana if by_kana else norm
        ref = conv(raw)
        print(f"== {ref_path}：{'按读音' if by_kana else '按字'}比较，参考稿 {len(ref)} 个{'假名' if by_kana else '字'}")
        for path in paths:
            for name, text in results(path).items():
                rate, (s, d, i), pairs = score(ref, conv(text))
                print(f"  {rate:7.2%}  替换{s:4} 漏{d:4} 多{i:4}  {name:8} {path}")
                t = totals.setdefault(f"{name} ({Path(path).suffix})", [0, 0])
                t[0] += s + d + i
                t[1] += len(ref)
                if verbose:
                    print("           " + "  ".join(errors(pairs)))
    if len(gs) > 1:
        print("== 合计（按参考稿长度加权）")
        for name, (err, n) in totals.items():
            print(f"  {err / max(1, n):7.2%}  {name}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1:])
