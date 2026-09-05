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


def make_request(url):
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

    return (
        BASE_URL
        + "?"
        + urllib.parse.urlencode(params)
    )


def calendar_search_url(title):
    params = [
        ("form", "eventSearch-1.form"),
        ("sp:fulltext[]", title),
        ("sp:categories[13495][]", "-"),
        ("sp:categories[13495][]", "__last__"),
        ("sp:dateFrom[]", dt.datetime.now(TZ).date().isoformat()),
        ("sp:dateTo[]", ""),
        ("sp:cmp", "eventSearch-1-0-searchResult"),
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

            text = "".join(
                child.itertext()
            ).strip()

            values[name] = html.unescape(text)

        title = values.get("title", "")
        link = values.get("link", "")
        guid = values.get("guid", "") or link
        description = values.get(
            "description",
            "",
        )

        if title and link:

            events.append(
                {
                    "title": title,
                    "link": link,
                    "guid": guid,
                    "description": description,
                }
            )

    return events


def find_ical_link(title):
    """
    Sucht auf der offiziellen Leverkusener
    Veranstaltungskalender-Seite nach dem
    'Termin speichern'-Link.

    Dieser Link enthält:
    sp:out=leverkusen-lapwing.iCalFromParameters
    """

    search_url = calendar_search_url(title)

    for attempt in range(3):

        try:

            data = make_request(
                search_url
            )

            page = data.decode(
                "utf-8",
                errors="replace",
            )

            # HTML-Entities zurückwandeln.
            page = html.unescape(page)

            # Alle Links untersuchen.
            links = re.findall(
                r'href\s*=\s*["\']([^"\']+)["\']',
                page,
                flags=re.I,
            )

            for link in links:

                decoded = html.unescape(
                    link
                )

                if (
                    "iCalFromParameters"
                    in decoded
                    or
                    "iCalFrom" in decoded
                    or
                    "iCal" in decoded
                ):

                    return urllib.parse.urljoin(
                        BASE_URL,
                        decoded,
                    )

            # Zweiter Versuch:
            # direkt nach dem charakteristischen
            # iCal-Parameter im HTML suchen.
            match = re.search(
                r'([^"\']*iCalFromParameters[^"\']*)',
                page,
                flags=re.I,
            )

            if match:

                link = match.group(1)

                return urllib.parse.urljoin(
                    BASE_URL,
                    html.unescape(link),
                )

            print(
                "Kein iCal-Link gefunden:",
                title,
            )

            return None

        except Exception as exc:

            print(
                "Fehler bei Kalender-Suche:",
                title,
                exc,
            )

            if attempt < 2:
                time.sleep(3)

    return None


def parse_ics(data, item, category):

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
    )

    if not start:

        print(
            "Keine DTSTART:",
            item["title"],
        )

        return None

    if not end:
        end = start

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
                ",",
            )
            .replace(
                "\\;",
                ";",
            )
            .replace(
                "\\\\",
                "\\",
            )
        )

    title = unescape(
        values.get(
            "SUMMARY",
            item["title"],
        )
    )

    description = unescape(
        values.get(
            "DESCRIPTION",
            item["description"],
        )
    )

    location = unescape(
        values.get(
            "LOCATION",
            "",
        )
    )

    url = values.get(
        "URL",
        item["link"],
    )

    def convert_ics_date(value):

        value = value.strip()

        # YYYYMMDD
        if (
            len(value) == 8
            and value.isdigit()
        ):

            return (
                value[0:4]
                + "-"
                + value[4:6]
                + "-"
                + value[6:8]
            )

        # YYYYMMDDTHHMMSSZ
        if value.endswith("Z"):

            return (
                value[0:4]
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

        # YYYYMMDDTHHMMSS
        if (
            len(value) >= 15
            and value[8] == "T"
        ):

            return (
                value[0:4]
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
        "title": title,
        "description": description,
        "url": url,
        "start": convert_ics_date(start),
        "end": convert_ics_date(end),
        "category": category,
        "location": location,
    }


def fetch_event(item, category):

    # Die eigentliche ICS-Datei wird jetzt
    # über die offizielle Kalenderseite gesucht.
    ical_url = find_ical_link(
        item["title"]
    )

    if not ical_url:

        print(
            "Kein iCal-Termin gefunden:",
            item["title"],
        )

        return None

    print(
        "  iCal gefunden:",
        item["title"],
    )

    for attempt in range(3):

        try:

            time.sleep(0.5)

            data = make_request(
                ical_url
            )

            event = parse_ics(
                data,
                item,
                category,
            )

            return event

        except Exception as exc:

            print(
                "ICS-Fehler:",
                item["title"],
                exc,
            )

            if attempt < 2:
                time.sleep(3)

    return None


def parse_datetime(value):

    value = str(
        value
    ).strip()

    # Ganztägiger Termin
    if (
        len(value) == 10
        and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}",
            value,
        )
    ):

        return dt.datetime.fromisoformat(
            value
        ).replace(
            tzinfo=TZ
        )

    value = value.replace(
        "Z",
        "+00:00",
    )

    try:

        d = dt.datetime.fromisoformat(
            value
        )

    except ValueError:

        d = dt.datetime.fromisoformat(
            value[:19]
        )

    if d.tzinfo is None:

        d = d.replace(
            tzinfo=TZ
        )

    return d.astimezone(
        dt.timezone.utc
    )


def ics_escape(value):

    return (
        str(value)
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


def make_ics(events):

    now = dt.datetime.now(
        dt.timezone.utc
    )

    stamp = now.strftime(
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

        start_raw = str(
            event["start"]
        ).strip()

        end_raw = str(
            event["end"]
        ).strip()

        all_day = (
            len(start_raw) == 10
            and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}",
                start_raw,
            )
        )

        if all_day:

            start_date = (
                dt.date.fromisoformat(
                    start_raw
                )
            )

            if (
                len(end_raw) == 10
                and re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}",
                    end_raw,
                )
            ):

                end_date = (
                    dt.date.fromisoformat(
                        end_raw
                    )
                    + dt.timedelta(
                        days=1
                    )
                )

            else:

                end_date = (
                    start_date
                    + dt.timedelta(
                        days=1
                    )
                )

            lines.extend(
                [
                    "BEGIN:VEVENT",

                    "UID:"
                    + ics_escape(
                        event["uid"]
                    ),

                    "DTSTAMP:"
                    + stamp,

                    "DTSTART;VALUE=DATE:"
                    + start_date.strftime(
                        "%Y%m%d"
                    ),

                    "DTEND;VALUE=DATE:"
                    + end_date.strftime(
                        "%Y%m%d"
                    ),

                    "SUMMARY:"
                    + ics_escape(
                        event["title"]
                    ),

                    "DESCRIPTION:"
                    + ics_escape(
                        event["description"]
                        + "\n\nKategorie: "
                        + event["category"]
                        + "\n\n"
                        + event["url"]
                    ),

                    "LOCATION:"
                    + ics_escape(
                        event["location"]
                    ),

                    "URL:"
                    + ics_escape(
                        event["url"]
                    ),

                    "END:VEVENT",
                ]
            )

            continue

        start = parse_datetime(
            start_raw
        )

        end = parse_datetime(
            end_raw
        )

        lines.extend(
            [
                "BEGIN:VEVENT",

                "UID:"
                + ics_escape(
                    event["uid"]
                ),

                "DTSTAMP:"
                + stamp,

                "DTSTART:"
                + start.strftime(
                    "%Y%m%dT%H%M%SZ"
                ),

                "DTEND:"
                + end.strftime(
                    "%Y%m%dT%H%M%SZ"
                ),

                "SUMMARY:"
                + ics_escape(
                    event["title"]
                ),

                "DESCRIPTION:"
                + ics_escape(
                    event["description"]
                    + "\n\nKategorie: "
                    + event["category"]
                    + "\n\n"
                    + event["url"]
                ),

                "LOCATION:"
                + ics_escape(
                    event["location"]
                ),

                "URL:"
                + ics_escape(
                    event["url"]
                ),

                "END:VEVENT",
            ]
        )

    lines.append(
        "END:VCALENDAR"
    )

    return (
        "\r\n".join(lines)
        + "\r\n"
    )


def main():

    all_events = []

    successful_feeds = 0

    for (
        category_id,
        category_name,
    ) in