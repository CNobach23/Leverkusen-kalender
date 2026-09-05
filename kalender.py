import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone


BASE_URL = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
OUTPUT_FILE = "veranstaltungen.ics"
TIMEZONE = "Europe/Berlin"

USER_AGENT = "Mozilla/5.0 (compatible; LeverkusenKalender/1.0)"


CATEGORIES = [
    ("42030", "Führungen & Touren"),
    ("42023", "Familie & Kinder"),
    ("42081", "Feste & Brauchtum"),
    ("42082", "Festivals & Open-Airs"),
    ("42078", "Karneval"),
    ("42029", "Lesungen & Literatur"),
    ("42032", "Märkte & Messen"),
    ("42036", "Partys & Nachtleben"),
    ("42031", "Spitzensport"),
    ("42037", "Tipps"),
    ("34821", "Warntage"),
]


def http_get(url, attempts=3, delay=2):
    """Lädt eine URL mit mehreren Versuchen."""
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml,"
                        "text/xml,text/calendar,*/*;q=0.8"
                    ),
                },
            )

            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return data.decode(charset, errors="replace")

        except Exception as exc:
            last_error = exc

            if attempt < attempts:
                time.sleep(delay)

    raise last_error


def build_rss_url(category_id):
    """Erzeugt die RSS-URL für eine Leverkusen-Kategorie."""

    today = date.today().isoformat()

    params = [
        ("sp:categories[13495][0]", "-"),
        ("sp:categories[13495][1]", "__last__"),
        ("sp:categories[13459][0]", category_id),
        ("sp:categories[13459][1]", "__last__"),
        ("sp:dateFrom[0]", today),
        ("sp:dateTo[0]", ""),
        ("sp:fulltext[0]", ""),
        ("sp:out", "rss"),
        ("sp:cmp", "eventSearch-1-0-searchResult"),
        ("action", "submit"),
    ]

    return BASE_URL + "?" + urllib.parse.urlencode(params)


def parse_rss(xml_text, category_name):
    """Liest Veranstaltungen aus dem RSS-Feed."""

    root = ET.fromstring(xml_text)
    events = []

    for item in root.findall(".//item"):

        def get_text(name):
            node = item.find(name)

            if node is None or node.text is None:
                return ""

            return html.unescape(node.text).strip()

        title = get_text("title")
        link = get_text("link")
        guid = get_text("guid")
        description = get_text("description")
        pubdate = get_text("pubDate")

        if not guid:
            guid = link

        if not title or not link:
            continue

        events.append(
            {
                "title": title,
                "link": link,
                "guid": guid,
                "description": description,
                "category": category_name,
                "pubdate": pubdate,
            }
        )

    return events


def normalize_url(url):
    """Normalisiert URLs für einen zuverlässigen Vergleich."""

    url = html.unescape(urllib.parse.unquote(url or "")).strip()

    parsed = urllib.parse.urlsplit(url)

    path = parsed.path.rstrip("/")

    if not path:
        path = "/"

    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.query,
            parsed.fragment,
        )
    )


def extract_ical_links(html_text):
    """
    Sucht nach offiziellen Leverkusener iCalFromParameters-Links.
    """

    text = html.unescape(html_text)

    links = []

    patterns = [
        r'href\s*=\s*["\']([^"\']*iCalFromParameters[^"\']*)["\']',
        r'href\s*=\s*["\']([^"\']*iCalendar[^"\']*)["\']',
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):

            href = match.group(1).strip()

            href = html.unescape(href)

            if href.startswith("/"):
                href = urllib.parse.urljoin(
                    BASE_URL,
                    href,
                )

            elif href.startswith("?"):
                href = BASE_URL + href

            elif not href.startswith(
                ("http://", "https://")
            ):
                href = urllib.parse.urljoin(
                    BASE_URL,
                    href,
                )

            if href not in links:
                links.append(href)

    return links


def candidate_event_url(ical_link):
    """
    Holt aus dem offiziellen iCal-Link die eigentliche
    Veranstaltungs-URL.
    """

    try:
        parsed = urllib.parse.urlsplit(ical_link)

        query = urllib.parse.parse_qs(
            parsed.query
        )

        values = query.get("url", [])

        if values:
            return normalize_url(values[0])

    except Exception:
        pass

    return ""


def find_ical_link(event):
    """
    Findet den exakt zu diesem RSS-Eintrag gehörenden iCal-Link.

    Wichtig:
    Bei wiederkehrenden Veranstaltungen darf nicht nur der Titel
    verglichen werden. Die Veranstaltungs-URL identifiziert die
    konkrete Veranstaltung bzw. den konkreten Termin.
    """

    search_params = [
        (
            "sp:fulltext[0]",
            event["title"],
        ),
        (
            "sp:dateFrom[0]",
            date.today().isoformat(),
        ),
        (
            "sp:dateTo[0]",
            "",
        ),
        (
            "sp:out",
            "html",
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

    search_url = (
        BASE_URL
        + "?"
        + urllib.parse.urlencode(search_params)
    )

    try:
        page = http_get(
            search_url,
            attempts=3,
        )

        links = extract_ical_links(page)

        wanted = normalize_url(
            event["link"]
        )

        # Wichtigster Fall:
        # Die URL im iCal-Link muss exakt zur RSS-Veranstaltung
        # passen.
        for link in links:

            if candidate_event_url(link) == wanted:
                return link

        # Falls nur ein Treffer vorhanden ist, können wir ihn
        # ebenfalls verwenden.
        if len(links) == 1:
            return links[0]

    except Exception as exc:

        print(
            f"  Suche fehlgeschlagen: {exc}"
        )

    # Zweiter Versuch:
    # Direkt die Veranstaltungsseite aufrufen.
    try:

        page = http_get(
            event["link"],
            attempts=2,
        )

        links = extract_ical_links(page)

        wanted = normalize_url(
            event["link"]
        )

        for link in links:

            if candidate_event_url(link) == wanted:
                return link

        if len(links) == 1:
            return links[0]

    except Exception as exc:

        print(
            f"  Veranstaltungsseite nicht erreichbar: {exc}"
        )

    return None


def unfold_ics(text):
    """Entfaltet gefaltete iCalendar-Zeilen."""

    lines = (
        text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )

    result = []

    for line in lines:

        if line.startswith((" ", "\t")) and result:
            result[-1] += line[1:]

        else:
            result.append(line)

    return result


def unescape_ics(value):
    """Entschärft iCalendar- und HTML-Escapes."""

    value = html.unescape(value or "")

    value = value.replace(
        "\\n",
        "\n",
    )

    value = value.replace(
        "\\N",
        "\n",
    )

    value = value.replace(
        "\\,",
        ",",
    )

    value = value.replace(
        "\\;",
        ";",
    )

    value = value.replace(
        "\\\\",
        "\\",
    )

    return value


def parse_ics_property(line):
    """Zerlegt eine iCalendar-Eigenschaft."""

    if ":" not in line:
        return None, {}, ""

    left, value = line.split(
        ":",
        1,
    )

    parts = left.split(";")

    name = parts[0].upper()

    params = {}

    for part in parts[1:]:

        if "=" in part:

            key, val = part.split(
                "=",
                1,
            )

            params[key.upper()] = val.strip('"')

    return name, params, value


def parse_ics(ics_text):
    """Liest den ersten VEVENT aus einer iCalendar-Datei."""

    lines = unfold_ics(ics_text)

    event = {}

    inside = False

    for line in lines:

        if line.strip() == "BEGIN:VEVENT":

            inside = True
            event = {}

            continue

        if line.strip() == "END:VEVENT":

            if event:
                return event

            inside = False

            continue

        if not inside:
            continue

        name, params, value = parse_ics_property(
            line
        )

        if not name:
            continue

        value = unescape_ics(value)

        event[name] = {
            "value": value,
            "params": params,
        }

    return event


def parse_ical_datetime(prop):
    """Wandelt einen iCalendar-Zeitwert in Python um."""

    value = prop["value"].strip()

    params = prop.get(
        "params",
        {},
    )

    value_type = params.get(
        "VALUE",
        "",
    ).upper()

    # Ganztägiger Termin
    if (
        value_type == "DATE"
        or re.fullmatch(r"\d{8}", value)
    ):

        return (
            datetime.strptime(
                value[:8],
                "%Y%m%d",
            ).date(),
            True,
        )

    # UTC-Zeit
    if value.endswith("Z"):

        dt = datetime.strptime(
            value,
            "%Y%m%dT%H%M%SZ",
        )

        dt = dt.replace(
            tzinfo=timezone.utc
        )

        return dt, False

    # Lokale Zeit.
    # Die Leverkusener Veranstaltungen verwenden Europe/Berlin.
    dt = datetime.strptime(
        value[:15],
        "%Y%m%dT%H%M%S",
    )

    return dt, False


def format_ics_datetime(
    value,
    all_day=False,
):
    """Formatiert Datum/Zeit für die Ausgabe."""

    if all_day:

        return value.strftime(
            "%Y%m%d"
        )

    if value.tzinfo is None:

        return value.strftime(
            "%Y%m%dT%H%M%S"
        )

    return value.astimezone(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def convert_event(
    ical,
    rss_event,
):
    """Verbindet die iCal-Daten mit der RSS-Kategorie."""

    if (
        "DTSTART" not in ical
        or "SUMMARY" not in ical
    ):
        return None

    start, all_day = parse_ical_datetime(
        ical["DTSTART"]
    )

    if "DTEND" in ical:

        end, end_all_day = parse_ical_datetime(
            ical["DTEND"]
        )

    else:

        end = start
        end_all_day = all_day

    summary = (
        unescape_ics(
            ical.get(
                "SUMMARY",
                {},
            ).get(
                "value",
                "",
            )
        )
        or rss_event["title"]
    )

    description = unescape_ics(
        ical.get(
            "DESCRIPTION",
            {},
        ).get(
            "value",
            "",
        )
    )

    location = unescape_ics(
        ical.get(
            "LOCATION",
            {},
        ).get(
            "value",
            "",
        )
    )

    url = unescape_ics(
        ical.get(
            "URL",
            {},
        ).get(
            "value",
            "",
        )
    )

    if not url:
        url = rss_event["link"]

    category_text = (
        f"Kategorie: {rss_event['category']}"
    )

    if description:

        description = (
            f"{description}\n\n"
            f"{category_text}\n\n"
            f"{url}"
        )

    else:

        description = (
            f"{category_text}\n\n"
            f"{url}"
        )

    return {
        "uid": rss_event["link"],
        "start": start,
        "end": end,
        "all_day": all_day,
        "summary": summary,
        "description": description,
        "location": location,
        "url": url,
        "category": rss_event["category"],
    }


def ics_escape(value):
    """Escaped Text für eine iCalendar-Datei."""

    value = html.unescape(
        value or ""
    )

    value = value.replace(
        "\\",
        "\\\\",
    )

    value = value.replace(
        ";",
        "\\;",
    )

    value = value.replace(
        ",",
        "\\,",
    )

    value = (
        value
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    value = value.replace(
        "\n",
        "\\n",
    )

    return value


def build_calendar(events):
    """Erzeugt die komplette veranstaltungen.ics."""

    now = datetime.now(
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
        key=lambda x: (
            x["start"],
            x["summary"].lower(),
        ),
    ):

        lines.append(
            "BEGIN:VEVENT"
        )

        lines.append(
            f"UID:{ics_escape(event['uid'])}"
        )

        lines.append(
            f"DTSTAMP:{now}"
        )

        if event["all_day"]:

            lines.append(
                "DTSTART;VALUE=DATE:"
                + format_ics_datetime(
                    event["start"],
                    True,
                )
            )

            if isinstance(
                event["end"],
                date,
            ):

                # Ganz wichtig:
                # DTEND ist in iCalendar bereits EXKLUSIV.
                # Deshalb wird hier KEIN zusätzlicher Tag
                # addiert.
                lines.append(
                    "DTEND;VALUE=DATE:"
                    + format_ics_datetime(
                        event["end"],
                        True,
                    )
                )

        else:

            if (
                isinstance(
                    event["start"],
                    datetime,
                )
                and event["start"].tzinfo is None
            ):

                lines.append(
                    f"DTSTART;TZID={TIMEZONE}:"
                    + format_ics_datetime(
                        event["start"]
                    )
                )

            else:

                lines.append(
                    "DTSTART:"
                    + format_ics_datetime(
                        event["start"]
                    )
                )

            if (
                isinstance(
                    event["end"],
                    datetime,
                )
                and event["end"].tzinfo is None
            ):

                lines.append(
                    f"DTEND;TZID={TIMEZONE}:"
                    + format_ics_datetime(
                        event["end"]
                    )
                )

            else:

                lines.append(
                    "DTEND:"
                    + format_ics_datetime(
                        event["end"]
                    )
                )

        lines.append(
            "SUMMARY:"
            + ics_escape(
                event["summary"]
            )
        )

        lines.append(
            "DESCRIPTION:"
            + ics_escape(
                event["description"]
            )
        )

        if event["location"]:

            lines.append(
                "LOCATION:"
                + ics_escape(
                    event["location"]
                )
            )

        if event["url"]:

            lines.append(
                "URL:"
                + ics_escape(
                    event["url"]
                )
            )

        lines.append(
            "CATEGORIES:"
            + ics_escape(
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

    all_events = []

    seen_rss = set()

    for (
        category_id,
        category_name,
    ) in CATEGORIES:

        print()
        print(
            f"Kategorie: {category_name}"
        )

        rss_url = build_rss_url(
            category_id
        )

        try:

            rss = http_get(
                rss_url,
                attempts=3,
            )

            rss_events = parse_rss(
                rss,
                category_name,
            )

            print(
                f"  RSS: {len(rss_events)} Veranstaltungen"
            )

        except Exception as exc:

            print(
                f"  FEHLER beim RSS-Abruf: {exc}"
            )

            continue

        for index, event in enumerate(
            rss_events,
            start=1,
        ):

            key = (
                event["link"],
                event["category"],
            )

            if key in seen_rss:
                continue

            seen_rss.add(key)

            print(
                f"  [{index}/{len(rss_events)}] "
                f"{event['title']}"
            )

            ical_link = find_ical_link(
                event
            )

            if not ical_link:

                print(
                    "    -> kein passender iCal-Link gefunden"
                )

                continue

            try:

                ical_text = http_get(
                    ical_link,
                    attempts=3,
                )

                parsed = parse_ics(
                    ical_text
                )

                converted = convert_event(
                    parsed,
                    event,
                )

                if converted:

                    all_events.append(
                        converted
                    )

                else:

                    print(
                        "    -> iCal enthält keinen vollständigen VEVENT"
                    )

            except Exception as exc:

                print(
                    "    -> iCal konnte nicht "
                    f"gelesen werden: {exc}"
                )

    # Exakte Veranstaltungsinstanzen deduplizieren.
    unique = {}

    for event in all_events:

        key = (
            normalize_url(
                event["uid"]
            ),
            event["start"],
        )

        unique[key] = event

    result = build_calendar(
        list(unique.values())
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        f.write(result)

    print()
    print(
        f"Fertig: {len(unique)} Veranstaltungen "
        f"in {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()