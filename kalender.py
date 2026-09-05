import datetime as dt
import html
import re
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

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def local_name(tag):
    return tag.split("}")[-1]


def text_of(element, name):
    for child in element:
        if local_name(child.tag) == name:
            return "".join(child.itertext()).strip()

    return ""


def parse_rss(data):
    root = ET.fromstring(data)
    events = []

    for item in root.iter():
        if local_name(item.tag) != "item":
            continue

        title = text_of(item, "title")
        link = text_of(item, "link")
        guid = text_of(item, "guid") or link
        description = text_of(item, "description")

        if title and link:
            events.append(
                {
                    "title": html.unescape(title),
                    "link": link,
                    "guid": guid,
                    "description": html.unescape(description),
                }
            )

    return events


def fetch_event(item, category):
    import time

    # Leverkusen stellt für einzelne Termine
    # eine eigene iCalendar-Datei bereit.
    ical_url = item["link"].rstrip("/") + "/ical/"

    for attempt in range(3):
        try:
            # Kleine Pause verhindert zu viele parallele
            # Anfragen an den Leverkusen-Server.
            time.sleep(0.7)

            data = download(ical_url)

            text = data.decode(
                "utf-8",
                errors="replace",
            )

            # Gefaltete ICS-Zeilen wieder zusammenführen.
            text = text.replace(
                "\r\n ",
                "",
            ).replace(
                "\n ",
                "",
            )

            values = {}

            for line in text.splitlines():
                if ":" not in line:
                    continue

                key, value = line.split(
                    ":",
                    1,
                )

                # Parameter wie ;TZID=Europe/Berlin entfernen.
                key = key.split(
                    ";",
                    1,
                )[0].upper()

                if key not in values:
                    values[key] = value.strip()

            start = values.get("DTSTART")
            end = values.get("DTEND")

            if not start:
                print(
                    "Keine DTSTART in ICS:",
                    ical_url,
                )
                return None

            if not end:
                end = start

            def unescape(value):
                return (
                    value
                    .replace("\\n", "\n")
                    .replace("\\N", "\n")
                    .replace("\\,", ",")
                    .replace("\\;", ";")
                    .replace("\\\\", "\\")
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

                # Ganztägiger Termin:
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

                # UTC:
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

                # Lokale Zeit:
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

        except Exception as exc:
            print(
                "ICS-Fehler:",
                ical_url,
                exc,
            )

            if attempt < 2:
                time.sleep(3)
            else:
                return None


def parse_datetime(value):
    value = str(value).strip()

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
        d = dt.datetime.fromisoformat(value)
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

        # Prüfen, ob es sich um einen
        # ganztägigen Termin handelt.
        all_day = (
            len(start_raw) == 10
            and re.fullmatch(
                r"\d{4}-\d{2}-\d{2}",
                start_raw,
            )
        )

        if all_day:

            start_date = dt.date.fromisoformat(
                start_raw
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

        # Uhrzeit-Termine
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
    ) in CATEGORIES.items():

        url = rss_url(
            category_id
        )

        print(
            "Lade:",
            category_name,
        )

        try:
            data = download(
                url
            )

            items = parse_rss(
                data
            )

            successful_feeds += 1

            print(
                "  RSS-Termine:",
                len(items),
            )

            for item in items:

                event = fetch_event(
                    item,
                    category_name,
                )

                if event:
                    all_events.append(
                        event
                    )

        except Exception as exc:

            print(
                "RSS-Fehler:",
                category_name,
                exc,
            )

    # Doppelte Termine entfernen.
    unique = {}

    for event in all_events:

        key = (
            event["uid"],
            event["start"],
        )

        unique[key] = event

    events = list(
        unique.values()
    )

    # Nach Datum/Uhrzeit sortieren.
    events.sort(
        key=lambda x: x["start"]
    )

    if successful_feeds == 0:
        raise RuntimeError(
            "Kein Leverkusen-RSS-Feed "
            "konnte geladen werden."
        )

    if not events:
        raise RuntimeError(
            "Die Feeds wurden geladen, "
            "aber es wurden keine Termine "
            "gefunden."
        )

    # ICS-Datei schreiben.
    with open(
        "veranstaltungen.ics",
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        f.write(
            make_ics(events)
        )

    print(
        "Fertig:",
        len(events),
        "Termine",
    )

    print(
        "Datei: veranstaltungen.ics"
    )


if __name__ == "__main__":
    main()