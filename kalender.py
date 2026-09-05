import html
import re
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE_URL = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
OUTPUT_FILE = "veranstaltungen.ics"
LOCAL_TZ = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (compatible; LeverkusenKalender/6.0)"

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


def get(url, timeout=15, tries=2):
    last = None

    for attempt in range(tries):
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

        except Exception as exc:
            last = exc

            if attempt + 1 < tries:
                time.sleep(1)

    raise last


def rss_url(category_id):
    params = [
        ("sp:categories[13495][0]", "-"),
        ("sp:categories[13495][1]", "__last__"),
        ("sp:categories[13459][0]", category_id),
        ("sp:categories[13459][1]", "__last__"),
        ("sp:dateFrom[0]", date.today().isoformat()),
        ("sp:dateTo[0]", ""),
        ("sp:fulltext[0]", ""),
        ("sp:out", "rss"),
        ("sp:cmp", "eventSearch-1-0-searchResult"),
        ("action", "submit"),
    ]

    return (
        BASE_URL
        + "?"
        + urllib.parse.urlencode(params)
    )


def parse_rss(text, category):
    root = ET.fromstring(text)

    out = []

    for item in root.iter():

        if item.tag.split("}")[-1] != "item":
            continue

        vals = {}

        for child in item:

            name = child.tag.split("}")[-1]

            vals[name] = html.unescape(
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


def unfold_ics(text):
    lines = (
        text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )

    out = []

    for line in lines:

        if (
            line.startswith((" ", "\t"))
            and out
        ):

            out[-1] += line[1:]

        else:

            out.append(line)

    return out


def parse_ics(text):
    event = {}
    inside = False

    for line in unfold_ics(text):

        if line == "BEGIN:VEVENT":

            inside = True
            event = {}

            continue

        if line == "END:VEVENT":

            return event

        if (
            not inside
            or ":" not in line
        ):

            continue

        left, value = line.split(
            ":",
            1,
        )

        name = left.split(
            ";",
            1,
        )[0].upper()

        event[name] = html.unescape(
            value
        )

    return event


def find_ical_on_page(page):

    page = html.unescape(page)

    links = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        page,
        re.I,
    )

    for link in links:

        if (
            "iCalFromParameters"
            in link
            or "iCalendar"
            in link
        ):

            return urllib.parse.urljoin(
                BASE_URL,
                link,
            )

    match = re.search(
        r'(https?://[^"\'\s<>]*'
        r'iCalFromParameters'
        r'[^"\'\s<>]*)',
        page,
        re.I,
    )

    if match:

        return html.unescape(
            match.group(1)
        )

    return None


def parse_ical_value(
    prop,
    value,
):

    value = (
        value or ""
    ).strip()

    if (
        prop
        and "VALUE=DATE"
        in prop.upper()
    ) or re.fullmatch(
        r"\d{8}",
        value,
    ):

        return (
            datetime.strptime(
                value[:8],
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


def parse_dt(value):

    if not value:
        return None, False

    value = str(
        value
    ).strip()

    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}",
        value,
    ):

        return (
            datetime.strptime(
                value,
                "%Y-%m-%d",
            ).date(),
            True,
        )

    try:

        return (
            datetime.fromisoformat(
                value.replace(
                    "Z",
                    "+00:00",
                )
            ),
            False,
        )

    except Exception:
        pass

    match = re.search(
        r"(20\d{2}-\d{2}-\d{2})"
        r"(?:[T ](\d{2}):(\d{2}))?",
        value,
    )

    if not match:
        return None, False

    if match.group(2):

        return (
            datetime.strptime(
                (
                    f"{match.group(1)} "
                    f"{match.group(2)}:"
                    f"{match.group(3)}"
                ),
                "%Y-%m-%d %H:%M",
            ),
            False,
        )

    return (
        datetime.strptime(
            match.group(1),
            "%Y-%m-%d",
        ).date(),
        True,
    )


def parse_jsonld(page):

    matches = re.findall(
        r'<script[^>]+type=["\']'
        r'application/ld\+json["\']'
        r'[^>]*>(.*?)</script>',
        page,
        re.I | re.S,
    )

    for raw in matches:

        try:

            import json

            obj = json.loads(
                html.unescape(
                    raw
                ).strip()
            )

        except Exception:

            continue

        stack = (
            obj
            if isinstance(
                obj,
                list,
            )
            else [obj]
        )

        while stack:

            cur = stack.pop()

            if isinstance(
                cur,
                dict,
            ):

                typ = cur.get(
                    "@type",
                    "",
                )

                if isinstance(
                    typ,
                    list,
                ):

                    types = [
                        str(x).lower()
                        for x in typ
                    ]

                else:

                    types = [
                        str(typ).lower()
                    ]

                if (
                    "event" in types
                    or "eventseries" in types
                ):

                    return cur

                stack.extend(
                    cur.values()
                )

            elif isinstance(
                cur,
                list,
            ):

                stack.extend(cur)

    return None


def fallback_from_url(item):

    match = re.search(
        r"/(20\d{2})-"
        r"(\d{2})-"
        r"(\d{2})"
        r"(?:-(\d{2})-(\d{2}))?/?$",
        item["link"],
    )

    if not match:
        return None, None, False

    y, m, d = map(
        int,
        match.group(
            1,
            2,
            3,
        ),
    )

    if match.group(4):

        start = datetime(
            y,
            m,
            d,
            int(match.group(4)),
            int(match.group(5)),
        )

        return (
            start,
            start,
            False,
        )

    start = date(
        y,
        m,
        d,
    )

    return (
        start,
        start + timedelta(
            days=1
        ),
        True,
    )


def make_event(
   