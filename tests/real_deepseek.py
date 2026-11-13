"""Runs files through the real DeepSeek API with the saved key, in a throwaway data folder."""
import json, os, shutil, sys, tempfile, time
from pathlib import Path

saved = Path(os.environ["LOCALAPPDATA"]) / "CaptionJury"
os.environ["CAPTIONJURY_HOME"] = tempfile.mkdtemp(prefix="captionjury-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import deepseek, pipeline, store
import local_models
local_models.use()

store.load_config()
shutil.copy(saved / "deepseek.key", store.KEYFILE)
real = json.loads((saved / "config.json").read_text(encoding="utf-8"))
store.save_config({k: real[k] for k in ("proof_provider", "proof_url", "proof_model", "glossary") if k in real})
cfg = store.load_config()
models = deepseek.test(cfg, store.get_key())
print("models", models, "using", cfg["proof_model"])
if cfg["proof_model"] not in models:
    store.save_config({"proof_model": os.environ.get("TEST_MODEL", "deepseek-flash")})
    print("configured model is not listed; testing with", store.load_config()["proof_model"])

for path, out in zip(sys.argv[1::2], sys.argv[2::2]):
    q = pipeline.Queue()
    t0 = time.time()
    q.start([path], "zh", True, ["SRT"], bool(os.environ.get("DIARIZE")), True)
    marks = {}
    while q.state["running"]:
        for lane in (q.state["current"] or {}).get("lanes", []):
            if lane["pct"] > 0:
                marks.setdefault(lane["key"], time.time() - t0)
        time.sleep(0.5)
    print(path[-20:], "error:", q.state["error"], "seconds", round(time.time() - t0), {k: round(v) for k, v in marks.items()})
    if q.state["finished"]:
        shutil.copy(store.JOBS / q.state["finished"] / "job.json", out)
