import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
ICAL_BASE = "https://www.leverkusen.de/"
OUT = "veranstaltungen.ics"

TZ = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (LeverkusenKalender/10.0)"

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

WEEKDAYS = (
    "Montag|Dienstag|Mittwoch|Donnerstag|"
    "Freitag|Samstag|Sonntag"
)


def get(url, timeout=20, tries=3):
    last = None

    for attempt in range(tries):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Connection": "close",
                    "Accept": (
                        "text/html,"
                        "application/xhtml+xml,"
                        "text/calendar,*/*"
                    ),
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
            last = exc

            if attempt + 1 < tries:
                time.sleep(1)

    raise last


def rss_url(category_id):
    params = [
        (
            "sp:categories[13495][0]",
            "-",
        ),
        (
            "sp:categories[13495][1]",
            "__last__",
        ),
        (
            "sp:categories[13459][0]",
            category_id,
        ),
        (
            "sp:categories[13459][1]",
            "__last__",
        ),
        (
            "sp:dateFrom[0]",
            datetime.now(TZ).date().isoformat(),
        ),
        (
            "sp:dateTo[0]",
            "",
        ),
        (
            "sp:fulltext[0]",
            "",
        ),
        (
            "sp:out",
            "rss",
        ),
        (
            "sp:cmp",
            "eventSearch-1-0-searchResult",
        ),
        (
            "action",
            "submit",
        ),
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

        title = values.get(
            "title",
            "",
        )

        link = values.get(
            "link",
            "",
        )

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


def clean_text(text):
    text = html.unescape(
        text or ""
    )

    text = re.sub(
        r"(?is)<script.*?</script>",
        " ",
        text,
    )

    text = re.sub(
        r"(?is)<style.*?</style>",
        " ",
        text,
    )

    text = re.sub(
        r"(?is)<[^>]+>",
        " ",
        text,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def parse_url_datetime(url):
    """
    Erkennt konkrete Lust-auf-Leverkusen-Instanzen
    wie:

    /2026-09-05-14-15/
    """

    path = urllib.parse.urlparse(
        url
    ).path

    match = re.search(
        r"/(20\d{2})-(\d{2})-(\d{2})"
        r"(?:-(\d{2})-(\d{2}))?/?$",
        path,
    )

    if not match:
        return None, False

    year = int(
        match.group(1)
    )

    month = int(
        match.group(2)
    )

    day = int(
        match.group(3)
    )

    if match.group(4):
        hour = int(
            match.group(4)
        )

        minute = int(
            match.group(5)
        )

        return (
            datetime(
                year,
                month,
                day,
                hour,
                minute,
                tzinfo=TZ,
            ),
            False,
        )

    return (
        date(
            year,
            month,
            day,
        ),
        True,
    )


def parse_page_datetime(
    page,
    title,
    url,
):
    """
    Liest Datum und Uhrzeit direkt aus
    der offiziellen Veranstaltungsseite.

    Besonders wichtig ist der Bereich
    'Termin-Details'.
    """

    url_value, url_all_day = (
        parse_url_datetime(url)
    )

    text = clean_text(page)

    date_patterns = [
        (
            rf"(?:{WEEKDAYS}),?\s+"
            rf"(\d{{1,2}})\.\s+"
            rf"(Januar|Februar|März|Maerz|"
            rf"April|Mai|Juni|Juli|August|"
            rf"September|Oktober|November|Dezember)"
            rf"\s+(\d{{4}})"
        ),
        (
            rf"(\d{{1,2}})\.\s*[–-]\s*"
            rf"(\d{{1,2}})\.\s+"
            rf"(Januar|Februar|März|Maerz|"
            rf"April|Mai|Juni|Juli|August|"
            rf"September|Oktober|November|Dezember)"
            rf"\s+(\d{{4}})"
        ),
        (
            rf"(\d{{1,2}})\.\s+"
            rf"(Januar|Februar|März|Maerz|"
            rf"April|Mai|Juni|Juli|August|"
            rf"September|Oktober|November|Dezember)"
            rf"\s+(\d{{4}})"
        ),
    ]

    dates = None

    for pattern in date_patterns:

        match = re.search(
            pattern,
            text,
            re.I,
        )

        if not match:
            continue

        groups = match.groups()

        try:

            if len(groups) == 3:

                day, month_name, year = groups

                d = date(
                    int(year),
                    MONTHS[
                        month_name.lower()
                    ],
                    int(day),
                )

                dates = (
                    d,
                    d,
                )

            else:

                day1, day2, month_name, year = groups

                dates = (
                    date(
                        int(year),
                        MONTHS[
                            month_name.lower()
                        ],
                        int(day1),
                    ),
                    date(
                        int(year),
                        MONTHS[
                            month_name.lower()
                        ],
                        int(day2),
                    ),
                )

            break

        except Exception:
            continue

    # Wenn die URL bereits ein konkretes Datum
    # enthält, dieses bevorzugen.
    if (
        url_value is not None
        and not url_all_day
    ):
        start = url_value

    elif dates:
        start = None

    elif (
        url_value is not None
        and url_all_day
    ):
        return (
            url_value,
            url_value,
            True,
        )

    else:
        return None

    time_patterns = [
        (
            r"Zeit:\s*"
            r"(\d{1,2}:\d{2})"
            r"\s*[–-]\s*"
            r"(\d{1,2}:\d{2})"
        ),
        (
            r"Zeit:\s*"
            r"(\d{1,2}:\d{2})"
        ),
        (
            r"Beginn:\s*"
            r"(\d{1,2}:\d{2})"
            r"\s*Uhr"
        ),
    ]

    time_match = None

    for pattern in time_patterns:

        time_match = re.search(
            pattern,
            text,
            re.I,
        )

        if time_match:
            break

    # Konkretes Datum/Uhrzeit aus URL
    if start is not None:

        if time_match:

            hour, minute = map(
                int,
                time_match.group(
                    1
                ).split(":"),
            )

            start = start.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )

            if (
                time_match.lastindex
                and time_match.lastindex >= 2
                and time_match.group(2)
            ):

                end_hour, end_minute = map(
                    int,
                    time_match.group(
                        2
                    ).split(":"),
                )

                end = start.replace(
                    hour=end_hour,
                    minute=end_minute,
                )

                if end <= start:
                    end += timedelta(
                        days=1
                    )

                return (
                    start,
                    end,
                    False,
                )

            return (
                start,
                start + timedelta(
                    hours=1
                ),
                False,
            )

        return (
            start,
            start + timedelta(
                hours=1
            ),
            False,
        )

    # Datum aus der Seite
    start_date, end_date = dates

    if not time_match:

        return (
            start_date,
            end_date,
            True,
        )

    hour, minute = map(
        int,
        time_match.group(
            1
        ).split(":"),
    )

    start = datetime.combine(
        start_date,
        dtime(
            hour,
            minute,
        ),
    ).replace(
        tzinfo=TZ
    )

    if (
        time_match.lastindex
        and time_match.lastindex >= 2
        and time_match.group(2)
    ):

        end_hour, end_minute = map(
            int,
            time_match.group(
                2
            ).split(":"),
        )

        end = datetime.combine(
            start_date,
            dtime(
                end_hour,
                end_minute,
            ),
        ).replace(
            tzinfo=TZ
        )

    else:

        end = (
            start
            + timedelta(
                hours=1
            )
        )

    if end_date > start_date:

        end = datetime.combine(
            end_date,
            end.time(),
        ).replace(
            tzinfo=TZ
        )

    if end <= start:

        end = (
            start
            + timedelta(
                hours=1
            )
        )

    return (
        start,
        end,
        False,
    )


def extract_location(page):
    text = clean_text(
        page
    )

    match = re.search(
        r"Veranstaltungsort\s+"
        r"(.*?)"
        r"(?:Veranstalter|Weitere Termine|"
        r"Ähnliche Veranstaltungen)",
        text,
        re.I,
    )

    if not match:
        return ""

    location = match.group(
        1
    ).strip()

    if len(location) > 250:
        location = location[:250]

    return location


def build_official_ical_url(
    event,
    start,
    end,
    all_day,
    location,
):
    if all_day:

        local_start = datetime.combine(
            start,
            dtime.min,
            tzinfo=TZ,
        )

        local_end = datetime.combine(
            end,
            dtime(
                23,
                59,
                59,
            ),
            tzinfo=TZ,
        )

    else:

        local_start = (
            start
            if start.tzinfo
            else start.replace(
                tzinfo=TZ
            )
        )

        local_end = (
            end
            if end.tzinfo
            else end.replace(
                tzinfo=TZ
            )
        )

    params = [
        (
            "end",
            str(
                int(
                    local_end.timestamp()
                )
            ),
        ),
        (
            "filename",
            event["title"],
        ),
        (
            "id",
            "",
        ),
        (
            "location",
            location,
        ),
        (
            "sp:out",
            "leverkusen-lapwing.iCalFromParameters",
        ),
        (
            "start",
            str(
                int(
                    local_start.timestamp()
                )
            ),
        ),
        (
            "summary",
            event["title"],
        ),
        (
            "url",
            event["link"],
        ),
    ]

    return (
        ICAL_BASE
        + "?"
        + urllib.parse.urlencode(
            params
        )
    )


def parse_ical(text):
    lines = (
        text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )

    unfolded = []

    for line in lines:

        if (
            line.startswith(
                (" ", "\t")
            )
            and unfolded
        ):
            unfolded[-1] += line[1:]

        else:
            unfolded.append(
                line
            )

    event = {}
    inside = False

    for line in unfolded:

        if line == "BEGIN:VEVENT":

            inside = True
            event = {}

        elif line == "END:VEVENT":

            if event:
                return event

            inside = False

        elif (
            inside
            and ":" in line
        ):

            left, value = line.split(
                ":",
                1,
            )

            key = left.split(
                ";",
                1,
            )[0].upper()

            event[key] = value

    return {}


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


def unescape_ical(value):
    return (
        html.unescape(
            value or ""
        )
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


def fetch_event(event):
    try:

        page = get(
            event["link"],
            timeout=20,
            tries=3,
        )

        parsed = parse_page_datetime(
            page,
            event["title"],
            event["link"],
        )

        if parsed is None:

            return (
                None,
                "kein Datum gefunden",
            )

        start, end, all_day = parsed

        location = extract_location(
            page
        )

        # Den offiziellen Leverkusener
        # "Termin speichern"-Endpoint
        # anhand der exakt ermittelten Daten
        # erzeugen.
        ical_url = build_official_ical_url(
            event,
            start,
            end,
            all_day,
            location,
        )

        try:

            ical_text = get(
                ical_url,
                timeout=15,
                tries=2,
            )

            if (
                "BEGIN:VEVENT"
                in ical_text
            ):

                source = parse_ical(
                    ical_text
                )

                if source.get(
                    "DTSTART"
                ):

                    source_start, source_all_day = (
                        ical_value(
                            source[
                                "DTSTART"
                            ]
                        )
                    )

                    source_end = (
                        source_start
                    )

                    if source.get(
                        "DTEND"
                    ):

                        source_end, _ = (
                            ical_value(
                                source[
                                    "DTEND"
                                ]
                            )
                        )

                    return (
                        {
                            "uid": (
                                unescape_ical(
                                    source.get(
                                        "UID",
                                        "",
                                    )
                                )
                                or event[
                                    "link"
                                ]
                            ),
                            "start": source_start,
                            "end": source_end,
                            "all_day": source_all_day,
                            "summary": (
                                unescape_ical(
                                    source.get(
                                        "SUMMARY",
                                        "",
                                    )
                                )
                                or event[
                                    "title"
                                ]
                            ),
                            "description": (
                                unescape_ical(
                                    source.get(
                                        "DESCRIPTION",
                                        "",
                                    )
                                )
                                or event[
                                    "description"
                                ]
                            ),
                            "location": (
                                unescape_ical(
                                    source.get(
                                        "LOCATION",
                                        "",
                                    )
                                )
                                or location
                            ),
                            "url": (
                                unescape_ical(
                                    source.get(
                                        "URL",
                                        "",
                                    )
                                )
                                or event[
                                    "link"
                                ]
                            ),
                            "category": event[
                                "category"
                            ],
                        },
                        None,
                    )

        except Exception:
            pass

        # Fallback: Die bereits exakt
        # aus der offiziellen Veranstaltungsseite
        # gelesenen Daten verwenden.
        return (
            {
                "uid": event[
                    "link"
                ],
                "start": start,
                "end": end,
                "all_day": all_day,
                "summary": event[
                    "title"
                ],
                "description": event[
                    "description"
                ],
                "location": location,
                "url": event[
                    "link"
                ],
                "category": event[
                    "category"
                ],
            },
            None,
        )

    except Exception as exc:

        return (
            None,
            str(exc),
        )


def escape_ics(value):
    return (
        str(
            value or ""
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


def sort_key(event):
    value = event[
        "start"
    ]

    if (
        isinstance(
            value,
            date,
        )
        and not isinstance(
            value,
            datetime,
        )
    ):

        value = datetime.combine(
            value,
            dtime.min,
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

        lines += [
            "BEGIN:VEVENT",
            "UID:"
            + escape_ics(
                event["uid"]
            ),
            "DTSTAMP:"
            + stamp,
        ]

        if event["all_day"]:

            lines.append(
                "DTSTART;VALUE=DATE:"
                + event[
                    "start"
                ].strftime(
                    "%Y%m%d"
                )
            )

            lines.append(
                "DTEND;VALUE=DATE:"
                + (
                    event["end"]
                    + timedelta(
                        days=1
                    )
                ).strftime(
                    "%Y%m%d"
                )
            )

        else:

            start = event[
                "start"
            ]

            end = event[
                "end"
            ]

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

        if description:
            description += "\n\n"

        description += (
            "Kategorie: "
            + event[
                "category"
            ]
        )

        if event["url"]:

            description += (
                "\n\n"
                + event["url"]
            )

        lines.append(
            "DESCRIPTION:"
            + escape_ics(
                description
            )
        )

        if event[
            "location"
        ]:

            lines.append(
                "LOCATION:"
                + escape_ics(
                    event[
                        "location"
                    ]
                )
            )

        if event["url"]:

            lines.append(
                "URL:"
                + escape_ics(
                    event["url"]
                )
            )

        lines.append(
            "CATEGORIES:"
            + escape_ics(
                event[
                    "category"
                ]
            )
        )

        lines.append(
            "END:VEVENT"
        )

    lines.append(
        "END:VCALENDAR"
    )

    return (
        "\r\n".join(
            lines
        )
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

    items = []
    seen = set()

    # -------------------------------------------------
    # RSS aller gewünschten Kategorien
    # -------------------------------------------------

    for category_id, category in CATEGORIES:

        try:

            found = parse_rss(
                get(
                    rss_url(
                        category_id
                    ),
                    timeout=20,
                    tries=3,
                ),
                category,
            )

            print(
                "RSS:",
                category,
                len(found),
            )

            for item in found:

                # Nicht nur nach URL deduplizieren,
                # weil eine Seite mehrere Termine
                # enthalten kann.
                key = (
                    item[
                        "link"
                    ],
                    item[
                        "title"
                    ],
                    category,
                )

                if key not in seen:

                    seen.add(
                        key
                    )

                    items.append(
                        item
                    )

        except Exception as exc:

            print(
                "RSS FEHLER:",
                category,
                exc,
            )

    print(
        "Eindeutige RSS-Einträge:",
        len(items),
    )

    # -------------------------------------------------
    # Einzelne Veranstaltungsseiten
    # -------------------------------------------------

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

            item = jobs[
                job
            ]

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
                    item[
                        "title"
                    ],
                    "-",
                    error,
                )

    # -------------------------------------------------
    # Wirklich identische Termine entfernen
    # -------------------------------------------------

    unique = {}

    for event in results:

        key = (
            event[
                "uid"
            ],
            str(
                event[
                    "start"
                ]
            ),
            str(
                event[
                    "end"
                ]
            ),
            event[
                "category"
            ],
        )

        unique[
            key
        ] = event

    events = list(
        unique.values()
    )

    # -------------------------------------------------
    # ICS schreiben
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
    # Statistik
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

    print("")
    print(
        "======================================"
    )

    print(
        "FERTIG!"
    )

    print(
        "Veranstaltungen geschrieben:",
        len(events),
    )

    print(
        "Nicht verarbeitet:",
        failures,
    )

    print(
        "======================================"
    )

    for _, category in CATEGORIES:

        print(
            category + ":",
            counts.get(
                category,
                0,
            ),
        )

    print(
        "======================================"
    )


if __name__ == "__main__":
    main()