# -*- coding: utf-8 -*-
"""
Локальный сервер для кнопок в report.html.

Запуск: python src/serve.py   (или двойной клик по start.bat)
Открывает http://localhost:8765/ — это data/report.html.

Endpoints (вызываются кнопками в отчёте через fetch):
  GET /api/update              — collect.py -> rescore.py -> build_report.py, затем export(visible)
  GET /api/export?mode=<m>     — export_vacancies.py <m>   (visible|new_interested|funnel)
  GET /api/rebuild             — только build_report.py (пересобрать отчёт без парсинга)

Сервер локальный (127.0.0.1), наружу ничего не публикует.
"""
import os, sys, io, json, subprocess, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
REPORT = os.path.join(DATA, "report.html")
PORT = 8765

ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def run_py(*args):
    """Запустить python-скрипт из src/, вернуть (ok, output)."""
    script = os.path.join(HERE, args[0])
    cmd = [sys.executable, script] + list(args[1:])
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True,
                           text=True, encoding="utf-8", timeout=900)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode == 0, out.strip()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # тихий лог

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, path):
        try:
            with io.open(path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"report.html not found - run update first")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        if path in ("/", "/report.html", "/index.html"):
            return self._html(REPORT)

        if path == "/api/update":
            steps = []
            ok, out = run_py("collect.py")
            steps.append(("collect", ok, out.splitlines()[-1] if out else ""))
            if ok:
                ok, out = run_py("rescore.py")
                steps.append(("rescore", ok, out.splitlines()[-1] if out else ""))
            if ok:
                ok, out = run_py("build_report.py")
                steps.append(("build_report", ok, out.splitlines()[-1] if out else ""))
            if ok:
                ok, out = run_py("export_vacancies.py", "visible")
                steps.append(("export", ok, out.splitlines()[-1] if out else ""))
            return self._json({"ok": ok, "steps": steps})

        if path == "/api/rebuild":
            ok, out = run_py("build_report.py")
            return self._json({"ok": ok, "msg": out.splitlines()[-1] if out else ""})

        if path == "/api/export":
            mode = (parse_qs(u.query).get("mode") or ["visible"])[0]
            if mode not in ("visible", "new_interested", "funnel", "unprocessed"):
                return self._json({"ok": False, "msg": f"bad mode: {mode}"}, 400)
            ok, out = run_py("export_vacancies.py", mode)
            return self._json({"ok": ok, "mode": mode,
                               "msg": out.splitlines()[-1] if out else ""})

        if path == "/api/status":
            from datetime import datetime, timezone, timedelta
            MSK = timezone(timedelta(hours=3))
            MONTHS_RU = ["января","февраля","марта","апреля","мая","июня","июля",
                         "августа","сентября","октября","ноября","декабря"]
            qs = parse_qs(u.query)
            vid = (qs.get("id") or [""])[0].strip()
            status = (qs.get("status") or [""])[0].strip()
            VALID = {"new", "interested", "applied", "interview", "offer",
                     "rejected", "skipped", "archived"}
            if not vid or status not in VALID:
                return self._json({"ok": False, "msg": "bad params"}, 400)
            store_path = os.path.join(DATA, "store.json")
            try:
                with io.open(store_path, encoding="utf-8") as f:
                    store = json.load(f)
                if vid not in store:
                    return self._json({"ok": False, "msg": f"not found: {vid}"}, 404)
                LABEL = {"new": "новая", "interested": "интересно", "applied": "откликнулся",
                         "interview": "собеседование", "offer": "оффер",
                         "rejected": "отказ", "skipped": "пропущена", "archived": "в архиве"}
                dt_now = datetime.now(MSK)
                now = dt_now.isoformat(timespec="seconds")
                date_fmt = f"{dt_now.day} {MONTHS_RU[dt_now.month-1]} {dt_now.year}, {dt_now.strftime('%H:%M')}"
                old = store[vid].get("status", "new")
                event = f"Статус: {LABEL.get(old, old)} → {LABEL.get(status, status)}"
                store[vid]["status"] = status
                store[vid].setdefault("history", []).append({"date": now, "event": event})
                with io.open(store_path, "w", encoding="utf-8") as f:
                    json.dump(store, f, ensure_ascii=False, indent=2)
                run_py("build_report.py")
                return self._json({"ok": True, "id": vid, "status": status,
                                   "history_entry": {"date_fmt": date_fmt, "event": event}})
            except Exception as ex:
                return self._json({"ok": False, "msg": str(ex)}, 500)

        self.send_response(404)
        self.end_headers()


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}/"
    print(f"Job Assistant server: {url}")
    print("Нажми Ctrl+C чтобы остановить.")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")
        srv.shutdown()


if __name__ == "__main__":
    main()
