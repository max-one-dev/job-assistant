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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(ROOT, "config.json")
STORE = os.path.join(ROOT, "data", "store.json")


def main():
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

    with io.open(STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)

    from collections import Counter
    bands = Counter(v.get("band") for v in store.values())
    print(f"Rescored {len(store)} vacancies.")
    print(f"  bands: green {bands['green']} · yellow {bands['yellow']} · "
          f"orange {bands['orange']} · red {bands['red']}")
    from collections import Counter as _C
    cats = _C(v.get("category") for v in store.values())
    print(f"  band changed: {band_changes} | v2 reject-type: {proj_hits} | WordPress roles: {wp_count}")
    print(f"  categories: A {cats['A']} · B {cats['B']} · C {cats['C']}")


if __name__ == "__main__":
    main()
