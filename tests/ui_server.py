"""Serves the UI to an ordinary browser for checking: pywebview's bridge is replaced by plain POSTs."""
import json
import os
import sys
import http.server

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app

app.TOKEN = "t"
SHIM = """<script>
window.pywebview = { api: new Proxy({}, { get: (_, name) => async (...args) => {
  const r = await fetch('/rpc/' + name, { method: 'POST', body: JSON.stringify(args) });
  return r.json();
} }) };
</script>"""


class Test(app.Handler):
    def do_GET(self):
        if self.path.split("?")[0] in ("/t/", "/t"):
            html = (app.UI / "index.html").read_text(encoding="utf-8").replace('<script src="app.js">', SHIM + '<script src="app.js">')
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        name = self.path.rsplit("/", 1)[1]
        args = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        body = json.dumps(getattr(api, name)(*args), ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


api = app.Api()
print("http://127.0.0.1:8765/t/", flush=True)
http.server.ThreadingHTTPServer(("127.0.0.1", 8765), Test).serve_forever()
