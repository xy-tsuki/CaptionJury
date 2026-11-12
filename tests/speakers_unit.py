"""Checks speaker numbering, sentence cuts and export without running any model."""
import os, sys, tempfile

os.environ["TINGXIE_HOME"] = tempfile.mkdtemp(prefix="tingxie-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import sentences, speakers, subtitles


def sl(a, b):
    return (a, b, {"start": int(a * 16000), "end": int(b * 16000), "t0_cs": int(a * 100), "t1_cs": int(b * 100)})


# Windows cover every chunk, in order, and wide ones overlap.
slices = [sl(i * 20, i * 20 + 18) for i in range(40)]
detail = speakers.windows(slices, 30)
assert sum(len(g) for g in detail) == 40 and [i for g in detail for i in g] == list(range(40))
wide = speakers.windows(slices, 300, 60)
assert len(wide) > 2 and all(set(a) & set(b) for a, b in zip(wide, wide[1:])) and wide[-1][-1] == 39
assert speakers.windows([sl(0, 500), sl(501, 900)], 300, 60) == [[0], [1]]

# Short windows restart at S1, so the same number in two windows can be two people.
texts = {0: "今天天气很好我们出门吧", 1: "好的我去拿一下东西", 2: "今天天气很好我们出门吧好的我去拿"}
short = [([0], [("S1", "今天天气很好我们出门吧")]), ([1], [("S1", "好的我去拿一下东西")]), ([2], [("S1", "今天天气很好我们出门吧"), ("S2", "好的我去拿")])]
long_ = [([0, 1, 2], [("S1", "今天天气很好我们出门吧"), ("S2", "好的我去拿一下东西"), ("S1", "今天天气很好我们出门吧"), ("S2", "好的我去拿")])]
who = speakers.resolve(texts, short, long_)
assert set(who[0]) == {"1"} and set(who[1]) == {"2"}, who
assert who[2][0] == "1" and who[2][-1] == "2", who

# A quick reply the wide window missed goes to someone else in the conversation, not the same person.
texts = {0: "你好吗我很好谢谢", 1: "吃饭了吗还没有呢"}
short = [([0], [("S1", "你好吗"), ("S2", "我很好谢谢")]), ([1], [("S1", "吃饭了吗"), ("S2", "还没有呢")])]
long_ = [([0, 1], [("S1", "你好吗"), ("S2", "我很好谢谢"), ("S1", "吃饭了吗还没有呢")])]
who = speakers.resolve(texts, short, long_)
assert who[1][0] != who[1][-1], who

# Numbering joins across wide windows when they share enough speech, and starts a new number when they do not.
a = {("c", n): "A" for n in range(10)}
b = {("c", n): "B" for n in range(10)}
b.update({("d", n): "C" for n in range(10)})
merged, count = speakers._join([a, b])
assert count == 2 and merged["c", 0] == 1 and merged["d", 0] == 2

# A sentence is cut where the speaker changes, also without punctuation between them.
text = "我感觉啥咋说"
assert sentences._cut_speakers([(0, 6)], list("111222")) == [(0, 3), (3, 6)]
assert sentences._cut_speakers([(0, 6)], list("112111")) == [(0, 6)]
assert sentences._cut_speakers([(0, 6)], [None, None, "1", "1", "2", "2"]) == [(0, 4), (4, 6)]
segs = sentences.build(sentences.stitch([sl(0, 6)], {0: text}, {}, {0: list("111222")}), "zh")
assert [(s["text"], s["speaker"]) for s in segs] == [("我感觉", "1"), ("啥咋说", "2")], segs

# Sentences keep their speaker through DeepSeek's edits.
g = [{"qwen": "金公塔在哪", "times": [], "speaker": "2", "a": 0, "b": 1, "lang": "zh", "alt": "", "alt_name": "", "chunk": 0}]
assert sentences.apply_edit(g, "晴空塔在哪")[0]["speaker"] == "2"
# “另一遍”的写法也跟着原句走：改写、拆分、合并后都还在
g[0]["alts"] = [{"field": "moss", "text": "晴空塔在哪呀"}]
assert sentences.apply_edit(g, "晴空塔在哪")[0]["alts"] == g[0]["alts"]
assert all(s["alts"] == g[0]["alts"] for s in sentences.apply_edit(g, "晴空塔\n在哪"))
g2 = g + [{"qwen": "好", "times": [], "speaker": "2", "a": 1, "b": 2, "lang": "zh", "chunk": 0}]
assert sentences.apply_edit(g2, "晴空塔在哪好")[0]["alts"] == g[0]["alts"]

# 补插段（extra）可以和 Qwen 的句子一起改：它的字只占位，不写进 qwen；时间从补插段开始
x = {"qwen": "", "text": "我们明天早上", "times": [], "speaker": "", "a": 10.0, "b": 11.2, "spoken_end": 11.2, "lang": "zh", "chunk": 0, "extra": True, "whisper": "我们明天早上"}
q = {"qwen": "八点见吧。", "times": [(11.3 + i * 0.2, 11.5 + i * 0.2) for i in range(5)], "speaker": "1", "a": 11.3, "b": 12.3, "lang": "zh", "chunk": 0}
new = sentences.apply_edit([x, q], "我们明天早上八点见吧。")
assert len(new) == 1 and new[0]["qwen"] == "八点见吧。" and new[0]["a"] == 10.0 and new[0]["speaker"] == "1" and "extra" not in new[0], new
only = sentences.apply_edit([x], "我们明天早上")
assert only[0]["qwen"] == "" and only[0]["extra"] and only[0]["whisper"] == "我们明天早上", only

# 整句改写（和原来的字对不上大半）时 apply_edit 不接，rough_edit 照用 AI 的文字，时间按字数均摊
y = {"qwen": "かぐらめは聖戦装。", "times": [], "speaker": "1", "a": 12.0, "b": 13.2, "spoken_end": 13.2, "lang": "ja", "chunk": 0, "whisper": "かぐらめは生前葬"}
x2 = dict(x, text="カグラムや正々そう", whisper="カグラムや正々そう", a=11.0, b=12.0, spoken_end=12.0)
assert sentences.apply_edit([x2, y], "神楽めあ生前葬。") is None
r = sentences.rough_edit([x2, y], "神楽めあ生前葬。\nいいね。")
assert [s["text"] for s in r] == ["神楽めあ生前葬。", "いいね。"] and r[0]["a"] == 11.0 and abs(r[-1]["b"] - 13.2) < 1e-9
assert r[0]["qwen"] == "かぐらめは聖戦装。" and r[0]["whisper"] == "カグラムや正々そうかぐらめは生前葬" and r[0]["speaker"] == "1" and "extra" not in r[0], r

# 没说完、不停顿接着说的半句合成一句；有句末标点、换了人、停顿长或合起来太长的不合
def fs(text, a, b, who="1"):
    return {"qwen": text, "text": text, "times": [(a, b)] * len(text), "a": a, "b": b, "speaker": who, "span": [0, len(text)]}


m = sentences.merge_fragments([fs("我们明天早上", 0, 1), fs("八点见吧。", 1.1, 2)])
assert len(m) == 1 and m[0]["qwen"] == "我们明天早上八点见吧。" and m[0]["b"] == 2 and len(m[0]["times"]) == len(m[0]["qwen"]), m
assert len(sentences.merge_fragments([fs("好的。", 0, 1), fs("走吧。", 1.1, 2)])) == 2
assert len(sentences.merge_fragments([fs("我们明天早上", 0, 1), fs("八点见吧。", 1.1, 2, "2")])) == 2
assert len(sentences.merge_fragments([fs("我们明天早上", 0, 1), fs("八点见吧。", 1.5, 2)])) == 2
assert len(sentences.merge_fragments([fs("我们明天早上一起去那个", 0, 1), fs("新开的商场里面看看有没有打折的衣服。", 1.1, 2)])) == 2

# Numbers follow first appearance and skip people with no sentence left.
segs = [{"speaker": "3"}, {"speaker": ""}, {"speaker": "1"}, {"speaker": "3"}]
assert speakers.number(segs) == {"1": "说话人1", "2": "说话人2"} and [s["speaker"] for s in segs] == ["1", "", "2", "1"]

# Every format carries the name, and only when asked to.
segs = [{"a": 1, "b": 2, "text": "你好", "speaker": "1"}, {"a": 3, "b": 4, "text": "再见", "speaker": ""}]
c = subtitles.cues(segs, {"1": "小明"})
assert "[小明] 你好" in subtitles.srt(c) and "\n再见\n" in subtitles.srt(c)
assert "<v 小明>你好" in subtitles.vtt(c) and "小明：你好\n再见\n" == subtitles.txt(c)
assert "Default,小明,0,0,0,,[小明] 你好" in subtitles.ass(c) and "Default,,0,0,0,,再见" in subtitles.ass(c)
assert "小明" not in subtitles.srt(subtitles.cues(segs))
# 去掉标点：句号删掉，逗号和顿号换成空格，其他标点不动
assert subtitles.plain("今日は、晴れ。そうだね，走吧。好吗？") == "今日は 晴れそうだね 走吧好吗？"
assert subtitles.cues([{"a": 0, "b": 1, "text": "你好，再见。"}], no_punct=True)[0][2] == "你好 再见"
assert subtitles.cues([{"a": 0, "b": 1, "text": "你好，再见。"}])[0][2] == "你好，再见。"
# Quiet speech the detector missed is kept when it stands out from the noise; plain noise between is dropped.
from core import chunks
lv = [100] * 300
lv[20:60] = [900] * 40  # speech the detector found
lv[75:90] = [200] * 15  # quiet speech it missed, 1.5 s after
lv[200:210] = [800] * 10  # a lone loud sound far away
vad = [{"start": 20 * 1600, "end": 60 * 1600}]
spans = chunks.regions(vad, lv)
assert spans[0][0] <= 20 and spans[0][1] >= 90 and any(a <= 200 < b for a, b in spans) and not any(a < 140 < b for a, b in spans), spans

# Long stretches are cut near 30 seconds, at their quietest moment, with no short scrap left at the end.
lv = [900] * 700
lv[330] = lv[331] = lv[332] = 10
assert chunks._cut(0, 700, lv) == [(0, 331), (331, 700)], chunks._cut(0, 700, lv)
assert all(250 <= b - a <= 350 for a, b in chunks._cut(0, 2000, [900] * 2000))
assert min(b - a for a, b in chunks._cut(0, 1044, [900] * 1044)) >= 300  # 104 s: three even pieces, not 30+30+30+14
assert chunks._cut(0, 1044, [900] * 1044, 15)[0][1] in range(113, 188)  # the second pass's first piece is about 15 s


# Overlapping chunks: each keeps the words in the part it owns, and the seam takes the later chunk's punctuation.
def words(text, t0, t1):
    step = (t1 - t0) / len(text)
    return [{"text": c, "t0": int((t0 + i * step) * 100), "t1": int((t0 + (i + 1) * step) * 100)} for i, c in enumerate(text)]


def chunk(a, b, own0, own1):
    return (a, b, {"start": int(a * 16000), "end": int(b * 16000), "t0_cs": int(a * 100), "t1_cs": int(b * 100), "own0": own0, "own1": own1})


sl2 = [chunk(0, 30.5, 0, 30), chunk(27, 50, 30, 50)]
w0 = words("我们今天要去东京", 26.0, 29.9)
for later, joined in (("要去东京塔看看。", "我们今天要去东京塔看看。"), ("要去东京。塔看看。", "我们今天要去东京。塔看看。")):
    w1 = words(later.replace("。", "")[:4], 28.0, 29.9) + words("塔看看", 30.1, 31.0)
    text, times, who, est = sentences.stitch(sl2, {0: "我们今天要去东京。", 1: later}, {0: w0, 1: w1})
    assert text == joined, text
    assert len(times) == len(text) and times[text.index("塔")][0] > 30
segs = sentences.build(sentences.stitch(sl2, {0: "我们今天要去东京。", 1: "要去东京塔看看。"}, {0: w0, 1: words("要去东京", 28, 29.9) + words("塔看看", 30.1, 31)}), "zh")
assert [s["text"] for s in segs] == ["我们今天要去东京塔看看。"] and abs(segs[0]["a"] - 26.0) < 0.01, segs

# A chunk that heard nothing before its cut keeps the earlier chunk's full stop.
text = sentences.stitch(sl2, {0: "我们到了。", 1: "下车吧。"}, {0: words("我们到了", 27, 29), 1: words("下车吧", 31, 32)})[0]
assert text == "我们到了。下车吧。", text

# Backup groups follow sentence edges and language, with spans into the group's joined text.
segs = [{"a": a, "spoken_end": a + 1, "lang": l, "qwen": q} for a, l, q in ((0, "zh", "你好。"), (1.5, "zh", "走吧。"), (3, "ja", "はい。"), (4.5, "zh", "好。"), (40, "zh", "再见。"))]
gs = sentences.groups(segs, "zh")
assert [[s["qwen"] for s in g] for g in gs] == [["你好。", "走吧。"], ["好。"], ["再见。"]] and segs[1]["span"] == [3, 6] and segs[1]["chunk"] == 0
# A chunk that only reads the hint back is dropped; real speech mentioning a hinted word is kept.
from core.pipeline import _echoes, _hint
h = _hint(["零重力座椅", "问界M9", "问界M8"])
assert _echoes("零重力座椅，问界M9，问界M8。", h) and not _echoes("这个零重力座椅真的很舒服，我们试一下吧", h) and not _echoes("嗯。", h)
print("all passed")
