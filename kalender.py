import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, time as dtime, timedelta
from zoneinfo import ZoneInfo

BASE = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
OUT = "veranstaltungen.ics"
TZ = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (LeverkusenKalender/8.0)"
CURRENT_YEAR = datetime.now(TZ).year

CATEGORIES = [
    ("42023", "Familie & Kinder"),
    ("42081", "Feste & Brauchtum"),
    ("42030", "Führungen & Touren"),
    ("42082", "Festivals & Open-Airs"),
    ("42078", "Karneval"),
    ("42029", "Lesungen & Literatur"),
    ("42032", "Märkte & Messen"),
    ("42036", "Partys & Nachtleben"),
    ("42031", "Spitzensport"),
    ("42037", "Tipps"),
    ("34821", "Warntage"),
]

MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}


def get(url, timeout=15, tries=3):
    err = None

    for n in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Connection": "close",
                },
            )

            with urllib.request.urlopen(
                req,
                timeout=timeout,
            ) as r:
                enc = (
                    r.headers.get_content_charset()
                    or "utf-8"
                )

                return r.read().decode(
                    enc,
                    errors="replace",
                )

        except Exception as e:
            err = e

            if n + 1 < tries:
                time.sleep(1)

    raise err


def rss_url(cid):
    p = [
        ("sp:categories[13495][0]", "-"),
        ("sp:categories[13495][1]", "__last__"),
        ("sp:categories[13459][0]", cid),
        ("sp:categories[13459][1]", "__last__"),
        (
            "sp:dateFrom[0]",
            date.today().isoformat(),
        ),
        ("sp:dateTo[0]", ""),
        ("sp:fulltext[0]", ""),
        ("sp:out", "rss"),
        (
            "sp:cmp",
            "eventSearch-1-0-searchResult",
        ),
        ("action", "submit"),
    ]

    return (
        BASE
        + "?"
        + urllib.parse.urlencode(p)
    )


def parse_rss(text, category):
    root = ET.fromstring(text)
    out = []

    for item in root.findall(".//item"):
        vals = {}

        for child in item:
            vals[
                child.tag.split("}")[-1]
            ] = html.unescape(
                "".join(
                    child.itertext()
                ).strip()
            )

        if (
            vals.get("title")
            and vals.get("link")
        ):
            out.append(
                {
                    "title": vals["title"],
                    "link": vals["link"],
                    "description": vals.get(
                        "description",
                        "",
                    ),
                    "category": category,
                }
            )

    return out


def unescape(v):
    return (
        html.unescape(v or "")
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def ical_value(value):
    value = value.strip()

    if re.fullmatch(
        r"\d{8}",
        value,
    ):
        return (
            datetime.strptime(
                value,
                "%Y%m%d",
            ).date(),
            True,
        )

    if value.endswith("Z"):
        return (
            datetime.strptime(
                value,
                "%Y%m%dT%H%M%SZ",
            ).replace(
                tzinfo=timezone.utc
            ),
            False,
        )

    return (
        datetime.strptime(
            value[:15],
            "%Y%m%dT%H%M%S",
        ),
        False,
    )


def parse_ics(text):
    lines = (
        text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )

    folded = []

    for line in lines:
        if (
            line.startswith(
                (" ", "\t")
            )
            and folded
        ):
            folded[-1] += line[1:]
        else:
            folded.append(line)

    ev = {}
    inside = False

    for line in folded:
        if line == "BEGIN:VEVENT":
            inside = True
            ev = {}

        elif line == "END:VEVENT":
            return ev

        elif (
            inside
            and ":" in line
        ):
            left, value = line.split(
                ":",
                1,
            )

            name = left.split(
                ";",
                1,
            )[0].upper()

            ev[name] = unescape(
                value
            )

    return ev


def page_ical_link(page):
    page = html.unescape(page)

    links = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        page,
        re.I,
    )

    for link in links:
        low = link.lower()

        if (
            "ical" in low
            or "termin-speichern" in low
        ):
            return urllib.parse.urljoin(
                BASE,
                link,
            )

    return None


def jsonld_event(page):
    pat = (
        r'<script[^>]+'
        r'type=["\']application/ld\+json'
        r'["\'][^>]*>'
        r'(.*?)'
        r'</script>'
    )

    for raw in re.findall(
        pat,
        page,
        re.I | re.S,
    ):
        try:
            obj = json.loads(
                html.unescape(
                    raw.strip()
                )
            )

        except Exception:
            continue

        stack = (
            obj
            if isinstance(obj, list)
            else [obj]
        )

        while stack:
            x = stack.pop()

            if isinstance(x, dict):
                typ = x.get(
                    "@type",
                    "",
                )

                if isinstance(
                    typ,
                    list,
                ):
                    types = [
                        str(t).lower()
                        for t in typ
                    ]
                else:
                    types = [
                        str(typ).lower()
                    ]

                if (
                    "event" in types
                    or "eventseries"
                    in types
                ):
                    return x

                stack.extend(
                    x.values()
                )

            elif isinstance(
                x,
                list,
            ):
                stack.extend(x)

    return None


def visible_text(page):
    s = html.unescape(page)

    s = re.sub(
        r"(?is)<(script|style|noscript).*?</\1>",
        " ",
        s,
    )

    s = re.sub(
        r"(?is)<[^>]+>",
        " ",
        s,
    )

    s = re.sub(
        r"\s+",
        " ",
        s,
    )

    return s.strip()


def extract_year(text, title=""):
    years = [
        int(x)
        for x in re.findall(
            r"\b(20\d{2})\b",
            title + " " + text,
        )
    ]

    if not years:
        return CURRENT_YEAR

    future = [
        y
        for y in years
        if y >= CURRENT_YEAR
    ]

    return (
        max(future)
        if future
        else max(years)
    )


def german_dates(text, title=""):
    month = (
        r"(Januar|Februar|März|Maerz|"
        r"April|Mai|Juni|Juli|August|"
        r"September|Oktober|November|Dezember)"
    )

    short_month = (
        r"(Jan|Feb|Mär|Maerz|Mrz|Apr|Mai|"
        r"Jun|Jul|Aug|Sep|Okt|Nov|Dez)"
    )

    year_default = extract_year(
        text,
        title,
    )

    patterns = [
        (
            rf"(\d{{1,2}})\.\s*[–-]\s*"
            rf"(\d{{1,2}})\.\s*{month}\s*(\d{{4}})"
        ),
        (
            rf"(\d{{1,2}})\.\s*"
            rf"(?:bis|–|-)\s*"
            rf"(\d{{1,2}})\.\s*"
            rf"{month}\s*(\d{{4}})"
        ),
        (
            rf"(\d{{1,2}})\.\s*"
            rf"{month}\s*(\d{{4}})"
        ),
        (
            rf"(\d{{1,2}})\.?\s*[–-]\s*"
            rf"(\d{{1,2}})\.?\s*"
            rf"{short_month}\.?\s*"
            rf"(\d{{4}})?"
        ),
        (
            rf"\b(\d{{1,2}})\.?\s+"
            rf"{short_month}\.?"
            rf"(?:\s+(\d{{4}}))?\b"
        ),
    ]

    for i, pat in enumerate(patterns):

        for m in re.finditer(
            pat,
            text,
            re.I,
        ):
            g = m.groups()

            try:
                if i in (0, 1):
                    d1, d2, mon, year = g

                    mo = MONTHS[
                        mon.lower()
                    ]

                    return (
                        date(
                            int(year),
                            mo,
                            int(d1),
                        ),
                        date(
                            int(year),
                            mo,
                            int(d2),
                        ),
                    )

                if i == 2:
                    d1, mon, year = g

                    mo = MONTHS[
                        mon.lower()
                    ]

                    d = date(
                        int(year),
                        mo,
                        int(d1),
                    )

                    return d, d

                if i == 3:
                    (
                        d1,
                        d2,
                        mon,
                        year,
                    ) = g

                    key = (
                        mon.lower()
                        .rstrip(".")
                    )

                    sm = {
                        "jan": 1,
                        "feb": 2,
                        "mär": 3,
                        "maerz": 3,
                        "mrz": 3,
                        "apr": 4,
                        "mai": 5,
                        "jun": 6,
                        "jul": 7,
                        "aug": 8,
                        "sep": 9,
                        "okt": 10,
                        "nov": 11,
                        "dez": 12,
                    }

                    mo = sm[key]

                    y = (
                        int(year)
                        if year
                        else year_default
                    )

                    return (
                        date(
                            y,
                            mo,
                            int(d1),
                        ),
                        date(
                            y,
                            mo,
                            int(d2),
                        ),
                    )

                d1, mon, year = g

                key = (
                    mon.lower()
                    .rstrip(".")
                )

                sm = {
                    "jan": 1,
                    "feb": 2,
                    "mär": 3,
                    "maerz": 3,
                    "mrz": 3,
                    "apr": 4,
                    "mai": 5,
                    "jun": 6,
                    "jul": 7,
                    "aug": 8,
                    "sep": 9,
                    "okt": 10,
                    "nov": 11,
                    "dez": 12,
                }

                mo = sm[key]

                y = (
                    int(year)
                    if year
                    else year_default
                )

                d = date(
                    y,
                    mo,
                    int(d1),
                )

                return d, d

            except Exception:
                continue

    for m in re.finditer(
        r"\b(20\d{2})[-/]"
        r"(\d{1,2})[-/]"
        r"(\d{1,2})\b",
        text,
    ):
        try:
            d = date(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
            )

            return d, d

        except Exception:
            pass

    return None, None


def german_times(text):
    pats = [
        (
            r"(\d{1,2}:\d{2})\s*"
            r"[–-]\s*"
            r"(\d{1,2}:\d{2})"
            r"\s*(?:Uhr)?"
        ),
        (
            r"(\d{1,2}:\d{2})\s*"
            r"(?:bis)\s*"
            r"(\d{1,2}:\d{2})"
            r"\s*(?:Uhr)?"
        ),
        (
            r"(?:von\s+)?"
            r"(\d{1,2}:\d{2})\s*"
            r"(?:Uhr)?\s*"
            r"(?:bis)\s*"
            r"(\d{1,2}:\d{2})"
            r"\s*(?:Uhr)?"
        ),
    ]

    for pat in pats:
        m = re.search(
            pat,
            text,
            re.I,
        )

        if m:
            return (
                m.group(1),
                m.group(2),
            )

    m = re.search(
        r"(?:ab\s+|Beginn:\s*|"
        r"Start\s*:\s*)?"
        r"(\d{1,2}:\d{2})"
        r"\s*Uhr",
        text,
        re.I,
    )

    if m:
        return (
            m.group(1),
            None,
        )

    return None, None


def embedded_event_datetime(page):
    raw = html.unescape(page)

    candidates = []

    patterns = [
        (
            r'(?:startDate|start_date|'
            r'start-date|data-start|'
            r'data-start-date|eventStart|'
            r'event_start)["\'=: ]+'
            r'([0-9]{4}-[0-9]{2}-[0-9]{2}'
            r'(?:[T ][0-9]{2}:[0-9]{2}'
            r'(?::[0-9]{2})?'
            r'(?:[+\-][0-9]{2}:?[0-9]{2}|Z)?)?)'
        ),
        (
            r'(?:endDate|end_date|'
            r'end-date|data-end|'
            r'data-end-date|eventEnd|'
            r'event_end)["\'=: ]+'
            r'([0-9]{4}-[0-9]{2}-[0-9]{2}'
            r'(?:[T ][0-9]{2}:[0-9]{2}'
            r'(?::[0-9]{2})?'
            r'(?:[+\-][0-9]{2}:?[0-9]{2}|Z)?)?)'
        ),
    ]

    for pat in patterns:
        candidates.extend(
            re.findall(
                pat,
                raw,
                re.I,
            )
        )

    if candidates:
        start, _ = parse_iso(
            candidates[0]
        )

        end = None

        if len(candidates) > 1:
            end, _ = parse_iso(
                candidates[1]
            )

        if start:
            return start, end

    return None, None


def make_local(d, hhmm):
    if not hhmm:
        return datetime.combine(
            d,
            dtime.min,
        ).replace(
            tzinfo=TZ
        )

    h, m = map(
        int,
        hhmm.split(":"),
    )

    return datetime.combine(
        d,
        dtime(h, m),
    ).replace(
        tzinfo=TZ
    )


def parse_page_dates(
    page,
    fallback_text="",
    title="",
):
    start_meta, end_meta = (
        embedded_event_datetime(page)
    )

    if start_meta is not None:

        if (
            end_meta is None
            and isinstance(
                start_meta,
                datetime,
            )
        ):
            end_meta = (
                start_meta
                + timedelta(hours=1)
            )

        elif end_meta is None:
            end_meta = start_meta

        elif (
            isinstance(
                start_meta,
                datetime,
            )
            and end_meta <= start_meta
        ):
            end_meta = (
                start_meta
                + timedelta(hours=1)
            )

        return (
            start_meta,
            end_meta,
            isinstance(
                start_meta,
                date,
            )
            and not isinstance(
                start_meta,
                datetime,
            ),
        )

    text = visible_text(page)

    d1, d2 = german_dates(
        text,
        title,
    )

    if (
        d1 is None
        and fallback_text
    ):
        d1, d2 = german_dates(
            visible_text(
                fallback_text
            ),
            title,
        )

    if d1 is None:
        return (
            None,
            None,
            False,
        )

    start_t, end_t = german_times(
        text
    )

    if (
        not start_t
        and fallback_text
    ):
        start_t, end_t = german_times(
            visible_text(
                fallback_text
            )
        )

    if not start_t:
        return (
            d1,
            d2,
            True,
        )

    start = make_local(
        d1,
        start_t,
    )

    if d2 > d1:
        end = make_local(
            d2,
            end_t or start_t,
        )

    elif end_t:
        end = make_local(
            d1,
            end_t,
        )

    else:
        end = (
            start
            + timedelta(hours=1)
        )

    if end <= start:
        end = (
            start
            + timedelta(hours=1)
        )

    return (
        start,
        end,
        False,
    )


def url_fallback(item):
    m = re.search(
        r"/(20\d{2})-(\d{2})-(\d{2})"
        r"(?:-(\d{2})-(\d{2}))?/?$",
        item["link"],
    )

    if not m:
        return (
            None,
            None,
            False,
        )

    y, mo, d = map(
        int,
        m.group(1, 2, 3),
    )

    if m.group(4):
        start = datetime(
            y,
            mo,
            d,
            int(m.group(4)),
            int(m.group(5)),
        )

        return (
            start,
            start + timedelta(hours=1),
            False,
        )

    start = date(
        y,
        mo,
        d,
    )

    return (
        start,
        start,
        True,
    )


def fetch_event(item):
    try:
        page = get(
            item["link"]
        )

        # -------------------------------------------------
        # 1. Offizieller iCalendar-Link
        # -------------------------------------------------

        ical = page_ical_link(
            page
        )

        if ical:
            try:
                src = parse_ics(
                    get(
                        ical,
                        12,
                        2,
                    )
                )

                if "DTSTART" in src:
                    start, all_day = (
                        ical_value(
                            src["DTSTART"]
                        )
                    )

                    end = start

                    if "DTEND" in src:
                        end, _ = ical_value(
                            src["DTEND"]
                        )

                    if (
                        not all_day
                        and isinstance(
                            start,
                            datetime,
                        )
                        and isinstance(
                            end,
                            datetime,
                        )
                    ):
                        if end <= start:
                            end = (
                                start
                                + timedelta(
                                    hours=1
                                )
                            )

                    return (
                        {
                            "uid": item[
                                "link"
                            ],
                            "start": start,
                            "end": end,
                            "all_day": all_day,
                            "summary": (
                                src.get(
                                    "SUMMARY"
                                )
                                or item[
                                    "title"
                                ]
                            ),
                            "description": (
                                src.get(
                                    "DESCRIPTION"
                                )
                                or item[
                                    "description"
                                ]
                            ),
                            "location": src.get(
                                "LOCATION",
                                "",
                            ),
                            "url": (
                                src.get(
                                    "URL"
                                )
                                or item[
                                    "link"
                                ]
                            ),
                            "category": item[
                                "category"
                            ],
                        },
                        None,
                    )

            except Exception:
                pass

        # -------------------------------------------------
        # 2. JSON-LD
        # -------------------------------------------------

        data = jsonld_event(
            page
        )

        if (
            data
            and data.get(
                "startDate"
            )
        ):
            start, all_day = parse_iso(
                data.get(
                    "startDate"
                )
            )

            end, _ = parse_iso(
                data.get(
                    "endDate"
                )
            )

            if start is not None:

                if (
                    end is None
                    or (
                        not all_day
                        and end <= start
                    )
                ):
                    end = (
                        start
                        + timedelta(
                            hours=1
                        )
                        if not all_day
                        else start
                    )

                loc = data.get(
                    "location",
                    "",
                )

                if isinstance(
                    loc,
                    dict,
                ):
                    loc = loc.get(
                        "name",
                        "",
                    )

                return (
                    {
                        "uid": item[
                            "link"
                        ],
                        "start": start,
                        "end": end,
                        "all_day": all_day,
                        "summary": (
                            data.get(
                                "name"
                            )
                            or item[
                                "title"
                            ]
                        ),
                        "description": (
                            data.get(
                                "description"
                            )
                            or item[
                                "description"
                            ]
                        ),
                        "location": (
                            loc or ""
                        ),
                        "url": (
                            data.get(
                                "url"
                            )
                            or item[
                                "link"
                            ]
                        ),
                        "category": item[
                            "category"
                        ],
                    },
                    None,
                )

        # -------------------------------------------------
        # 3. Datum/Uhrzeit aus sichtbarem Seitentext
        # -------------------------------------------------

        start, end, all_day = (
            parse_page_dates(
                page,
                item[
                    "description"
                ],
                item[
                    "title"
                ],
            )
        )

        if start is not None:
            return (
                {
                    "uid": item[
                        "link"
                    ],
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                    "summary": item[
                        "title"
                    ],
                    "description": item[
                        "description"
                    ],
                    "location": "",
                    "url": item[
                        "link"
                    ],
                    "category": item[
                        "category"
                    ],
                },
                None,
            )

        # -------------------------------------------------
        # 4. Datum aus URL
        # -------------------------------------------------

        start, end, all_day = (
            url_fallback(item)
        )

        if start is not None:
            return (
                {
                    "uid": item[
                        "link"
                    ],
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                    "summary": item[
                        "title"
                    ],
                    "description": item[
                        "description"
                    ],
                    "location": "",
                    "url": item[
                        "link"
                    ],
                    "category": item[
                        "category"
                    ],
                },
                None,
            )

        return (
            None,
            "kein Datum gefunden",
        )

    except Exception as e:
        return (
            None,
            str(e),
        )


def parse_iso(v):
    if not v:
        return (
            None,
            False,
        )

    v = str(v).strip()

    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}",
        v,
    ):
        return (
            datetime.strptime(
                v,
                "%Y-%m-%d",
            ).date(),
            True,
        )

    try:
        return (
            datetime.fromisoformat(
                v.replace(
                    "Z",
                    "+00:00",
                )
            ),
            False,
        )

    except Exception:
        return (
            None,
            False,
        )


def esc(v):
    return (
        html.unescape(
            str(v or "")
        )
        .replace(
            "\\",
            "\\\\",
        )
        .replace(
            ";",
            "\\;",
        )
        .replace(
            ",",
            "\\,",
        )
        .replace(
            "\r",
            "",
        )
        .replace(
            "\n",
            "\\n",
        )
    )


def sort_key(e):
    x = e["start"]

    if (
        isinstance(x, date)
        and not isinstance(
            x,
            datetime,
        )
    ):
        x = datetime.combine(
            x,
            datetime.min.time(),
            tzinfo=TZ,
        )

    elif x.tzinfo is None:
        x = x.replace(
            tzinfo=TZ
        )

    return x.astimezone(
        timezone.utc
    ).timestamp()


def make_ics(events):
    stamp = (
        datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%dT%H%M%SZ"
        )
    )

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CNobach23//Leverkusen Kalender//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Leverkusen Veranstaltungen",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]

    for e in sorted(
        events,
        key=sort_key,
    ):
        lines += [
            "BEGIN:VEVENT",
            "UID:"
            + esc(
                e["uid"]
            ),
            "DTSTAMP:"
            + stamp,
        ]

        if e["all_day"]:

            lines.append(
                "DTSTART;VALUE=DATE:"
                + e["start"].strftime(
                    "%Y%m%d"
                )
            )

            # DTEND bei Ganztagsterminen
            # ist nach iCalendar exklusiv.
            end_date = (
                e["end"]
                + timedelta(
                    days=1
                )
            )

            lines.append(
                "DTEND;VALUE=DATE:"
                + end_date.strftime(
                    "%Y%m%d"
                )
            )

        else:
            s = (
                e["start"]
                if e["start"].tzinfo
                else e["start"].replace(
                    tzinfo=TZ
                )
            )

            d = (
                e["end"]
                if e["end"].tzinfo
                else e["end"].replace(
                    tzinfo=TZ
                )
            )

            lines.append(
                "DTSTART:"
                + s.astimezone(
                    timezone.utc
                ).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
            )

            lines.append(
                "DTEND:"
                + d.astimezone(
                    timezone.utc
                ).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
            )

        desc = e[
            "description"
        ]

        if desc:
            desc += "\n\n"

        desc += (
            "Kategorie: "
            + e["category"]
            + "\n\n"
            + e["url"]
        )

        lines.append(
            "SUMMARY:"
            + esc(
                e["summary"]
            )
        )

        lines.append(
            "DESCRIPTION:"
            + esc(
                desc
            )
        )

        if e["location"]:
            lines.append(
                "LOCATION:"
                + esc(
                    e["location"]
                )
            )

        lines.append(
            "URL:"
            + esc(
                e["url"]
            )
        )

        lines.append(
            "CATEGORIES:"
            + esc(
                e["category"]
            )
        )

        lines.append(
            "END:VEVENT"
        )

    lines.append(
        "END:VCALENDAR"
    )

    return (
        "\r\n".join(lines)
        + "\r\n"
    )


def main():
    items = []
    seen = set()

    # -----------------------------------------------------
    # RSS aller 11 gewünschten Kategorien laden
    # -----------------------------------------------------

    for cid, category in CATEGORIES:

        try:
            found = parse_rss(
                get(
                    rss_url(cid),
                    15,
                    3,
                ),
                category,
            )

            print(
                "Kategorie:",
                category,
                "RSS:",
                len(found),
            )

            for item in found:

                if (
                    item["link"]
                    not in seen
                ):
                    seen.add(
                        item["link"]
                    )

                    items.append(
                        item
                    )

        except Exception as e:
            print(
                "RSS FEHLER:",
                category,
                e,
            )

    print(
        "Eindeutige RSS-Veranstaltungen:",
        len(items),
    )

    # -----------------------------------------------------
    # Veranstaltungsseiten parallel verarbeiten
    # -----------------------------------------------------

    results = []
    failures = 0

    with ThreadPoolExecutor(
        max_workers=4
    ) as pool:

        jobs = {
            pool.submit(
                fetch_event,
                item,
            ): item
            for item in items
        }

        for job in as_completed(
            jobs
        ):
            result, error = (
                job.result()
            )

            if result:
                results.append(
                    result
                )

            else:
                failures += 1

                print(
                    "FEHLER:",
                    jobs[job][
                        "title"
                    ],
                    "-",
                    error,
                )

    # -----------------------------------------------------
    # Doppelte identische Termine entfernen
    # -----------------------------------------------------

    unique = {
        (
            e["uid"],
            e["start"],
        ): e
        for e in results
    }

    # -----------------------------------------------------
    # ICS schreiben
    # -----------------------------------------------------

    with open(
        OUT,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        f.write(
            make_ics(
                list(
                    unique.values()
                )
            )
        )

    # -----------------------------------------------------
    # Statistik ausgeben
    # -----------------------------------------------------

    counts = {}

    for e in unique.values():
        counts[
            e["category"]
        ] = (
            counts.get(
                e["category"],
                0,
            )
            + 1
        )

    print(
        "FERTIG!"
    )

    print(
        "Veranstaltungen geschrieben:",
        len(unique),
    )

    print(
        "Nicht verarbeitet:",
        failures,
    )

    for _, category in CATEGORIES:
        print(
            category + ":",
            counts.get(
                category,
                0,
            ),
        )


if __name__ == "__main__":
    main()