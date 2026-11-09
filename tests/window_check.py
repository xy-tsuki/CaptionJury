"""Opens the real WebView2 window hidden and checks that the pywebview bridge reaches the page."""
import os
import sys
import tempfile
import threading
import time

os.environ["TINGXIE_HOME"] = tempfile.mkdtemp(prefix="tingxie-win-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
import http.server
import webview
import app

api = app.Api()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
win = webview.create_window("听写", f"http://127.0.0.1:{srv.server_port}/{app.TOKEN}/", js_api=api, hidden=True)
api._window = win


def check(w):
    time.sleep(3)
    print('dbg', w.evaluate_js("JSON.stringify([document.querySelector('#sys').outerHTML, typeof S, S.cfg && S.cfg.gpu_backend, booted, S.lang])"))
    print("view size:", w.evaluate_js("document.querySelector('#view').innerHTML.length"))
    print("buttons:", w.evaluate_js("document.querySelectorAll('.seg button').length"))
    w.evaluate_js("location.hash='#settings'")
    time.sleep(1.5)
    print("settings ready:", w.evaluate_js("!!document.querySelector('#ksave')"))
    w.destroy()


webview.start(check, win)
