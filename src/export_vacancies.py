# -*- coding: utf-8 -*-
"""
Exports data/vacancies.json (JSON array, WITHOUT cover letters) from data/store.json.

Three modes (each mode = a separate button in report.html):
  visible         — то же, что видно в отчёте: visible()-фильтр + свёртка дублей (по умолчанию)
  new_interested  — «что откликнуться»: только статусы new + interested (проходящие порог качества)
  funnel          — «трекер воронки»: только interested/applied/interview/offer/rejected (без new)

Usage:
  python src/export_vacancies.py [visible|new_interested|funnel]
"""
import json, io, os, sys

# переиспользуем хелперы из билдера отчёта (импорт безопасен: main() под __main__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_report as br

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
STORE = os.path.join(DATA, "store.json")
OUT = os.path.join(DATA, "vacancies.json")

MODES = ("visible", "new_interested", "funnel", "unprocessed")
FUNNEL = ("interested", "applied", "interview", "offer", "rejected")

# поля экспорта: id/score/rank/status/v2_status/name/company/salary/matched/description


def visible(v):
    """Та же логика, что в build_report.main().visible()."""
    if v.get("status") == "archived":
        return False
    if v.get("status") in br.SHORTLIST or v.get("status") == "rejected":
        return True
    if v.get("non_dev"):
        return False
    if v.get("one_off"):
        return False
    if v.get("is_wordpress") and v.get("other_cms"):
        return False
    if v.get("project_employment"):
        return True
    return v.get("probability", 0) >= 50


def select(store, mode):
    vals = list(store.values())
    if mode == "visible":
        return [v for v in vals if visible(v)]
    if mode == "new_interested":
        return [v for v in vals if v.get("status") in ("new", "interested") and visible(v)]
    if mode == "funnel":
        return [v for v in vals if v.get("status") in FUNNEL]
    if mode == "unprocessed":
        return [v for v in vals if v.get("status") in ("new", "interested") and visible(v)]
    raise ValueError(f"unknown mode: {mode}")


def dedupe(items):
    """Свёртка дублей (name+company): оставляем лучший представитель — как в отчёте."""
    def rep_rank(v):
        return (1 if v.get("status") in br.SHORTLIST else 0,
                1 if v.get("letter") else 0,
                v.get("probability", 0))
    best = {}
    for v in items:
        k = ((v.get("name") or "").strip().lower(), (v.get("company") or "").strip().lower())
        if k not in best or rep_rank(v) > rep_rank(best[k]):
            best[k] = v
    return list(best.values())


def slim(v):
    sal = v.get("salary") or {}
    sal_from = sal.get("from")
    sal_to = sal.get("to")
    cur = sal.get("currency") or "RUR"
    if sal_from and sal_to:
        salary = f"{sal_from}–{sal_to} {cur}"
    elif sal_from:
        salary = f"от {sal_from} {cur}"
    elif sal_to:
        salary = f"до {sal_to} {cur}"
    else:
        salary = None
    return {
        "id": int(v["id"]) if str(v.get("id", "")).isdigit() else v.get("id"),
        "score": v.get("score"),
        "rank": v.get("final_rank"),
        "status": v.get("status"),
        "v2_status": v.get("v2_status"),
        "name": v.get("name"),
        "company": v.get("company"),
        "salary": salary,
        "matched": v.get("matched"),
        "description": v.get("description"),
    }


def export(mode="visible"):
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    store = br.read_json(STORE, {})
    items = dedupe(select(store, mode))
    items.sort(key=lambda v: -((v.get("final_rank") or v.get("probability", 0) or 0)
                               * br.freshness_info(v)[2]))
    out = [slim(v) for v in items]
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("[\n")
        for i, v in enumerate(out):
            sep = "" if i == len(out) - 1 else ","
            f.write("  " + json.dumps(v, ensure_ascii=False) + sep + "\n")
        f.write("]\n")
    return len(out)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "new_interested"
    n = export(mode)
    print(f"vacancies.json exported: {n} vacancies (mode={mode}, no letters)")


if __name__ == "__main__":
    main()
