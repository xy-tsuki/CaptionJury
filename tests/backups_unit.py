"""Several backup models at once: picking them, attaching their text, and handing it to DeepSeek."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import deepseek, models, sentences


def seg(i, text, lang="zh", **kw):
    return dict({"id": i, "a": i * 3.0, "b": i * 3.0 + 2, "lang": lang, "qwen": text, "speaker": ""}, **kw)


# Each backup goes to DeepSeek under its own name and counts as support for an edit.
s = seg(1, "金公塔在哪", mimo="金宫塔在哪", whisper="晴空塔在哪")
row = deepseek._row(s)
assert row["backups"] == {"MiMo": "金宫塔在哪", "Whisper": "晴空塔在哪"}, row
assert deepseek.content_doubt({"text": "晴空塔在哪"}, [s]) is None
assert "backups" not in deepseek._row(seg(2, "好"))

# A sentence counts as confirmed when any backup heard it as Qwen did, punctuation aside.
assert not sentences.backups_agree(s)
assert sentences.backups_agree(seg(1, "好的。", whisper="好的"))

# A backup's text goes only to the sentences of the languages it was picked for.
grp = [seg(1, "你好。", span=[0, 3]), seg(2, "こんにちは。", lang="ja", span=[3, 9])]
sentences.attach_backup(grp, "你好。こんにちは。", "你好。こんにちは", ("ja",), "whisper")
assert grp[1]["whisper"] == "こんにちは" and not grp[0].get("whisper"), grp

# A transcript without punctuation is cut at the same places, not one character off at every edge.
grp = [seg(1, "它有两种零重力啊，", span=[0, 9]), seg(2, "还有一种是折平掉。", span=[9, 18]), seg(3, "先复原一下。", span=[18, 24])]
sentences.attach_backup(grp, "它有两种零重力啊，还有一种是折平掉。先复原一下。", "它有两种零重力还有一种是直接折平掉先复原一下", ("zh",), "whisper")
assert [s["whisper"] for s in grp] == ["它有两种零重力", "还有一种是直接折平掉", "先复原一下"], [s["whisper"] for s in grp]
sentences.attach_backup(grp, "它有两种零重力啊，还有一种是折平掉。先复原一下。", "他有两种零重力呀还有一种是折平掉先复原一下", ("zh",), "mimo")
assert [s["mimo"] for s in grp] == ["他有两种零重力呀", "还有一种是折平掉", "先复原一下"], [s["mimo"] for s in grp]

# Picking: only installed models that know the language, in a fixed order.
cfg = {k: __file__ for m in models.MODELS.values() for k in m["files"]}  # every file "exists"
assert models.backups_for(dict(cfg, backups={"zh": ["whisper", "mimo"]}), "zh") == ["mimo", "whisper"]
assert models.backups_for(dict(cfg, backups={"ja": ["mimo", "whisper"]}), "ja") == ["mimo", "whisper"]
assert models.backups_for(dict(cfg, backups={"ja": ["mimo"]}), "zh") == []  # 只为日语勾选时不用于中文
assert models.backups_for(dict(cfg, whisper_model="missing.bin", backups={"zh": ["whisper"]}), "zh") == []
assert models.backups_for(dict(cfg, backups={}), "ja") == ["whisper"]  # nothing picked yet: the defaults

# 设置里还留着已经去掉的模型（parakeet）时，读设置会把它去掉；某种语言只剩它的，改回默认。
import json, os, tempfile
os.environ["TINGXIE_HOME"] = tempfile.mkdtemp(prefix="tingxie-test-")
import importlib
from core import store
importlib.reload(store)
store.HOME.mkdir(parents=True, exist_ok=True)
store.CONFIG.write_text(json.dumps({"backups": {"zh": ["mimo"], "ja": ["parakeet"]}}), encoding="utf-8")
assert store.load_config()["backups"] == {"zh": ["mimo"], "ja": ["whisper"]}
store.CONFIG.write_text(json.dumps({"backups": {"zh": [], "ja": ["whisper", "parakeet"]}}), encoding="utf-8")
assert store.load_config()["backups"] == {"zh": [], "ja": ["whisper"]}

# New sentences carry an empty field for every transcript.
assert all(k in sentences.OTHERS for k in models.BACKUPS)
print("all passed")
