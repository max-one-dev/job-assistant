# -*- coding: utf-8 -*-
"""
Builds data/report.html from data/store.json (the persistent journal).
Shows interview-pass probability (colored band), the reason tags (pros/risks),
topics to review before the interview, per-vacancy history and the cover letter.
Filters by band and by status.
"""
import json, io, os, html
from datetime import datetime, timezone, timedelta

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
    """Return (age_days|None, bucket, sort_factor) from published_at."""
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

TARGET_SCORE = 110  # set from config in main(); score >= target => 100%
REVIEWED = set()    # vacancy ids the user has already reviewed (from feedback.json)

BAND_INFO = {
    "green":  ("🟢", "Можно откликаться сразу"),
    "yellow": ("🟡", "Откликнуться, но повторить темы ниже"),
    "orange": ("🟠", "Отклик — если вакансия очень интересна"),
    "red":    ("🔴", "Лучше не тратить время"),
}
STATUS_LABEL = {"new": "новая", "interested": "интересно", "applied": "откликнулся",
                "interview": "собеседование", "offer": "оффер",
                "rejected": "отказ", "skipped": "пропущена", "archived": "в архиве"}


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
    """WordPress vacancy passes the salary gate: >=100k RUB, or salary not stated."""
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


def non_wp_low_salary(v):
    """Non-WordPress vacancy with stated RUB salary below 100k — user: недопустимо."""
    if v.get("is_wordpress"):
        return False
    s = v.get("salary") or {}
    hi = s.get("to") or s.get("from")
    cur = (s.get("currency") or "").upper()
    if hi is None:
        return False
    return cur in ("RUR", "RUB", "") and hi < 100000


SHORTLIST = ("interested", "applied", "interview", "offer")


def card(v):
    e = html.escape
    vid = v["id"]
    m = v.get("matched", {})
    pros = m.get("plus", [])
    risks = m.get("minus", [])
    band = v.get("band", "red")
    prob = v.get("probability", 0)
    icon, reco = BAND_INFO.get(band, BAND_INFO["red"])
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
        letter_html = f'<div class="skip">🚫 Рекомендую пропустить эту вакансию. {e(v.get("recommend_reason") or "")}</div>'
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
    hist_html = "".join(f"<li><span class='hd'>{e(ru_dt(h.get('date','')))}</span> {e(h.get('event',''))}</li>" for h in hist)
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
        '<div class="warn">⚠ Доступ к вакансии на hh ограничен — страница открывается только '
        'после входа под пользователем, у которого есть доступ. Данные сохранены из поиска на момент, '
        'когда вакансия была публичной; отклик по ссылке может не сработать.</div>')

    wp_pri = is_wp_priority(v)
    reviewed = vid in REVIEWED
    project = bool(v.get("project_employment"))
    age, fbucket, _ = freshness_info(v)
    flags = []
    if fbucket == "f3":
        flags.append(f"<span class='flag fresh'>🔥 свежая{'' if age is None else (' · сегодня' if age == 0 else f' · {age} дн')}</span>")
    elif age is not None:
        icon = "🕰" if fbucket == "old" else "📅"
        flags.append(f"<span class='flag age'>{icon} {age} дн</span>")
    cat = v.get("category")
    if cat:
        flags.append(f"<span class='flag cat-{e(cat)}'>Категория {e(cat)}</span>")
    if wp_pri:
        flags.append("<span class='flag wp'>⭐ WordPress</span>")
    if project:
        flags.append("<span class='flag proj'>🕒 проектная занятость</span>")
    if v.get("senior"):
        flags.append("<span class='flag sen'>👴 Senior/Lead</span>")
    if v.get("english_required"):
        flags.append("<span class='flag en'>🌐 нужен английский</span>")
    if reviewed:
        flags.append("<span class='flag rev'>✓ просмотрено</span>")
    flags_html = f'<div class="flags">{" ".join(flags)}</div>' if flags else ""

    return f"""
    <div class="card {band}{' wppri' if wp_pri else ''}" data-band="{band}" data-status="{e(status)}" data-id="{e(str(vid))}" data-wp="{1 if wp_pri else 0}" data-rev="{1 if reviewed else 0}" data-cat="{e(cat or '')}" data-fresh="{fbucket}">
      <div class="top">
        <div class="score {band}" title="балл: {v.get('score',0)}">{prob}<span class="pct">%</span></div>
        <div class="titlebox">
          <a class="title" href="{e(v['url'])}" target="_blank">{e(v.get('name') or '(без названия)')}</a>
          <div class="meta">{e(v.get('company') or '')} · {e(v.get('area') or '')} · {e(money(v.get('salary')))}</div>
          <div class="meta pub">📅 опубликована: {e(pub) or '—'}</div>
          {flags_html}
        </div>
        <div class="badges">
          <div class="verdict {band}">{icon} {e(reco)}</div>
          <div class="status st-{e(status)}">{e(STATUS_LABEL.get(status, status))}</div>
        </div>
      </div>
      {warn_html}
      <div class="row"><b>Совпадения:</b> {pros_html}</div>
      <div class="row"><b>Риски:</b> {risks_html}</div>
      {topics_html}
      <details><summary>Навыки и описание</summary>
        <div class="skills">{e(', '.join(v.get('key_skills') or []))}</div>
        {desc_block}</details>
      <details class="hist"><summary>История ({len(hist)})</summary><ul>{hist_html}</ul></details>
      {letter_html}
      <div class="actions">
        <a class="btn" href="{e(v.get('apply_url'))}" target="_blank">Откликнуться на hh →</a>
        <a class="btn ghost" href="{e(v['url'])}" target="_blank">Открыть вакансию</a>
        <button class="stn stn-applied" onclick="setStatus('{vid}','applied')">✓ Откликнулся</button>
        <button class="stn stn-rejected" onclick="setStatus('{vid}','rejected')">✕ Отказ</button>
        <button class="stn stn-archived" onclick="setStatus('{vid}','skipped')">⊘ Пропустить</button>
        <span class="idtag">id {e(vid)} · тип {e(v.get('vac_type') or '—')} · балл {v.get('score',0)} · rank {v.get('final_rank',0)} · найдена: {e(ru_dt(v.get('first_seen','')))}</span>
      </div>
    </div>"""


def main():
    global TARGET_SCORE, REVIEWED
    cfg = read_json(CONFIG, {})
    TARGET_SCORE = (cfg.get("probability") or {}).get("target_score", TARGET_SCORE)

    feedback = read_json(FEEDBACK, {})
    REVIEWED = set(feedback.keys())

    store = read_json(STORE, {})

    def visible(v):
        if v.get("status") == "archived":                 # архив/закрыта — прячем всегда
            return False
        if v.get("status") in SHORTLIST or v.get("status") == "rejected":  # воронка — показываем всегда
            return True
        if v.get("non_dev"):                              # не-разработческая роль — прячем
            return False
        if v.get("one_off"):                              # разовое задание — прячем
            return False
        if v.get("is_wordpress") and v.get("other_cms"):   # WP + другая CMS — прячем
            return False
        if v.get("project_employment"):                    # проектная — показываем (внизу)
            return True
        return v.get("probability", 0) >= 50               # рейтинг < 50% — прячем

    def tier(v):
        if v.get("project_employment"):
            return 2                                       # проектная — в самый низ
        if is_wp_priority(v):
            return 0                                       # WordPress-приоритет — вверх
        return 1

    visible_list = [v for v in store.values() if visible(v)]

    # Схлопываем дубликаты (одинаковые name+company): оставляем лучший представитель
    def rep_rank(v):
        return (1 if v.get("status") in SHORTLIST else 0,
                1 if v.get("letter") else 0,
                v.get("probability", 0))
    best = {}
    for v in visible_list:
        k = ((v.get("name") or "").strip().lower(), (v.get("company") or "").strip().lower())
        if k not in best or rep_rank(v) > rep_rank(best[k]):
            best[k] = v
    shown = list(best.values())
    n_dupes = len(visible_list) - len(shown)
    hidden0 = len(store) - len(shown)
    n_unreviewed = sum(1 for v in shown if v["id"] not in REVIEWED)
    def sort_rank(v):
        base = v.get("final_rank", v.get("probability", 0)) or 0
        return base * freshness_info(v)[2]           # буст свежести
    items = sorted(shown, key=lambda v: (tier(v), -sort_rank(v)))
    cnt = {b: sum(1 for v in items if v.get("band") == b) for b in BAND_INFO}
    n_letters = sum(1 for v in items if v.get("letter"))
    n_wp = sum(1 for v in items if is_wp_priority(v))
    total = len(items)
    n_reviewed = total - n_unreviewed
    from collections import Counter
    stc = Counter(v.get("status", "new") for v in items)
    catc = Counter(v.get("category") for v in items)
    frc = Counter(freshness_info(v)[1] for v in items)
    cards = "\n".join(card(v) for v in items)

    doc = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Job Assistant — журнал</title>
<style>
 body{{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}}
 header{{position:sticky;top:0;background:#161a22;padding:14px 22px;border-bottom:1px solid #262b36;z-index:5}}
 header h1{{margin:0 0 6px;font-size:18px}} .sum{{color:#9aa4b2;font-size:13px}}
 .toolbar{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:2px 0 8px}}
 .toolbar .tb{{background:#232a36;color:#cbd5e1;border:1px solid #333c4a;border-radius:6px;padding:5px 12px;cursor:pointer;font-size:13px}}
 .toolbar .tb:hover{{background:#2b3442}} .toolbar .tb:disabled{{opacity:.5;cursor:default}}
 .toolbar .tb.up{{background:#2f6feb;color:#fff;border-color:#2f6feb;font-weight:600}}
 .toolbar .tb.up:hover{{background:#3b7bf7}}
 .toolbar .tb-sep{{color:#6b7480;font-size:12px;margin-left:8px}}
 .toolbar .tb-msg{{font-size:12px;color:#8bc34a;margin-left:6px}} .toolbar .tb-msg.err{{color:#ff8787}} .toolbar .tb-msg.wait{{color:#ffd43b}}
 .filters{{margin-top:10px}} .filters small{{color:#6b7480;margin-right:6px;display:inline-block;min-width:74px}}
 .frow{{margin-bottom:6px;display:flex;flex-wrap:wrap;align-items:center;gap:6px}}
 .filters button{{background:#232a36;color:#cbd5e1;border:1px solid #333c4a;border-radius:6px;padding:4px 11px;cursor:pointer;font-size:13px}}
 .filters button.active{{background:#2f6feb;color:#fff;border-color:#2f6feb}}
 .filters .cnt{{opacity:.65;font-weight:600;font-size:11px;margin-left:3px}}
 .filters button.active .cnt{{opacity:.9}}
 .filters .reset{{background:#3a1f1f;color:#ffb3b3;border-color:#5a2a2a;margin-left:auto}}
 #nores{{color:#9aa4b2;text-align:center;padding:30px}}
 main{{padding:18px 22px;max-width:900px;margin:0 auto}}
 .card{{background:#161a22;border:1px solid #262b36;border-left:4px solid #555;border-radius:10px;padding:14px 16px;margin-bottom:14px}}
 .card.green{{border-left-color:#37b24d}} .card.yellow{{border-left-color:#f59f00}} .card.orange{{border-left-color:#f76707}} .card.red{{border-left-color:#e03131;opacity:.8}}
 .card.wppri{{border-left-color:#7048e8;box-shadow:inset 3px 0 0 #7048e8}}
 .flags{{margin-top:5px;display:flex;gap:6px;flex-wrap:wrap}}
 .flag{{font-size:11px;padding:2px 8px;border-radius:10px}}
 .flag.wp{{background:#241a45;color:#b197fc}} .flag.en{{background:#0f3038;color:#66d9e8}}
 .flag.proj{{background:#3a2f12;color:#ffd8a8}} .flag.rev{{background:#1c2a1c;color:#8bc34a}}
 .flag.sen{{background:#3a1f2a;color:#f7a1c4}}
 .flag.fresh{{background:#3a2410;color:#ffb066;font-weight:700}} .flag.age{{background:#20242c;color:#8a93a2}}
 .flag.cat-A{{background:#173a24;color:#69db7c;font-weight:700}} .flag.cat-B{{background:#3d3413;color:#ffd43b;font-weight:700}} .flag.cat-C{{background:#2a2f3a;color:#9aa4b2;font-weight:700}}
 .top{{display:flex;align-items:center;gap:12px}}
 .score{{font-size:24px;font-weight:700;min-width:58px;text-align:center;border-radius:8px;padding:6px 0;background:#232a36}}
 .score .pct{{font-size:13px;opacity:.7}}
 .score.green{{color:#51cf66}} .score.yellow{{color:#ffd43b}} .score.orange{{color:#ffa94d}} .score.red{{color:#ff8787}}
 .titlebox{{flex:1}} .title{{color:#4dabf7;font-weight:600;text-decoration:none;font-size:16px}} .title:hover{{text-decoration:underline}}
 .meta{{color:#9aa4b2;font-size:13px;margin-top:2px}} .pub{{color:#7d8695;font-size:12px;margin-top:3px}}
 .badges{{display:flex;flex-direction:column;gap:4px;align-items:flex-end;max-width:230px}}
 .verdict,.status{{font-size:12px;padding:3px 10px;border-radius:20px;white-space:nowrap;text-align:center}}
 .verdict{{white-space:normal}}
 .verdict.green{{background:#183d23;color:#69db7c}} .verdict.yellow{{background:#3d3413;color:#ffd43b}} .verdict.orange{{background:#3d2913;color:#ffa94d}} .verdict.red{{background:#3d1a1a;color:#ff8787}}
 .status{{background:#232a36;color:#9aa4b2}}
 .st-interested{{background:#12304a;color:#74c0fc}} .st-applied{{background:#1c3a2a;color:#69db7c}}
 .st-interview{{background:#33234a;color:#b197fc}} .st-offer{{background:#183d23;color:#51cf66}}
 .st-rejected{{background:#3d1a1a;color:#ff8787}} .st-skipped{{background:#2a2f3a;color:#6b7480}}
 .row{{margin-top:8px;font-size:13px}} .row b{{color:#9aa4b2}}
 .tag{{display:inline-block;font-size:12px;padding:2px 8px;border-radius:12px;margin:2px}}
 .tag.good{{background:#173a24;color:#69db7c}} .tag.bad{{background:#3a1717;color:#ff8787}}
 .tag.warn2{{background:#3a2f12;color:#ffd8a8;font-style:italic}}
 .topics{{margin-top:10px;background:#12233a;border:1px solid #24405f;border-radius:8px;padding:8px 12px;font-size:13px}}
 .topics b{{color:#9ec5ff}} .topics ul{{margin:6px 0 2px;padding-left:20px}} .topics li{{margin:2px 0;color:#cbd5e1}}
 details{{margin-top:10px}} summary{{cursor:pointer;color:#9aa4b2;font-size:13px}}
 .skills{{color:#cbd5e1;font-size:13px;margin:8px 0}} .desc{{color:#c4ccd8;font-size:13px;white-space:pre-wrap;max-height:360px;overflow:auto;background:#12151b;padding:12px 14px;border-radius:6px}}
 .desc.hh{{white-space:normal}} .desc.hh p{{margin:0 0 10px}} .desc.hh ul,.desc.hh ol{{margin:6px 0 10px;padding-left:22px}} .desc.hh li{{margin:3px 0}} .desc.hh strong,.desc.hh b{{color:#e6e6e6}}
 .warn{{margin-top:10px;background:#3a2a12;border:1px solid #5a4420;color:#ffd8a8;font-size:12px;padding:8px 10px;border-radius:6px}}
 .letter{{margin-top:12px;background:#12151b;border:1px solid #2a3340;border-radius:8px;padding:10px}}
 .letter-head{{font-size:13px;color:#9aa4b2;margin-bottom:6px;display:flex;align-items:center;gap:10px}}
 .profile{{background:#232a36;padding:2px 8px;border-radius:10px;font-size:12px}}
 .copy{{margin-left:auto;background:#2f6feb;color:#fff;border:0;border-radius:6px;padding:4px 10px;cursor:pointer}}
 .letter pre{{white-space:pre-wrap;font:13px/1.55 inherit;color:#e6e6e6;margin:0}}
 .nolatter{{margin-top:10px;color:#6b7480;font-size:12px;font-style:italic}}
 .skip{{margin-top:10px;background:#2a1a1a;border:1px solid #5a2a2a;color:#ffb3b3;font-size:12px;padding:8px 10px;border-radius:6px}}
 .actions{{margin-top:12px;display:flex;gap:8px;align-items:center}}
 .btn{{background:#37b24d;color:#fff;text-decoration:none;padding:7px 14px;border-radius:7px;font-size:13px}} .btn.ghost{{background:#232a36;color:#cbd5e1}}
 .stn{{background:none;border:1px solid #333c4a;color:#9aa4b2;padding:5px 11px;border-radius:6px;cursor:pointer;font-size:12px}} .stn:hover{{border-color:#555;color:#e6e6e6}}
 .stn-applied{{border-color:#2e7d4f;color:#69db7c}} .stn-applied:hover{{background:#173a24}} .stn-rejected{{border-color:#7d1a1a;color:#ff8787}} .stn-rejected:hover{{background:#3d1a1a}} .stn-archived{{border-color:#444;color:#6b7480}} .stn-archived:hover{{background:#222629}}
 .idtag{{margin-left:auto;color:#4a515e;font-size:11px}}
</style></head><body>
<header>
  <h1>Job Assistant — журнал вакансий ({len(items)})</h1>
  <div class="toolbar">
    <button class="tb up" onclick="apiUpdate(this)" title="Спарсить свежие вакансии с hh.ru и пересобрать отчёт (~1-2 мин)">🔄 Обновить вакансии</button>
    <span class="tb-sep">Сохранить список в файл vacancies.json:</span>
    <button class="tb" onclick="apiExport('visible',this)" title="Все вакансии, которые видны в этом отчёте">💾 Все из отчёта</button>
    <button class="tb" onclick="apiExport('new_interested',this)" title="Только новые и отмеченные «интересно» — те, на которые ещё стоит откликнуться">💾 Куда откликнуться (новые + интересные)</button>
    <button class="tb" onclick="apiExport('funnel',this)" title="Только те, что уже в работе: интересно / откликнулся / собеседование / оффер / отказ">💾 Мои отклики (воронка)</button>
    <span id="tbmsg" class="tb-msg"></span>
  </div>
  <div class="sum">🟢 {cnt['green']} · 🟡 {cnt['yellow']} · 🟠 {cnt['orange']} · 🔴 {cnt['red']} · ✉️ писем: {n_letters} · ⭐ WordPress: {n_wp} · 👀 непросмотрено: {n_unreviewed}
    <br><small>число в карточке = оценка вероятности пройти интервью (эвристика по твоим правилам, не гарантия). Скрыто (рейтинг &lt;50%, не-разработчик, reject-типы, архив, разовые, WP+другая CMS): {hidden0}; из них дубликатов свёрнуто: {n_dupes}. Отмеченные «интересно/отклик/отказ» показываются всегда.</small></div>
  <div class="filters">
    <div class="frow"><small>шанс:</small>
      <button class="active" data-k="band" data-v="all" onclick="flt(this)">все <b class="cnt">{total}</b></button>
      <button data-k="band" data-v="green" onclick="flt(this)">🟢 90+ <b class="cnt">{cnt['green']}</b></button>
      <button data-k="band" data-v="yellow" onclick="flt(this)">🟡 75–89 <b class="cnt">{cnt['yellow']}</b></button>
      <button data-k="band" data-v="orange" onclick="flt(this)">🟠 60–74 <b class="cnt">{cnt['orange']}</b></button>
      <button data-k="band" data-v="red" onclick="flt(this)">🔴 &lt;60 <b class="cnt">{cnt['red']}</b></button></div>
    <div class="frow"><small>категория:</small>
      <button class="active" data-k="cat" data-v="all" onclick="flt(this)">все <b class="cnt">{total}</b></button>
      <button data-k="cat" data-v="A" onclick="flt(this)">A <b class="cnt">{catc['A']}</b></button>
      <button data-k="cat" data-v="B" onclick="flt(this)">B <b class="cnt">{catc['B']}</b></button>
      <button data-k="cat" data-v="C" onclick="flt(this)">C <b class="cnt">{catc['C']}</b></button></div>
    <div class="frow"><small>статус:</small>
      <button class="active" data-k="status" data-v="all" onclick="flt(this)">все <b class="cnt">{total}</b></button>
      <button data-k="status" data-v="new" onclick="flt(this)">новые <b class="cnt">{stc['new']}</b></button>
      <button data-k="status" data-v="interested" onclick="flt(this)">интересно <b class="cnt">{stc['interested']}</b></button>
      <button data-k="status" data-v="applied" onclick="flt(this)">откликнулся <b class="cnt">{stc['applied']}</b></button>
      <button data-k="status" data-v="interview" onclick="flt(this)">собес <b class="cnt">{stc['interview']}</b></button>
      <button data-k="status" data-v="rejected" onclick="flt(this)">отказ <b class="cnt">{stc['rejected']}</b></button>
      <button data-k="status" data-v="skipped" onclick="flt(this)">пропущ. <b class="cnt">{stc['skipped']}</b></button></div>
    <div class="frow"><small>свежесть:</small>
      <button class="active" data-k="fresh" data-v="all" onclick="flt(this)">все <b class="cnt">{total}</b></button>
      <button data-k="fresh" data-v="f3" onclick="flt(this)">🔥 ≤3 дн <b class="cnt">{frc['f3']}</b></button>
      <button data-k="fresh" data-v="f14" onclick="flt(this)">≤14 дн <b class="cnt">{frc['f14']}</b></button>
      <button data-k="fresh" data-v="f30" onclick="flt(this)">≤30 дн <b class="cnt">{frc['f30']}</b></button>
      <button data-k="fresh" data-v="old" onclick="flt(this)">🕰 стар. <b class="cnt">{frc['old']}</b></button>
      <button data-k="fresh" data-v="unk" onclick="flt(this)">без даты <b class="cnt">{frc['unk']}</b></button></div>
    <div class="frow"><small>приоритет:</small>
      <button class="active" data-k="wp" data-v="all" onclick="flt(this)">все <b class="cnt">{total}</b></button>
      <button data-k="wp" data-v="1" onclick="flt(this)">⭐ WordPress <b class="cnt">{n_wp}</b></button>
      <small style="margin-left:14px">ревью:</small>
      <button class="active" data-k="rev" data-v="all" onclick="flt(this)">все <b class="cnt">{total}</b></button>
      <button data-k="rev" data-v="0" onclick="flt(this)">👀 непросмотренные <b class="cnt">{n_unreviewed}</b></button>
      <button data-k="rev" data-v="1" onclick="flt(this)">✓ просмотренные <b class="cnt">{n_reviewed}</b></button>
      <button class="reset" onclick="resetF()">✕ сбросить</button></div>
  </div>
</header>
<main><div id="nores" style="display:none">Ничего не найдено по выбранным фильтрам.</div>{cards}</main>
<script>
 var DIMS=['band','status','wp','rev','cat','fresh'];
 var F={{band:'all',status:'all',wp:'all',rev:'all',cat:'all',fresh:'all'}};
 var CARDS=[];
 function matchExcept(c,ex){{
   for(var i=0;i<DIMS.length;i++){{var k=DIMS[i];
     if(k===ex) continue;
     if(F[k]!=='all' && c.dataset[k]!==F[k]) return false;}}
   return true;}}
 function apply(){{
   var shown=0;
   CARDS.forEach(function(c){{var ok=matchExcept(c,null);c.style.display=ok?'':'none';if(ok)shown++;}});
   document.querySelectorAll('.filters button[data-k]').forEach(function(b){{
     var k=b.dataset.k,v=b.dataset.v,n=0;
     CARDS.forEach(function(c){{if(matchExcept(c,k)&&(v==='all'||c.dataset[k]===v))n++;}});
     var el=b.querySelector('.cnt'); if(el) el.textContent=n;}});
   var note=document.getElementById('nores'); if(note) note.style.display=shown?'none':'';}}
 function flt(btn){{var k=btn.dataset.k;
   document.querySelectorAll('.filters button[data-k=\"'+k+'\"]').forEach(function(b){{b.classList.remove('active');}});
   btn.classList.add('active'); F[k]=btn.dataset.v; apply();}}
 function resetF(){{DIMS.forEach(function(k){{F[k]='all';}});
   document.querySelectorAll('.filters button[data-k]').forEach(function(b){{b.classList.toggle('active',b.dataset.v==='all');}});
   apply();}}
 document.addEventListener('DOMContentLoaded',function(){{CARDS=Array.prototype.slice.call(document.querySelectorAll('.card'));apply();}});
 function copyLetter(id){{navigator.clipboard.writeText(document.getElementById('letter-'+id).innerText);}}
 function tbMsg(t,cls){{var m=document.getElementById('tbmsg');if(m){{m.textContent=t;m.className='tb-msg'+(cls?' '+cls:'');}}}}
 function tbBusy(b){{document.querySelectorAll('.toolbar .tb').forEach(function(x){{x.disabled=b;}});}}
 function apiUpdate(btn){{
   if(location.protocol==='file:'){{tbMsg('Открой отчёт через start.bat (нужен локальный сервер).','err');return;}}
   tbBusy(true);tbMsg('Обновляю вакансии — парсинг hh.ru, это ~1-2 мин…','wait');
   fetch('/api/update').then(function(r){{return r.json();}}).then(function(d){{
     if(d.ok){{tbMsg('Готово. Перезагружаю отчёт…');setTimeout(function(){{location.reload();}},700);}}
     else{{tbBusy(false);var s=(d.steps||[]).map(function(x){{return x[0]+':'+(x[1]?'ok':'FAIL');}}).join(' ');tbMsg('Ошибка обновления. '+s,'err');}}
   }}).catch(function(e){{tbBusy(false);tbMsg('Нет связи с сервером. Запусти start.bat. ('+e+')','err');}});}}
 function apiExport(mode,btn){{
   if(location.protocol==='file:'){{tbMsg('Открой отчёт через start.bat (нужен локальный сервер).','err');return;}}
   tbBusy(true);tbMsg('Экспортирую ('+mode+')…','wait');
   fetch('/api/export?mode='+encodeURIComponent(mode)).then(function(r){{return r.json();}}).then(function(d){{
     tbBusy(false);
     if(d.ok){{tbMsg('✓ '+(d.msg||('vacancies.json ('+mode+')')));}}
     else{{tbMsg('Ошибка экспорта: '+(d.msg||''),'err');}}
   }}).catch(function(e){{tbBusy(false);tbMsg('Нет связи с сервером. Запусти start.bat. ('+e+')','err');}});}}
 function setStatus(vid,status){{
   if(location.protocol==='file:'){{tbMsg('Открой отчёт через start.bat.','err');return;}}
   var SL={{new:'новая',interested:'интересно',applied:'откликнулся',interview:'собеседование',offer:'оффер',rejected:'отказ',skipped:'пропущена',archived:'в архиве'}};
   fetch('/api/status?id='+vid+'&status='+status)
     .then(function(r){{return r.json();}})
     .then(function(d){{
       if(d.ok){{
         var c=document.querySelector('.card[data-id="'+vid+'"]');
         if(c){{c.dataset.status=status;var b=c.querySelector('.status');if(b){{b.textContent=SL[status]||status;b.className='status st-'+status;}}}}
         apply();
       }}else{{tbMsg('Ошибка: '+(d.msg||''),'err');}}
     }}).catch(function(e){{tbMsg('Нет связи: '+e,'err');}});}}
</script></body></html>"""
    with io.open(REPORT, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"report.html built: {len(items)} vacancies | " +
          " ".join(f"{b}:{cnt[b]}" for b in BAND_INFO) + f" | letters:{n_letters}")


if __name__ == "__main__":
    main()
