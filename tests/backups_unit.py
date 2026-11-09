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
assert sentences.backups_agree(seg(1, "好的。", parakeet="好的"))

# A backup's text goes only to the sentences of the languages it was picked for.
grp = [seg(1, "你好。", span=[0, 3]), seg(2, "こんにちは。", lang="ja", span=[3, 9])]
sentences.attach_backup(grp, "你好。こんにちは。", "你好。こんにちは", ("ja",), "whisper")
assert grp[1]["whisper"] == "こんにちは" and not grp[0].get("whisper"), grp

# Picking: only installed models that know the language, in a fixed order.
cfg = {k: __file__ for m in models.MODELS.values() for k in m["files"]}  # every file "exists"
assert models.backups_for(dict(cfg, backups={"zh": ["whisper", "mimo"]}), "zh") == ["mimo", "whisper"]
assert models.backups_for(dict(cfg, backups={"ja": ["mimo", "whisper"]}), "ja") == ["whisper"]  # MiMo knows no Japanese
assert models.backups_for(dict(cfg, whisper_model="missing.bin", backups={"zh": ["whisper"]}), "zh") == []
assert models.backups_for(dict(cfg, backups={}), "ja") == ["parakeet"]  # nothing picked yet: the defaults

# New sentences carry an empty field for every transcript.
assert all(k in sentences.OTHERS for k in models.BACKUPS)
print("all passed")
