import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

BASE = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
OUT = "veranstaltungen.ics"
TZ = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (LeverkusenKalender/7.0)"

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
    err = None

    for n in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Connection": "close",
                },
            )

            with urllib.request.urlopen(req, timeout=timeout) as r:
                enc = r.headers.get_content_charset() or "utf-8"
                return r.read().decode(enc, errors="replace")

        except Exception as e:
            err = e

            if n + 1 < tries:
                time.sleep(1)

    raise err


def rss_url(cid):
    p = [
        ("sp:categories[13495][0]", "-"),
        ("sp:categories[13495][1]", "__last__"),
        ("sp:categories[13459][0]", cid),
        ("sp:categories[13459][1]", "__last__"),
        ("sp:dateFrom[0]", date.today().isoformat()),
        ("sp:dateTo[0]", ""),
        ("sp:fulltext[0]", ""),
        ("sp:out", "rss"),
        ("sp:cmp", "eventSearch-1-0-searchResult"),
        ("action", "submit"),
    ]

    return BASE + "?" + urllib.parse.urlencode(p)


def parse_rss(text, category):
    root = ET.fromstring(text)
    out = []

    for item in root.findall(".//item"):
        vals = {}

        for child in item:
            vals[child.tag.split("}")[-1]] = html.unescape(
                "".join(child.itertext()).strip()
            )

        if vals.get("title") and vals.get("link"):
            out.append(
                {
                    "title": vals["title"],
                    "link": vals["link"],
                    "description": vals.get("description", ""),
                    "category": category,
                }
            )

    return out


def unescape(v):
    return (
        html.unescape(v or "")
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def ical_value(value):
    value = value.strip()

    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date(), True

    if value.endswith("Z"):
        return (
            datetime.strptime(
                value,
                "%Y%m%dT%H%M%SZ",
            ).replace(tzinfo=timezone.utc),
            False,
        )

    return (
        datetime.strptime(
            value[:15],
            "%Y%m%dT%H%M%S",
        ),
        False,
    )


def parse_ics(text):
    lines = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )

    folded = []

    for line in lines:
        if line.startswith((" ", "\t")) and folded:
            folded[-1] += line[1:]
        else:
            folded.append(line)

    ev = {}
    inside = False

    for line in folded:
        if line == "BEGIN:VEVENT":
            inside = True
            ev = {}

        elif line == "END:VEVENT":
            return ev

        elif inside and ":" in line:
            left, value = line.split(":", 1)
            name = left.split(";", 1)[0].upper()
            ev[name] = unescape(value)

    return ev


def page_ical_link(page):
    page = html.unescape(page)

    links = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        page,
        re.I,
    )

    for link in links:
        if (
            "iCalFromParameters" in link
            or "iCalendar" in link
        ):
            return urllib.parse.urljoin(BASE, link)

    return None


def jsonld_event(page):
    pat = (
        r'<script[^>]+type=["\']application/ld\+json'
        r'["\'][^>]*>(.*?)</script>'
    )

    for raw in re.findall(
        pat,
        page,
        re.I | re.S,
    ):
        try:
            obj = json.loads(
                html.unescape(raw).strip()
            )
        except Exception:
            continue

        stack = obj if isinstance(obj, list) else [obj]

        while stack:
            x = stack.pop()

            if isinstance(x, dict):
                typ = x.get("@type", "")

                types = (
                    [str(t).lower() for t in typ]
                    if isinstance(typ, list)
                    else [str(typ).lower()]
                )

                if (
                    "event" in types
                    or "eventseries" in types
                ):
                    return x

                stack.extend(x.values())

            elif isinstance(x, list):
                stack.extend(x)

    return None


def url_fallback(item):
    m = re.search(
        r"/(20\d{2})-(\d{2})-(\d{2})"
        r"(?:-(\d{2})-(\d{2}))?/?$",
        item["link"],
    )

    if not m:
        return None, None, False

    y, mo, d = map(
        int,
        m.group(1, 2, 3),
    )

    if m.group(4):
        start = datetime(
            y,
            mo,
            d,
            int(m.group(4)),
            int(m.group(5)),
        )

        return start, start, False

    start = date(y, mo, d)

    return start, start, True


def fetch_event(item):
    try:
        page = get(item["link"])

        ical = page_ical_link(page)

        if ical:
            try:
                src = parse_ics(
                    get(
                        ical,
                        12,
                        2,
                    )
                )

                if "DTSTART" in src:
                    start, all_day = ical_value(
                        src["DTSTART"]
                    )

                    end = start

                    if "DTEND" in src:
                        end, _ = ical_value(
                            src["DTEND"]
                        )

                    return (
                        {
                            "uid": item["link"],
                            "start": start,
                            "end": end,
                            "all_day": all_day,
                            "summary": (
                                src.get("SUMMARY")
                                or item["title"]
                            ),
                            "description": (
                                src.get("DESCRIPTION")
                                or item["description"]
                            ),
                            "location": src.get(
                                "LOCATION",
                                "",
                            ),
                            "url": (
                                src.get("URL")
                                or item["link"]
                            ),
                            "category": item["category"],
                        },
                        None,
                    )

            except Exception:
                pass

        data = jsonld_event(page)

        if data and data.get("startDate"):
            start, all_day = parse_iso(
                data.get("startDate")
            )

            end, _ = parse_iso(
                data.get("endDate")
            )

            if start is not None:
                if end is None:
                    end = start

                loc = data.get(
                    "location",
                    "",
                )

                if isinstance(loc, dict):
                    loc = loc.get(
                        "name",
                        "",
                    )

                return (
                    {
                        "uid": item["link"],
                        "start": start,
                        "end": end,
                        "all_day": all_day,
                        "summary": (
                            data.get("name")
                            or item["title"]
                        ),
                        "description": (
                            data.get("description")
                            or item["description"]
                        ),
                        "location": loc or "",
                        "url": (
                            data.get("url")
                            or item["link"]
                        ),
                        "category": item["category"],
                    },
                    None,
                )

        start, end, all_day = url_fallback(item)

        if start is not None:
            return (
                {
                    "uid": item["link"],
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                    "summary": item["title"],
                    "description": item["description"],
                    "location": "",
                    "url": item["link"],
                    "category": item["category"],
                },
                None,
            )

        return None, "kein Datum gefunden"

    except Exception as e:
        return None, str(e)


def parse_iso(v):
    if not v:
        return None, False

    v = str(v).strip()

    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}",
        v,
    ):
        return (
            datetime.strptime(
                v,
                "%Y-%m-%d",
            ).date(),
            True,
        )

    try:
        return (
            datetime.fromisoformat(
                v.replace("Z", "+00:00")
            ),
            False,
        )

    except Exception:
        return None, False


def esc(v):
    return (
        html.unescape(str(v or ""))
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def sort_key(e):
    x = e["start"]

    if isinstance(x, date) and not isinstance(
        x,
        datetime,
    ):
        x = datetime.combine(
            x,
            datetime.min.time(),
            tzinfo=TZ,
        )

    elif x.tzinfo is None:
        x = x.replace(tzinfo=TZ)

    return x.astimezone(
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

    for e in sorted(
        events,
        key=sort_key,
    ):
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
            s = (
                e["start"]
                if e["start"].tzinfo
                else e["start"].replace(
                    tzinfo=TZ
                )
            )

            d = (
                e["end"]
                if e["end"].tzinfo
                else e["end"].replace(
                    tzinfo=TZ
                )
            )

            lines.append(
                "DTSTART:"
                + s.astimezone(
                    timezone.utc
                ).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
            )

            lines.append(
                "DTEND:"
                + d.astimezone(
                    timezone.utc
                ).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
            )

        desc = e["description"]

        if desc:
            desc += "\n\n"

        desc += (
            "Kategorie: "
            + e["category"]
            + "\n\n"
            + e["url"]
        )

        lines.append(
            "SUMMARY:"
            + esc(e["summary"])
        )

        lines.append(
            "DESCRIPTION:"
            + esc(desc)
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
    items = []
    seen = set()

    for cid, category in CATEGORIES:
        try:
            found = parse_rss(
                get(
                    rss_url(cid),
                    15,
                    3,
                ),
                category,
            )

            print(
                "Kategorie:",
                category,
                "RSS:",
                len(found),
            )

            for item in found:
                if item["link"] not in seen:
                    seen.add(item["link"])
                    items.append(item)

        except Exception as e:
            print(
                "RSS FEHLER:",
                category,
                e,
            )

    print(
        "Eindeutige RSS-Veranstaltungen:",
        len(items),
    )

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

        for job in as_completed(jobs):
            result, error = job.result()

            if result:
                results.append(result)

            else:
                failures += 1

                print(
                    "FEHLER:",
                    jobs[job]["title"],
                    "-",
                    error,
                )

    unique = {
        (e["uid"], e["start"]): e
        for e in results
    }

    with open(
        OUT,
        "w",
        encoding="utf-8",
        newline="",
    ) as f:
        f.write(
            make_ics(
                list(unique.values())
            )
        )

    counts = {}

    for e in unique.values():
        counts[e["category"]] = (
            counts.get(
                e["category"],
                0,
            )
            + 1
        )

    print("FERTIG!")
    print(
        "Veranstaltungen geschrieben:",
        len(unique),
    )
    print(
        "Nicht verarbeitet:",
        failures,
    )

    for _, category in CATEGORIES:
        print(
            category + ":",
            counts.get(
                category,
                0,
            ),
        )


if __name__ == "__main__":
    main()