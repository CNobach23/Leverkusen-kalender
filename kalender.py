import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE_URL = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
OUTPUT_FILE = "veranstaltungen.ics"
LOCAL_TZ = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (compatible; LeverkusenKalender/5.0)"

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

    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Connection": "close",
                },
            )

            with urllib.request.urlopen(
                req,
                timeout=timeout,
            ) as r:

                enc = (
                    r.headers.get_content_charset()
                    or "utf-8"
                )

                return r.read().decode(
                    enc,
                    errors="replace",
                )

        except Exception as e:
            last = e

            if attempt + 1 < tries:
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

    return (
        BASE_URL
        + "?"
        + urllib.parse.urlencode(params)
    )


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
                "".join(
                    child.itertext()
                ).strip()
            )

        if (
            vals.get("title")
            and vals.get("link")
        ):

            out.append(
                {
                    "title": vals["title"],
                    "link": vals["link"],
                    "description": vals.get(
                        "description",
                        "",
                    ),
                    "category": category,
                }
            )

    return out


def jsonld_objects(page):
    pattern = (
        r'<script[^>]+type=["\']'
        r'application/ld\+json["\']'
        r'[^>]*>(.*?)</script>'
    )

    out = []

    for raw in re.findall(
        pattern,
        page,
        re.I | re.S,
    ):

        raw = html.unescape(
            raw
        ).strip()

        raw = re.sub(
            r"^\s*<!--|-->\s*$",
            "",
            raw,
        ).strip()

        try:
            obj = json.loads(raw)

            if isinstance(
                obj,
                list,
            ):
                out.extend(obj)
            else:
                out.append(obj)

        except Exception:
            pass

    return out


def walk(obj):

    if isinstance(
        obj,
        dict,
    ):

        yield obj

        for value in obj.values():
            yield from walk(value)

    elif isinstance(
        obj,
        list,
    ):

        for value in obj:
            yield from walk(value)


def parse_dt(value):

    if not value:
        return None, False

    value = str(
        value
    ).strip()

    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}",
        value,
    ):

        return (
            datetime.strptime(
                value,
                "%Y-%m-%d",
            ).date(),
            True,
        )

    try:

        return (
            datetime.fromisoformat(
                value.replace(
                    "Z",
                    "+00:00",
                )
            ),
            False,
        )

    except Exception:
        pass

    match = re.search(
        r"(20\d{2}-\d{2}-\d{2})"
        r"(?:[T ](\d{2}):(\d{2}))?",
        value,
    )

    if not match:
        return None, False

    if match.group(2):

        return (
            datetime.strptime(
                (
                    f"{match.group(1)} "
                    f"{match.group(2)}:"
                    f"{match.group(3)}"
                ),
                "%Y-%m-%d %H:%M",
            ),
            False,
        )

    return (
        datetime.strptime(
            match.group(1),
            "%Y-%m-%d",
        ).date(),
        True,
    )


def event_from_page(
    item,
    page,
):

    data = None

    for root in jsonld_objects(
        page
    ):

        for obj in walk(root):

            typ = obj.get(
                "@type",
                "",
            )

            if isinstance(
                typ,
                list,
            ):

                types = [
                    str(x).lower()
                    for x in typ
                ]

            else:

                types = [
                    str(typ).lower()
                ]

            if (
                "event" in types
                or "eventseries" in types
            ):

                data = obj
                break

        if data:
            break

    start = None
    end = None
    all_day = False
    location = ""

    name = item["title"]
    description = item["description"]

    if data:

        start, all_day = parse_dt(
            data.get(
                "startDate"
            )
        )

        end, _ = parse_dt(
            data.get(
                "endDate"
            )
        )

        name = (
            data.get("name")
            or name
        )

        description = (
            data.get("description")
            or description
        )

        loc = data.get(
            "location"
        )

        if isinstance(
            loc,
            dict,
        ):

            location = (
                loc.get(
                    "name",
                    "",
                )
                or ""
            )

        elif isinstance(
            loc,
            str,
        ):

            location = loc

    # Fallback für terminbezogene URLs
    if start is None:

        match = re.search(
            r"/(20\d{2})-"
            r"(\d{2})-"
            r"(\d{2})"
            r"(?:-(\d{2})-(\d{2}))?/?$",
            item["link"],
        )

        if match:

            if match.group(4):

                start = datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    int(match.group(4)),
                    int(match.group(5)),
                )

                all_day = False

            else:

                start = date(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                )

                all_day = True

    if start is None:

        raise ValueError(
            "kein Startdatum gefunden"
        )

    if end is None:

        if all_day:

            end = (
                start
                + timedelta(days=1)
            )

        else:

            end = start

    return {
        "uid": item["link"],
        "start": start,
        "end": end,
        "all_day": all_day,
        "summary": html.unescape(
            str(name)
        ),
        "description": html.unescape(
            str(description or "")
        ),
        "location": html.unescape(
            str(location)
        ),
        "url": item["link"],
        "category": item["category"],
    }


def fetch(item):

    try:

        page = get(
            item["link"],
            timeout=15,
            tries=2,
        )

        return (
            event_from_page(
                item,
                page,
            ),
            None,
        )

    except Exception as e:

        return (
            None,
            str(e),
        )


def esc(value):

    return (
        html.unescape(
            str(value or "")
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

    value = event["start"]

    if isinstance(
        value,
        datetime,
    ):

        if value.tzinfo is None:

            value = value.replace(
                tzinfo=LOCAL_TZ
            )

    else:

        value = datetime.combine(
            value,
            datetime.min.time(),
            tzinfo=LOCAL_TZ,
        )

    return value.astimezone(
        timezone.utc
    ).isoformat()


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
            + esc(
                event["uid"]
            ),
            "DTSTAMP:"
            + stamp,
        ]

        if event["all_day"]:

            lines.append(
                "DTSTART;VALUE=DATE:"
                + event["start"].strftime(
                    "%Y%m%d"
                )
            )

            lines.append(
                "DTEND;VALUE=DATE:"
                + event["end"].strftime(
                    "%Y%m%d"
                )
            )

        else:

            start = event["start"]
            end = event["end"]

            if start.tzinfo is None:

                start = start.replace(
                    tzinfo=LOCAL_TZ
                )

            if end.tzinfo is None:

                end = end.replace(
                    tzinfo=LOCAL_TZ
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
            + esc(
                event["summary"]
            )
        )

        description = (
            event["description"]
        )

        if description:

            description += "\n\n"

        description += (
            "Kategorie: "
            + event["category"]
            + "\n\n"
            + event["url"]
        )

        lines.append(
            "DESCRIPTION:"
            + esc(description)
        )

        if event["location"]:

            lines.append(
                "LOCATION:"
                + esc(
                    event["location"]
                )
            )

        lines += [
            "URL:"
            + esc(
                event["url"]
            ),
            "CATEGORIES:"
            + esc(
                event["category"]
            ),
            "END:VEVENT",
        ]

    lines.append(
        "END:VCALENDAR"
    )

    return (
        "\r\n".join(lines)
        + "\r\n"
    )


def main():

    rss_events = []
    seen = set()

    # Nur 11 RSS-Abfragen.
    for (
        category_id,
        category,
    ) in CATEGORIES:

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

            for item in items:

                if item["link"] in seen:
                    continue

                seen.add(
                    item["link"]
                )

                rss_events.append(
                    item
                )

        except Exception as e:

            print(
                "  RSS FEHLER:",
                e,
            )

    print(
        "Eindeutige RSS-Veranstaltungen:",
        len(rss_events),
    )

    results = []
    failed = 0

    # Nur 4 parallele Veranstaltungsseiten.
    with ThreadPoolExecutor(
        max_workers=4
    ) as pool:

        jobs = {
            pool.submit(
                fetch,
                item,
            ): item
            for item in rss_events
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
                    len(rss_events),
                    jobs[job]["title"],
                    "-",
                    error,
                )

    unique = {
        (
            event["uid"],
            event["start"],
        ): event
        for event in results
    }

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
        newline="",
    ) as file:

        file.write(
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