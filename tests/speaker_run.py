"""Runs files through the local models with speaker separation, without DeepSeek, in a throwaway data folder.

Usage: speaker_run.py LANG AUDIO [AUDIO ...]. Subtitles are written next to AUDIO, so pass copies, not originals.
"""
import collections, os, sys, tempfile, time

os.environ["TINGXIE_HOME"] = tempfile.mkdtemp(prefix="tingxie-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import pipeline, store

lang = sys.argv[1]
for path in sys.argv[2:]:
    q = pipeline.Queue()
    t0 = time.time()
    q.start([path], lang, False, ["SRT", "ASS", "VTT", "TXT"], True)
    marks, best = {}, 0
    while q.state["running"]:
        cur = q.state["current"] or {}
        assert cur.get("pct", 0) >= best, "overall bar moved backwards"
        best = cur.get("pct", 0)
        for lane in cur.get("lanes", []):
            if lane["pct"] > 0:
                marks.setdefault(lane["key"], round(time.time() - t0))
        time.sleep(0.3)
    print(path[-24:], "error:", q.state["error"], "seconds", round(time.time() - t0), marks, [x["state"] for x in q.state["current"]["lanes"]])
    if not q.state["finished"]:
        continue
    job = store.load_job(q.state["finished"])
    print("warnings", job["warnings"], q.state["current"].get("warnings"), "speakers", job["speakers"])
    print("cues", len(job["segments"]), collections.Counter(s["speaker"] or "未标注" for s in job["segments"]))
    for s in job["segments"]:
        print(f'{s["a"]:7.2f} {s["speaker"] or "-":>2} {s["text"]}')
