"""用真模型跑完整流程，校对接口换成本地的假服务。AGENT=1 时改走"复制给 Agent"：脚本自己扮演用户，把假回复粘贴回去。"""
import http.server, json, os, sys, threading, time, tempfile
os.environ["TINGXIE_HOME"] = tempfile.mkdtemp(prefix="tingxie-test-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
from core import store, pipeline
import local_models
local_models.use()
if os.environ.get("BACKUPS"):  # e.g. {"zh": ["mimo", "whisper"], "ja": ["whisper"]}
    store.save_config({"backups": json.loads(os.environ["BACKUPS"])})

def fake_reply(data):
    """假校对：按输入数据造一份回复（大纲，或者几条有对有错的修改）。"""
    if True:
        if "transcript" in data:
            reply = {"outline": f"测试大纲，共 {len(data['transcript'])} 句"}
        else:
            assert data["outline"].startswith("测试大纲") and len(data["segments"]) > 1
            g = data["segments"]  # stand-ins for real repairs: a correction, a merge, a split, a bad id
            comma = next((x for x in g[5:] if "，" in x["primary"]), None)
            reply = {"items": [{"ids": [g[0]["id"]], "text": g[0]["primary"] + "改", "uncertain": False, "reason": "测试修改"},
                               {"ids": [g[2]["id"], g[3]["id"]], "text": g[2]["primary"] + g[3]["primary"], "uncertain": False, "reason": ""},
                               {"ids": [9999], "text": "x", "uncertain": False, "reason": ""}]}
            if comma:
                reply["items"].append({"ids": [comma["id"]], "text": comma["primary"].replace("，", "，\n", 1), "uncertain": True, "reason": "测试存疑"})
    return reply


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        reply = fake_reply(json.loads(body["messages"][1]["content"]))
        out = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(reply, ensure_ascii=False)}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
store.save_config({"proof_mode": "api", "proof_provider": "deepseek", "proof_url": f"http://127.0.0.1:{srv.server_port}"})
store.set_key("sk-test-value")
assert store.get_key() == "sk-test-value" and b"sk-test" not in store.KEYFILE.read_bytes()
AGENT = os.environ.get("AGENT") == "1"
if AGENT:
    store.save_config({"proof_mode": "agent"})

path, lang, opt = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
q = pipeline.Queue(); t0 = time.time(); q.start(path.split("|"), lang, opt, ["SRT", "ASS", "VTT", "TXT"], bool(os.environ.get("DIARIZE")), os.environ.get("COMPARE", "1" if opt else "") == "1")
last = None
while q.state["running"]:
    c = q.state["current"]
    if c and c.get("agent") and q.agent_prompt:  # 扮演用户：先贴一次不合格的，再贴带说明文字的正确回复
        step, prompt = c["agent"]["step"], q.agent_prompt
        assert q.agent_reply("proof" if step == "outline" else "outline", "{}")  # 步骤不对
        assert q.agent_reply(step, "这里没有 JSON")
        data = json.loads(prompt.rsplit("\n\n", 1)[1])  # 提示词最后一段是输入数据
        why = q.agent_reply(step, "好的，结果如下：\n```json\n" + json.dumps(fake_reply(data), ensure_ascii=False) + "\n```")
        assert why is None, why
        print(f"{time.time()-t0:5.1f}s 已粘贴 Agent 的{'大纲' if step == 'outline' else '校对'}结果")
        time.sleep(0.5)
    if c:
        s = " ".join(f"{l['key']}:{l['state']}" for l in c["lanes"])
        if s != last:
            r = c.get("remaining")
            print(f"{time.time()-t0:5.1f}s {c['pct']:5.1f}% left {'-' if r is None else round(r)} q={q.state['queue_pct']:.0f} {s}"); last = s
    time.sleep(1)
print("error:", q.state["error"], "time", round(time.time()-t0, 1))
if q.state["finished"]:
    j = store.load_job(q.state["finished"])
    if os.environ.get("JOBOUT"):
        json.dump(j, open(os.environ["JOBOUT"], "w", encoding="utf-8"), ensure_ascii=False)
    print("duration", j["duration"], "segments", len(j["segments"]), j["warnings"], j["written"])
    for s in j["segments"][:40]:
        print(f"{s['id']:3} {s['a']:7.2f}-{s['b']:7.2f} {s['lang']:3} {s['status']:4} Q={s['qwen'][:34]} | " + " ".join(f"{k}={s[k][:34]}" for k in ("mimo", "whisper") if s.get(k)))
