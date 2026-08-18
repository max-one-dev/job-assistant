# -*- coding: utf-8 -*-
"""
Job Assistant - collector.
Reads PUBLIC hh.ru pages (the same ones any visitor sees without login),
parses the embedded HH-Lux-InitialState JSON and MERGES new vacancies into
data/store.json - the single persistent journal. Nothing is ever deleted:
existing records keep their status/history/letter; only last_seen is refreshed.

No official API, no auth, no auto-apply. Personal use only.
"""
import json, re, html, time, io, os, sys, urllib.parse, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import write_json_atomic, backup_store
from datetime import datetime, timezone, timedelta

MSK = timezone(timedelta(hours=3))

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
CONFIG = os.path.join(ROOT, "config.json")
STORE = os.path.join(DATA, "store.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def log(m):
    sys.stdout.write(m + "\n"); sys.stdout.flush()


def read_json(path, default):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, data):
    write_json_atomic(path, data, indent=2)


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "ru,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def initial_state(page_html):
    m = re.search(r'id="HH-Lux-InitialState">(.*?)</template>', page_html, re.S)
    return json.loads(html.unescape(m.group(1))) if m else {}


def clean_html(s):
    """Keep hh's formatting HTML (p/ul/li/strong/br) but drop anything unsafe."""
    s = re.sub(r"(?is)<(script|style|iframe)[^>]*>.*?</\1>", "", s or "")
    return (s or "").strip()


def html_to_text(s):
    """Flatten HTML to plain text but PRESERVE line breaks and list bullets."""
    if not s:
        return ""
    s = re.sub(r"(?i)<(br|/p|/li|/div|/h[1-6]|/tr)\s*/?>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "• ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def basic_from_search(v):
    comp = v.get("compensation", {}) or {}
    company = v.get("company", {}) or {}
    area = v.get("area", {}) or {}
    vid = str(v.get("vacancyId") or "")
    return vid, {
        "id": vid,
        "url": f"https://hh.ru/vacancy/{vid}",
        "apply_url": f"https://hh.ru/applicant/vacancy_response?vacancyId={vid}",
        "name": v.get("name"),
        "company": company.get("name") if isinstance(company, dict) else company,
        "area": area.get("name") if isinstance(area, dict) else area,
        "salary": {"from": comp.get("from"), "to": comp.get("to"),
                    "currency": comp.get("currencyCode") or comp.get("currency")},
        "published_at": v.get("publicationTime") or v.get("creationTime"),
    }


def search_items(cfg):
    """Returns {id: basic_fields} from search listings (order preserved)."""
    s = cfg["search"]
    items = {}
    for query in s["queries"]:
        for page in range(s["max_pages"]):
            params = {"text": query, "items_on_page": s["items_on_page"], "page": page}
            if s.get("schedule"):
                params["schedule"] = s["schedule"]
            if s.get("area"):
                params["area"] = s["area"]
            url = "https://hh.ru/search/vacancy?" + urllib.parse.urlencode(params)
            data = initial_state(fetch(url))
            vsr = data.get("vacancySearchResult", {}) or {}
            vlist = vsr.get("vacancies", []) or []
            for v in vlist:
                vid, basic = basic_from_search(v)
                if vid and vid not in items:
                    items[vid] = basic
            log(f"  search '{query}' page {page}: {len(vlist)} vacancies")
            pages = (vsr.get("paging") or {}).get("pages") or []
            if not vlist or page >= len(pages) - 1:
                break
            time.sleep(s["request_delay_sec"])
        time.sleep(s["request_delay_sec"])
    return items


def fetch_detail(vid):
    data = initial_state(fetch(f"https://hh.ru/vacancy/{vid}"))
    vv = data.get("vacancyView", {}) or {}
    ks = vv.get("keySkills", {}) or {}
    raw_ks = ks.get("keySkill", []) if isinstance(ks, dict) else (ks or [])
    skills = [(k.get("name") if isinstance(k, dict) else k) for k in raw_ks]
    comp = vv.get("compensation", {}) or {}
    company = vv.get("company", {}) or {}
    area = vv.get("area", {}) or {}
    return {
        "id": str(vid),
        "url": f"https://hh.ru/vacancy/{vid}",
        "apply_url": f"https://hh.ru/applicant/vacancy_response?vacancyId={vid}",
        "name": vv.get("name"),
        "company": company.get("name") if isinstance(company, dict) else company,
        "area": area.get("name") if isinstance(area, dict) else area,
        "salary": {"from": comp.get("from"), "to": comp.get("to"),
                    "currency": comp.get("currencyCode") or comp.get("currency")},
        "work_format": vv.get("workFormat") or vv.get("@workSchedule"),
        "published_at": vv.get("publicationDate") or vv.get("creationTime"),
        "key_skills": skills,
        "description": html_to_text(vv.get("description", "")),
        "description_html": clean_html(vv.get("description", "")),
    }


def term_present(term, hay):
    return re.search(r"\b" + re.escape(term.lower()) + r"\b", hay) is not None


def _hay(vac):
    return " ".join([vac.get("name") or "", " ".join(vac.get("key_skills") or []),
                     vac.get("description") or ""]).lower()


_FW_ALIASES = {"React": ["react"], "Vue": ["vue", "vue.js", "vuejs"], "Symfony": ["symfony"]}
_FRONT_SEPARATE = re.compile(
    r"фронт\w*\s+вынес|вынесен\w*\s+в отдельн|отдельн\w*\s+приложени|"
    r"не\s+зона ответственности|отдельн\w*\s+фронт|фронтенд[^.]{0,40}отдельн")

# Проектная занятость (не постоянная) — ловим по формулировкам, не по слову "проект"
_PROJECT_EMPLOYMENT = re.compile(
    r"проектн\w*\s+(занятост|работ|основ)|(занятост|сотрудничеств|формат работы)\w*[:\s-]{0,3}проектн|"
    r"на\s+проектной\s+основе|работа\s+на\s+проект\b|на\s+время\s+проекта|проектная\s+форма|"
    r"под\s+конкретн\w*\s+проект|разов\w*\s+проект")

# WordPress-разработчик как роль: WP + dev-признак в названии/навыках, но не SEO/контент/QA/Битрикс в НАЗВАНИИ
_WP_TOKEN = re.compile(r"wordpress|woocommerce|вордпресс")
_DEV_TOKEN = re.compile(r"разработчик|developer|программист|back-?end|front-?end|full.?stack|инженер|engineer")
_NONDEV_NAME = re.compile(
    r"seo|линкбилд|link.?build|pbn|контент|content manager|\bqa\b|тестировщик|битрикс|bitrix|1с|"
    r"project manager|product|менеджер|дизайн|аналитик|administrator|админист|маркетолог|linkbuilder")
# Требуется английский: явный уровень или прямое требование
_ENGLISH_REQ = re.compile(
    r"english\s*[:\-]?\s*(b2|c1|c2|upper|advanced|fluent)|"
    r"(английск\w*)[^.]{0,25}(b2|c1|c2|свободн|продвинут|разговорн|обязателен|уверенн)|"
    r"fluent english|advanced english|upper[-\s]?intermediate")

# Другие CMS помимо WordPress/WooCommerce (для штрафа WP-вакансиям с зоопарком CMS)
_OTHER_CMS = re.compile(
    r"битрикс|bitrix|joomla|drupal|modx|typo3|opencart|тильда|tilda|magento|shopify|"
    r"umi\.?cms|netcat|host\s?cms|craft\s?cms|ghost|октобер|october\s?cms|prestashop")


def is_wordpress_role(vac):
    name = (vac.get("name") or "").lower()
    nk = name + " " + " ".join(vac.get("key_skills") or []).lower()
    return bool(_WP_TOKEN.search(nk) and _DEV_TOKEN.search(nk) and not _NONDEV_NAME.search(name))


def english_required(vac):
    return bool(_ENGLISH_REQ.search(_hay(vac)))


def requires_other_cms(vac):
    """True if the vacancy mentions a CMS other than WordPress/WooCommerce."""
    return bool(_OTHER_CMS.search(_hay(vac)))


# Конкретная проприетарная тема/сборщик, опыт с которой обязателен (у кандидата нет)
_PAGE_BUILDER = re.compile(r"avada|fusion\s*builder|\bdivi\b|wpbakery|wp\s*bakery|bricks\s*builder|beaver\s*builder")
# Роль-фронтенд (в НАЗВАНИИ), не backend/fullstack/php
_FRONTEND_ROLE_NAME = re.compile(r"front-?end|фронтенд|фронт-энд")
_BACKENDISH_NAME = re.compile(r"back-?end|бэкенд|full.?stack|фулстек|php|wordpress|битрикс")


def is_project_employment(vac):
    return bool(_PROJECT_EMPLOYMENT.search(_hay(vac)))


def is_frontend_role(vac):
    name = (vac.get("name") or "").lower()
    return bool(_FRONTEND_ROLE_NAME.search(name)) and not _BACKENDISH_NAME.search(name)


def needs_specific_builder(vac):
    return bool(_PAGE_BUILDER.search(_hay(vac)))


# Senior/Lead в НАЗВАНИИ (нежелательно, но не стоп — кандидат готов пробовать)
_SENIOR = re.compile(r"\bsenior\b|сеньор|ведущ(ий|его|ая|им)|team\s*lead|teamlead|тимлид|тех\.?лид|tech\s*lead")
# Вечерний/ночной/сменный график (стоп, кроме ramp-up)
_EVENING_SHIFT = re.compile(r"вечерн\w*\s+(график|смен|время)|ночн\w*\s+смен|сменн\w*\s+график|"
                            r"график\s+2\s*/\s*2|посменн|работа\s+в\s+ночь|ночны\w*\s+график")
# Неполный день сейчас -> полный позже (исключение для графика/зарплаты)
_RAMP_UP = re.compile(r"неполн\w*\s+день[^.]{0,80}(затем|потом|далее|после|перевод|перейд)\w*[^.]{0,25}полн|"
                      r"сначала\s+неполн|первое время[^.]{0,40}(неполн|пол\s*дня|полдня)|"
                      r"частичн\w*\s+занятост\w*[^.]{0,80}полн\w*\s+занятост|"
                      r"переход\w*\s+на\s+(полн|full)|возможн\w*\s+переход[^.]{0,30}полн|"
                      r"увеличен\w*\s+нагруз|расширен\w*\s+сотрудничеств|"
                      r"при\s+успешн\w*\s+работе[^.]{0,40}(полн|full|перевод)|"
                      r"зарплата\s+указана[^.]{0,30}part|только\s+для\s+part")
# Разовое задание (стоп)
_ONE_OFF = re.compile(r"разов\w*\s+задани|разов\w*\s+проект|единоразов|одноразов\w*\s+задани")


def is_senior_role(vac):
    return bool(_SENIOR.search((vac.get("name") or "").lower()))


def is_one_off(vac):
    return bool(_ONE_OFF.search(_hay(vac)))


# Не-разработческая роль (SEO/контент/менеджер/дизайн/аналитик и т.п.) — по НАЗВАНИЮ
_NONDEV_ROLE = re.compile(r"seo|сео|контент|маркетолог|smm|таргетолог|копирайт|редактор|"
                          r"менеджер|дизайн|аналитик|тестировщик|\bqa\b|консультант|"
                          r"специалист по продвиж|линкбилд|pbn|рекрут|hr\b|"
                          r"директолог|контекстн\w* реклам|контекстолог|ppc")


def is_non_developer(vac):
    name = (vac.get("name") or "").lower()
    return bool(_NONDEV_ROLE.search(name)) and not _DEV_TOKEN.search(name)


def _optional_near(hay, alias, window=70):
    """True if the tech near `alias` is framed as optional (plus/nice-to-have)."""
    opt = re.compile(r"будет плюсом|как плюс|желательн|преимуществ|will be a plus|nice to have|"
                     r"плюсом будет|приветствуется")
    for m in re.finditer(re.escape(alias), hay):
        s, e = max(0, m.start() - window), m.end() + window
        if opt.search(hay[s:e]):
            return True
    return False


def _near(hay, term, pattern, window=140):
    """True if `term` appears within `window` chars of any match of compiled `pattern`."""
    for mt in pattern.finditer(hay):
        s, e = max(0, mt.start() - window), mt.end() + window
        if term in hay[s:e]:
            return True
    return False


def _kw_present(term, hay):
    """Keyword presence with a few aliases."""
    if term == "Bitrix":
        return "bitrix" in hay or "битрикс" in hay
    if term == "CI/CD":
        return "ci/cd" in hay or "ci / cd" in hay or term_present("cicd", hay)
    if term == "REST API":
        return "rest api" in hay or "restful" in hay or term_present("rest", hay)
    if term == "Team Lead":
        return any(t in hay for t in ("team lead", "teamlead", "тимлид", "тим лид"))
    if term == "WordPress":
        return "wordpress" in hay or "вордпресс" in hay or "woocommerce" in hay
    return term_present(term, hay)


def score_vacancy(vac, cfg):
    sc = cfg["scoring"]
    hay = _hay(vac)
    total = 0
    matched = {"plus": [], "minus": []}

    def add(pts, label):
        nonlocal total
        total += pts
        (matched["plus"] if pts >= 0 else matched["minus"]).append(f"{label} ({pts:+d})")

    for term, pts in sc["keywords"].items():
        if _kw_present(term, hay):
            add(pts, term)

    remote = any(w in hay for w in ("удал", "дистан", "remote", "гибрид"))
    if remote:
        add(sc["remote_bonus"], "Удалёнка")

    sal = vac.get("salary") or {}
    amt = sal.get("from") or sal.get("to")
    cur = (sal.get("currency") or "").upper()
    s = sc["salary"]
    if amt and cur in ("RUR", "RUB", ""):
        if amt >= s["tier1_amount"]:
            add(s["tier1_bonus"], f"ЗП ≥ {s['tier1_amount'] // 1000}к")
        if amt >= s["tier2_amount"]:
            add(s["tier2_bonus"], f"ЗП ≥ {s['tier2_amount'] // 1000}к")

    # Только офис: явный запрет удалёнки И формат не удалённый
    if not remote and re.search(
            r"только в офисе|работа только в офисе|без удал[её]нк|"
            r"удал[её]нн\w* (формат|работа)\w* не предусмотр|не предусмотрен\w* удал",
            hay):
        add(sc["office_only_penalty"], "Только офис")

    # Требуется переезд: требование-формулировка, не удалёнка, без отрицаний/перков
    reloc_req = re.search(r"переезд обязат|обязательн\w* переезд|требуется переезд|"
                          r"готовность к переезду|релокац", hay)
    reloc_neg = re.search(r"без переезда|переезд\w* не |не требу\w* переезд|"
                          r"релокац\w* не |без релокац|возможен переезд|возможность переезд|"
                          r"помощь (с|в) переезд|компенс\w* переезд|помощь.{0,15}виз", hay)
    if not remote and reloc_req and not reloc_neg:
        add(sc["relocation_penalty"], "Требуется переезд")

    # Senior + обязательные 5+ лет ИМЕННО с Laravel: требование «5+ лет» рядом с Laravel
    exp5 = re.compile(r"(от\s*5|5\s*\+|не менее\s*5|5-ти|минимум\s*5)\s*(лет|год\w*|years)"
                      r"|опыт\D{0,25}5\s*(лет|год\w*|years)")
    if term_present("laravel", hay) and _near(hay, "laravel", exp5, window=140):
        add(sc["senior_laravel_5y_penalty"], "Senior 5+ лет Laravel")

    # Фреймворки без опыта кандидата — штраф ТОЛЬКО если реально в требованиях
    front_sep = bool(_FRONT_SEPARATE.search(hay))
    for fw, pts in (sc.get("gap_frameworks") or {}).items():
        aliases = _FW_ALIASES.get(fw, [fw.lower()])
        if not any(term_present(a, hay) for a in aliases):
            continue
        if any(_optional_near(hay, a) for a in aliases):   # «будет плюсом / желательно»
            continue
        if fw in ("React", "Vue") and front_sep:            # фронт вынесен отдельной командой
            continue
        add(pts, f"{fw} (нет опыта)")

    # Проектная занятость — не постоянная работа, кандидату не подходит
    if "project_employment_penalty" in sc and _PROJECT_EMPLOYMENT.search(hay):
        add(sc["project_employment_penalty"], "Проектная занятость")

    # Правила для WordPress-вакансий: сильный бонус (это профиль кандидата) + доп. штрафы
    if is_wordpress_role(vac):
        if "wordpress_role_bonus" in sc:
            add(sc["wordpress_role_bonus"], "WordPress-роль")
        if "wp_other_cms_penalty" in sc and _OTHER_CMS.search(hay):
            add(sc["wp_other_cms_penalty"], "WP + другая CMS")
        if "wp_english_penalty" in sc and english_required(vac):
            add(sc["wp_english_penalty"], "WP + обязателен английский")
        if "page_builder_penalty" in sc and _PAGE_BUILDER.search(hay):
            add(sc["page_builder_penalty"], "Нужна конкретная тема/сборщик (Avada/Divi)")

    # Роль-фронтенд (обязателен React/Vue/Next как ядро) — не профиль кандидата
    if "frontend_role_penalty" in sc and is_frontend_role(vac):
        add(sc["frontend_role_penalty"], "Фронтенд-роль")

    # Senior/Lead — нежелательно (мягкий штраф, вакансия остаётся видимой)
    if "senior_penalty" in sc and is_senior_role(vac):
        add(sc["senior_penalty"], "Senior/Lead уровень")

    # Вечерний/ночной/сменный график — стоп, кроме случая «неполный день -> полный»
    if "schedule_penalty" in sc and _EVENING_SHIFT.search(hay) and not _RAMP_UP.search(hay):
        add(sc["schedule_penalty"], "Вечерний/ночной/сменный график")

    # Требование свободного английского (для не-WP; у WP свой штраф)
    if "english_required_penalty" in sc and english_required(vac) and not is_wordpress_role(vac):
        add(sc["english_required_penalty"], "Нужен свободный английский")

    return total, matched


def interview_probability(score, cfg):
    target = cfg.get("probability", {}).get("target_score", 110)
    return max(0, min(100, round(score * 100 / target)))


def band_of(p):
    if p >= 90:
        return "green"
    if p >= 75:
        return "yellow"
    if p >= 60:
        return "orange"
    return "red"


TOPIC_RULES = [
    (["mysql", "postgresql", "postgres", "mariadb", "sql"], "SQL: JOIN, индексы, оптимизация запросов"),
    (["laravel"], "Laravel: очереди (Queues), Eloquent, сервис-контейнер"),
    (["docker"], "Docker и Docker Compose"),
    (["redis"], "Redis: кеширование и очереди"),
    (["rabbitmq", "kafka", "очеред"], "Очереди сообщений (RabbitMQ/Kafka)"),
    (["rest api", "restful", "rest"], "Проектирование REST API"),
    (["микросервис", "microservice"], "Микросервисная архитектура"),
    (["phpunit", "тест"], "Тестирование (PHPUnit)"),
    (["ci/cd", "gitlab", "github actions", "cicd"], "CI/CD пайплайны"),
    (["solid", "паттерн", "ооп"], "ООП, SOLID, паттерны проектирования"),
    (["yii"], "Yii2: устройство фреймворка и отличия от Laravel"),
    (["symfony", "doctrine"], "Symfony и Doctrine ORM"),
    (["golang", "go"], "Go: goroutines и channels"),
    (["wordpress", "woocommerce"], "WordPress/WooCommerce: хуки, кастомные типы"),
    (["mongo"], "MongoDB (NoSQL)"),
    (["elasticsearch", "manticore", "sphinx"], "Полнотекстовый поиск (Elasticsearch)"),
    (["kubernetes", "k8s"], "Kubernetes (основы)"),
    (["vue", "react"], "Frontend: Vue/React (база)"),
]


def review_topics(vac, limit=5):
    hay = _hay(vac)
    seen = set()
    topics = []
    for keys, topic in TOPIC_RULES:
        if any(term_present(k, hay) for k in keys) and topic not in seen:
            seen.add(topic)
            required = any(_mandatory(k, hay) for k in keys)
            topics.append({"topic": topic, "required": required})
    # Required topics first, then optional; cap at limit
    topics.sort(key=lambda t: not t["required"])
    return topics[:limit]


# ================= Career Assistant v2 scoring =================

_V2_ADD_TERMS = {
    "WordPress": r"wordpress|woocommerce|вордпресс",
    "WooCommerce": r"woocommerce|вукоммерс",
    "Backend": r"back-?end|бэкенд|бекенд",
    "REST API": r"rest api|restful|\brest\b",
    "Архитектура": r"архитектур",
    "Интеграции": r"интеграц",
    "Performance": r"performance|производительн|оптимизац|pagespeed|core web vitals|быстродейств",
    "Legacy": r"legacy|легаси",
    "Docker": r"docker",
    "Composer": r"composer",
    "Git": r"\bgit\b|gitlab|github",
    "CI/CD": r"ci/cd|ci / cd|cicd|gitlab ci|github actions|pipeline|пайплайн",
    "Laravel": r"laravel",
    "Symfony": r"symfony",
    "Vue": r"vue",
    "React": r"react",
}
_OPTIONAL_V2 = re.compile(r"будет плюсом|как плюс|желательн|преимуществ|will be a plus|nice to have|"
                          r"плюсом будет|приветствуется|не обязательн")
_EXP2 = re.compile(r"(от\s*2|2\s*\+|не менее\s*2|минимум\s*2|2-х|более\s*2)\s*(лет|год\w*|years)")


_MAND_MARKER = re.compile(r"обязательн|требуется|треб\w*|must[\s-]?have|"
                          r"уверенн\w* (владени|знани|опыт)|коммерческ\w* опыт|основн\w* стек|"
                          r"глубок\w* знани|отличн\w* знани|нужен опыт|необходим|опыт работы с")
_PART_TIME = re.compile(r"неполн\w* (день|занятост|рабоч)|part[\s-]?time|частичн\w* занятост|"
                        r"пол\s*дня|полдня|подработк|неполн\w* ставк|0[.,]5 ставки")
_HOURLY = re.compile(r"почас|оплата за час|hourly|за час работы")


def _mandatory(pattern, hay):
    """Технология считается обязательной только если рядом есть маркер обязательности
    и нет пометки «будет плюсом»."""
    for m in re.finditer(pattern, hay):
        s, e = max(0, m.start() - 120), m.end() + 120
        w = hay[s:e]
        if _OPTIONAL_V2.search(w):
            continue
        if _MAND_MARKER.search(w):
            return True
    return False


def employment_type(vac):
    hay = _hay(vac)
    if _HOURLY.search(hay):
        return "hourly"
    if _PROJECT_EMPLOYMENT.search(hay):
        return "project"
    if _PART_TIME.search(hay):
        return "part_time"
    if re.search(r"полн\w* занятост|полный рабочий день|полный день|full[\s-]?time|5/2|5 / 2", hay):
        return "full_time"
    return "unspecified"


def classify_type(vac):
    hay = _hay(vac)
    name = (vac.get("name") or "").lower()
    if re.search(r"bitrix|битрикс", name):
        return "bitrix"
    if re.search(r"devops|\bsre\b|инфраструктурн\w* инженер|систем\w* администратор|"
                 r"sys[\s-]?admin|системн\w* инженер|администратор серверов|"
                 r"linux[\s-]?админ|инженер эксплуатац|эникей", name):
        return "devops"
    if re.search(r"\bqa\b|тестировщик|quality assurance|инженер по тестир|автотест", name):
        return "qa"
    if re.search(r"security|безопасн|пентест|infosec|аппсек", name):
        return "security"
    if re.search(r"blockchain|блокчейн|web3|solidity|смарт-контракт", name):
        return "blockchain"
    if re.search(r"\bjava\b(?!script)|джава(?!скрипт)", name):
        return "java"
    if re.search(r"python|django|flask", name):
        return "python"
    if re.search(r"mobile|android|\bios\b|flutter|react native|мобильн|kotlin|swift", name):
        return "mobile"
    if re.search(r"\bgo\b|golang", name) and not re.search(r"php|laravel|wordpress", name):
        return "go"
    if is_non_developer(vac):
        return "non_dev"
    if is_wordpress_role(vac):
        return "wordpress"
    if is_frontend_role(vac):
        return "frontend"
    if re.search(r"laravel", name):
        return "laravel_backend"
    if re.search(r"fullstack|full.?stack|фулстек|фул-?стек", name) or \
       (re.search(r"php", hay) and re.search(r"vue|react", hay)):
        return "fullstack_php"
    if re.search(r"php", hay):
        return "php_backend"
    return "other"


def is_symfony_primary(vac, vtype):
    if vtype in ("wordpress", "laravel_backend"):
        return False
    name = (vac.get("name") or "").lower()
    if "laravel" in name:
        return False
    ks = " ".join(vac.get("key_skills") or []).lower()
    prominent = ("symfony" in name) or ("symfony" in ks)
    return prominent and _mandatory(r"symfony", _hay(vac))


def evaluate_v2(vac, cfg):
    v2 = cfg.get("v2") or {}
    hay = _hay(vac)
    vtype = classify_type(vac)
    reject_types = set(v2.get("reject_types", []))

    score = v2.get("base_score", 50)
    matched = {"plus": [], "minus": []}

    def add(pts, label):
        nonlocal score
        score += pts
        (matched["plus"] if pts >= 0 else matched["minus"]).append(f"{label} ({pts:+d})")

    for term, pts in (v2.get("add") or {}).items():
        if re.search(_V2_ADD_TERMS[term], hay):
            add(pts, term)

    pen = v2.get("penalty") or {}
    lar_2y = term_present("laravel", hay) and bool(_EXP2.search(hay)) and _mandatory(r"laravel", hay)
    if lar_2y:
        add(pen.get("laravel_2y", 0), "Laravel обязателен 2+ года")
    if vtype == "laravel_backend":
        add(pen.get("laravel_primary", 0), "Laravel основной стек")
    if vtype == "frontend" and "react" in hay:
        add(pen.get("react_primary", 0), "React основной стек")
    if vtype == "frontend" and "vue" in hay and "react" not in hay:
        add(pen.get("vue_primary", 0), "Vue основной стек")

    # инфра-технологии штрафуем ТОЛЬКО если они обязательны; считаем «чужие» обязательные
    missing_mandatory = 0
    hard_reason = lar_2y
    for key, pat in (("postgresql", r"postgres"), ("redis", r"redis"),
                     ("rabbitmq", r"rabbitmq|раббит"), ("kafka", r"kafka|кафка"),
                     ("elasticsearch", r"elasticsearch|opensearch|эластик"),
                     ("aws", r"\baws\b|amazon web services"), ("ddd", r"\bddd\b|domain-driven"),
                     ("highload", r"высоконагру|highload|high-load|высок\w* нагруз")):
        if _mandatory(pat, hay):
            add(pen.get(key, 0), key)
            missing_mandatory += 1
            if key in ("ddd", "highload"):
                hard_reason = True
    if re.search(r"микросервис|microservice|распредел[её]нн\w* систем|distributed system", hay):
        hard_reason = True

    tp = v2.get("type_prob") or {}
    if vtype == "laravel_backend" and lar_2y:
        prob = tp.get("laravel_backend_2y", tp.get("laravel_backend", 0.75))
    elif vtype == "fullstack_php" and _mandatory(r"vue|react", hay):
        prob = tp.get("fullstack_php_vue", tp.get("fullstack_php", 0.70))
    else:
        prob = tp.get(vtype, tp.get("other", 0.55))

    status = "reject" if vtype in reject_types else "recommended"
    if status == "reject":
        prob = 0.0

    salary_risk = None
    ov = v2.get("overrides") or {}
    if status != "reject":
        # Senior: понижать по РЕАЛЬНЫМ требованиям, а не по слову в названии
        if is_senior_role(vac):
            if hard_reason or missing_mandatory >= 2:
                prob *= ov.get("senior_hard_factor", 0.5)
                matched["minus"].append("Senior по реальным требованиям (архитектура/highload/чужой стек)")
            else:
                prob *= ov.get("senior_soft_factor", 0.85)
                matched["minus"].append("Senior формально в названии")
        if is_symfony_primary(vac, vtype):
            add(ov.get("symfony_primary_penalty", 0), "Symfony как основной стек")
            prob = min(prob, ov.get("symfony_primary_prob_cap", 0.45))
        if _EVENING_SHIFT.search(hay) and not _RAMP_UP.search(hay):
            prob *= ov.get("schedule_prob_factor", 0.5)
            matched["minus"].append("Вечерний/ночной/сменный график")
        if english_required(vac):
            prob *= ov.get("english_prob_factor", 0.85)
            matched["minus"].append("Требуется свободный английский")

        # Зарплата по типу занятости
        salcfg = v2.get("salary") or {}
        sal = vac.get("salary") or {}
        amt = sal.get("from") or sal.get("to")
        cur = (sal.get("currency") or "").upper()
        etype = employment_type(vac)
        ramp = bool(_RAMP_UP.search(hay))
        if amt and cur in ("RUR", "RUB", ""):
            smin = salcfg.get("min", 130000)
            if etype == "part_time":
                if not ramp:
                    prob *= salcfg.get("parttime_no_rampup_factor", 0.9)
                    salary_risk = "Part-time без перехода на full-time (-10%)"
            elif etype in ("full_time", "unspecified"):
                if amt < smin and not ramp:
                    prob *= salcfg.get("fulltime_below_min_factor", 0.85)
                    salary_risk = f"ЗП ниже минимума {smin // 1000}k"
        if salary_risk:
            matched["minus"].append(salary_risk)

    score = max(0, score)
    prob_pct = int(round(prob * 100))
    final_rank = round(score * prob, 1)
    if status == "reject":
        category = "C"
    elif prob_pct >= 85:
        category = "A"
    elif prob_pct >= 55:
        category = "B"
    else:
        category = "C"
    # сильную тех-вакансию не топим из-за зарплаты: минимум B, риск отмечен отдельно
    if salary_risk and category == "A":
        category = "B"
    return {"vac_type": vtype, "score": score, "matched": matched,
            "interview_probability": prob_pct, "final_rank": final_rank,
            "category": category, "v2_status": status}


def main():
    cfg = read_json(CONFIG, {})
    store = read_json(STORE, {})
    now = datetime.now(MSK).isoformat(timespec="seconds")

    delay = cfg["search"]["request_delay_sec"]
    log("Searching hh.ru public pages...")
    items = search_items(cfg)
    all_ids = list(items)
    new_ids = [i for i in all_ids if i not in store]
    seen_again = [i for i in all_ids if i in store]
    log(f"Found {len(all_ids)} vacancies: {len(new_ids)} new, {len(seen_again)} already known.")

    for vid in seen_again:
        store[vid]["last_seen"] = now

    added = 0
    for vid in new_ids[:cfg["search"]["max_new_details_per_run"]]:
        basic = items[vid]
        accessible = True
        try:
            vac = fetch_detail(vid)
        except urllib.error.HTTPError as e:
            if getattr(e, "code", None) == 403:
                accessible = False
                vac = dict(basic)
                vac.update({"work_format": None, "key_skills": [],
                            "description": "", "description_html": ""})
                log(f"  ! {vid}: доступ ограничен (403) — сохранён по данным из поиска")
            else:
                log(f"  {vid}: HTTP {getattr(e, 'code', '?')}")
                time.sleep(delay)
                continue
        except Exception as e:
            log(f"  {vid}: ERROR {e}")
            time.sleep(delay)
            continue
        if not vac.get("published_at"):
            vac["published_at"] = basic.get("published_at")
        ev2 = evaluate_v2(vac, cfg)
        prob = ev2["interview_probability"]
        bnd = band_of(prob)
        topics = review_topics(vac)
        ev = (f"Найдена. Категория {ev2['category']}, тип {ev2['vac_type']}, "
              f"вероятность интервью {prob}%, балл {ev2['score']}." if accessible
              else "Найдена в поиске, но страница закрыта для просмотра (доступ ограничен).")
        vac.update({
            "score": ev2["score"], "matched": ev2["matched"],
            "probability": prob, "band": bnd, "review_topics": topics,
            "vac_type": ev2["vac_type"], "category": ev2["category"],
            "v2_status": ev2["v2_status"], "final_rank": ev2["final_rank"],
            "accessible": accessible,
            "is_wordpress": is_wordpress_role(vac),
            "english_required": english_required(vac),
            "other_cms": requires_other_cms(vac),
            "project_employment": is_project_employment(vac),
            "one_off": is_one_off(vac),
            "senior": is_senior_role(vac),
            "non_dev": is_non_developer(vac),
            "first_seen": now, "last_seen": now,
            "status": "new", "profile": None, "letter": None,
            "history": [{"date": now, "event": ev}],
        })
        store[vid] = vac
        added += 1
        log(f"  {'+' if accessible else '!'} {vid}: {prob:>3}%  {bnd}")
        time.sleep(delay)

    backup_store(STORE)
    write_json(STORE, store)
    log(f"\nDone. +{added} new. store.json now holds {len(store)} vacancies.")


if __name__ == "__main__":
    main()
