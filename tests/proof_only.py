"""Sends a finished job's sentences to the real DeepSeek API and prints what it would change. No ASR, nothing is saved."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import deepseek, store

cfg = store.load_config()
cfg["deepseek_model"] = sys.argv[2] if len(sys.argv) > 2 else "deepseek-flash"
cfg["deepseek_url"] = "https://api.deepseek.com"
job = json.load(open(sys.argv[1], encoding="utf-8"))
t = time.time()
outline, terms = deepseek.outline(cfg, store.get_key(), job["segments"])
print("outline seconds", round(time.time() - t), "terms", terms)
items, dropped = deepseek.proofread(cfg, store.get_key(), job["segments"], outline)
print("dropped", dropped)
print("seconds", round(time.time() - t))
print("OUTLINE:", outline)
by_id = {s["id"]: s for s in job["segments"]}
for it in items:
    print(it.get("ids"), "U" if it.get("uncertain") else " ", " + ".join(by_id[i]["qwen"] for i in it.get("ids", []) if i in by_id), "=>", repr(it.get("text")), "|", it.get("reason"))
