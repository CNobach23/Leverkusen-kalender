import datetime as dt
import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo


CATEGORIES = {
    "42023": "Familie & Kinder",
    "42081": "Feste & Brauchtum",
    "42030": "Führungen & Touren",
    "42082": "Festivals & Open-Airs",
    "42078": "Karneval",
    "42029": "Lesungen & Literatur",
    "42032": "Märkte & Messen",
    "42036": "Partys & Nachtleben",
    "42031": "Spitzensport",
    "42037": "Tipps",
    "34821": "Warntage",
}

BASE_URL = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/index.php"
TZ = ZoneInfo("Europe/Berlin")


def request(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; LeverkusenKalender/1.0)"
            )
        },
    )

    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def rss_url(category_id):
    today = dt.datetime.now(TZ).date().isoformat()

    params = [
        ("form", "eventSearch-1.form"),
        ("sp:fulltext[]", ""),
        ("sp:categories[13495][]", "-"),
        ("sp:categories[13495][]", "__last__"),
        ("sp:categories[13459][]", category_id),
        ("sp:categories[13459][]", "__last__"),
        ("sp:dateFrom[]", today),
        ("sp:dateTo[]", ""),
        ("sp:out", "rss"),
        ("sp:cmp", "eventSearch-1-0-searchResult"),
        ("action", "submit"),
    ]

    return BASE_URL + "?" + urllib.parse.urlencode(params)


def parse_rss(data):
    root = ET.fromstring(data)
    events = []

    for item in root.iter():

        if item.tag.split("}")[-1] != "item":
            continue

        vals = {}

        for child in item:

            name = child.tag.split("}")[-1]

            vals[name] = html.unescape(
                "".join(child.itertext()).strip()
            )

        title = vals.get("title", "")
        link = vals.get("link", "")

        if title and link:

            events.append(
                {
                    "title": title,
                    "link": link,
                    "guid": vals.get("guid", "") or link,
                    "description": vals.get(
                        "description",
                        "",
                    ),
                }
            )

    return events


def find_ical_link(event_url):

    page = request(event_url).decode(
        "utf-8",
        errors="replace",
    )

    page = html.unescape(page)

    patterns = [
        r'href\s*=\s*["\']([^"\']*iCalFromParameters[^"\']*)["\']',
        r'href\s*=\s*["\']([^"\']*iCal[^"\']*)["\']',
        r'(["\'])([^"\']*iCalFromParameters[^"\']*)\1',
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            page,
            flags=re.I,
        ):

            if len(match.groups()) == 1:
                link = match.group(1)
            else:
                link = match.group(2)

            return urllib.parse.urljoin(
                event_url,
                link,
            )

    match = re.search(
        r'[^"\']*iCalFromParameters[^"\']*',
        page,
        flags=re.I,
    )

    if match:

        return urllib.parse.urljoin(
            event_url,
            match.group(0),
        )

    return None


def parse_ics(data, item, category):

    text = data.decode(
        "utf-8",
        errors="replace",
    )

    text = re.sub(
        r"\r?\n[ \t]",
        "",
        text,
    )

    vals = {}

    for line in text.splitlines():

        if ":" not in line:
            continue

        key, value = line.split(
            ":",
            1,
        )

        key = key.split(
            ";",
            1,
        )[0].upper()

        if key not in vals:
            vals[key