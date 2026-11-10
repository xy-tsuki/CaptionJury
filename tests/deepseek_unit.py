"""Batching and output checks of DeepSeek proofreading, with a scripted stand-in for the API."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import deepseek


def seg(i, text, a=None, speaker="", **kw):
    a = i * 3.0 if a is None else a
    return dict({"id": i, "a": a, "b": a + 2, "lang": "zh", "qwen": text, "speaker": speaker}, **kw)


# Batches are about BATCH long, end at the longest pause in reach, and cover everything once.
segs = [seg(i, "话") for i in range(100)]
segs[38]["a"] += 30  # a long pause before sentence 38
for s in segs[39:]:
    s["a"] += 30
bs = deepseek.batches(segs)
assert bs[0] == (0, 38), bs
assert [x for a, b in bs for x in range(a, b)] == list(range(100))
assert all(b - a <= deepseek.BATCH * 1.3 for a, b in bs), bs
assert deepseek.batches(segs[:35]) == [(0, 35)]

# Items that cannot be applied.
own = {s["id"]: s for s in [seg(1, "对，可以调节。", speaker="1"), seg(2, "嗯。", speaker="1"), seg(3, "这个跟M九一样的", speaker="2"), seg(4, "是吧。", speaker="2")]}
err = deepseek.structure_error
assert err({"ids": [3, 4], "text": "x"}, own, set()) is None
assert "不相邻" in err({"ids": [1, 3], "text": "x"}, own, set())
assert "不全在本批" in err({"ids": [9], "text": "x"}, own, set())
assert "不同说话人" in err({"ids": [2, 3], "text": "x"}, own, set())
assert "重复" in err({"ids": [3], "text": "x"}, own, {3})
assert "不能删除" in err({"ids": [1], "text": ""}, own, set())
assert err({"ids": [2], "text": ""}, own, set()) is None  # an interjection may go
assert err({"ids": ["a"], "text": "x"}, own, set())

# Edits the transcripts back are fine; inventions are kept but marked doubtful, never dropped.
g = [seg(7, "零重力座椅展开或收回时，再不只是调节。", qwen2="零重力座椅展开或收回时暂不支持调节。", moss="零重力座椅展开或收回时暂不支持调节。")]
doubt = deepseek.content_doubt
assert doubt({"text": "零重力座椅展开或收回时暂不支持调节。"}, g) is None
assert doubt({"text": "零重力座椅展开或收回时暂不能调节。"}, g) is None  # one homophone-like change is allowed
assert "都没有" in doubt({"text": "零重力座椅打开以后你就可以完全躺平休息了。"}, g)
assert doubt({"text": "富士山真好看", }, [seg(1, "富士三真好看")]) is None
assert doubt({"text": ""}, g) is None

# A batch run: a bad item gets one chance to be redone, a doubtful one is kept as uncertain.
calls = []


def fake_ask(client, cfg, system, payload, history=()):
    calls.append((payload, list(history)))
    if not history:
        reply = {"items": [{"ids": [7], "text": "零重力座椅打开以后你就可以完全躺平休息了。", "uncertain": False, "reason": "通顺"},
                           {"ids": [8, 9], "text": "这个跟M9是一样的，是吧。", "uncertain": False, "reason": ""},
                           {"ids": [99], "text": "x"}]}
    else:
        reply = {"items": [{"ids": [10], "text": "好。", "uncertain": False, "reason": ""}]}
    return reply, json.dumps(reply, ensure_ascii=False)


deepseek._ask = fake_ask
segs = [g[0], seg(8, "这个跟M九一样的", qwen2="这个跟M9是一样的"), seg(9, "是吧。"), seg(10, "好")]
items, dropped = deepseek._proof_batch(None, {}, {"outline": "", "glossary": []}, segs, 0, 4)
assert len(calls) == 2 and "无法套用" in calls[1][1][0][1] and "[99]" in calls[1][1][0][1]
assert [it["ids"] for it in items] == [[7], [8, 9], [10]] and dropped == 0
assert items[0]["uncertain"] and "校验" in items[0]["reason"] and items[0]["reason"].startswith("通顺")
assert not items[1]["uncertain"]
assert calls[0][0]["context_before"] == [] and [r["id"] for r in calls[0][0]["segments"]] == [7, 8, 9, 10]

# Context rows are sent but are not the batch's own.
calls.clear()
items, dropped = deepseek._proof_batch(None, {}, {"outline": "", "glossary": []}, segs, 2, 4)
assert [r["id"] for r in calls[0][0]["context_before"]] == [7, 8]
assert [it["ids"] for it in items] == [[10]] and dropped == 0  # 7 and 8 are context here: refused, then redone
# 大纲输入：其他模型写法和 Qwen 一样（忽略标点）时只发 Qwen 的文字；不一样时附上，重复的写法只留一份
assert deepseek._outline_row({"qwen": "去浅草寺。", "whisper": "去浅草寺", "mimo": ""}) == "去浅草寺。"
row = deepseek._outline_row({"qwen": "去浅草四。", "whisper": "去浅草寺", "mimo": "去浅草寺。", "moss": "去浅草"})
assert row == {"primary": "去浅草四。", "others": {"MiMo": "去浅草寺。", "MOSS": "去浅草"}}, row
print("all passed")
