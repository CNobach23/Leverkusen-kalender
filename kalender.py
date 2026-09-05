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


def download(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def rss_url(category):
    today = dt.datetime.now(TZ).date().isoformat()

    params = [
        ("form", "eventSearch-1.form"),
        ("sp:fulltext[0]", ""),
        ("sp:categories[13495][0]", "-"),
        ("sp:categories[13495][1]", "__last__"),
        ("sp:categories[13459][0]", category),
        ("sp:categories[13459][1]", "__last__"),
        ("sp:dateFrom[0]", today),
        ("sp:dateTo[0]", ""),
        ("sp:out", "rss"),
        ("sp:cmp", "eventSearch-1-0-searchResult"),
        ("action", "submit"),
    ]

    return BASE_URL + "?" + urllib.parse.urlencode(params)


def search_url(title):
    params = [
        ("form", "eventSearch-1.form"),
        ("sp:fulltext[0]", title),
        ("sp:dateFrom[0]", dt.datetime.now(TZ).date().isoformat()),
        ("sp:dateTo[0]", ""),
        ("action", "submit"),
    ]

    return BASE_URL + "?" + urllib.parse.urlencode(params)


def parse_rss(data):
    root = ET.fromstring(data)
    result = []

    for item in root.iter():

        if item.tag.split("}")[-1] != "item":
            continue

        values = {}

        for child in item:
            name = child.tag.split("}")[-1]
            values[name] = html.unescape(
                "".join(child.itertext()).strip()
            )

        title = values.get("title", "")
        link = values.get("link", "")

        if title and link:
            result.append({
                "title": title,
                "link": link,
                "guid": values.get("guid", link),
                "description": values.get("description", ""),
            })

    return result


def find_ical(title):

    page = download(
        search_url(title)
    ).decode(
        "utf-8",
        errors="replace",
    )

    page = html.unescape(page)

    # Link zum offiziellen "Termin speichern"
    # suchen.
    matches = re.findall(
        r'href\s*=\s*["\']([^"\']*iCalFromParameters[^"\']*)["\']',
        page,
        flags=re.I,
    )

    if matches:
        return urllib.parse.urljoin(
            BASE_URL,
            matches[0],
        )

    # Fallback, falls der Link anders
    # eingebettet ist.
    match = re.search(
        r'([^"\']*iCalFromParameters[^"\']*)',
        page,
        flags=re.I,
    )

    if match:
        return urllib.parse.urljoin(
            BASE_URL,
            match.group(1),
        )

    return None


def parse_ical(data, item, category):

    text = data.decode(
        "utf-8",
        errors="replace",
    )

    # Gefaltete ICS-Zeilen zusammenführen.
    text = re.sub(
        r"\r?\n[ \t]",
        "",
        text,
    )

    values = {}

    for line in text.splitlines():

        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.split(";", 1)[0].upper()

        if key not in values:
            values[key] = value.strip()

    start = values.get("DTSTART")
    end = values.get("DTEND", start)

    if not start:
        return None

    def unescape(value):
        return (
            value
            .replace("\\n", "\n")
            .replace("\\N", "\n")
            .replace("\\,", ",")
            .replace("\\;", ";")
            .replace("\\\\", "\\")
        )

    def convert(value):

        if len(value) == 8 and value.isdigit():
            return (
                value[:4]
                + "-"
                + value[4:6]
                + "-"
                + value[6:8]
            )

        if value.endswith("Z"):
            return (
                value[:4]
                + "-"
                + value[4:6]
                + "-"
                + value[6:8]
                + "T"
                + value[9:11]
                + ":"
                + value[11:13]
                + ":"
                + value[13:15]
                + "+00:00"
            )

        if len(value) >= 15 and value[8] == "T":
            return (
                value[:4]
                + "-"
                + value[4:6]
                + "-"
                + value[6:8]
                + "T"
                + value[9:11]
                + ":"
                + value[11:13]
                + ":"
                + value[13:15]
            )

        return value

    return {
        "uid": item["guid"],
        "title": unescape(
            values.get("SUMMARY", item["title"])
        ),
        "description": unescape(
            values.get(
                "DESCRIPTION",
                item["description"],
            )
        ),
        "location": unescape(
            values.get("LOCATION", "")
        ),
        "url": values.get(
            "URL",
            item["link"],
        ),
        "start": convert(start),
        "end": convert(end),
        "category": category,
    }


def fetch_event(item, category):

    for attempt in range(3):

        try:

            ical = find_ical(
                item["title"]
            )

            if not ical:
                print(
                    "Kein iCal-Link:",
                    item["title"],
                )
                return None

            print(
                "  iCal gefunden:",
                item["title"],
            )

            return parse_ical(
                download(ical),
                item,
                category,
            )

        except Exception as exc:

            print(
                "Fehler:",
                item["title"],
                exc,
            )

            if attempt < 2:
                time.sleep(3)

    return None


def parse_datetime(value):

    if len(value) == 10:
        return dt.datetime.fromisoformat(
            value
        ).replace(
            tzinfo=TZ
        )

    value = value.replace(
        "Z",
        "+00:00",
    )

    result = dt.datetime.fromisoformat(
        value
    )

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=TZ
        )

    return result.astimezone(
        dt.timezone.utc
    )


def escape(value):

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def make_ics(events):

    stamp = dt.datetime.now(
        dt.timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
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

    for event in events:

        start = event["start"]
        end = event["end"]

        lines += [
            "BEGIN:VEVENT",
            "UID:" + escape(event["uid"]),
            "DTSTAMP:" + stamp,
        ]

        if len(start) == 10:

            start_date = dt.date.fromisoformat(start)

            if len(end) == 10:
                end_date = (
                    dt.date.fromisoformat(end)
                    + dt.timedelta(days=1)
                )
            else:
                end_date = (
                    start_date
                    + dt.timedelta(days=1)
                )

            lines += [
                "DTSTART;VALUE=DATE:"
                + start_date.strftime("%Y%m%d"),
                "DTEND;VALUE=DATE:"
                + end_date.strftime("%Y%m%d"),
            ]

        else:

            start_dt = parse_datetime(start)
            end_dt = parse_datetime(end)

            lines += [
                "DTSTART:"
                + start_dt.strftime(
                    "%Y%m%dT%H%M%SZ"
                ),
                "DTEND:"
                + end_dt.strftime(
                    "%Y%m%dT%H%M%SZ"
                ),
            ]

        lines += [
            "SUMMARY:" + escape(event["title"]),
            "DESCRIPTION:" + escape(
                event["description"]
                + "\n\nKategorie: "
                + event["category"]
                + "\n\n"
                + event["url"]
            ),
            "LOCATION:" + escape(
                event["location"]
            ),
            "URL:" + escape(
                event["url"]
            ),
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")

    return "\r\n".join(lines) + "\r\n"


def main():

    all_events = []
    feeds_ok = 0

    for category_id, category_name in CATEGORIES.items():

        print()
        print(
            "KATEGORIE:",
            category_name,
        )

        try:

            items = parse_rss(
                download(
                    rss_url(category_id)
                )
            )

            feeds_ok += 1

            print(
                "RSS-Termine:",
                len(items),
            )

            for item in items:

                event = fetch_event(
                    item,
                    category_name,
                )

                if event:
                    all_events.append(event)

        except Exception as exc:

            print(
                "RSS-Fehler:",
                category_name,
                exc,
            )

    unique = {}

    for event in all_events:

        key = (
            event["uid"],
            event["start"],
        )

        unique[key] = event

    events = sorted(
        unique.values(),
        key=lambda x: x["start"],
    )

    print()
    print(
        "================================"
    )
    print(
        "GEFUNDENE TERMINE:",
        len(events),
    )
    print(
        "================================"
    )

    if feeds_ok == 0:
        raise RuntimeError(
            "Kein RSS-Feed konnte geladen werden."
        )

    if not events:
        raise RuntimeError(
            "Keine Termine gefunden."
        )

    with open(
        "veranstaltungen.ics",
        "w",
        encoding="utf-8",
        newline="",
    ) as file:

        file.write(
            make_ics(events)
        )

    print(
        "Kalender erfolgreich erzeugt."
    )


if __name__ == "__main__":
    main()