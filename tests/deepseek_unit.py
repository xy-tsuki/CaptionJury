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


class FakeChat:
    """代替真实接口：第一次回一组有错的条目，第二次（收到反馈后）只回重做的那条。"""
    batch, attempts = deepseek.BATCH, 2

    def ask(self, system, payload, history=()):
        return fake_ask(system, payload, history)


def fake_ask(system, payload, history=()):
    calls.append((payload, list(history)))
    if not history:
        reply = {"items": [{"ids": [7], "text": "零重力座椅打开以后你就可以完全躺平休息了。", "uncertain": False, "reason": "通顺"},
                           {"ids": [8, 9], "text": "这个跟M9是一样的，是吧。", "uncertain": False, "reason": ""},
                           {"ids": [99], "text": "x"}]}
    else:
        reply = {"items": [{"ids": [10], "text": "好。", "uncertain": False, "reason": ""}]}
    return reply, json.dumps(reply, ensure_ascii=False)


segs = [g[0], seg(8, "这个跟M九一样的", qwen2="这个跟M9是一样的"), seg(9, "是吧。"), seg(10, "好")]
items, dropped = deepseek._proof_batch(FakeChat(), {"outline": "", "glossary": []}, segs, 0, 4)
assert len(calls) == 2 and "无法套用" in calls[1][1][0][1] and "[99]" in calls[1][1][0][1]
assert [it["ids"] for it in items] == [[7], [8, 9], [10]] and dropped == 0
assert items[0]["uncertain"] and "校验" in items[0]["reason"] and items[0]["reason"].startswith("通顺")
assert not items[1]["uncertain"]
assert calls[0][0]["context_before"] == [] and [r["id"] for r in calls[0][0]["segments"]] == [7, 8, 9, 10]

# Context rows are sent but are not the batch's own.
calls.clear()
items, dropped = deepseek._proof_batch(FakeChat(), {"outline": "", "glossary": []}, segs, 2, 4)
assert [r["id"] for r in calls[0][0]["context_before"]] == [7, 8]
assert [it["ids"] for it in items] == [[10]] and dropped == 0  # 7 and 8 are context here: refused, then redone
# 大纲输入：其他模型写法和 Qwen 一样（忽略标点）时只发 Qwen 的文字；不一样时附上，重复的写法只留一份
assert deepseek._outline_row({"qwen": "去浅草寺。", "whisper": "去浅草寺", "mimo": ""}) == "去浅草寺。"
row = deepseek._outline_row({"qwen": "去浅草四。", "whisper": "去浅草寺", "mimo": "去浅草寺。", "moss": "去浅草"})
assert row == {"primary": "去浅草四。", "others": {"MiMo": "去浅草寺。", "MOSS": "去浅草"}}, row

# 交给 Agent 时只给一次机会：结构不对的条目直接丢掉，不再追问
class OnceChat(FakeChat):
    attempts = 1


calls.clear()
items, dropped = deepseek._proof_batch(OnceChat(), {"outline": "", "glossary": []}, segs, 0, 4)
assert len(calls) == 1 and dropped == 1 and [it["ids"] for it in items] == [[7], [8, 9]]

# 粘贴回来的回复：前后有说明文字、代码块、思考过程都能取出 JSON
assert deepseek.parse_reply('好的，结果如下：\n```json\n{"items": []}\n```\n有问题再说') == {"items": []}
assert deepseek.parse_reply('<think>{"x":1}</think>{"outline": "大纲", "terms": []}')["outline"] == "大纲"
assert deepseek.parse_reply('前言 {"items": [{"ids": [1], "text": "a"}]} 后记')["items"][0]["ids"] == [1]
for bad in ("没有 JSON", '{"items": [', "[1, 2]"):
    try:
        deepseek.parse_reply(bad)
        raise AssertionError(bad)
    except ValueError:
        pass
assert deepseek.check_shape("outline", {"outline": "x"}) is None and deepseek.check_shape("outline", {"items": []})
assert deepseek.check_shape("proof", {"items": []}) is None and deepseek.check_shape("proof", {"outline": "x"})
assert "只回复一个 JSON" in deepseek.prompt_text("说明", {"a": 1}) and deepseek.prompt_text("说明", {"a": 1}).startswith("说明")

# 接口不认某些参数时，去掉再试；记住以后不再带
import httpx
seen = []


def handler(request):
    body = json.loads(request.content)
    seen.append(sorted(body))
    if "response_format" in body:
        return httpx.Response(400, text='{"error": "response_format is not supported"}')
    if body.get("max_tokens", 0) > 16384:
        return httpx.Response(400, text='{"error": "max_tokens must be at most 16384"}')
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '```json\n{"items": []}\n```'}}]})


chat = deepseek.Chat({"proof_provider": "custom", "proof_url": "http://x/v1", "proof_model": "m"}, "k")
chat.client = httpx.Client(base_url="http://x/v1", transport=httpx.MockTransport(handler))
assert chat.ask("s", {"a": 1})[0] == {"items": []}
assert "response_format" in chat.drop and chat.max_tokens == 16384 and len(seen) == 3
seen.clear()
chat.ask("s", {"a": 1})
assert len(seen) == 1  # 第二次直接用学到的参数

# 文件名交给大纲和校对：两边的输入里都有 title
class TitleChat(FakeChat):
    def ask(self, system, payload, history=()):
        calls.append((payload, list(history)))
        return ({"outline": "大纲", "terms": []} if "transcript" in payload else {"items": []}), "{}"


calls.clear()
deepseek.outline({}, TitleChat(), segs, "【神楽めあ】葬礼")
deepseek.proofread({}, TitleChat(), segs, "大纲", "【神楽めあ】葬礼")
assert all(c[0]["title"] == "【神楽めあ】葬礼" for c in calls) and len(calls) >= 2, calls

# 词库：整理粘贴或导入的词表，去掉编号、符号、标题和重复
assert deepseek.clean_terms("人物：\n1. 神楽めあ\n- 懲役太郎\n* \"香典\"\n神楽めあ\n```\n\n生前葬") == ["神楽めあ", "懲役太郎", "香典", "生前葬"]
# 生成词库的提示词：接口方式要 JSON，Agent 方式要纯词表，都带上用户填的信息和文件名
system, payload = deepseek.terms_prompt({"topic": "葬礼杂谈", "people": "神楽めあ", "files": ["a【神楽めあ】.mp4"]})
assert "JSON" in system and payload["涉及的人物和团体"] == "神楽めあ" and payload["files"] == ["a【神楽めあ】.mp4"]
text = deepseek.terms_prompt({"topic": "葬礼杂谈"}, agent=True)
assert isinstance(text, str) and "每行一个词" in text and "葬礼杂谈" in text


class TermsChat(FakeChat):
    def ask(self, system, payload, history=()):
        return {"terms": ["神楽めあ", " 香典 ", "神楽めあ", ""]}, "{}"


assert deepseek.make_terms(TermsChat(), {"topic": "x"}) == ["神楽めあ", "香典"]
print("all passed")
