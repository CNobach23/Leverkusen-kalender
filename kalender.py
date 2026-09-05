import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

BASE = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
OUT = "veranstaltungen.ics"

TZ = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (LeverkusenKalender/9.0)"

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


def get(url, timeout=20, tries=3):
    last_error = None

    for attempt in range(tries):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Connection": "close",
                    "Accept": "*/*",
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout,
            ) as response:

                encoding = (
                    response.headers.get_content_charset()
                    or "utf-8"
                )

                return response.read().decode(
                    encoding,
                    errors="replace",
                )

        except Exception as exc:
            last_error = exc

            if attempt + 1 < tries:
                time.sleep(1)

    raise last_error


def rss_url(category_id):
    params = [
        ("sp:categories[13495][0]", "-"),
        ("sp:categories[13495][1]", "__last__"),
        ("sp:categories[13459][0]", category_id),
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
        + "index.php?"
        + urllib.parse.urlencode(params)
    )


def official_ical_url(category_id):
    params = [
        ("sp:categories[13495][0]", "-"),
        ("sp:categories[13495][1]", "__last__"),
        ("sp:categories[13459][0]", category_id),
        ("sp:categories[13459][1]", "__last__"),
        (
            "sp:dateFrom[0]",
            date.today().isoformat(),
        ),
        ("sp:dateTo[0]", ""),
        ("sp:fulltext[0]", ""),
        (
            "sp:out",
            "leverkusen-lapwing.iCalFromParameters",
        ),
        (
            "sp:cmp",
            "eventSearch-1-0-searchResult",
        ),
        ("action", "submit"),
    ]

    return (
        BASE
        + "index.php?"
        + urllib.parse.urlencode(params)
    )


def parse_rss(text, category):
    root = ET.fromstring(text)
    events = []

    for item in root.findall(".//item"):

        values = {}

        for child in item:
            key = child.tag.split("}")[-1]

            values[key] = html.unescape(
                "".join(
                    child.itertext()
                ).strip()
            )

        title = values.get("title", "")
        link = values.get("link", "")

        if title and link:
            events.append(
                {
                    "title": title,
                    "link": link,
                    "description": values.get(
                        "description",
                        "",
                    ),
                    "category": category,
                }
            )

    return events


def unfold_ical(text):
    lines = (
        text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )

    result = []

    for line in lines:
        if (
            line.startswith((" ", "\t"))
            and result
        ):
            result[-1] += line[1:]
        else:
            result.append(line)

    return result


def parse_ical_events(text):
    lines = unfold_ical(text)

    events = []
    current = None

    for line in lines:

        if line == "BEGIN:VEVENT":
            current = {}

        elif line == "END:VEVENT":
            if current:
                events.append(current)

            current = None

        elif current is not None and ":" in line:

            key, value = line.split(
                ":",
                1,
            )

            key = key.split(
                ";",
                1,
            )[0].upper()

            current[key] = value

    return events


def ical_datetime(value):
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


def clean(value):
    if not value:
        return ""

    return (
        html.unescape(value)
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def extract_event(event, category):
    if "DTSTART" not in event:
        return None

    try:
        start, all_day = ical_datetime(
            event["DTSTART"]
        )

        if "DTEND" in event:
            end, _ = ical_datetime(
                event["DTEND"]
            )
        else:
            end = start

        if (
            not all_day
            and isinstance(start, datetime)
            and isinstance(end, datetime)
            and end <= start
        ):
            # Falls die Quelle ausnahmsweise
            # keine sinnvolle Endzeit liefert.
            from datetime import timedelta

            end = start + timedelta(
                hours=1
            )

        uid = clean(
            event.get(
                "UID",
                "",
            )
        )

        summary = clean(
            event.get(
                "SUMMARY",
                "",
            )
        )

        description = clean(
            event.get(
                "DESCRIPTION",
                "",
            )
        )

        location = clean(
            event.get(
                "LOCATION",
                "",
            )
        )

        url = clean(
            event.get(
                "URL",
                "",
            )
        )

        if not uid:
            uid = (
                url
                or summary
                + "-"
                + str(start)
            )

        return {
            "uid": uid,
            "start": start,
            "end": end,
            "all_day": all_day,
            "summary": summary,
            "description": description,
            "location": location,
            "url": url,
            "category": category,
        }

    except Exception:
        return None


def fetch_category_ical(category_id, category):
    """
    Holt den offiziellen iCalendar-Feed der
    jeweiligen Leverkusener Kategorie.
    """

    url = official_ical_url(
        category_id
    )

    try:
        text = get(
            url,
            timeout=25,
            tries=3,
        )

        if (
            "BEGIN:VCALENDAR" not in text
            or "BEGIN:VEVENT" not in text
        ):
            print(
                "iCal nicht erkannt:",
                category,
            )
            return []

        raw_events = parse_ical_events(
            text
        )

        events = []

        for raw in raw_events:
            event = extract_event(
                raw,
                category,
            )

            if event:
                events.append(event)

        print(
            "iCal:",
            category,
            len(events),
        )

        return events

    except Exception as exc:
        print(
            "iCal FEHLER:",
            category,
            "-",
            exc,
        )

        return []


def fallback_individual_link_from_page(
    page,
    title,
):
    """
    Fallback:
    Sucht auf einer offiziellen HTML-Seite
    nach einem passenden 'Termin speichern'-Link.
    """

    page = html.unescape(page)

    pattern = re.compile(
        r'href\s*=\s*["\']'
        r'([^"\']+)'
        r'["\'][^>]*>'
        r'\s*Termin speichern',
        re.I,
    )

    links = pattern.findall(
        page
    )

    if links:
        return urllib.parse.urljoin(
            BASE,
            links[0],
        )

    return None


def parse_rss_category(
    category_id,
    category,
):
    """
    RSS bleibt als Kontrollquelle erhalten.
    Die eigentlichen Termine kommen aber
    möglichst direkt aus dem offiziellen iCal.
    """

    try:
        text = get(
            rss_url(category_id),
            timeout=20,
            tries=3,
        )

        events = parse_rss(
            text,
            category,
        )

        print(
            "RSS:",
            category,
            len(events),
        )

        return events

    except Exception as exc:
        print(
            "RSS FEHLER:",
            category,
            exc,
        )

        return []


def escape_ics(value):
    value = str(
        value or ""
    )

    return (
        value
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def sort_key(event):
    value = event["start"]

    if (
        isinstance(value, date)
        and not isinstance(
            value,
            datetime,
        )
    ):
        value = datetime.combine(
            value,
            datetime.min.time(),
            tzinfo=TZ,
        )

    elif value.tzinfo is None:
        value = value.replace(
            tzinfo=TZ
        )

    return value.astimezone(
        timezone.utc
    ).timestamp()


def make_ics(events):
    stamp = datetime.now(
        timezone.utc
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

    for event in sorted(
        events,
        key=sort_key,
    ):

        lines.append(
            "BEGIN:VEVENT"
        )

        lines.append(
            "UID:"
            + escape_ics(
                event["uid"]
            )
        )

        lines.append(
            "DTSTAMP:"
            + stamp
        )

        if event["all_day"]:

            lines.append(
                "DTSTART;VALUE=DATE:"
                + event["start"].strftime(
                    "%Y%m%d"
                )
            )

            # DTEND bei Ganztagsterminen
            # ist exklusiv.
            from datetime import timedelta

            end_date = (
                event["end"]
                + timedelta(days=1)
            )

            lines.append(
                "DTEND;VALUE=DATE:"
                + end_date.strftime(
                    "%Y%m%d"
                )
            )

        else:

            start = event["start"]

            end = event["end"]

            if start.tzinfo is None:
                start = start.replace(
                    tzinfo=TZ
                )

            if end.tzinfo is None:
                end = end.replace(
                    tzinfo=TZ
                )

            lines.append(
                "DTSTART:"
                + start.astimezone(
                    timezone.utc
                ).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
            )

            lines.append(
                "DTEND:"
                + end.astimezone(
                    timezone.utc
                ).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
            )

        lines.append(
            "SUMMARY:"
            + escape_ics(
                event["summary"]
            )
        )

        description = event[
            "description"
        ]

        if event["category"]:
            if description:
                description += "\n\n"

            description += (
                "Kategorie: "
                + event["category"]
            )

        if event["url"]:
            if description:
                description += "\n\n"

            description += event[
                "url"
            ]

        lines.append(
            "DESCRIPTION:"
            + escape_ics(
                description
            )
        )

        if event["location"]:
            lines.append(
                "LOCATION:"
                + escape_ics(
                    event["location"]
                )
            )

        if event["url"]:
            lines.append(
                "URL:"
                + escape_ics(
                    event["url"]
                )
            )

        if event["category"]:
            lines.append(
                "CATEGORIES:"
                + escape_ics(
                    event["category"]
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

    print(
        "======================================"
    )
    print(
        "Leverkusen Kalender – Start"
    )
    print(
        "======================================"
    )

    all_events = []

    rss_counts = {}

    # -------------------------------------------------
    # 1. RSS-Kategorien prüfen
    # -------------------------------------------------

    print(
        "\nRSS-Kategorien:"
    )

    for category_id, category in CATEGORIES:

        events = parse_rss_category(
            category_id,
            category,
        )

        rss_counts[
            category
        ] = len(events)

    print(
        "\n======================================"
    )
    print(
        "Offizielle iCalendar-Feeds:"
    )
    print(
        "======================================"
    )

    # -------------------------------------------------
    # 2. Offizielle iCalendar-Feeds
    # -------------------------------------------------

    with ThreadPoolExecutor(
        max_workers=4
    ) as executor:

        jobs = {
            executor.submit(
                fetch_category_ical,
                category_id,
                category,
            ): category
            for category_id, category
            in CATEGORIES
        }

        for job in as_completed(
            jobs
        ):
            category = jobs[job]

            try:
                events = job.result()

                all_events.extend(
                    events
                )

            except Exception as exc:
                print(
                    "FEHLER:",
                    category,
                    exc,
                )

    # -------------------------------------------------
    # 3. Doppelte Events entfernen
    # -------------------------------------------------

    unique = {}

    for event in all_events:

        key = (
            event["uid"],
            str(event["start"]),
            str(event["end"]),
            event["category"],
        )

        unique[key] = event

    events = list(
        unique.values()
    )

    # -------------------------------------------------
    # 4. Statistik
    # -------------------------------------------------

    counts = {}

    for event in events:

        category = event[
            "category"
        ]

        counts[
            category
        ] = counts.get(
            category,
            0,
        ) + 1

    # -------------------------------------------------
    # 5. ICS schreiben
    # -------------------------------------------------

    with open(
        OUT,
        "w",
        encoding="utf-8",
        newline="",
    ) as file:

        file.write(
            make_ics(
                events
            )
        )

    # -------------------------------------------------
    # 6. Ergebnis
    # -------------------------------------------------

    print(
        "\n======================================"
    )

    print(
        "FERTIG!"
    )

    print(
        "Veranstaltungen geschrieben:",
        len(events),
    )

    print(
        "======================================"
    )

    for _, category in CATEGORIES:

        print(
            category
            + ":",
            counts.get(
                category,
                0,
            ),
            "(RSS:",
            rss_counts.get(
                category,
                0,
            ),
            ")",
        )

    print(
        "======================================"
    )


if __name__ == "__main__":
    main()