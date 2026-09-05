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


BASE_URL = (
    "https://www.leverkusen.de/"
    "stadt-erleben/veranstaltungskalender/index.php"
)

TZ = ZoneInfo("Europe/Berlin")


def download(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; LeverkusenKalender/1.0)"
            )
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:
        return response.read()


def rss_url(category_id):
    today = (
        dt.datetime.now(TZ)
        .date()
        .isoformat()
    )

    params = [
        ("form", "eventSearch-1.form"),
        ("sp:fulltext[0]", ""),
        ("sp:categories[13495][0]", "-"),
        ("sp:categories[13495][1]", "__last__"),
        ("sp:categories[13459][0]", category_id),
        ("sp:categories[13459][1]", "__last__"),
        ("sp:dateFrom[0]", today),
        ("sp:dateTo[0]", ""),
        ("sp:out", "rss"),
        (
            "sp:cmp",
            "eventSearch-1-0-searchResult",
        ),
        ("action", "submit"),
    ]

    return (
        BASE_URL
        + "?"
        + urllib.parse.urlencode(params)
    )


def parse_rss(data):
    root = ET.fromstring(data)

    events = []

    for item in root.iter():

        if item.tag.split("}")[-1] != "item":
            continue

        values = {}

        for child in item:

            name = child.tag.split("}")[-1]

            values[name] = html.unescape(
                "".join(
                    child.itertext()
                ).strip()
            )

        title = values.get(
            "title",
            "",
        )

        link = values.get(
            "link",
            "",
        )

        guid = (
            values.get(
                "guid",
                "",
            )
            or link
        )

        if title and link:

            events.append(
                {
                    "title": title,
                    "link": link,
                    "guid": guid,
                    "description": values.get(
                        "description",
                        "",
                    ),
                }
            )

    return events


def find_ical_link(event_url):
    """
    Öffnet genau die individuelle
    Veranstaltungsseite des RSS-Termins
    und sucht dort den offiziellen
    'Termin speichern'-iCal-Link.
    """

    page = download(
        event_url
    ).decode(
        "utf-8",
        errors="replace",
    )

    page = html.unescape(page)

    patterns = [
        r'href\s*=\s*["\']'
        r'([^"\']*iCalFromParameters[^"\']*)'
        r'["\']',

        r'href\s*=\s*["\']'
        r'([^"\']*icalfromparameters[^"\']*)'
        r'["\']',
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            page,
            flags=re.I,
        )

        if match:

            link = match.group(1)

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


def parse_ics(
    data,
    item,
    category,
):
    text = data.decode(
        "utf-8",
        errors="replace",
    )

    # Gefaltete ICS-Zeilen
    # wieder zusammenführen.
    text = re.sub(
        r"\r?\n[ \t]",
        "",
        text,
    )

    values = {}

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

        if key not in values:
            values[key] = value.strip()

    start = values.get(
        "DTSTART"
    )

    end = values.get(
        "DTEND"
    ) or start

    if not start:
        return None

    def unescape(value):

        return (
            value
            .replace(
                "\\n",
                "\n",
            )
            .replace(
                "\\N",
                "\n",
            )
            .replace(
                "\\,",