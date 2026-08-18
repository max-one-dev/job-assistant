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
import os, sys, io, json, subprocess, webbrowser, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import write_json_atomic
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


# ── Background update state ──
_bg_lock = threading.Lock()
_bg_state = {"running": False, "steps": [], "current": "", "done": True, "ok": True,
             "total": 0, "step_idx": 0, "finished_at": None}
_bg_cancel = threading.Event()


def _bg_update():
    global _bg_state
    _bg_cancel.clear()
    pipeline = [
        ("collect.py",         "сбор вакансий",     []),
        ("check_closed.py",    "проверка закрытых", []),
        ("rescore.py",         "пересчёт баллов",   []),
        ("build_report.py",    "отчёт",             []),
        ("export_vacancies.py","экспорт",            ["visible"]),
    ]
    total = len(pipeline)
    with _bg_lock:
        _bg_state = {"running": True, "steps": [], "current": pipeline[0][1], "done": False,
                     "ok": None, "total": total, "step_idx": 0, "finished_at": None}
    ok = True
    steps = []
    for i, (script, label, args) in enumerate(pipeline):
        if _bg_cancel.is_set():
            with _bg_lock:
                _bg_state.update({"running": False, "current": "", "done": True, "ok": False,
                                   "steps": steps[:], "step_idx": i, "finished_at": time.time()})
            return
        with _bg_lock:
            _bg_state["current"] = label
            _bg_state["step_idx"] = i
        ok, out = run_py(script, *args)
        steps.append({"name": label, "ok": ok})
        with _bg_lock:
            _bg_state["steps"] = steps[:]
        if not ok:
            break
    with _bg_lock:
        _bg_state.update({"running": False, "current": "", "done": True, "ok": ok,
                          "step_idx": total, "finished_at": time.time()})


# ── Check-closed state ──
_check_lock = threading.Lock()
_check_state = {"running": False, "total": 0, "checked": 0, "closed": 0,
                "done": True, "ok": True, "current": ""}
_check_cancel = threading.Event()


def _check_closed_thread():
    global _check_state
    _check_cancel.clear()
    import sys as _sys
    sys_path = list(_sys.path)

    try:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location("check_closed",
                                             os.path.join(HERE, "check_closed.py"))
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Count targets first
        import io as _io, json as _json
        store_path = os.path.join(DATA, "store.json")
        try:
            with _io.open(store_path, encoding="utf-8") as f:
                _store = _json.load(f)
        except FileNotFoundError:
            _store = {}
        _to_check = [v for v in _store.values()
                     if v.get("status") in mod.CHECK_STATUSES and mod.is_visible(v)]
        total = len(_to_check)

        with _check_lock:
            _check_state.update({"running": True, "total": total,
                                  "checked": 0, "closed": 0,
                                  "done": False, "ok": None, "current": "проверка"})

        def progress_cb(checked, total, closed):
            with _check_lock:
                _check_state.update({"checked": checked, "total": total, "closed": closed})
            if _check_cancel.is_set():
                raise InterruptedError("cancelled")

        try:
            closed = mod.main(progress_cb=progress_cb)
            with _check_lock:
                _check_state.update({"running": False, "done": True, "ok": True,
                                      "current": "", "closed": closed})
        except InterruptedError:
            with _check_lock:
                _check_state.update({"running": False, "done": True, "ok": False,
                                      "current": "отменено"})
    except Exception as ex:
        with _check_lock:
            _check_state.update({"running": False, "done": True, "ok": False,
                                  "current": str(ex)})


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

        if path == "/api/mark-reviewed":
            from datetime import date
            qs = parse_qs(u.query)
            vid = (qs.get("id") or [""])[0].strip()
            if not vid:
                return self._json({"ok": False, "msg": "missing id"}, 400)
            feedback_path = os.path.join(DATA, "feedback.json")
            try:
                try:
                    with io.open(feedback_path, encoding="utf-8") as f:
                        fb = json.load(f)
                except FileNotFoundError:
                    fb = {}
                fb[vid] = {"verdict": "reviewed", "reason": "manual_review",
                           "note": "Отмечено просмотренным вручную",
                           "date": str(date.today())}
                write_json_atomic(feedback_path, fb)
                run_py("build_report.py")
                return self._json({"ok": True, "id": vid})
            except Exception as ex:
                return self._json({"ok": False, "msg": str(ex)}, 500)

        if path == "/api/update-bg":
            with _bg_lock:
                if _bg_state.get("running"):
                    return self._json({"ok": False, "msg": "Обновление уже запущено"})
            threading.Thread(target=_bg_update, daemon=True).start()
            return self._json({"ok": True})

        if path == "/api/progress":
            with _bg_lock:
                return self._json(dict(_bg_state))

        if path == "/api/check-closed":
            with _check_lock:
                if _check_state.get("running"):
                    return self._json({"ok": False, "msg": "Проверка уже запущена"})
            threading.Thread(target=_check_closed_thread, daemon=True).start()
            return self._json({"ok": True})

        if path == "/api/check-progress":
            with _check_lock:
                return self._json(dict(_check_state))

        if path == "/api/cancel-bg":
            _bg_cancel.set()
            return self._json({"ok": True})

        if path == "/api/cancel-check":
            _check_cancel.set()
            return self._json({"ok": True})

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
                         "rejected": "отказ", "skipped": "пропущена", "archived": "Закрыта"}
                dt_now = datetime.now(MSK)
                now = dt_now.isoformat(timespec="seconds")
                date_fmt = f"{dt_now.day} {MONTHS_RU[dt_now.month-1]} {dt_now.year}, {dt_now.strftime('%H:%M')}"
                old = store[vid].get("status", "new")
                event = f"Статус: {LABEL.get(old, old)} → {LABEL.get(status, status)}"
                store[vid]["status"] = status
                store[vid].setdefault("history", []).append({"date": now, "event": event})
                write_json_atomic(store_path, store)
                run_py("build_report.py")
                return self._json({"ok": True, "id": vid, "status": status,
                                   "history_entry": {"date_fmt": date_fmt, "event": event}})
            except Exception as ex:
                return self._json({"ok": False, "msg": str(ex)}, 500)

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path

        if path == "/api/export":
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            try:
                cfg = json.loads(body)
            except Exception:
                return self._json({"ok": False, "msg": "invalid JSON"}, 400)
            mode = cfg.get("mode", "custom")
            if mode == "custom":
                cfg_json = json.dumps(cfg, ensure_ascii=False)
                ok, out = run_py("export_vacancies.py", "--custom", cfg_json)
            else:
                if mode not in ("visible", "new_interested", "funnel", "unprocessed"):
                    return self._json({"ok": False, "msg": f"bad mode: {mode}"}, 400)
                ok, out = run_py("export_vacancies.py", mode)
            return self._json({"ok": ok, "mode": mode,
                               "msg": out.splitlines()[-1] if out else ""})

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
