import html
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
OUTPUT_FILE = "veranstaltungen.ics"
USER_AGENT = "Mozilla/5.0 (compatible; LeverkusenKalender/3.0)"

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

    for _ in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Connection": "close",
                },
            )

            with urllib.request.urlopen(
                req,
                timeout=timeout,
            ) as r:
                raw = r.read()
                enc = r.headers.get_content_charset() or "utf-8"
                return raw.decode(enc, errors="replace")

        except Exception as e:
            last = e
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

    return BASE_URL + "?" + urllib.parse.urlencode(params)


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
                "".join(child.itertext()).strip()
            )

        title = vals.get("title", "")
        link = vals.get("link", "")

        if title and link:
            out.append(
                {
                    "title": title,
                    "link": link,
                    "guid": vals.get("guid") or link,
                    "description": vals.get(
                        "description",
                        "",
                    ),
                    "category": category,
                }
            )

    return out


def norm_url(url):
    url = html.unescape(
        urllib.parse.unquote(
            url or ""
        )
    ).strip()

    p = urllib.parse.urlsplit(url)

    path = p.path.rstrip("/") or "/"

    return urllib.parse.urlunsplit(
        (
            p.scheme.lower(),
            p.netloc.lower(),
            path,
            p.query,
            "",
        )
    )


def ical_candidates(page):
    page = html.unescape(page)

    links = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        page,
        flags=re.I,
    )

    out = []

    for link in links:

        if (
            "iCalFromParameters" not in link
            and "iCalFrom" not in link
        ):
            continue

        full = urllib.parse.urljoin(
            BASE_URL,
            link,
        )

        if full not in out:
            out.append(full)

    return out


def candidate_url(ical):
    try:
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(
                ical
            ).query
        )

        values = query.get(
            "url",
            [],
        )

        if values:
            return norm_url(
                values[0]
            )

    except Exception:
        pass

    return ""


def find_ical(event):

    params = [
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

    search = (
        BASE_URL
        + "?"
        + urllib.parse.urlencode(params)
    )

    try:

        page = get(
            search,
            timeout=15,
            tries=2,
        )

        links = ical_candidates(
            page
        )

        wanted = norm_url(
            event["link"]
        )

        # Exakte Veranstaltungs-URL
        # hat Vorrang.
        for link in links:

            if candidate_url(link) == wanted:
                return link

        # Nur wenn genau ein Treffer
        # existiert, verwenden wir ihn
        # ersatzweise.
        if len(links) == 1:
            return links[0]

    except Exception as e:

        print(
            "Suche:",
            event["title"],
            e,
        )

    return None


def unfold(text):
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

    for line in unfold(text):

        if line == "BEGIN:VEVENT":
            inside = True
            event = {}
            continue

        if line == "END:VEVENT":
            return event

        if not inside or ":" not in line:
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


def parse_value(value):

    value = (
        value or ""
    ).strip()

    # Ganztägig
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

    # UTC
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

    # Lokale Zeit
    if (
        len(value) >= 15
        and value[8] == "T"
    ):

        return (
            datetime.strptime(
                value[:15],
                "%Y%m%dT%H%M%S",
            ),
            False,
        )

    return (
        None,
        False,
    )


def clean_ics(value):

    value = html.unescape(
        value or ""
    )

    return (
        value
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def fetch_event(event):

    ical = find_ical(
        event
    )

    if not ical:

        return (
            None,
            "kein passender iCal-Link",
        )

    try:

        data = get(
            ical,
            timeout=15,
            tries=2,
        )

        source = parse_ics(
            data
        )

        if "DTSTART" not in source:

            return (
                None,
                "iCal ohne DTSTART",
            )

        start, all_day = parse_value(
            source["DTSTART"]
        )

        if start is None:

            return (
                None,
                "ungültiger DTSTART",
            )

        if "DTEND" in source:

            end, _ = parse_value(
                source["DTEND"]
            )

        else:

            end = start

        return (
            {
                "uid": event["link"],
                "start": start,
                "end": end,
                "all_day": all_day,
                "summary": clean_ics(
                    source.get(
                        "SUMMARY",
                        event["title"],
                    )
                ),
                "description": clean_ics(
                    source.get(
                        "DESCRIPTION",
                        event["description"],
                    )
                ),
                "location": clean_ics(
                    source.get(
                        "LOCATION",
                        "",
                    )
                ),
                "url": clean_ics(
                    source.get(
                        "URL",
                        event["link"],
                    )
                ) or event["link"],
                "category": event["category"],
            },
            None,
        )

    except Exception as e:

        return (
            None,
            str(e),
        )


def esc(value):

    value = html.unescape(
        str(value or "")
    )

    return (
        value
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


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

    events = sorted(
        events,
        key=lambda x: (
            x["start"],
            x["summary"].lower(),
        ),
    )

    for e in events:

        lines += [
            "BEGIN:VEVENT",
            "UID:" + esc(e["uid"]),
            "DTSTAMP:" + stamp,
        ]

        if e["all_day"]:

            lines.append(
                "DTSTART;VALUE=DATE:"
                + e["start"].strftime(
                    "%Y%m%d"
                )
            )

            lines.append(
                "DTEND;VALUE=DATE:"
                + e["end"].strftime(
                    "%Y%m%d"
                )
            )

        else:

            start = e["start"]
            end = e["end"]

            if start.tzinfo is None:
                start = start.replace(
                    tzinfo=timezone(
                        timedelta(hours=2)
                    )
                )

            if end.tzinfo is None:
                end = end.replace(
                    tzinfo=timezone(
                        timedelta(hours=2)
                    )
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
            + esc(e["summary"])
        )

        description = e["description"]

        if description:
            description += "\n\n"

        description += (
            "Kategorie: "
            + e["category"]
            + "\n\n"
            + e["url"]
        )

        lines.append(
            "DESCRIPTION:"
            + esc(description)
        )

        if e["location"]:

            lines.append(
                "LOCATION:"
                + esc(e["location"])
            )

        lines.append(
            "URL:"
            + esc(e["url"])
        )

        lines.append(
            "CATEGORIES:"
            + esc(e["category"])
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

    events = []
    seen = set()

    # RSS-Feeds laden
    for category_id, category in CATEGORIES:

        print(
            "Kategorie:",
            category,
        )

        try:

            items = parse_rss(
                get(
                    rss_url(
                        category_id
                    ),
                    timeout=15,
                    tries=3,
                ),
                category,
            )

            print(
                "  RSS:",
                len(items),
                "Veranstaltungen",
            )

        except Exception as e:

            print(
                "  RSS FEHLER:",
                e,
            )

            continue

        for item in items:

            if item["link"] in seen:
                continue

            seen.add(
                item["link"]
            )

            events.append(
                item
            )

    print(
        "Eindeutige RSS-Veranstaltungen:",
        len(events),
    )

    results = []
    failed = 0

    # Nur 3 parallele Anfragen.
    with ThreadPoolExecutor(
        max_workers=3
    ) as pool:

        jobs = {
            pool.submit(
                fetch_event,
                event,
            ): event
            for event in events
        }

        for i, job in enumerate(
            as_completed(jobs),
            1,
        ):

            result, error = (
                job.result()
            )

            if result:

                results.append(
                    result
                )

            else:

                failed += 1

                print(
                    "FEHLER",
                    i,
                    "/",
                    len(events),
                    jobs[job]["title"],
                    "-",
                    error,
                )

    unique = {}

    for event in results:

        unique[
            (
                event["uid"],
                event["start"],
            )
        ] = event

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:

        f.write(
            make_ics(
                list(
                    unique.values()
                )
            )
        )

    print()
    print(
        "FERTIG!"
    )

    print(
        "Veranstaltungen geschrieben:",
        len(unique),
    )

    print(
        "Nicht verarbeitet:",
        failed,
    )


if __name__ == "__main__":
    main()