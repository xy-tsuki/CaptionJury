"""逐字时间：对齐失败堆在一处的时间不用，没有时间的字按语速排在有时间的字旁边，句子起止取估算时间。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import sentences


def w(text, t0, t1):
    return {"text": text, "t0": t0, "t1": t1}


# 对齐模型把开头一串字堆在 0 上（Qwen 漏听了块开头的一段时会这样）：这几个字当作没有时间。
words = [w("你", 0, 0), w("好", 0, 0), w("。", 0, 0), w("我", 0, 0), w("们", 3000, 3020), w("走", 3020, 3040), w("吧", 3040, 3060)]
assert sentences._collapsed(words) == {0, 1, 2, 3}
t = "你好。我们走吧"
tm = sentences.char_times(t, words)
assert tm[:4] == [None] * 4 and tm[4] == (30.0, 30.2), tm
# 删掉的字要占位：不能把后面同样的字错配上来。
words2 = [w("す", 0, 0), w("み", 0, 0), w("ま", 0, 0), w("形", 3000, 3010), w("み", 3010, 3020)]
assert sentences.char_times("すみま形み", words2)[1] is None

# 单个长度为 0 的词、或者时间不同的 0 长度词，不算堆在一处。
assert not sentences._collapsed([w("あ", 1600, 1600), w("り", 1600, 1608), w("ま", 1608, 1616)])
assert not sentences._collapsed([w("あ", 100, 100), w("い", 200, 200), w("う", 300, 300)])

# 块开头没有时间的字紧挨着第一个有时间的字、按每字 0.2 秒往前排，不摊满整块。
est = sentences._estimate(t, tm, 0.0, 35.0)
assert 29.0 < est[0] < 30.0 and est == sorted(est), est
# 结尾同理。
tm2 = [(1.0, 1.2), None, None]
est2 = sentences._estimate("好的吗", tm2, 0.0, 30.0)
assert est2[2] < 2.0, est2

# 整段重放：句子的时间不再是 0，按顺序排。
stream = sentences.stitch([(0.0, 35.0, {"own0": 0.0, "own1": 35.0})], {0: t}, {0: words})
segs = sentences.build(stream, "zh")
assert [s["qwen"] for s in segs] == ["你好。", "我们走吧"], segs
assert 29.0 < segs[0]["a"] < segs[0]["b"] <= segs[1]["a"], [(s["a"], s["b"]) for s in segs]

# 只有一大串字堆在一处才提醒用户；三五个字堆在一起只是语速快。
assert not sentences.lost_alignment(words)
assert sentences.lost_alignment([w("字", 0, 0)] * 12 + [w("们", 3000, 3020)])
print("all passed")
