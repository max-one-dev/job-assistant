# -*- coding: utf-8 -*-
"""
Проверяет закрытые вакансии на hh.ru.

Проверяет все видимые вакансии: new / interested / applied / interview / offer.

Детектирование — ТОЛЬКО через HH-Lux-InitialState JSON:
  - vacancyView.approved == False      → вакансия снята с публикации
  - vacancyView.closedForApplicants == True → вакансия закрыта для откликов
  - HTTP 404                           → вакансия удалена
Строковый поиск "vacancyInArchive" / "Вакансия в архиве" НЕ используется —
эти строки присутствуют в JS-бандле на каждой странице hh.ru вне зависимости от статуса.

Параллельность: WORKERS воркеров, каждый делает по одному запросу в DELAY сек.
Запуск: python src/check_closed.py
"""
import io, json, os, sys, time, queue, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import write_json_atomic
import urllib.request, urllib.error
import html as html_mod
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
STORE = os.path.join(DATA, "store.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/125.0.0.0 Safari/537.36")

DELAY   = 2.0   # секунд между запросами внутри одного воркера
WORKERS = 3     # параллельных воркеров (≈ 3 вкладки браузера)

MSK = timezone(timedelta(hours=3))

# Все видимые вакансии, кроме уже архивированных/пропущенных/отклонённых
CHECK_STATUSES = {"new", "interested", "applied", "interview", "offer"}


def _read_json(path):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _write_json(path, obj):
    write_json_atomic(path, obj, indent=2)


def fetch_page(vid):
    url = f"https://hh.ru/vacancy/{vid}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", errors="replace")


def _parse_initial_state(html):
    """Извлекает HH-Lux-InitialState JSON из страницы."""
    marker = "HH-Lux-InitialState"
    idx = html.find(marker)
    if idx == -1:
        return None
    start = html.find(">", idx)
    if start == -1:
        return None
    start += 1
    end = html.find("</", start)
    if end == -1 or end - start > 2_000_000:
        return None
    try:
        return json.loads(html_mod.unescape(html[start:end]))
    except Exception:
        return None


def is_closed(vid):
    """Возвращает (closed: bool, reason: str)."""
    try:
        html = fetch_page(vid)
    except urllib.error.HTTPError as e:
        if getattr(e, "code", None) == 404:
            return True, "HTTP 404"
        return False, f"HTTP {getattr(e, 'code', '?')}"
    except Exception as ex:
        return False, f"network: {ex}"

    state = _parse_initial_state(html)
    if state is None:
        return False, "no JSON (captcha?)"

    vv = state.get("vacancyView") or {}
    if vv.get("approved") is False:
        return True, "approved=false"
    if vv.get("closedForApplicants") is True:
        return True, "closedForApplicants=true"

    return False, "ok"


def is_visible(v):
    """Используется serve.py для подсчёта целевых вакансий."""
    s = v.get("status", "new")
    if s == "archived":
        return False
    if s in ("interested", "applied", "interview", "offer", "rejected"):
        return True
    if v.get("non_dev") or v.get("one_off"):
        return False
    if v.get("is_wordpress") and v.get("other_cms"):
        return False
    return v.get("probability", 0) >= 50


def _worker(q, results, lock, total, progress_cb, initial_sleep):
    """Воркер: последовательно забирает вакансии из очереди, делает запрос, спит DELAY."""
    if initial_sleep:
        time.sleep(initial_sleep)
    while True:
        try:
            v = q.get_nowait()
        except queue.Empty:
            return
        vid = str(v["id"])
        closed_flag, reason = is_closed(vid)
        with lock:
            results.append((v, closed_flag, reason))
            n_checked = len(results)
            n_closed = sum(1 for _, c, _ in results if c)
        label = "ЗАКРЫТА" if closed_flag else "открыта"
        print(f"  [{n_checked}/{total}] {vid} {label} ({reason})", flush=True)
        if progress_cb:
            progress_cb(n_checked, total, n_closed)
        time.sleep(DELAY)


def main(progress_cb=None):
    """
    Проверяет все видимые вакансии (new/interested/applied/interview/offer).
    progress_cb(checked, total, closed) вызывается после каждой проверки.
    Возвращает количество закрытых.
    """
    store = _read_json(STORE)

    to_check = [
        v for v in store.values()
        if v.get("status") in CHECK_STATUSES and is_visible(v)
    ]

    total = len(to_check)
    workers = min(WORKERS, total) if total > 0 else 0
    print(f"Проверяю {total} вакансий ({workers} воркера, задержка {DELAY}с)…", flush=True)

    if total == 0:
        print("Нечего проверять.", flush=True)
        return 0

    q = queue.Queue()
    for v in to_check:
        q.put(v)

    results = []
    lock = threading.Lock()
    stagger = DELAY / workers

    threads = []
    for i in range(workers):
        t = threading.Thread(
            target=_worker,
            args=(q, results, lock, total, progress_cb, i * stagger),
            daemon=True,
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    # Применяем результаты к store
    now = datetime.now(MSK).isoformat(timespec="seconds")
    closed = 0
    for v, closed_flag, reason in results:
        if closed_flag:
            v["status"] = "archived"
            v.setdefault("history", []).append({
                "date": now,
                "event": f"Вакансия закрыта на hh.ru (авто, {reason})"
            })
            closed += 1

    if closed > 0:
        _write_json(STORE, store)
        import subprocess
        build = os.path.join(HERE, "build_report.py")
        subprocess.run([sys.executable, build], cwd=ROOT,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                       capture_output=True)

    print(f"Готово: закрыто {closed} из {total}", flush=True)
    return closed


if __name__ == "__main__":
    main()
