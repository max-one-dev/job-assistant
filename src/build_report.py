# -*- coding: utf-8 -*-
"""
Builds data/report.html from data/store.json (the persistent journal).
"""
import json, io, os, html, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import write_json_atomic

MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
             "августа", "сентября", "октября", "ноября", "декабря"]
MSK = timezone(timedelta(hours=3))


def ru_dt(s, with_time=True):
    if not s:
        return ""
    dt = None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(MSK)
    out = f"{dt.day} {MONTHS_RU[dt.month - 1]} {dt.year}"
    if with_time:
        out += f", {dt.strftime('%H:%M')}"
    return out


def _parse_dt(s):
    if not s:
        return None
    dt = None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK)


def freshness_info(v):
    dt = _parse_dt(v.get("published_at"))
    if dt is None:
        return (None, "unk", 1.0)
    age = max(0, (datetime.now(MSK) - dt).days)
    if age <= 3:
        return (age, "f3", 1.30)
    if age <= 14:
        return (age, "f14", 1.10)
    if age <= 30:
        return (age, "f30", 0.95)
    return (age, "old", 0.80)


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
STORE = os.path.join(DATA, "store.json")
CONFIG = os.path.join(ROOT, "config.json")
FEEDBACK = os.path.join(DATA, "feedback.json")
REPORT = os.path.join(DATA, "report.html")

TARGET_SCORE = 110
REVIEWED = set()

BAND_INFO = {
    "green":  ("🟢", "Можно откликаться сразу"),
    "yellow": ("🟡", "Откликнуться, но повторить темы ниже"),
    "orange": ("🟠", "Отклик — если вакансия очень интересна"),
    "red":    ("🔴", "Лучше не тратить время"),
}
STATUS_LABEL = {"new": "новая", "interested": "интересно", "applied": "откликнулся",
                "interview": "собеседование", "offer": "оффер",
                "rejected": "отказ", "skipped": "пропущена", "archived": "Закрыта"}


def read_json(path, default):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def money(sal):
    if not sal:
        return ""
    a, b, c = sal.get("from"), sal.get("to"), sal.get("currency") or ""
    if not a and not b:
        return ""
    if a and b:
        return f"{a:,}–{b:,} {c}".replace(",", " ")
    if a:
        return f"от {a:,} {c}".replace(",", " ")
    return f"до {b:,} {c}".replace(",", " ")


def wp_salary_ok(v):
    s = v.get("salary") or {}
    hi = s.get("to") or s.get("from")
    cur = (s.get("currency") or "").upper()
    if hi is None:
        return True
    if cur in ("RUR", "RUB", ""):
        return hi >= 100000
    return True


def is_wp_priority(v):
    return bool(v.get("is_wordpress")) and wp_salary_ok(v)


SHORTLIST = ("interested", "applied", "interview", "offer")


def card(v):
    e = html.escape
    vid = v["id"]
    m = v.get("matched", {})
    pros = m.get("plus", [])
    risks = m.get("minus", [])
    band = v.get("band", "red")
    prob = v.get("probability", 0)
    status = v.get("status", "new")
    hist = v.get("history", [])
    topics = v.get("review_topics") or []

    letter = v.get("letter")
    if letter:
        letter_html = f"""
        <div class="letter">
          <div class="letter-head">✉️ Письмо <span class="profile">{e(v.get('profile') or '')}</span>
             <button class="copy" onclick="copyLetter('{vid}')">Копировать</button></div>
          <pre id="letter-{vid}">{e(letter)}</pre></div>"""
    elif v.get("recommend") == "skip":
        letter_html = f'<div class="skip">🚫 Рекомендую пропустить. {e(v.get("recommend_reason") or "")}</div>'
    else:
        letter_html = f'<div class="nolatter">Письма нет. Скажи Claude: «напиши письмо для {e(vid)}».</div>'

    pros_html = "".join(f"<span class='tag good'>{e(p)}</span>" for p in pros) or "<i>—</i>"
    if risks:
        risks_html = "".join(f"<span class='tag bad'>{e(r)}</span>" for r in risks)
        if prob < 100:
            risks_html += (f"<span class='tag warn2'>до 100% не хватило баллов: "
                           f"{v.get('score',0)}/{TARGET_SCORE}</span>")
    elif prob < 100:
        risks_html = (f"<span class='tag warn2'>до 100% не хватило баллов: "
                      f"{v.get('score',0)}/{TARGET_SCORE} — неполный набор ключевых требований</span>")
    else:
        risks_html = "<i>—</i>"

    hist_html = "".join(
        f"<li><span class='hd'>{e(ru_dt(h.get('date','')))}</span> {e(h.get('event',''))}</li>"
        for h in hist)
    pub = ru_dt(v.get("published_at"))

    if v.get("description_html"):
        desc_block = f'<div class="desc hh">{v["description_html"]}</div>'
    else:
        desc_block = f'<div class="desc">{e((v.get("description") or "")[:3000])}</div>'

    topics_html = ""
    if topics:
        lis = "".join(f"<li>{e(t)}</li>" for t in topics)
        topics_html = f'<div class="topics"><b>📚 Повторить перед интервью:</b><ul>{lis}</ul></div>'

    accessible = v.get("accessible", True)
    warn_html = "" if accessible else (
        '<div class="warn">⚠ Доступ к вакансии на hh ограничен.</div>')

    wp_pri = is_wp_priority(v)
    reviewed = vid in REVIEWED
    project = bool(v.get("project_employment"))
    age, fbucket, _ = freshness_info(v)
    flags = []
    if fbucket == "f3":
        flags.append(f"<span class='flag fresh'>🔥 свежая{'' if age is None else (' · сегодня' if age == 0 else f' · {age} дн')}</span>")
    elif age is not None:
        fi = "🕰" if fbucket == "old" else "📅"
        flags.append(f"<span class='flag age'>{fi} {age} дн</span>")
    if wp_pri:
        flags.append("<span class='flag wp'>⭐ WordPress</span>")
    if project:
        flags.append("<span class='flag proj'>🕒 проектная</span>")
    if v.get("senior"):
        flags.append("<span class='flag sen'>👴 Senior/Lead</span>")
    if v.get("english_required"):
        flags.append("<span class='flag en'>🌐 английский</span>")
    if reviewed:
        flags.append("<span class='flag rev'>✓ просмотрено</span>")
    flags_html = f'<div class="flags">{" ".join(flags)}</div>' if flags else ""

    skip_btn = ('' if status in ('skipped', 'archived') else
        f"""<button class="stn stn-skip" onclick="setStatus('{vid}','skipped')">⊘ Пропустить</button>""")
    mark_btn = ('' if reviewed else
        f"""<button class="stn stn-rev" onclick="markReviewed('{vid}')">👁 Просмотрено</button>""")

    apply_url = e(v.get('apply_url') or '')
    apply_btn = ('' if status == 'archived' else
        f'<a class="btn" href="{apply_url}" target="_blank">Откликнуться на hh →</a>')

    if status == 'archived':
        applied_btn = ''
        rejected_btn = ''
    elif status == 'applied':
        applied_btn = f'<button class="stn stn-interview" onclick="setStatus(\'{vid}\',\'interview\')">🎯 Собес</button>'
        rejected_btn = f'<button class="stn stn-rejected" onclick="setStatus(\'{vid}\',\'rejected\')">✕ Отказ</button>'
    else:
        applied_btn = f'<button class="stn stn-applied" onclick="setStatus(\'{vid}\',\'applied\')">✓ Откликнулся</button>'
        rejected_btn = ''
    pub_iso = e(v.get("published_at") or "")
    added_iso = e(v.get("first_seen") or "")

    return f"""
    <div class="card {band}{' wppri' if wp_pri else ''}" data-band="{band}" data-status="{e(status)}" data-id="{e(str(vid))}" data-wp="{1 if wp_pri else 0}" data-rev="{1 if reviewed else 0}" data-fresh="{fbucket}" data-pub="{pub_iso}" data-added="{added_iso}">
      <div class="top">
        <div class="score {band}" title="балл: {v.get('score',0)}">{prob}<span class="pct">%</span></div>
        <div class="titlebox">
          <a class="title" href="{e(v['url'])}" target="_blank">{e(v.get('name') or '(без названия)')}</a>
          <div class="meta">{e(v.get('company') or '')} · {e(v.get('area') or '')} · {e(money(v.get('salary')))}</div>
          <div class="meta pub">📅 опубликована: {e(pub) or '—'}</div>
          {flags_html}
        </div>
        <div class="badges">
          <div class="status st-{e(status)}">{e(STATUS_LABEL.get(status, status))}</div>
        </div>
      </div>
      {warn_html}
      <div class="card-body">
        <div class="row"><b>Совпадения:</b> {pros_html}</div>
        <div class="row"><b>Риски:</b> {risks_html}</div>
        {topics_html}
        <details><summary>Навыки и описание</summary>
          <div class="skills">{e(', '.join(v.get('key_skills') or []))}</div>
          {desc_block}</details>
        <details class="hist"><summary>История ({len(hist)})</summary><ul>{hist_html}</ul></details>
        {letter_html}
      </div>
      <div class="actions">
        {apply_btn}
        <a class="btn ghost" href="{e(v['url'])}" target="_blank">Открыть вакансию</a>
        {applied_btn}
        {rejected_btn}
        {skip_btn}
        {mark_btn}
        <span class="idtag">id {e(vid)} · {e(v.get('vac_type') or '—')} · балл {v.get('score',0)} · rank {v.get('final_rank',0)}</span>
      </div>
    </div>"""


def sf_section(key, label, open_default, buttons_html):
    arrow = "▼" if open_default else "▶"
    col_class = "" if open_default else " sf-col"
    return f"""  <div class="sf-section">
    <div class="sf-title" onclick="toggleSF('{key}')">
      <span class="sf-label">{label}</span>
      <span class="sf-dot" id="sfd-{key}"></span>
      <span class="sf-arr" id="sfa-{key}">{arrow}</span>
    </div>
    <div class="sf-body{col_class}" id="sfb-{key}">
{buttons_html}
    </div>
  </div>"""


def main():
    global TARGET_SCORE, REVIEWED
    cfg = read_json(CONFIG, {})
    TARGET_SCORE = (cfg.get("probability") or {}).get("target_score", TARGET_SCORE)

    feedback = read_json(FEEDBACK, {})
    REVIEWED = set(feedback.keys())

    store = read_json(STORE, {})


    # Auto-mark applied/interview/offer vacancies as reviewed
    from datetime import date as _date
    _fb_changed = False
    for _v in store.values():
        if _v.get("status") in ("applied", "interview", "offer") and str(_v["id"]) not in feedback:
            feedback[str(_v["id"])] = {
                "verdict": "reviewed", "reason": "auto_applied",
                "note": "Авто: статус откликнулся/собес/оффер",
                "date": str(_date.today())
            }
            _fb_changed = True
    if _fb_changed:
        write_json_atomic(FEEDBACK, feedback)
    REVIEWED = set(feedback.keys())

    def visible(v):
        if v.get("status") in SHORTLIST or v.get("status") in ("rejected", "archived"):
            return True
        if v.get("non_dev") or v.get("one_off"):
            return False
        if v.get("is_wordpress") and v.get("other_cms"):
            return False
        if v.get("project_employment"):
            return True
        return v.get("probability", 0) >= 50

    def tier(v):
        if v.get("project_employment"):
            return 2
        if is_wp_priority(v):
            return 0
        return 1

    visible_list = [v for v in store.values() if visible(v)]

    def rep_rank(v):
        return (1 if v.get("status") in SHORTLIST else 0,
                1 if v.get("letter") else 0,
                v.get("probability", 0))
    # Archived не участвуют в дедупликации — они только рендерятся отдельно
    dedup_list = [v for v in visible_list if v.get("status") != "archived"]
    best = {}
    for v in dedup_list:
        k = ((v.get("name") or "").strip().lower(), (v.get("company") or "").strip().lower())
        if k not in best or rep_rank(v) > rep_rank(best[k]):
            best[k] = v
    shown_deduped = list(best.values())
    archived_shown = [v for v in visible_list if v.get("status") == "archived"]
    shown = shown_deduped + archived_shown
    n_dupes = len(dedup_list) - len(shown_deduped)
    hidden0 = len(store) - len(shown)
    n_unreviewed = sum(1 for v in shown_deduped if v["id"] not in REVIEWED)

    def sort_rank(v):
        base = v.get("final_rank", v.get("probability", 0)) or 0
        return base * freshness_info(v)[2]

    items = sorted(shown, key=lambda v: (tier(v), -sort_rank(v)))
    cnt = {b: sum(1 for v in items if v.get("band") == b) for b in BAND_INFO}
    n_letters = sum(1 for v in items if v.get("letter"))
    n_wp = sum(1 for v in items if is_wp_priority(v))
    total = len(items)
    n_reviewed = total - n_unreviewed
    from collections import Counter
    stc = Counter(v.get("status", "new") for v in items)
    frc = Counter(freshness_info(v)[1] for v in items)
    cards = "\n".join(card(v) for v in items)

    def sb(k, v, label, cnt_val, active=False):
        cls = ' class="active"' if active else ''
        return f'      <button{cls} data-k="{k}" data-v="{v}" onclick="flt(this)">{label} <span class="cnt">{cnt_val}</span></button>'

    status_btns = "\n".join([
        sb("status", "all",        "все",          total),
        sb("status", "new",        "новые",         stc["new"],        active=True),
        sb("status", "interested", "интересно",     stc["interested"]),
        sb("status", "applied",    "откликнулся",   stc["applied"]),
        sb("status", "interview",  "собес",         stc["interview"]),
        sb("status", "offer",      "оффер",         stc["offer"]),
        sb("status", "rejected",   "отказ",         stc["rejected"]),
        sb("status", "skipped",    "пропущ.",       stc["skipped"]),
        sb("status", "archived",   "Закрыта",       stc["archived"]),
    ])
    rev_btns = "\n".join([
        sb("rev", "all", "все",         total,        active=True),
        sb("rev", "0",   "👀 непросм.", n_unreviewed),
        sb("rev", "1",   "✓ просм.",    n_reviewed),
    ])
    band_btns = "\n".join([
        sb("band", "all",    "все",       total,        active=True),
        sb("band", "green",  "🟢 90+",    cnt["green"]),
        sb("band", "yellow", "🟡 75–89",  cnt["yellow"]),
        sb("band", "orange", "🟠 60–74",  cnt["orange"]),
        sb("band", "red",    "🔴 &lt;60", cnt["red"]),
    ])
    fresh_btns = "\n".join([
        sb("fresh", "all", "все",       total,      active=True),
        sb("fresh", "f3",  "🔥 ≤3 дн", frc["f3"]),
        sb("fresh", "f14", "≤14 дн",   frc["f14"]),
        sb("fresh", "f30", "≤30 дн",   frc["f30"]),
        sb("fresh", "old", "🕰 стар.",  frc["old"]),
        sb("fresh", "unk", "без даты", frc["unk"]),
    ])
    wp_btns = "\n".join([
        sb("wp", "all", "все",          total, active=True),
        sb("wp", "1",   "⭐ WordPress", n_wp),
    ])

    sidebar_html = "\n".join([
        sf_section("status", "Статус",    open_default=True,  buttons_html=status_btns),
        sf_section("rev",    "Ревью",     open_default=True,  buttons_html=rev_btns),
        sf_section("band",   "Шанс",      open_default=True,  buttons_html=band_btns),
        sf_section("fresh",  "Свежесть",  open_default=False, buttons_html=fresh_btns),
        sf_section("wp",     "Приоритет", open_default=False, buttons_html=wp_btns),
    ])

    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Job Assistant — журнал</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>💼</text></svg>">
<script>(function(){{
  var _ls={{}};
  try{{['theme','compact','sort','sf-status','sf-rev','sf-band','sf-fresh','sf-wp'].forEach(function(k){{_ls[k]=localStorage.getItem(k);}});}}catch(e){{}}
  window.__ls=_ls;
  if(_ls['theme']==='light')document.documentElement.classList.add('light');
  var css=[];
  if(_ls['compact']==='1')css.push(
    '.card{{padding:8px 12px!important;margin-bottom:5px!important}}',
    '.card-body,.card .warn{{display:none!important}}',
    '.card .flags{{margin-top:2px!important}}',
    '.card .actions{{margin-top:7px!important}}',
    '.card .top{{align-items:center!important}}'
  );
  var _d={{status:1,rev:1,band:1,fresh:0,wp:0}};
  Object.keys(_d).forEach(function(k){{
    var v=_ls['sf-'+k];if(v===null||v===undefined)return;
    var open=v==='1';if(open===!!_d[k])return;
    if(open){{css.push('#sfb-'+k+'{{max-height:500px;opacity:1}}');}}
    else{{css.push('#sfb-'+k+'{{max-height:0;opacity:0}}');}}
  }});
  if(css.length){{var s=document.createElement('style');s.id='fouc-fix';s.textContent=css.join('');document.head.appendChild(s);}}
}}());</script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;background:#0f1115;color:#e6e6e6;display:flex;flex-direction:column;height:100vh;overflow:hidden}}

/* ── HEADER ── */
header{{flex-shrink:0;background:#161a22;border-bottom:1px solid #262b36;padding:12px 22px;z-index:10}}
header h1{{font-size:17px;margin-bottom:5px;font-weight:600}}
.sum{{color:#9aa4b2;font-size:13px;margin-top:3px;line-height:1.6}}
.toolbar{{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin:3px 0 5px}}
.tb{{background:#232a36;color:#cbd5e1;border:1px solid #333c4a;border-radius:6px;padding:5px 11px;cursor:pointer;font-size:13px;white-space:nowrap}}
.tb:hover{{background:#2b3442}} .tb:disabled{{opacity:.5;cursor:default}}
.tb.up{{background:#2f6feb;color:#fff;border-color:#2f6feb;font-weight:600}} .tb.up:hover{{background:#3b7bf7}}
.tb.compact-on{{background:#2a3a2a;color:#8bc34a;border-color:#3a5a3a}}
.tb-msg{{font-size:12px;color:#8bc34a;margin-left:4px}} .tb-msg.err{{color:#ff8787}} .tb-msg.wait{{color:#ffd43b}}
.sort-sel{{background:#232a36;color:#9aa4b2;border:1px solid #333c4a;border-radius:6px;padding:5px 8px;font-size:12px;cursor:pointer;margin-left:auto}}
.sort-sel:hover{{background:#2b3442;color:#e6e6e6}}

/* ── SIDEBAR ACTIONS ── */
.sb-section{{padding:8px 8px 10px;border-bottom:1px solid #1e2530}}
.sb-lbl{{font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:#6b7480;margin-bottom:5px;font-weight:600}}
.sb-row{{display:flex;gap:5px;align-items:center}}
.sb-sel{{flex:1;background:#1a2030;color:#9aa4b2;border:1px solid #252d3c;border-radius:5px;padding:4px 6px;font-size:12px;cursor:pointer;min-width:0}}
.sb-sel:hover,.sb-sel:focus{{background:#222c3c;color:#e0e6f0;outline:none}}
.sb-btn{{background:#1e3a5f;color:#74c0fc;border:1px solid #2a5080;border-radius:5px;padding:4px 10px;cursor:pointer;font-size:14px;flex-shrink:0;line-height:1}}
.sb-btn:hover{{background:#234870}} .sb-btn:disabled{{opacity:.5;cursor:default}}
.sb-btn-cfg{{display:block;width:100%;background:#1a2030;color:#8a95a3;border:1px solid #252d3c;border-radius:5px;padding:4px 8px;cursor:pointer;font-size:12px;text-align:left;margin-top:5px}}
.sb-btn-cfg:hover{{background:#222c3c;color:#e0e6f0}}
.sb-sum{{font-size:11px;color:#555e6b;margin-top:4px;line-height:1.4;padding:0 1px}}
.sb-progress{{display:none;padding:5px 8px;border-bottom:1px solid #1e2530}}

/* ── EXPORT CONFIGURATOR ── */
dialog.ex-modal{{background:#161a22;color:#e6e6e6;border:1px solid #333c4a;border-radius:12px;padding:0;max-width:520px;width:95vw;box-shadow:0 20px 60px rgba(0,0,0,.65);position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);margin:0}}
dialog.ex-modal::backdrop{{background:rgba(0,0,0,.55)}}
.ex-hdr{{display:flex;justify-content:space-between;align-items:center;padding:15px 20px 12px;border-bottom:1px solid #262b36}}
.ex-hdr h2{{font-size:15px;font-weight:600}}
.ex-cls{{background:none;border:none;color:#6b7480;font-size:20px;cursor:pointer;padding:2px 6px;line-height:1}}
.ex-cls:hover{{color:#e6e6e6}}
.ex-body{{padding:16px 20px;max-height:58vh;overflow-y:auto}}
.ex-sec-lbl{{font-size:10px;text-transform:uppercase;letter-spacing:.7px;color:#6b7480;margin-bottom:7px;font-weight:600}}
.ex-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:4px;margin-bottom:6px}}
.ex-chk{{display:flex;align-items:center;gap:5px;font-size:12px;cursor:pointer;padding:4px 6px;border-radius:4px;color:#9aa4b2;user-select:none}}
.ex-chk:hover{{background:#1a2030;color:#e6e6e6}}
.ex-chk input[type=checkbox]{{width:13px;height:13px;cursor:pointer;accent-color:#2f6feb;flex-shrink:0}}
.ex-hr{{border:none;border-top:1px solid #1e2530;margin:12px 0}}
.ex-stat-grid{{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px}}
.ex-fields-row{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:12px}}
.ex-fields-row-2{{grid-template-columns:repeat(2,1fr)}}
.ex-field-lbl{{font-size:11px;color:#6b7480;margin-bottom:4px}}
.ex-inp{{background:#0f1115;color:#e6e6e6;border:1px solid #333c4a;border-radius:5px;padding:4px 8px;font-size:12px;width:100%;box-sizing:border-box}}
.ex-inp:focus{{outline:none;border-color:#2f6feb}}
.ex-rsel{{background:#0f1115;color:#e6e6e6;border:1px solid #333c4a;border-radius:5px;padding:4px 8px;font-size:12px;cursor:pointer;width:100%;box-sizing:border-box}}
.ex-rsel:focus{{outline:none;border-color:#2f6feb}}
.ex-ftr{{display:flex;justify-content:space-between;align-items:center;padding:10px 20px 14px;border-top:1px solid #262b36}}
.ex-ftr-r{{display:flex;gap:7px}}

/* ── LAYOUT ── */
.layout{{display:flex;flex:1;overflow:hidden;min-height:0}}

/* ── SIDEBAR ── */
.sidebar{{width:230px;flex-shrink:0;background:#161a22;border-right:1px solid #262b36;display:flex;flex-direction:column;overflow:hidden}}
.sidebar-scroll{{flex:1;overflow-y:auto;padding:10px 8px 4px}}
.sidebar-scroll::-webkit-scrollbar{{width:4px}}
.sidebar-scroll::-webkit-scrollbar-track{{background:transparent}}
.sidebar-scroll::-webkit-scrollbar-thumb{{background:#2a3340;border-radius:4px}}
.sidebar-scroll::-webkit-scrollbar-thumb:hover{{background:#3a4555}}
.sidebar-footer{{flex-shrink:0;padding:8px;border-top:1px solid #1e2530;background:#161a22}}
.sf-section{{margin-bottom:2px}}
.sf-title{{display:flex;align-items:center;gap:5px;font-size:10px;text-transform:uppercase;letter-spacing:0.6px;color:#6b7480;padding:7px 6px 5px;cursor:pointer;border-radius:4px;user-select:none;margin-top:3px}}
.sf-title:hover{{color:#9aa4b2;background:#1a2030}}
.sf-label{{flex:1}}
.sf-dot{{width:6px;height:6px;background:#74c0fc;border-radius:50%;flex-shrink:0;display:none}}
.sf-has-active .sf-dot{{display:inline-block}}
.sf-arr{{font-size:9px;opacity:.55;flex-shrink:0}}
.sf-body{{overflow:hidden;max-height:500px;opacity:1;transition:max-height 0.22s ease-out,opacity 0.18s ease}}
.sf-body.sf-col{{max-height:0;opacity:0}}
.sidebar .sf-body button{{display:flex;justify-content:space-between;align-items:center;width:100%;background:#1a2030;color:#8a95a3;border:1px solid #252d3c;border-radius:5px;padding:5px 9px;cursor:pointer;font-size:12px;margin-bottom:2px;text-align:left;line-height:1.3}}
.sidebar .sf-body button:hover{{background:#222c3c;color:#e0e6f0}}
.sidebar .sf-body button.active{{background:#1e3a5f;color:#74c0fc;border-color:#2a5080;font-weight:600}}
.sidebar .sf-body button.active:hover{{background:#234870}}
.cnt{{opacity:.6;font-size:11px;font-weight:600;flex-shrink:0;margin-left:4px}}
.sidebar .sf-body button.active .cnt{{opacity:.85}}
.reset-btn{{width:100%;background:#1e2530;color:#6b7480;border:1px solid #252d3c;border-radius:6px;padding:6px;cursor:pointer;font-size:12px;text-align:center}}
.reset-btn:hover{{background:#2a1f1f;color:#ffb3b3;border-color:#5a2a2a}}

/* ── CONTENT ── */
.content{{flex:1;overflow-y:auto;min-width:0}}
.content::-webkit-scrollbar{{width:6px}}
.content::-webkit-scrollbar-track{{background:transparent}}
.content::-webkit-scrollbar-thumb{{background:#2a3340;border-radius:4px}}
.content::-webkit-scrollbar-thumb:hover{{background:#3a4555}}
main{{padding:16px 20px}}

/* ── SORT BAR & ACTIVE TAGS ── */
.sort-bar{{display:flex;align-items:center;gap:10px;margin-bottom:10px;font-size:12px;color:#6b7480}}
#active-tags{{display:none;flex-wrap:wrap;gap:6px;margin-bottom:10px}}
.atag{{background:#12304a;color:#74c0fc;border:1px solid #1e4870;border-radius:20px;padding:3px 10px;font-size:12px;display:flex;align-items:center;gap:5px;cursor:default}}
.atag-x{{cursor:pointer;opacity:.7;font-size:14px;line-height:1}} .atag-x:hover{{opacity:1}}
#nores{{color:#9aa4b2;text-align:center;padding:40px}}

/* ── CARDS ── */
.card{{background:#161a22;border:1px solid #262b36;border-left:4px solid #555;border-radius:10px;padding:13px 15px;margin-bottom:11px;transition:opacity 0.15s}}
.card.green{{border-left-color:#37b24d}} .card.yellow{{border-left-color:#f59f00}} .card.orange{{border-left-color:#f76707}} .card.red{{border-left-color:#e03131;opacity:.8}}
.card.wppri{{border-left-color:#7048e8}}
.flags{{margin-top:4px;display:flex;gap:4px;flex-wrap:wrap}}
.flag{{font-size:11px;padding:2px 7px;border-radius:10px}}
.flag.wp{{background:#241a45;color:#b197fc}} .flag.en{{background:#0f3038;color:#66d9e8}}
.flag.proj{{background:#3a2f12;color:#ffd8a8}} .flag.rev{{background:#1c2a1c;color:#8bc34a}}
.flag.sen{{background:#3a1f2a;color:#f7a1c4}}
.flag.fresh{{background:#3a2410;color:#ffb066;font-weight:700}} .flag.age{{background:#20242c;color:#8a93a2}}
.top{{display:flex;align-items:flex-start;gap:12px}}
.score{{font-size:24px;font-weight:700;min-width:56px;text-align:center;border-radius:8px;padding:5px 0;background:#232a36;flex-shrink:0}}
.score .pct{{font-size:13px;opacity:.7}}
.score.green{{color:#51cf66}} .score.yellow{{color:#ffd43b}} .score.orange{{color:#ffa94d}} .score.red{{color:#ff8787}}
.titlebox{{flex:1;min-width:0}}
.title{{color:#4dabf7;font-weight:600;text-decoration:none;font-size:15px}} .title:hover{{text-decoration:underline}}
.meta{{color:#9aa4b2;font-size:13px;margin-top:2px}} .pub{{color:#7d8695;font-size:12px;margin-top:1px}}
.badges{{display:flex;flex-direction:column;gap:4px;align-items:flex-end;flex-shrink:0}}
.status{{font-size:13px;padding:5px 12px;border-radius:20px;white-space:nowrap;background:#232a36;color:#9aa4b2}}
.st-interested{{background:#12304a;color:#74c0fc}} .st-applied{{background:#1c3a2a;color:#69db7c}}
.st-interview{{background:#33234a;color:#b197fc}} .st-offer{{background:#183d23;color:#51cf66}}
.st-rejected{{background:#3d1a1a;color:#ff8787}} .st-skipped{{background:#2a2f3a;color:#6b7480}}
.st-archived{{background:#2d1a1a;color:#ff6b6b}}
.card-body{{}}
.row{{margin-top:8px;font-size:13px}} .row b{{color:#9aa4b2}}
.tag{{display:inline-block;font-size:12px;padding:2px 8px;border-radius:12px;margin:2px}}
.tag.good{{background:#173a24;color:#69db7c}} .tag.bad{{background:#3a1717;color:#ff8787}}
.tag.warn2{{background:#3a2f12;color:#ffd8a8;font-style:italic}}
.topics{{margin-top:10px;background:#12233a;border:1px solid #24405f;border-radius:8px;padding:8px 12px;font-size:13px}}
.topics b{{color:#9ec5ff}} .topics ul{{margin:6px 0 2px;padding-left:20px}} .topics li{{margin:2px 0;color:#cbd5e1}}
details{{margin-top:9px}} summary{{cursor:pointer;color:#9aa4b2;font-size:13px}}
.skills{{color:#cbd5e1;font-size:13px;margin:6px 0}}
.desc{{color:#c4ccd8;font-size:13px;white-space:pre-wrap;max-height:360px;overflow:auto;background:#12151b;padding:10px 14px;border-radius:6px}}
.desc.hh{{white-space:normal}} .desc.hh p{{margin:0 0 10px}} .desc.hh ul,.desc.hh ol{{margin:6px 0 10px;padding-left:22px}} .desc.hh li{{margin:3px 0}} .desc.hh strong,.desc.hh b{{color:#e6e6e6}}
.warn{{margin-top:8px;background:#3a2a12;border:1px solid #5a4420;color:#ffd8a8;font-size:12px;padding:7px 10px;border-radius:6px}}
.letter{{margin-top:10px;background:#12151b;border:1px solid #2a3340;border-radius:8px;padding:10px}}
.letter-head{{font-size:13px;color:#9aa4b2;margin-bottom:6px;display:flex;align-items:center;gap:10px}}
.profile{{background:#232a36;padding:2px 8px;border-radius:10px;font-size:12px}}
.copy{{margin-left:auto;background:#2f6feb;color:#fff;border:0;border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px}}
.letter pre{{white-space:pre-wrap;font:13px/1.55 inherit;color:#e6e6e6}}
.nolatter{{margin-top:8px;color:#6b7480;font-size:12px;font-style:italic}}
.skip{{margin-top:8px;background:#2a1a1a;border:1px solid #5a2a2a;color:#ffb3b3;font-size:12px;padding:7px 10px;border-radius:6px}}
.actions{{margin-top:10px;display:flex;gap:7px;align-items:center;flex-wrap:wrap}}
.btn{{background:#37b24d;color:#fff;text-decoration:none;padding:6px 13px;border-radius:7px;font-size:13px;white-space:nowrap}}
.btn.ghost{{background:#232a36;color:#cbd5e1}}
.stn{{background:none;border:1px solid #333c4a;color:#9aa4b2;padding:5px 10px;border-radius:6px;cursor:pointer;font-size:12px;white-space:nowrap}}
.stn:hover{{border-color:#555;color:#e6e6e6}}
.stn-applied{{border-color:#2e7d4f;color:#69db7c}} .stn-applied:hover{{background:#173a24}}
.stn-interview{{border-color:#7048e8;color:#b197fc}} .stn-interview:hover{{background:#241a45}}
.stn-rejected{{border-color:#7d1a1a;color:#ff8787}} .stn-rejected:hover{{background:#3d1a1a}}
.stn-skip{{border-color:#444;color:#6b7480}} .stn-skip:hover{{background:#2a3040}}
.stn-rev{{border-color:#1e4060;color:#74c0fc}} .stn-rev:hover{{background:#12304a}}
.idtag{{margin-left:auto;color:#4a515e;font-size:11px}}

/* ── COMPACT MODE ── */
main.compact .card{{padding:8px 12px;margin-bottom:5px}}
main.compact .card-body{{display:none}}
main.compact .card .warn{{display:none}}
main.compact .card .flags{{margin-top:2px}}
main.compact .card .actions{{margin-top:7px}}
main.compact .card .top{{align-items:center}}

/* ── LIGHT THEME ── */
html.light body{{background:#f0f4f8;color:#1a202c}}
html.light header{{background:#fff;border-color:#e2e8f0}}
html.light .sum{{color:#4a5568}}
html.light .sum small{{color:#a0aec0}}
html.light .tb{{background:#fff;color:#374151;border-color:#e2e8f0}}
html.light .tb:hover{{background:#edf2f7}}
html.light .tb.up{{background:#2563eb;color:#fff;border-color:#2563eb}}
html.light .tb.up:hover{{background:#1d4ed8}}
html.light .tb.compact-on{{background:#dcfce7;color:#166534;border-color:#86efac}}
html.light .sort-sel{{background:#fff;color:#4a5568;border-color:#e2e8f0}}
html.light .sidebar{{background:#f8fafc;border-color:#e2e8f0}}
html.light .sidebar-scroll::-webkit-scrollbar-thumb{{background:#cbd5e0}}
html.light .sf-title{{color:#718096}}
html.light .sf-title:hover{{color:#4a5568;background:#edf2f7}}
html.light .sf-dot{{background:#2563eb}}
html.light .sf-has-active .sf-dot{{display:inline-block}}
html.light .sf-arr{{color:#9ca3af}}
html.light .sidebar .sf-body button{{background:#fff;color:#4a5568;border-color:#e2e8f0}}
html.light .sidebar .sf-body button:hover{{background:#edf2f7;color:#1a202c}}
html.light .sidebar .sf-body button.active{{background:#ebf8ff;color:#1d4ed8;border-color:#bfdbfe}}
html.light .sidebar-footer{{background:#f8fafc;border-color:#e2e8f0}}
html.light .reset-btn{{background:#fff;color:#718096;border-color:#e2e8f0}}
html.light .reset-btn:hover{{background:#fee2e2;color:#991b1b;border-color:#fca5a5}}
html.light .content::-webkit-scrollbar-thumb{{background:#cbd5e0}}
html.light .card{{background:#fff;border-left-color:#94a3b8;border-color:#e2e8f0}}
html.light .card.green{{border-left-color:#22c55e}}
html.light .card.yellow{{border-left-color:#eab308}}
html.light .card.orange{{border-left-color:#f97316}}
html.light .card.red{{border-left-color:#ef4444}}
html.light .title{{color:#1d4ed8}}
html.light .meta{{color:#4a5568}}
html.light .pub{{color:#718096}}
html.light .score{{background:#f1f5f9}}
html.light .score.green{{color:#16a34a}}
html.light .score.yellow{{color:#ca8a04}}
html.light .score.orange{{color:#ea580c}}
html.light .score.red{{color:#dc2626}}
html.light .status{{background:#f1f5f9;color:#4a5568}}
html.light .st-interested{{background:#dbeafe;color:#1d4ed8}}
html.light .st-applied{{background:#dcfce7;color:#166534}}
html.light .st-interview{{background:#ede9fe;color:#5b21b6}}
html.light .st-offer{{background:#d1fae5;color:#065f46}}
html.light .st-rejected{{background:#fee2e2;color:#991b1b}}
html.light .st-skipped{{background:#f1f5f9;color:#6b7280}}
html.light .st-archived{{background:#fee2e2;color:#b91c1c}}
html.light .flag.wp{{background:#ede9fe;color:#5b21b6}}
html.light .flag.en{{background:#ecfeff;color:#155e75}}
html.light .flag.proj{{background:#fef3c7;color:#92400e}}
html.light .flag.rev{{background:#dcfce7;color:#166534}}
html.light .flag.sen{{background:#fce7f3;color:#9d174d}}
html.light .flag.fresh{{background:#fff7ed;color:#c2410c}}
html.light .flag.age{{background:#f1f5f9;color:#475569}}
html.light .row b{{color:#4a5568}}
html.light .tag.good{{background:#dcfce7;color:#166534}}
html.light .tag.bad{{background:#fee2e2;color:#991b1b}}
html.light .tag.warn2{{background:#fef3c7;color:#92400e}}
html.light details summary{{color:#6b7280}}
html.light .skills{{color:#374151}}
html.light .desc{{background:#f8fafc;color:#374151;border-color:#e2e8f0}}
html.light .desc.hh strong,html.light .desc.hh b{{color:#1a202c}}
html.light .warn{{background:#fef9c3;border-color:#fbbf24;color:#92400e}}
html.light .topics{{background:#eff6ff;border-color:#bfdbfe}}
html.light .topics b{{color:#1d4ed8}}
html.light .topics li{{color:#1e3a5f}}
html.light .letter{{background:#f8fafc;border-color:#e2e8f0}}
html.light .letter-head{{color:#4a5568}}
html.light .letter pre{{color:#1a202c}}
html.light .copy{{background:#2563eb}}
html.light .nolatter{{color:#718096}}
html.light .skip{{background:#fee2e2;border-color:#fca5a5;color:#991b1b}}
html.light .btn{{background:#22c55e}}
html.light .btn.ghost{{background:#e2e8f0;color:#374151}}
html.light .stn{{border-color:#d1d5db;color:#374151}}
html.light .stn:hover{{border-color:#9ca3af;color:#111827}}
html.light .stn-applied{{border-color:#22c55e;color:#166534}}
html.light .stn-applied:hover{{background:#dcfce7}}
html.light .stn-rejected{{border-color:#ef4444;color:#991b1b}}
html.light .stn-rejected:hover{{background:#fee2e2}}
html.light .stn-interview{{border-color:#7c3aed;color:#5b21b6}}
html.light .stn-interview:hover{{background:#ede9fe}}
html.light .stn-skip{{border-color:#d1d5db;color:#6b7280}} html.light .stn-skip:hover{{background:#f1f5f9;border-color:#9ca3af}}
html.light .stn-rev{{border-color:#3b82f6;color:#1d4ed8}}
html.light .stn-rev:hover{{background:#eff6ff}}
html.light .idtag{{color:#9ca3af}}
html.light .atag{{background:#eff6ff;color:#1d4ed8;border-color:#bfdbfe}}
html.light #nores{{color:#6b7280}}
html.light .sort-bar{{color:#718096}}
html.light .tb-msg{{color:#374151}}
html.light .tb-msg.wait{{color:#d97706}}
html.light .tb-msg.err{{color:#dc2626}}
html.light .tb-msg.ok{{color:#16a34a}}
html.light .sb-section{{border-color:#e2e8f0}}
html.light .sb-lbl{{color:#9ca3af}}
html.light .sb-sel{{background:#fff;color:#4a5568;border-color:#e2e8f0}}
html.light .sb-sel:hover{{background:#edf2f7;color:#1a202c}}
html.light .sb-btn{{background:#ebf8ff;color:#1d4ed8;border-color:#bfdbfe}}
html.light .sb-btn:hover{{background:#dbeafe}}
html.light .sb-btn-cfg{{background:#fff;color:#4a5568;border-color:#e2e8f0}}
html.light .sb-btn-cfg:hover{{background:#edf2f7;color:#1a202c}}
html.light .sb-sum{{color:#a0aec0}}
html.light .sb-progress{{border-color:#e2e8f0}}
html.light dialog.ex-modal{{background:#fff;color:#1f2937;border-color:#e2e8f0;box-shadow:0 20px 60px rgba(0,0,0,.15)}}
html.light dialog.ex-modal::backdrop{{background:rgba(0,0,0,.3)}}
html.light .ex-hdr{{border-color:#e2e8f0}}
html.light .ex-cls{{color:#9ca3af}}
html.light .ex-cls:hover{{color:#374151}}
html.light .ex-sec-lbl{{color:#9ca3af}}
html.light .ex-chk{{color:#4b5563}}
html.light .ex-chk:hover{{background:#f3f4f6;color:#1f2937}}
html.light .ex-field-lbl{{color:#9ca3af}}
html.light .ex-inp{{background:#f9fafb;color:#1f2937;border-color:#e2e8f0}}
html.light .ex-rsel{{background:#f9fafb;color:#1f2937;border-color:#e2e8f0}}
html.light .ex-hr{{border-color:#e2e8f0}}
html.light .ex-ftr{{border-color:#e2e8f0}}
html.light .ex-stat-grid .ex-chk{{color:#4b5563}}
</style></head><body>

<!-- ═══ HEADER ═══ -->
<header>
  <h1>Job Assistant — журнал вакансий ({len(items)})</h1>
  <div class="toolbar">
    <button class="tb" id="btn-theme" onclick="toggleTheme()">☀ Светлая тема</button>
    <button class="tb" id="btn-compact" onclick="toggleCompact()">⊟ Компакт</button>
    <select class="sort-sel" id="sort-sel" onchange="sortCards(this.value)" title="Сортировка">
      <option value="rank">↕ По рейтингу</option>
      <option value="pub">↕ По дате публикации</option>
      <option value="added">↕ По дате добавления</option>
    </select>
  </div>
  <div class="sum">🟢 {cnt['green']} · 🟡 {cnt['yellow']} · 🟠 {cnt['orange']} · 🔴 {cnt['red']} · ✉️ {n_letters} · ⭐ {n_wp} · 👀 непросм: {n_unreviewed}
    <br><small style="color:#555e6b">Скрыто {hidden0} (рейтинг &lt;50%, архив, дубли {n_dupes} и др.). Вероятность — эвристика, не гарантия.</small></div>
</header>

<!-- ═══ EXPORT CONFIGURATOR MODAL ═══ -->
<dialog id="ex-modal" class="ex-modal">
  <div class="ex-hdr">
    <h2>⚙ Кастомная конфигурация экспорта</h2>
    <button class="ex-cls" onclick="exModalClose()" title="Закрыть">✕</button>
  </div>
  <div class="ex-body">
    <div class="ex-sec-lbl">Поля в выгрузке</div>
    <div class="ex-grid" id="ex-fields">
      <label class="ex-chk"><input type="checkbox" value="id" checked> ID</label>
      <label class="ex-chk"><input type="checkbox" value="name" checked> Название</label>
      <label class="ex-chk"><input type="checkbox" value="company" checked> Компания</label>
      <label class="ex-chk"><input type="checkbox" value="salary" checked> Зарплата</label>
      <label class="ex-chk"><input type="checkbox" value="score" checked> Балл</label>
      <label class="ex-chk"><input type="checkbox" value="rank" checked> Ранк</label>
      <label class="ex-chk"><input type="checkbox" value="status" checked> Статус</label>
      <label class="ex-chk"><input type="checkbox" value="v2_status" checked> Оценка Claude</label>
      <label class="ex-chk"><input type="checkbox" value="matched" checked> Совпадения</label>
      <label class="ex-chk"><input type="checkbox" value="description" checked> Описание</label>
    </div>
    <hr class="ex-hr">
    <div class="ex-sec-lbl">Статусы вакансий</div>
    <div class="ex-stat-grid" id="ex-statuses">
      <label class="ex-chk"><input type="checkbox" value="new" checked> Новые</label>
      <label class="ex-chk"><input type="checkbox" value="interested" checked> Интересно</label>
      <label class="ex-chk"><input type="checkbox" value="applied"> Откликнулся</label>
      <label class="ex-chk"><input type="checkbox" value="interview"> Собеседование</label>
      <label class="ex-chk"><input type="checkbox" value="offer"> Оффер</label>
      <label class="ex-chk"><input type="checkbox" value="rejected"> Отказ</label>
      <label class="ex-chk"><input type="checkbox" value="skipped"> Пропущена</label>
      <label class="ex-chk"><input type="checkbox" value="archived"> Закрыта</label>
    </div>
    <hr class="ex-hr">
    <div class="ex-fields-row">
      <div><div class="ex-field-lbl">Мин. зарплата</div><input class="ex-inp" type="number" id="ex-min-salary" min="0" value="0" placeholder="0 = без фильтра"></div>
      <div><div class="ex-field-lbl">Мин. рейтинг</div><input class="ex-inp" type="number" id="ex-min-score" min="0" value="0" placeholder="0 = без фильтра"></div>
      <div><div class="ex-field-lbl">Лимит вакансий</div><input class="ex-inp" type="number" id="ex-limit" min="0" value="0" placeholder="0 = без лимита"></div>
    </div>
    <div class="ex-fields-row ex-fields-row-2">
      <div><div class="ex-field-lbl">Вероятность</div>
        <select class="ex-rsel" id="ex-prob-band">
          <option value="">Все группы</option>
          <option value="high">Высокая (70%+)</option>
          <option value="medium">Средняя (50–69%)</option>
          <option value="low">Низкая (&lt;50%)</option>
        </select>
      </div>
      <div><div class="ex-field-lbl">Сортировка</div>
        <select class="ex-rsel" id="ex-sort">
          <option value="rank">По рейтингу</option>
          <option value="pub">По дате публикации</option>
          <option value="added">По дате добавления</option>
        </select>
      </div>
    </div>
  </div>
  <div class="ex-ftr">
    <button class="tb" onclick="exReset()">Сбросить</button>
    <div class="ex-ftr-r">
      <button class="tb" onclick="exModalClose()">Отмена</button>
      <button class="tb up" onclick="exSave()">Сохранить</button>
    </div>
  </div>
</dialog>

<script>(function(){{
  var ls=window.__ls||{{}};
  if(ls['theme']==='light'){{var b=document.getElementById('btn-theme');if(b)b.textContent='🌙 Тёмная тема';}}
  if(ls['compact']==='1'){{var b=document.getElementById('btn-compact');if(b){{b.textContent='⊞ Полный';b.classList.add('compact-on');}}}}
  if(ls['sort']&&ls['sort']!=='rank'){{var s=document.getElementById('sort-sel');if(s)s.value=ls['sort'];}}
}}());</script>

<!-- ═══ LAYOUT: sidebar + content ═══ -->
<div class="layout">

  <aside class="sidebar">
    <div class="sidebar-scroll">

    <!-- ── Обновление ── -->
    <div class="sb-section">
      <div class="sb-lbl">Обновление</div>
      <div class="sb-row">
        <select class="sb-sel" id="sb-update-sel">
          <option value="check">Проверить закрытые</option>
          <option value="update">Обновить без проверки актуальности</option>
          <option value="update-bg">Обновить с проверкой актуальности в фоне</option>
        </select>
        <button class="sb-btn" id="sb-update-btn" onclick="apiActionRun()" title="Запустить">▶</button>
      </div>
    </div>

    <!-- ── Экспорт ── -->
    <div class="sb-section">
      <div class="sb-lbl">Экспорт в vacancies.json</div>
      <div class="sb-row">
        <select class="sb-sel" id="sb-export-sel" onchange="onExportPresetChange(this.value)">
          <option value="unprocessed">Необработанные</option>
          <option value="funnel">Мои отклики</option>
          <option value="new_interested">Рекомендации</option>
          <option value="visible">Все из отчёта</option>
          <option value="custom">Настроить</option>
        </select>
        <button class="sb-btn" id="btn-export" onclick="apiExportRun()" title="Экспортировать">💾</button>
      </div>
      <div id="sb-cfg-row" style="display:none">
        <button class="sb-btn-cfg" onclick="exModalOpen()">⚙ Настроить</button>
      </div>
      <div id="ex-sum" class="sb-sum"></div>
    </div>

    <!-- ── Прогресс ── -->
    <div class="sb-progress"><span id="tbmsg" class="tb-msg"></span></div>

{sidebar_html}
    </div>
    <div class="sidebar-footer">
      <button class="reset-btn" id="reset-btn" onclick="resetF()">✕ Сбросить фильтры</button>
    </div>
  </aside>

  <div class="content">
    <main>
      <div class="sort-bar">
        <span id="shown-count">{total} вакансий</span>
      </div>
      <div id="active-tags"></div>
      <div id="nores" style="display:none">Ничего не найдено.</div>
      {cards}
    </main>
  </div>

</div>

<script>
// ── State ──
var DIMS = ['band','status','wp','rev','fresh'];
var F = {{band:'all',status:'new',wp:'all',rev:'all',fresh:'all'}};
var CARDS = [];
var ORIGINAL_ORDER = null;

var DIM_NAMES = {{
  status:{{new:'новые',interested:'интересно',applied:'откликнулся',interview:'собес',offer:'оффер',rejected:'отказ',skipped:'пропущ.',archived:'Закрыта'}},
  rev:{{'0':'👀 непросм.','1':'✓ просм.'}},
  band:{{green:'🟢 90+',yellow:'🟡 75–89',orange:'🟠 60–74',red:'🔴 <60'}},
  fresh:{{f3:'🔥 ≤3 дн',f14:'≤14 дн',f30:'≤30 дн',old:'🕰 стар.',unk:'без даты'}},
  wp:{{'1':'⭐ WordPress'}},
}};
var DIM_LABELS = {{status:'Статус',rev:'Ревью',band:'Шанс',fresh:'Свежесть',wp:'Приоритет'}};

// ── Core filter ──
function matchExcept(c,ex){{
  for(var i=0;i<DIMS.length;i++){{var k=DIMS[i];
    if(k===ex)continue;
    if(F[k]!=='all'&&c.dataset[k]!==F[k])return false;}}
  return true;}}

function apply(){{
  var shown=0;
  CARDS.forEach(function(c){{var ok=matchExcept(c,null);c.style.display=ok?'':'none';if(ok)shown++;}});
  document.querySelectorAll('.sidebar button[data-k]').forEach(function(b){{
    var k=b.dataset.k,v=b.dataset.v,n=0;
    CARDS.forEach(function(c){{if(matchExcept(c,k)&&(v==='all'||c.dataset[k]===v))n++;}});
    var el=b.querySelector('.cnt');if(el)el.textContent=n;}});
  var no=document.getElementById('nores');if(no)no.style.display=shown?'none':'';
  var sc=document.getElementById('shown-count');if(sc)sc.textContent=shown+' вакансий';
  updateActiveTags();
  updateSFIndicators();
  updateResetBtn();}}

function flt(btn){{var k=btn.dataset.k;
  document.querySelectorAll('.sidebar button[data-k="'+k+'"]').forEach(function(b){{b.classList.remove('active');}});
  btn.classList.add('active');F[k]=btn.dataset.v;apply();}}

function resetF(){{
  DIMS.forEach(function(k){{F[k]='all';}});
  document.querySelectorAll('.sidebar button[data-k]').forEach(function(b){{b.classList.toggle('active',b.dataset.v==='all');}});
  apply();}}

// ── Active filter tags ──
function updateActiveTags(){{
  var ct=document.getElementById('active-tags');if(!ct)return;
  ct.innerHTML='';var any=false;
  DIMS.forEach(function(k){{
    if(F[k]==='all')return;
    any=true;
    var label=(DIM_NAMES[k]||{{}})[F[k]]||F[k];
    var tag=document.createElement('span');
    tag.className='atag';
    tag.innerHTML=(DIM_LABELS[k]||k)+': '+label+' <span class="atag-x">×</span>';
    (function(dim){{
      tag.querySelector('.atag-x').addEventListener('click',function(e){{
        e.stopPropagation();F[dim]='all';
        document.querySelectorAll('.sidebar button[data-k="'+dim+'"]').forEach(function(b){{b.classList.toggle('active',b.dataset.v==='all');}});
        apply();}});}})(k);
    ct.appendChild(tag);}});
  ct.style.display=any?'flex':'none';}}

// ── SF dot indicators ──
function updateSFIndicators(){{
  ['status','rev','band','fresh','wp'].forEach(function(key){{
    var dot=document.getElementById('sfd-'+key);
    var title=dot?dot.closest('.sf-title'):null;
    if(title)title.classList.toggle('sf-has-active',F[key]!=='all');}});}}

// ── Reset button counter ──
function updateResetBtn(){{
  var n=DIMS.filter(function(k){{return F[k]!=='all';}}).length;
  var btn=document.getElementById('reset-btn');
  if(btn)btn.textContent=n>0?'✕ Сбросить ('+n+')':'✕ Сбросить фильтры';}}

// ── Collapsible sidebar sections ──
function toggleSF(key){{
  var body=document.getElementById('sfb-'+key);
  var arr=document.getElementById('sfa-'+key);
  if(!body)return;
  var col=body.classList.toggle('sf-col');
  if(arr)arr.textContent=col?'▶':'▼';
  try{{localStorage.setItem('sf-'+key,col?'0':'1');}}catch(e){{}}
  updateSFIndicators();}}

function initSF(){{
  var ls=window.__ls||{{}};
  ['status','rev','band','fresh','wp'].forEach(function(key){{
    var stored=ls['sf-'+key];if(stored===null||stored===undefined)return;
    var open=stored==='1';
    var body=document.getElementById('sfb-'+key);
    var arr=document.getElementById('sfa-'+key);
    if(body)body.classList.toggle('sf-col',!open);
    if(arr)arr.textContent=open?'▼':'▶';}});}}

// ── Compact mode ──
function toggleCompact(){{
  var main=document.querySelector('main');
  var btn=document.getElementById('btn-compact');
  if(!main)return;
  var on=main.classList.toggle('compact');
  if(btn){{btn.textContent=on?'⊞ Полный':'⊟ Компакт';btn.classList.toggle('compact-on',on);}}
  try{{localStorage.setItem('compact',on?'1':'0');}}catch(e){{}}}}

function initCompact(){{
  var ls=window.__ls||{{}};
  if(ls['compact']==='1'){{
    var main=document.querySelector('main');if(main)main.classList.add('compact');
    var btn=document.getElementById('btn-compact');
    if(btn){{btn.textContent='⊞ Полный';btn.classList.add('compact-on');}}}};}}

// ── Sort ──
function sortCards(by){{
  var main=document.querySelector('main');if(!main||!ORIGINAL_ORDER)return;
  var sorted;
  if(by==='rank'){{sorted=ORIGINAL_ORDER.slice();}}
  else{{sorted=CARDS.slice().sort(function(a,b){{
    var va=a.dataset[by==='pub'?'pub':'added']||'';
    var vb=b.dataset[by==='pub'?'pub':'added']||'';
    return vb.localeCompare(va);}});}}
  sorted.forEach(function(c){{main.appendChild(c);}});
  CARDS=sorted;
  try{{localStorage.setItem('sort',by);}}catch(e){{}}}}

function initSort(){{
  var ls=window.__ls||{{}};
  var stored=ls['sort'];
  if(stored&&stored!=='rank'){{
    var sel=document.getElementById('sort-sel');if(sel)sel.value=stored;
    sortCards(stored);}};}}

// ── Mark reviewed ──
function markReviewed(vid){{
  if(location.protocol==='file:'){{tbMsg('Открой через start.bat.','err');return;}}
  fetch('/api/mark-reviewed?id='+vid)
    .then(function(r){{return r.json();}})
    .then(function(d){{
      if(d.ok){{
        var c=document.querySelector('.card[data-id="'+vid+'"]');
        if(c){{
          c.dataset.rev='1';
          var fl=c.querySelector('.flags');
          if(fl){{var sp=document.createElement('span');sp.className='flag rev';sp.textContent='✓ просмотрено';fl.appendChild(sp);}}
          var btn=c.querySelector('.stn-rev');if(btn)btn.remove();}}
        apply();
      }}else{{tbMsg('Ошибка: '+(d.msg||''),'err');}}}})
    .catch(function(e){{tbMsg('Нет связи: '+e,'err');}});}}

// ── API helpers ──
function copyLetter(id){{navigator.clipboard.writeText(document.getElementById('letter-'+id).innerText);}}
function tbMsg(t,cls){{
  var m=document.getElementById('tbmsg');
  if(!m)return;
  m.textContent=t;
  m.className='tb-msg'+(cls?' '+cls:'');
  var p=document.querySelector('.sb-progress');
  if(p)p.style.display=t?'block':'none';}}
function tbBusy(b){{
  document.querySelectorAll('.toolbar .tb').forEach(function(x){{x.disabled=b;}});
  var eb=document.getElementById('btn-export');if(eb)eb.disabled=b;}}

function setUpdateBtn(cancel){{
  var b=document.getElementById('sb-update-btn');
  if(!b)return;
  b.textContent=cancel?'✕':'▶';
  b.title=cancel?'Отменить':'Запустить';
  b.onclick=cancel?apiCancelRun:apiActionRun;
  b.disabled=false;}}

function apiCancelRun(){{
  var mode=(document.getElementById('sb-update-sel')||{{}}).value||'check';
  if(mode==='check'){{fetch('/api/cancel-check').catch(function(){{}});}}
  else{{fetch('/api/cancel-bg').catch(function(){{}});}}
  setUpdateBtn(false);
  tbBusy(false);
  tbMsg('Остановлено.','err');
  if(_bgTimer){{clearTimeout(_bgTimer);_bgTimer=null;}}
  if(_checkTimer){{clearTimeout(_checkTimer);_checkTimer=null;}}}}

function apiActionRun(){{
  if(location.protocol==='file:'){{tbMsg('Открой через start.bat.','err');return;}}
  var mode=(document.getElementById('sb-update-sel')||{{}}).value||'check';
  var btn=document.getElementById('sb-update-btn');
  if(btn)btn.disabled=true;
  if(mode==='check'){{
    tbMsg('Проверяю закрытые вакансии…','wait');
    fetch('/api/check-closed').then(function(r){{return r.json();}}).then(function(d){{
      if(!d.ok){{setUpdateBtn(false);tbMsg(d.msg||'Ошибка','err');return;}}
      setUpdateBtn(true);
      pollCheck();
    }}).catch(function(e){{setUpdateBtn(false);tbMsg('Нет связи','err');}});
  }}else if(mode==='update'){{
    tbBusy(true);tbMsg('Обновляю — парсинг hh.ru, ~1–2 мин…','wait');
    fetch('/api/update').then(function(r){{return r.json();}}).then(function(d){{
      if(d.ok){{tbMsg('Готово. Перезагружаю…');setTimeout(function(){{location.reload();}},700);}}
      else{{tbBusy(false);setUpdateBtn(false);var s=(d.steps||[]).map(function(x){{return x[0]+':'+(x[1]?'ok':'FAIL');}}).join(' ');tbMsg('Ошибка: '+s,'err');}}
    }}).catch(function(e){{tbBusy(false);setUpdateBtn(false);tbMsg('Нет связи. ('+e+')','err');}});
  }}else{{
    fetch('/api/update-bg').then(function(r){{return r.json();}}).then(function(d){{
      if(!d.ok){{setUpdateBtn(false);tbMsg(d.msg||'Ошибка','err');return;}}
      tbBusy(true);setUpdateBtn(true);tbMsg('Запущено в фоне…','wait');
      pollBg();
    }}).catch(function(e){{setUpdateBtn(false);tbMsg('Нет связи','err');}});}}}}

var _bgTimer=null;
function pollBg(){{
  _bgTimer=setTimeout(function(){{
    fetch('/api/progress').then(function(r){{return r.json();}}).then(function(d){{
      var steps=(d.steps||[]).map(function(s){{return s.name+(s.ok?'✓':'✗');}}).join(' → ');
      if(d.done){{
        tbBusy(false);setUpdateBtn(false);
        if(d.ok){{tbMsg('✓ '+steps+'. Перезагружаю…');setTimeout(function(){{location.reload();}},1500);}}
        else{{tbMsg('✗ Ошибка: '+steps,'err');}}
      }}else{{
        var cur=d.current?d.current+'…':'';
        var n=(d.step_idx!==undefined?d.step_idx:( (d.steps||[]).length))+1;
        var tot=d.total||0;
        var prog=tot?' ['+n+'/'+tot+']':'';
        tbMsg((steps?steps+' → ':'')+cur+prog,'wait');
        pollBg();}}
    }}).catch(function(){{pollBg();}});
  }},2000);}}

var _checkTimer=null;
function pollCheck(){{
  _checkTimer=setTimeout(function(){{
    fetch('/api/check-progress').then(function(r){{return r.json();}}).then(function(d){{
      if(d.done){{
        setUpdateBtn(false);
        if(d.ok){{
          if(d.closed>0){{
            tbMsg('Закрыто '+d.closed+' из '+d.total+'. Перезагружаю…');
            setTimeout(function(){{location.reload();}},1200);
          }}else{{tbMsg('✓ Все открыты: проверено '+d.total+' вакансий.');}}
        }}else{{tbMsg('Ошибка проверки: '+(d.current||''),'err');}}
      }}else{{
        tbMsg('Проверка: '+d.checked+'/'+d.total+' (закрыто: '+d.closed+')','wait');
        pollCheck();}}
    }}).catch(function(){{pollCheck();}});
  }},2500);}}

// ── Export configurator ──
var EX_DEF={{fields:['id','name','company','salary','score','rank','status','v2_status','matched','description'],filters:{{statuses:['new','interested'],min_salary:0,min_score:0,probability_band:'',limit:0}},sort:'rank'}};

function exCfg(){{
  try{{var c=JSON.parse(localStorage.getItem('ex_cfg'));return c||JSON.parse(JSON.stringify(EX_DEF));}}
  catch(e){{return JSON.parse(JSON.stringify(EX_DEF));}}}}

function exSumText(c){{
  var f=(c.fields||[]).length;
  var st=((c.filters||{{}}).statuses||[]).join(', ')||'—';
  var lim=(c.filters||{{}}).limit||0;
  return 'Поля: '+f+'/10 · '+st+(lim>0?' · Лимит: '+lim:'');}}

function onExportPresetChange(val){{
  var row=document.getElementById('sb-cfg-row');
  var sm=document.getElementById('ex-sum');
  if(val==='custom'){{
    if(row)row.style.display='';
    if(sm)sm.textContent=exSumText(exCfg());
  }}else{{
    if(row)row.style.display='none';
    if(sm)sm.textContent='';
  }}}}

function exModalOpen(){{
  var c=exCfg();
  document.querySelectorAll('#ex-fields input').forEach(function(el){{el.checked=(c.fields||[]).indexOf(el.value)!==-1;}});
  document.querySelectorAll('#ex-statuses input').forEach(function(el){{el.checked=((c.filters||{{}}).statuses||[]).indexOf(el.value)!==-1;}});
  var fil=c.filters||{{}};
  document.getElementById('ex-min-salary').value=fil.min_salary||0;
  document.getElementById('ex-min-score').value=fil.min_score||0;
  document.getElementById('ex-prob-band').value=fil.probability_band||'';
  document.getElementById('ex-limit').value=fil.limit||0;
  document.getElementById('ex-sort').value=c.sort||'rank';
  document.getElementById('ex-modal').showModal();}}

function exModalClose(){{document.getElementById('ex-modal').close();}}

function exReadForm(){{
  var fields=[];
  document.querySelectorAll('#ex-fields input:checked').forEach(function(el){{fields.push(el.value);}});
  var statuses=[];
  document.querySelectorAll('#ex-statuses input:checked').forEach(function(el){{statuses.push(el.value);}});
  return {{fields:fields,filters:{{statuses:statuses,min_salary:parseInt(document.getElementById('ex-min-salary').value)||0,min_score:parseInt(document.getElementById('ex-min-score').value)||0,probability_band:document.getElementById('ex-prob-band').value||'',limit:parseInt(document.getElementById('ex-limit').value)||0}},sort:document.getElementById('ex-sort').value||'rank'}};}}

function exSave(){{
  var c=exReadForm();
  if(!c.fields.length){{alert('Выбери хотя бы одно поле.');return;}}
  if(!c.filters.statuses.length){{alert('Выбери хотя бы один статус.');return;}}
  try{{localStorage.setItem('ex_cfg',JSON.stringify(c));}}catch(e){{}}
  var sm=document.getElementById('ex-sum');if(sm)sm.textContent=exSumText(c);
  exModalClose();}}

function exReset(){{
  var d=EX_DEF;
  document.querySelectorAll('#ex-fields input').forEach(function(el){{el.checked=d.fields.indexOf(el.value)!==-1;}});
  document.querySelectorAll('#ex-statuses input').forEach(function(el){{el.checked=d.filters.statuses.indexOf(el.value)!==-1;}});
  document.getElementById('ex-min-salary').value=0;
  document.getElementById('ex-min-score').value=0;
  document.getElementById('ex-prob-band').value='';
  document.getElementById('ex-limit').value=0;
  document.getElementById('ex-sort').value='rank';}}

function apiExportRun(){{
  if(location.protocol==='file:'){{tbMsg('Открой через start.bat.','err');return;}}
  var preset=(document.getElementById('sb-export-sel')||{{}}).value||'unprocessed';
  tbBusy(true);tbMsg('Экспортирую…','wait');
  if(preset==='custom'){{
    var cfg=exCfg();cfg.mode='custom';
    fetch('/api/export',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(cfg)}})
      .then(function(r){{return r.json();}}).then(function(d){{
        tbBusy(false);
        if(d.ok){{tbMsg('✓ vacancies.json (кастомный) сохранён');}}
        else{{tbMsg('Ошибка: '+(d.msg||''),'err');}}
      }}).catch(function(e){{tbBusy(false);tbMsg('Нет связи. ('+e+')','err');}});
  }}else{{
    fetch('/api/export?mode='+encodeURIComponent(preset))
      .then(function(r){{return r.json();}}).then(function(d){{
        tbBusy(false);
        if(d.ok){{tbMsg('✓ vacancies.json ('+preset+') сохранён');}}
        else{{tbMsg('Ошибка: '+(d.msg||''),'err');}}
      }}).catch(function(e){{tbBusy(false);tbMsg('Нет связи. ('+e+')','err');}});}}}}

function initExport(){{
  var ep=document.getElementById('sb-export-sel');
  onExportPresetChange(ep?ep.value:'unprocessed');}}

function setStatus(vid,status){{
  if(location.protocol==='file:'){{tbMsg('Открой через start.bat.','err');return;}}
  var SL={{new:'новая',interested:'интересно',applied:'откликнулся',interview:'собеседование',offer:'оффер',rejected:'отказ',skipped:'пропущена',archived:'Закрыта'}};
  fetch('/api/status?id='+vid+'&status='+status)
    .then(function(r){{return r.json();}})
    .then(function(d){{
      if(d.ok){{
        var c=document.querySelector('.card[data-id="'+vid+'"]');
        if(c){{
          c.dataset.status=status;
          var b=c.querySelector('.status');if(b){{b.textContent=SL[status]||status;b.className='status st-'+status;}}
          var sk=c.querySelector('.stn-skip');if(sk)sk.style.display=status==='skipped'?'none':'';
          // Auto-review on applied/interview/offer
          if(['applied','interview','offer'].indexOf(status)!==-1&&c.dataset.rev!=='1'){{
            c.dataset.rev='1';
            var fl=c.querySelector('.flags');
            if(fl&&!c.querySelector('.flag.rev')){{var sp=document.createElement('span');sp.className='flag rev';sp.textContent='✓ просмотрено';fl.appendChild(sp);}}
            var rb=c.querySelector('.stn-rev');if(rb)rb.remove();}}
          // Swap buttons when setting to applied
          if(status==='applied'){{
            var ab=c.querySelector('.stn-applied');
            if(ab){{ab.className='stn stn-interview';ab.textContent='🎯 Собес';ab.setAttribute('onclick',"setStatus('"+vid+"','interview')");}}
            if(!c.querySelector('.stn-rejected')){{
              var act=c.querySelector('.actions');
              if(act){{var rb2=document.createElement('button');rb2.className='stn stn-rejected';
                rb2.setAttribute('onclick',"setStatus('"+vid+"','rejected')");rb2.textContent='\u2715 Отказ';
                var si=c.querySelector('.stn-interview');if(si&&si.nextSibling)act.insertBefore(rb2,si.nextSibling);else if(si)act.appendChild(rb2);}}}}}}
          if(d.history_entry){{
            var hd=c.querySelector('details.hist');
            if(hd){{var ul=hd.querySelector('ul');var sm=hd.querySelector('summary');
              if(ul){{var li=document.createElement('li');li.innerHTML='<span class="hd">'+d.history_entry.date_fmt+'</span> '+d.history_entry.event;ul.appendChild(li);}}
              if(sm){{var n=hd.querySelectorAll('li').length;sm.textContent='История ('+n+')';}}}}}}
        }}
        apply();
      }}else{{tbMsg('Ошибка: '+(d.msg||''),'err');}}
    }}).catch(function(e){{tbMsg('Нет связи: '+e,'err');}});}}

// ── Theme ──
function toggleTheme(){{
  var light=document.documentElement.classList.toggle('light');
  var btn=document.getElementById('btn-theme');
  if(btn)btn.textContent=light?'🌙 Тёмная тема':'☀ Светлая тема';
  try{{localStorage.setItem('theme',light?'light':'dark');}}catch(e){{}}}}

function initTheme(){{
  var ls=window.__ls||{{}};
  if(ls['theme']==='light'){{
    // html.light already set in head script
    var btn=document.getElementById('btn-theme');
    if(btn)btn.textContent='🌙 Тёмная тема';}};}}

// ── Resume polling after page reload ──
function resumePolling(){{
  fetch('/api/progress').then(function(r){{return r.json();}}).then(function(d){{
    var steps=(d.steps||[]).map(function(s){{return s.name+(s.ok?'✓':'✗');}}).join(' → ');
    if(d.running){{
      tbBusy(true);setUpdateBtn(true);
      var n=(d.step_idx!==undefined?d.step_idx:( (d.steps||[]).length))+1;
      var tot=d.total||0;
      var prog=tot?' ['+n+'/'+tot+']':'';
      tbMsg((steps?steps+' → ':'')+( d.current?d.current+'…':'' )+prog,'wait');
      pollBg();
    }}else if(d.done&&steps&&d.finished_at){{
      var age=Math.floor(Date.now()/1000)-(d.finished_at||0);
      if(age<120)tbMsg(d.ok?'✓ '+steps:'✗ '+steps,d.ok?'':'err');
    }}
  }}).catch(function(){{}});
  fetch('/api/check-progress').then(function(r){{return r.json();}}).then(function(d){{
    if(d.running){{
      setUpdateBtn(true);
      tbMsg('Проверка: '+d.checked+'/'+d.total+' (закрыто: '+d.closed+')','wait');
      pollCheck();
    }}
  }}).catch(function(){{}});}}

// ── Init ──
document.addEventListener('DOMContentLoaded',function(){{
  CARDS=Array.prototype.slice.call(document.querySelectorAll('.card'));
  ORIGINAL_ORDER=CARDS.slice();
  initSF();
  initCompact();
  initSort();
  initTheme();
  initExport();
  var ff=document.getElementById('fouc-fix');if(ff)ff.remove();
  apply();
  resumePolling();
  var exModal=document.getElementById('ex-modal');
  if(exModal)exModal.addEventListener('click',function(e){{
    var r=this.getBoundingClientRect();
    if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)this.close();
  }});}});
</script></body></html>"""

    with io.open(REPORT, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"report.html built: {len(items)} vacancies | " +
          " ".join(f"{b}:{cnt[b]}" for b in BAND_INFO) + f" | letters:{n_letters}")


if __name__ == "__main__":
    main()
