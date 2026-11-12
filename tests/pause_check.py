"""Pausing must freeze the bar and the clock; cancelling must stop the run."""
import os, sys, tempfile, time
os.environ["TINGXIE_HOME"] = tempfile.mkdtemp(prefix="tingxie-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import pipeline
import local_models
local_models.use()

q = pipeline.Queue()
q.start([sys.argv[1]], "mix", False, ["SRT"])
while not (q.state["current"] and q.state["current"]["pct"] > 12):
    time.sleep(0.2)
q.set_paused(True)
time.sleep(1)
c = q.state["current"]
before = (c["pct"], c["elapsed"])
time.sleep(4)
after = (c["pct"], c["elapsed"])
print("frozen while paused:", before == after, before)
q.set_paused(False)
time.sleep(2)
print("moves after resume:", c["elapsed"] > before[1], round(c["elapsed"] - before[1], 1))
q.stop()
time.sleep(1.5)
print("stopped:", not q.state["running"], "error:", q.state["error"])
