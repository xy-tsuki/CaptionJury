"""把一个做完的任务的句子发给设置里的校对接口，打印它会改什么。不跑识别，什么都不保存。会把文字发出去，用之前先问用户。"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import deepseek, store

cfg = store.load_config()
if len(sys.argv) > 2:
    cfg["proof_model"] = sys.argv[2]
job = json.load(open(sys.argv[1], encoding="utf-8"))
t = time.time()
with deepseek.Chat(cfg, store.get_key()) as chat:
    outline, terms = deepseek.outline(cfg, chat, job["segments"])
    print("outline seconds", round(time.time() - t), "terms", terms)
    items, dropped = deepseek.proofread(cfg, chat, job["segments"], outline)
print("dropped", dropped)
print("seconds", round(time.time() - t))
print("OUTLINE:", outline)
by_id = {s["id"]: s for s in job["segments"]}
for it in items:
    print(it.get("ids"), "U" if it.get("uncertain") else " ", " + ".join(by_id[i]["qwen"] for i in it.get("ids", []) if i in by_id), "=>", repr(it.get("text")), "|", it.get("reason"))
