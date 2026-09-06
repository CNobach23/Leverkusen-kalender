import gzip
import html
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# ============================================================
# LEVERKUSEN WEB-CAL KALENDER
# ============================================================

CATEGORIES = OrderedDict([
    ("Familie & Kinder", 42023),
    ("Feste & Brauchtum", 42081),
    ("Führungen & Touren", 42030),
    ("Festivals & Open-Airs", 42082),
    ("Karneval", 42078),
    ("Lesungen & Literatur", 42029),
    ("Märkte & Messen", 42032),
    ("Partys & Nachtleben", 42036),
    ("Spitzensport", 42031),
    ("Tipps", 42037),
    ("Warntage", 34821),
])

BASE_RSS = (
    "https://www.leverkusen.de/"
    "stadt-erleben/veranstaltungskalender/index.php"
)

OUTPUT_FILE = "veranstaltungen.ics"

TIMEZONE = ZoneInfo("Europe/Berlin")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.leverkusen.de/",
}


# ============================================================
# HTTP
# ============================================================

def fetch(url, timeout=30):

    request = urllib.request.Request(
        url,
        headers=HEADERS
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:

            data = response.read()

            content_type = response.headers.get(
                "Content-Type",
                ""
            )

            content_encoding = response.headers.get(
                "Content-Encoding",
                ""
            )

            final_url = response.geturl()
            status = response.status

            # ------------------------------------------------
            # gzip erkennen und dekomprimieren
            # ------------------------------------------------

            if (
                content_encoding.lower() == "gzip"
                or data[:2] == b"\x1f\x8b"
            ):

                data = gzip.decompress(data)

            # ------------------------------------------------
            # Zeichensatz bestimmen
            # ------------------------------------------------

            charset = "utf-8"

            match = re.search(
                r"charset=([^\s;]+)",
                content_type,
                re.IGNORECASE
            )

            if match:
                charset = match.group(1).strip('"')

            try:
                text = data.decode(
                    charset,
                    errors="replace"
                )
            except LookupError:
                text = data.decode(
                    "utf-8",
                    errors="replace"
                )

            return text, final_url

    except Exception as exc:

        print(
            f"FEHLER beim Abruf: {url}"
        )
        print(
            f"  {exc}"
        )

        return "", url


# ============================================================
# RSS
# ============================================================

def rss_url(category_id):

    params = [
        ("sp:categories[13495][0]", "-"),
        ("sp:categories[13495][1]", "__last__"),
        ("sp:categories[13459][0]", str(category_id)),
        ("sp:categories[13459][1]", "__last__"),
        ("sp:dateFrom[0]", "2026-09-05"),
        ("sp:dateTo[0]", ""),
        ("sp:fulltext[0]", ""),
        ("sp:out", "rss"),
        ("sp:cmp", "eventSearch-1-0-searchResult"),
        ("action", "submit"),
    ]

    return (
        BASE_RSS
        + "?"
        + urllib.parse.urlencode(params)
    )


def parse_rss(xml_text):

    root = ET.fromstring(xml_text)

    events = []

    for item in root.findall(".//item"):

        title = item.findtext(
            "title",
            default=""
        )

        link = item.findtext(
            "link",
            default=""
        )

        description = item.findtext(
            "description",
            default=""
        )

        title = html.unescape(
            title
        ).strip()

        link = html.unescape(
            link
        ).strip()

        description = html.unescape(
            description
        ).strip()

        if title and link:

            events.append({
                "title": title,
                "link": link,
                "description": description,
            })

    return events


# ============================================================
# TEXT / HTML
# ============================================================

def clean_text(value):

    value = html.unescape(
        value or ""
    )

    value = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        value,
        flags=re.I | re.S
    )

    value = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        value,
        flags=re.I | re.S
    )

    value = re.sub(
        r"<[^>]+>",
        " ",
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


# ============================================================
# DATUM
# ============================================================

MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
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


def parse_date(text):

    pattern = (
        r"(\d{1,2})\.\s*"
        r"(Januar|Februar|März|April|Mai|Juni|Juli|"
        r"August|September|Oktober|November|Dezember)"
        r"\s+(\d{4})"
    )

    match = re.search(
        pattern,
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    day = int(match.group(1))

    month_name = match.group(2).lower()

    month = MONTHS.get(
        month_name
    )

    year = int(
        match.group(3)
    )

    if not month:
        return None

    try:

        return datetime(
            year,
            month,
            day
        ).date()

    except ValueError:

        return None


# ============================================================
# UHRZEIT
# ============================================================

def parse_times(text):

    # --------------------------------------------------------
    # Bevorzugt:
    # "Zeit: 16:00 – 17:00 Uhr"
    # bzw.
    # "Beginn: 11:00 Uhr"
    # --------------------------------------------------------

    time_range_patterns = [

        r"Zeit:\s*(\d{1,2}):(\d{2})\s*[–\-]\s*(\d{1,2}):(\d{2})",

        r"Beginn:\s*(\d{1,2}):(\d{2})"
        r".{0,120}?"
        r"(?:Ende|bis)\s*:?\s*"
        r"(\d{1,2}):(\d{2})",

    ]

    for pattern in time_range_patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            return (
                (
                    int(match.group(1)),
                    int(match.group(2))
                ),
                (
                    int(match.group(3)),
                    int(match.group(4))
                )
            )

    # --------------------------------------------------------
    # Nur Beginn vorhanden
    # --------------------------------------------------------

    start_patterns = [

        r"Beginn:\s*(\d{1,2}):(\d{2})",

        r"(?:Beginn|Start|Los geht es)"
        r"\s*(?:um|:)?\s*"
        r"(\d{1,2}):(\d{2})\s*Uhr",

    ]

    for pattern in start_patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            return (
                (
                    int(match.group(1)),
                    int(match.group(2))
                ),
                None
            )

    # --------------------------------------------------------
    # Allgemeine Zeitangabe
    # --------------------------------------------------------

    general = re.search(
        r"(\d{1,2}):(\d{2})\s*Uhr",
        text
    )

    if general:

        return (
            (
                int(general.group(1)),
                int(general.group(2))
            ),
            None
        )

    return None, None


# ============================================================
# VERANSTALTUNGSINFORMATIONEN
# ============================================================

def extract_event_data(event):

    html_text, final_url = fetch(
        event["link"]
    )

    if not html_text:

        return None

    text = clean_text(
        html_text
    )

    # --------------------------------------------------------
    # Datum
    # --------------------------------------------------------

    event_date = parse_date(
        text
    )

    if not event_date:

        print(
            f"  KEIN DATUM: {event['title']}"
        )

        return None

    # --------------------------------------------------------
    # Zeit
    # --------------------------------------------------------

    start_time, end_time = parse_times(
        text
    )

    # --------------------------------------------------------
    # Start
    # --------------------------------------------------------

    if start_time:

        start = datetime(
            event_date.year,
            event_date.month,
            event_date.day,
            start_time[0],
            start_time[1],
            tzinfo=TIMEZONE
        )

    else:

        # Ganztägige Veranstaltung
        start = datetime(
            event_date.year,
            event_date.month,
            event_date.day,
            tzinfo=TIMEZONE
        )

    # --------------------------------------------------------
    # Ende
    # --------------------------------------------------------

    if end_time:

        end = datetime(
            event_date.year,
            event_date.month,
            event_date.day,
            end_time[0],
            end_time[1],
            tzinfo=TIMEZONE
        )

        # Falls eine Veranstaltung über Mitternacht geht
        if end <= start:
            end += timedelta(
                days=1
            )

    elif start_time:

        # Bei vorhandener Startzeit, aber fehlender Endzeit:
        # 2 Stunden Standarddauer.
        end = start + timedelta(
            hours=2
        )

    else:

        # Ganztägig
        end = start + timedelta(
            days=1
        )

    return {
        "title": event["title"],
        "url": final_url,
        "date": event_date,
        "start": start,
        "end": end,
        "description": text,
    }


# ============================================================
# ICS
# ============================================================

def ics_escape(value):

    value = str(value)

    value = value.replace(
        "\\",
        "\\\\"
    )

    value = value.replace(
        ";",
        "\\;"
    )

    value = value.replace(
        ",",
        "\\,"
    )

    value = value.replace(
        "\r",
        ""
    )

    value = value.replace(
        "\n",
        "\\n"
    )

    return value


def fold_ics_line(line):

    # RFC 5545:
    # Zeilen dürfen maximal 75 Oktette lang sein.
    # Für UTF-8 ist eine einfache Zeichenbegrenzung
    # hier ausreichend praktisch.
    result = []

    while len(line) > 73:

        result.append(
            line[:73]
        )

        line = " " + line[73:]

    result.append(line)

    return "\r\n".join(result)


def format_dt(dt):

    return dt.strftime(
        "%Y%m%dT%H%M%S"
    )


def make_uid(event):

    encoded = urllib.parse.quote(
        event["url"],
        safe=""
    )

    return (
        encoded[:180]
        + "@leverkusen-kalender"
    )


def make_vevent(event, categories):

    lines = []

    lines.append(
        "BEGIN:VEVENT"
    )

    lines.append(
        f"UID:{make_uid(event)}"
    )

    lines.append(
        f"DTSTAMP:{datetime.now(tz=TIMEZONE).strftime('%Y%m%dT%H%M%SZ')}"
    )

    # --------------------------------------------------------
    # DATE / TIME
    # --------------------------------------------------------

    lines.append(
        f"DTSTART;TZID=Europe/Berlin:"
        f"{format_dt(event['start'])}"
    )

    lines.append(
        f"DTEND;TZID=Europe/Berlin:"
        f"{format_dt(event['end'])}"
    )

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    lines.append(
        "SUMMARY:"
        + ics_escape(
            event["title"]
        )
    )

    # --------------------------------------------------------
    # KATEGORIEN
    # --------------------------------------------------------

    unique_categories = []

    for category in categories:

        if category not in unique_categories:
            unique_categories.append(
                category
            )

    if unique_categories:

        lines.append(
            "CATEGORIES:"
            + ",".join(
                ics_escape(c)
                for c in unique_categories
            )
        )

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    lines.append(
        "URL:"
        + event["url"]
    )

    # --------------------------------------------------------
    # BESCHREIBUNG
    # --------------------------------------------------------

    description = event.get(
        "description",
        ""
    )

    # Nicht die komplette Website in den Kalender schreiben.
    # Nur einen sinnvollen Ausschnitt.
    if description:

        # Hauptnavigation und typische Seitenteile entfernen,
        # soweit möglich.
        description = re.sub(
            r"\s+",
            " ",
            description
        ).strip()

        if len(description) > 1500:

            description = (
                description[:1500]
                + "…"
            )

        lines.append(
            "DESCRIPTION:"
            + ics_escape(
                description
            )
        )

    lines.append(
        "END:VEVENT"
    )

    return "\r\n".join(
        fold_ics_line(line)
        for line in lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 80
    )

    print(
        "LEVERKUSEN WEB-CAL KALENDER"
    )

    print(
        "=" * 80
    )

    all_events = OrderedDict()

    category_counts = OrderedDict()

    # --------------------------------------------------------
    # RSS laden
    # --------------------------------------------------------

    for category, category_id in CATEGORIES.items():

        print()
        print(
            f"Kategorie: {category}"
        )

        url = rss_url(
            category_id
        )

        xml_text, _ = fetch(
            url
        )

        if not xml_text:

            print(
                "  RSS konnte nicht geladen werden."
            )

            category_counts[category] = 0

            continue

        try:

            events = parse_rss(
                xml_text
            )

        except Exception as exc:

            print(
                f"  RSS-Fehler: {exc}"
            )

            category_counts[category] = 0

            continue

        print(
            f"  RSS-Veranstaltungen: {len(events)}"
        )

        category_counts[category] = len(
            events
        )

        for event in events:

            key = event["link"]

            if key not in all_events:

                all_events[key] = {
                    "title": event["title"],
                    "link": event["link"],
                    "description": event["description"],
                    "categories": [category],
                }

            else:

                if category not in all_events[key]["categories"]:

                    all_events[key]["categories"].append(
                        category
                    )

    print()
    print(
        "=" * 80
    )

    print(
        f"EINDEUTIGE VERANSTALTUNGEN: "
        f"{len(all_events)}"
    )

    print(
        "=" * 80
    )

    # --------------------------------------------------------
    # Veranstaltungsseiten auswerten
    # --------------------------------------------------------

    processed = []

    failed = []

    for number, event in enumerate(
        all_events.values(),
        1
    ):

        print(
            f"[{number}/{len(all_events)}] "
            f"{event['title']}"
        )

        data = extract_event_data(
            event
        )

        if data:

            data["categories"] = event[
                "categories"
            ]

            processed.append(
                data
            )

        else:

            failed.append(
                event
            )

    # --------------------------------------------------------
    # ICS schreiben
    # --------------------------------------------------------

    print()
    print(
        "=" * 80
    )

    print(
        "SCHREIBE KALENDER"
    )

    print(
        "=" * 80
    )

    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CNobach23//Leverkusen Kalender//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Leverkusen Veranstaltungen",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]

    for event in processed:

        ics_lines.append(
            make_vevent(
                event,
                event["categories"]
            )
        )

    ics_lines.append(
        "END:VCALENDAR"
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline=""
    ) as file:

        file.write(
            "\r\n".join(
                ics_lines
            )
            + "\r\n"
        )

    # --------------------------------------------------------
    # Statistik
    # --------------------------------------------------------

    print()
    print(
        "=" * 80
    )

    print(
        "FERTIG!"
    )

    print(
        "=" * 80
    )

    print(
        f"Veranstaltungen geschrieben: "
        f"{len(processed)}"
    )

    print(
        f"Nicht verarbeitet: "
        f"{len(failed)}"
    )

    print()

    for category in CATEGORIES:

        count = sum(
            1
            for event in processed
            if category in event["categories"]
        )

        print(
            f"{category}: {count}"
        )

    if failed:

        print()
        print(
            "NICHT VERARBEITETE VERANSTALTUNGEN:"
        )

        for event in failed[:50]:

            print(
                f"  - {event['title']}"
            )

    print()
    print(
        f"Datei: {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()