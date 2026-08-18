# -*- coding: utf-8 -*-
"""
Rescore ALL vacancies in data/store.json using the current rules from config.json
and the scoring logic in collect.py. Recomputes score / matched / probability /
band / review_topics / is_wordpress / english_required in place.

Preserves everything the user/Claude added: letter, profile, status, history,
first_seen, last_seen, accessible, description, urls, etc.

Run after changing config.json or the scoring rules:
    PYTHONIOENCODING=utf-8 python src/rescore.py
"""
import json, io, os
import collect  # same dir; reuses score_vacancy / band_of / etc.
from utils import write_json_atomic, backup_store

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(ROOT, "config.json")
STORE = os.path.join(ROOT, "data", "store.json")


def dedup_new_vacancies(store):
    """Ставит статус skipped дублям new-вакансий (одинаковые name+company).
    Использует точно ту же логику видимости и приоритета что build_report.py,
    поэтому счётчик check_closed совпадает с тем что видно в отчёте.
    Возвращает кол-во пропущенных дублей."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=3))).isoformat(timespec="seconds")

    SHORTLIST = {"interested", "applied", "interview", "offer"}

    def visible(v):
        s = v.get("status", "new")
        if s == "archived": return False
        if s in SHORTLIST or s == "rejected": return True
        if v.get("non_dev") or v.get("one_off"): return False
        if v.get("is_wordpress") and v.get("other_cms"): return False
        if v.get("project_employment"): return True
        return v.get("probability", 0) >= 50

    def rep_rank(v):
        s = v.get("status", "new")
        return (
            1 if s in SHORTLIST else 0,   # воронка всегда выигрывает
            1 if v.get("letter") else 0,  # письмо важнее
            v.get("probability", 0),
            1 if s == "new" else 0,       # new бьёт skipped при равном prob
        )

    visible_list = [v for v in store.values() if visible(v)]

    best = {}
    for v in visible_list:
        k = ((v.get("name") or "").strip().lower(),
             (v.get("company") or "").strip().lower())
        if k not in best or rep_rank(v) > rep_rank(best[k]):
            best[k] = v

    shown_ids = {v["id"] for v in best.values()}

    skipped = 0
    for v in visible_list:
        if v.get("status") == "new" and v["id"] not in shown_ids:
            k = ((v.get("name") or "").strip().lower(),
                 (v.get("company") or "").strip().lower())
            winner = best.get(k)
            winner_name = (winner.get("name") or "")[:35] if winner else "?"
            v["status"] = "skipped"
            v["duplicate_of"] = str(winner["id"]) if winner else None
            v.setdefault("history", []).append({
                "date": now,
                "event": f"Пропущена авто: дубль ({winner_name})"
            })
            skipped += 1
    return skipped


def main():
    backup_store(STORE)
    with io.open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    with io.open(STORE, encoding="utf-8") as f:
        store = json.load(f)

    band_changes = 0
    proj_hits = 0
    wp_count = 0
    for v in store.values():
        old_band = v.get("band")
        ev2 = collect.evaluate_v2(v, cfg)
        prob = ev2["interview_probability"]
        band = collect.band_of(prob)
        v["score"] = ev2["score"]
        v["matched"] = ev2["matched"]
        v["probability"] = prob
        v["band"] = band
        v["vac_type"] = ev2["vac_type"]
        v["category"] = ev2["category"]
        v["v2_status"] = ev2["v2_status"]
        v["final_rank"] = ev2["final_rank"]
        v["review_topics"] = collect.review_topics(v)
        v["is_wordpress"] = collect.is_wordpress_role(v)
        v["english_required"] = collect.english_required(v)
        v["other_cms"] = collect.requires_other_cms(v)
        v["project_employment"] = collect.is_project_employment(v)
        v["one_off"] = collect.is_one_off(v)
        v["senior"] = collect.is_senior_role(v)
        v["non_dev"] = collect.is_non_developer(v)
        if v["v2_status"] == "reject":
            proj_hits += 1
        if v["is_wordpress"]:
            wp_count += 1
        if old_band != band:
            band_changes += 1

    n_dupes = dedup_new_vacancies(store)

    write_json_atomic(STORE, store, indent=1)

    from collections import Counter
    bands = Counter(v.get("band") for v in store.values())
    print(f"Rescored {len(store)} vacancies.")
    print(f"  bands: green {bands['green']} · yellow {bands['yellow']} · "
          f"orange {bands['orange']} · red {bands['red']}")
    from collections import Counter as _C
    cats = _C(v.get("category") for v in store.values())
    print(f"  band changed: {band_changes} | v2 reject-type: {proj_hits} | WordPress roles: {wp_count}")
    print(f"  categories: A {cats['A']} · B {cats['B']} · C {cats['C']}")
    if n_dupes:
        print(f"  dupes skipped: {n_dupes}")


if __name__ == "__main__":
    main()
