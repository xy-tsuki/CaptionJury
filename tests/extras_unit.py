"""Qwen 漏掉、其他模型听到的文字：切分时找出来，估好时间，插成存疑字幕。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import sentences


def seg(i, text, a, b, lang="zh", **kw):
    return dict({"id": i, "a": a, "b": b, "spoken_end": b - sentences.TAIL, "lang": lang, "qwen": text, "text": text,
                 "status": "ok", "why": "", "speaker": ""}, **kw)


def group(*texts):
    out, pos = [], 0
    for i, (t, a, b) in enumerate(texts, 1):
        out.append(seg(i, t, a, b, span=[pos, pos + len(t)]))
        pos += len(t)
    return out


# 夹在两句中间的一句，Qwen 没有，备用模型有：切分照旧，多出来的找得到，并知道前后是哪两句。
g = group(("今天天气很好。", 0.0, 2.0), ("我们去公园。", 6.0, 8.0))
found = sentences.attach_backup(g, "今天天气很好。我们去公园。", "今天天气很好中间多出来一句话我们去公园", ("zh",), "whisper")
assert [s["whisper"] for s in g] == ["今天天气很好", "我们去公园"], [s["whisper"] for s in g]
assert [(b["id"], a["id"], t) for b, a, t in found] == [(1, 2, "中间多出来一句话")], found

# 末尾多出来的、开头多出来的。
g = group(("今天天气很好。", 3.0, 5.0),)
found = sentences.attach(g, "说起来啊今天天气很好。然后吃饭吧。", "moss")
assert g[0]["moss"] == "今天天气很好。", g
assert [(b and b["id"], a and a["id"], t) for b, a, t in found] == [(None, 1, "说起来啊"), (1, None, "然后吃饭吧。")], found

# 一个字、语气词、只有标点的不算。
assert not sentences.leftovers([(0, 3)], "你好。嗯")
assert not sentences.leftovers([(0, 3)], "你好。啊啊")
assert not sentences.leftovers([(3, 6)], "。，你好。")
assert sentences.leftovers([(0, 3)], "你好。真的吗")  == [(0, "真的吗")]

# 对不上的句子（None）不影响后面的位置。
assert sentences.leftovers([(0, 3), None, (6, 9)], "你好。多一句再见。") == [(0, "多一句")]

# 估时间：跟在前一句后面；空隙太小就不插。
segs = [seg(1, "今天天气很好。", 0.0, 2.0), seg(2, "我们去公园。", 6.0, 8.0)]
a, b, lang = sentences.extra_spot(segs, segs[0], segs[1], "中间多出来一句话")
assert a == segs[0]["spoken_end"] and b == a + 2.0 and lang == "zh", (a, b)
a, b, _ = sentences.extra_spot(segs, None, segs[1], "说起来")  # 紧挨着后一句
assert b == 6.0 and a == 5.0, (a, b)
tight = [seg(1, "今天天气很好。", 0.0, 2.0), seg(2, "我们去公园。", 2.1, 4.0)]
assert sentences.extra_spot(tight, tight[0], tight[1], "多出来的") is None

# 插入：几个模型听到同一段时合成一条，默认采用写法一致最多的那份，标成存疑，Qwen 一栏为空。
extras = [
    {"a": 1.85, "b": 3.85, "lang": "zh", "field": "whisper", "text": "中间多出来一句话"},
    {"a": 1.85, "b": 3.6, "lang": "zh", "field": "mimo", "text": "中间多出来一句话。"},
    {"a": 1.9, "b": 3.0, "lang": "zh", "field": "moss", "text": "中间多处来一句"},
]
out = sentences.place_extras([dict(s) for s in segs], extras)
assert [s["id"] for s in out] == [1, 2, 3]
new = out[1]
assert new["qwen"] == "" and new["status"] == "ask" and new["why"] == sentences.EXTRA_WHY, new
assert new["text"] == "中间多出来一句话。", new["text"]  # MiMo 和 Whisper 一致，MiMo 排在前面
assert new["whisper"] == "中间多出来一句话" and new["moss"] == "中间多处来一句" and new["qwen2"] == ""
assert out[0]["b"] <= new["a"] < new["b"] <= out[2]["a"], out
# 只有一份写法时就用它；时间不重叠的分成两条。
out = sentences.place_extras([dict(s) for s in segs], [
    {"a": 1.85, "b": 2.9, "lang": "zh", "field": "moss", "text": "第一段"},
    {"a": 4.5, "b": 6.0, "lang": "zh", "field": "whisper", "text": "第二段"},
])
assert [s["text"] for s in out] == ["今天天气很好。", "第一段", "第二段", "我们去公园。"], [s["text"] for s in out]
# 没有空隙的不插。
assert len(sentences.place_extras([dict(s) for s in tight], [{"a": 1.9, "b": 2.1, "lang": "zh", "field": "moss", "text": "多出"}])) == 2
assert sentences.place_extras(segs, []) is segs

# 整块都没有 Qwen 的句子时，MOSS 的文字按句拆开，时间按字数分，不出一条三十多秒的字幕。
parts = sentences.spread("大丈夫？その手続ちょっと急いでんだけど。うん。", 36.0, 48.0)
assert [p for _, _, p in parts] == ["大丈夫？", "その手続ちょっと急いでんだけど。"], parts  # "うん。"是语气词
assert parts[0][0] == 36.0 and abs(parts[-1][1] - 48.0) < 1e-9 and parts[0][1] == parts[1][0]

# MOSS 卡住时的输出：说话人标记、一行里的重复、连着很多行的同一句都只留一遍；连着两遍的照常保留。
from core import speakers
turns = [("S01", "問題ない？"), ("S01", "うん？")] + [("S01", "2月1日")] * 30 + [("S02", "陣[S02]形を切って"), ("S01", "はい"), ("S01", "はい")]
assert speakers.clean_turns(turns) == [("S01", "問題ない？"), ("S01", "うん？"), ("S01", "2月1日"), ("S02", "陣形を切って"), ("S01", "はい"), ("S01", "はい")]
# 日语里只有 MiMo 听到的不插；有别的模型一起听到、或者是中文时照常插。
ja = [dict(s, lang="ja") for s in segs]
assert len(sentences.place_extras([dict(s) for s in ja], [{"a": 1.85, "b": 3.0, "lang": "ja", "field": "mimo", "text": "コルン、コルンの"}])) == 2
assert len(sentences.place_extras([dict(s) for s in ja], [
    {"a": 1.85, "b": 3.0, "lang": "ja", "field": "mimo", "text": "だめ"},
    {"a": 1.9, "b": 2.8, "lang": "ja", "field": "whisper", "text": "だめ"},
])) == 3
assert len(sentences.place_extras([dict(s) for s in ja], [{"a": 1.85, "b": 3.0, "lang": "ja", "field": "whisper", "text": "だめ"}])) == 3
assert len(sentences.place_extras([dict(s) for s in segs], [{"a": 1.85, "b": 3.0, "lang": "zh", "field": "mimo", "text": "中间多出来一句话"}])) == 3
print("all passed")
