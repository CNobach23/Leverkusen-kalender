import gzip
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, time as dtime, timedelta
from zoneinfo import ZoneInfo


BASE = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
OUT = "veranstaltungen.ics"
GEO_CACHE_FILE = "geo_cache.json"

TZ = ZoneInfo("Europe/Berlin")

UA = (
    "LeverkusenWebCal/1.0 "
    "(https://github.com/CNobach23/Leverkusen-kalender)"
)

# Öffentlicher Nominatim-Dienst von OpenStreetMap.
# Bei regelmäßig automatisierten Abfragen maximal 4 Anfragen/Minute.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DELAY = 15.1


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


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

def get(url, timeout=20, tries=3, user_agent=UA):
    error = None

    for attempt in range(tries):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": user_agent,
                    "Connection": "close",
                    "Accept-Encoding": "gzip",
                },
            )

            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()

                # GitHub Actions bekommt die Leverkusen-Seiten teilweise
                # gzip-komprimiert. Ohne diese Zeile sieht Python nur
                # Binärdaten statt HTML.
                encoding = (
                    response.headers.get("Content-Encoding") or ""
                ).lower()

                if encoding == "gzip":
                    raw = gzip.decompress(raw)

                charset = (
                    response.headers.get_content_charset()
                    or "utf-8"
                )

                return raw.decode(charset, errors="replace")

        except Exception as exc:
            error = exc

            if attempt + 1 < tries:
                time.sleep(1)

    raise error


# ------------------------------------------------------------
# RSS
# ------------------------------------------------------------

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

    return BASE + "?" + urllib.parse.urlencode(params)


def parse_rss(text, category):
    root = ET.fromstring(text)
    events = []

    for item in root.findall(".//item"):
        values = {}

        for child in item:
            name = child.tag.split("}")[-1]

            values[name] = html.unescape(
                "".join(child.itertext()).strip()
            )

        if values.get("title") and values.get("link"):
            events.append(
                {
                    "title": values["title"],
                    "link": values["link"],
                    "description": values.get("description", ""),
                    "category": category,
                }
            )

    return events


# ------------------------------------------------------------
# iCalendar
# ------------------------------------------------------------

def unescape_ical(value):
    return (
        html.unescape(value or "")
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def ical_value(value):
    value = value.strip()

    if re.fullmatch(r"\d{8}", value):
        return (
            datetime.strptime(value, "%Y%m%d").date(),
            True,
        )

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

    event = {}
    inside_event = False

    for line in folded:

        if line == "BEGIN:VEVENT":
            inside_event = True
            event = {}

        elif line == "END:VEVENT":
            return event

        elif inside_event and ":" in line:
            left, value = line.split(":", 1)

            name = left.split(";", 1)[0].upper()

            event[name] = unescape_ical(value)

    return event


def page_ical_link(page):
    page = html.unescape(page)

    links = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        page,
        re.I,
    )

    for link in links:
        low = link.lower()

        if (
            "ical" in low
            or "termin-speichern" in low
        ):
            return urllib.parse.urljoin(
                BASE,
                link,
            )

    return None


# ------------------------------------------------------------
# JSON-LD
# ------------------------------------------------------------

def jsonld_event(page):
    pattern = (
        r'<script[^>]+type=["\']'
        r'application/ld\+json'
        r'["\'][^>]*>(.*?)</script>'
    )

    blocks = re.findall(
        pattern,
        page,
        re.I | re.S,
    )

    for raw in blocks:

        try:
            obj = json.loads(
                html.unescape(raw).strip()
            )

        except Exception:
            continue

        stack = (
            obj
            if isinstance(obj, list)
            else [obj]
        )

        while stack:

            current = stack.pop()

            if isinstance(current, dict):

                typ = current.get(
                    "@type",
                    "",
                )

                if isinstance(typ, list):
                    types = [
                        str(t).lower()
                        for t in typ
                    ]
                else:
                    types = [
                        str(typ).lower()
                    ]

                if (
                    "event" in types
                    or "eventseries" in types
                ):
                    return current

                stack.extend(
                    current.values()
                )

            elif isinstance(current, list):
                stack.extend(current)

    return None


def jsonld_location(data):
    if not isinstance(data, dict):
        return ""

    location = data.get(
        "location",
        "",
    )

    if isinstance(location, dict):

        name = location.get(
            "name",
            "",
        )

        address = location.get(
            "address",
            "",
        )

        if isinstance(address, dict):

            parts = [
                address.get(
                    "streetAddress",
                    "",
                ),
                address.get(
                    "postalCode",
                    "",
                ),
                address.get(
                    "addressLocality",
                    "",
                ),
            ]

            address = ", ".join(
                part
                for part in parts
                if part
            )

        if name and address:
            return f"{name}, {address}"

        return (
            name
            or address
            or ""
        )

    return str(location or "")


def jsonld_geo(data):
    if not isinstance(data, dict):
        return None

    geo = data.get("geo")

    if isinstance(geo, dict):

        try:
            latitude = float(
                geo.get("latitude")
            )

            longitude = float(
                geo.get("longitude")
            )

            if (
                -90 <= latitude <= 90
                and -180 <= longitude <= 180
            ):
                return (
                    latitude,
                    longitude,
                )

        except (
            TypeError,
            ValueError,
        ):
            pass

    return None


# ------------------------------------------------------------
# HTML
# ------------------------------------------------------------

def visible_text(page):
    text = html.unescape(page)

    text = re.sub(
        r"(?is)<(script|style|noscript).*?</\1>",
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


def extract_visible_location(page):
    text = visible_text(page)

    # Typische Ortsangabe mit anschließendem Stadtteil.
    patterns = [

        r"Veranstaltungsort\s+(.*?)(?=\s+"
        r"(?:Schlebusch|Opladen|Wiesdorf|Rheindorf|"
        r"Hitdorf|Bürrig|Küppersteg|Manfort|Quettingen|"
        r"Steinbüchel|Lützenkirchen|Alkenrath|"
        r"Bergisch Neukirchen)\b)",

        r"Veranstaltungsort\s+(.*?)(?=\s+\d{4,5}\s+Leverkusen)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I,
        )

        if match:

            value = re.sub(
                r"\s+",
                " ",
                match.group(1),
            ).strip(
                " ,.-"
            )

            if value:
                return value

    # Fallback: HTML-Bereich zwischen Veranstaltungsort
    # und Google Maps.
    match = re.search(
        r"Veranstaltungsort(.*?)"
        r"(?:Google Maps|Ähnliche Veranstaltungen)",
        page,
        re.I | re.S,
    )

    if match:

        chunk = visible_text(
            match.group(1)
        )

        chunk = re.sub(
            r"\s+",
            " ",
            chunk,
        ).strip()

        chunk = re.sub(
            r"^(?:\s*[:\-]\s*)",
            "",
            chunk,
        )

        if chunk:
            return chunk[:500]

    return ""


# ------------------------------------------------------------
# Deutsche Datums- und Zeitangaben
# ------------------------------------------------------------

def german_dates(text):

    month = (
        r"(Januar|Februar|März|Maerz|April|Mai|"
        r"Juni|Juli|August|September|Oktober|"
        r"November|Dezember)"
    )

    patterns = [

        rf"(\d{{1,2}})\.\s*[–-]\s*"
        rf"(\d{{1,2}})\.\s*{month}\s*(\d{{4}})",

        rf"(\d{{1,2}})\.\s*"
        rf"(?:bis|–|-)\s*"
        rf"(\d{{1,2}})\.\s*{month}\s*(\d{{4}})",

        rf"(\d{{1,2}})\.\s*"
        rf"{month}\s*(\d{{4}})",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I,
        )

        if not match:
            continue

        groups = match.groups()

        try:

            if len(groups) == 4:

                d1, d2, month_name, year = groups

                month_number = MONTHS[
                    month_name.lower()
                ]

                return (
                    date(
                        int(year),
                        month_number,
                        int(d1),
                    ),
                    date(
                        int(year),
                        month_number,
                        int(d2),
                    ),
                )

            d1, month_name, year = groups

            month_number = MONTHS[
                month_name.lower()
            ]

            day = date(
                int(year),
                month_number,
                int(d1),
            )

            return day, day

        except Exception:
            pass

    return None, None


def german_times(text):

    patterns = [

        r"(\d{1,2}:\d{2})\s*[–-]\s*"
        r"(\d{1,2}:\d{2})\s*Uhr",

        r"(\d{1,2}:\d{2})\s*"
        r"(?:bis|–|-)\s*"
        r"(\d{1,2}:\d{2})\s*Uhr",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I,
        )

        if match:
            return (
                match.group(1),
                match.group(2),
            )

    match = re.search(
        r"(?:ab\s+|Start\s*:\s*)?"
        r"(\d{1,2}:\d{2})\s*Uhr",
        text,
        re.I,
    )

    if match:
        return (
            match.group(1),
            None,
        )

    return None, None


def make_local(day, hhmm):

    if not hhmm:
        return datetime.combine(
            day,
            dtime.min,
        ).replace(
            tzinfo=TZ
        )

    hour, minute = map(
        int,
        hhmm.split(":"),
    )

    return datetime.combine(
        day,
        dtime(
            hour,
            minute,
        ),
    ).replace(
        tzinfo=TZ
    )


def parse_page_dates(
    page,
    fallback_text="",
):

    text = visible_text(page)

    start_date, end_date = german_dates(
        text
    )

    if (
        start_date is None
        and fallback_text
    ):
        start_date, end_date = german_dates(
            visible_text(
                fallback_text
            )
        )

    if start_date is None:
        return None, None, False

    start_time, end_time = german_times(
        text
    )

    if (
        not start_time
        and fallback_text
    ):
        start_time, end_time = german_times(
            visible_text(
                fallback_text
            )
        )

    if not start_time:
        return (
            start_date,
            end_date,
            True,
        )

    start = make_local(
        start_date,
        start_time,
    )

    if end_date > start_date:

        end = make_local(
            end_date,
            end_time or start_time,
        )

    elif end_time:

        end = make_local(
            start_date,
            end_time,
        )

    else:

        end = start + timedelta(
            hours=1
        )

    if end <= start:
        end = start + timedelta(
            hours=1
        )

    return (
        start,
        end,
        False,
    )


# ------------------------------------------------------------
# URL-Datum als letzte Reserve
# ------------------------------------------------------------

def url_fallback(item):

    match = re.search(
        r"/(20\d{2})-(\d{2})-(\d{2})"
        r"(?:-(\d{2})-(\d{2}))?/?$",
        item["link"],
    )

    if not match:
        return None, None, False

    year, month, day = map(
        int,
        match.group(1, 2, 3),
    )

    if match.group(4):

        start = datetime(
            year,
            month,
            day,
            int(match.group(4)),
            int(match.group(5)),
        )

        return (
            start,
            start + timedelta(hours=1),
            False,
        )

    start = date(
        year,
        month,
        day,
    )

    return (
        start,
        start,
        True,
    )


# ------------------------------------------------------------
# Einzelne Veranstaltung
# ------------------------------------------------------------

def fetch_event(item):

    try:

        page = get(
            item["link"]
        )

        location = ""
        geo = None

        # ----------------------------------------------------
        # 1. Offizieller iCalendar-Datensatz
        # ----------------------------------------------------

        ical = page_ical_link(page)

        if ical:

            try:

                source = parse_ics(
                    get(
                        ical,
                        12,
                        2,
                    )
                )

                if "DTSTART" in source:

                    start, all_day = ical_value(
                        source["DTSTART"]
                    )

                    end = start

                    if "DTEND" in source:
                        end, _ = ical_value(
                            source["DTEND"]
                        )

                    if (
                        not all_day
                        and isinstance(
                            start,
                            datetime,
                        )
                        and isinstance(
                            end,
                            datetime,
                        )
                        and end <= start
                    ):
                        end = start + timedelta(
                            hours=1
                        )

                    location = source.get(
                        "LOCATION",
                        "",
                    )

                    # Falls die Quelle selbst GEO liefert.
                    if source.get("GEO"):

                        try:

                            latitude, longitude = (
                                source["GEO"].split(
                                    ";",
                                    1,
                                )
                            )

                            geo = (
                                float(latitude),
                                float(longitude),
                            )

                        except Exception:
                            pass

                    if not location:
                        location = (
                            extract_visible_location(
                                page
                            )
                        )

                    return {
                        "uid": item["link"],
                        "start": start,
                        "end": end,
                        "all_day": all_day,
                        "summary": (
                            source.get(
                                "SUMMARY"
                            )
                            or item["title"]
                        ),
                        "description": (
                            source.get(
                                "DESCRIPTION"
                            )
                            or item["description"]
                        ),
                        "location": location,
                        "geo": geo,
                        "url": (
                            source.get("URL")
                            or item["link"]
                        ),
                        "category": item["category"],
                    }, None

            except Exception:
                pass

        # ----------------------------------------------------
        # 2. JSON-LD
        # ----------------------------------------------------

        data = jsonld_event(page)

        if (
            data
            and data.get("startDate")
        ):

            start, all_day = parse_iso(
                data.get("startDate")
            )

            end, _ = parse_iso(
                data.get("endDate")
            )

            if start is not None:

                if (
                    end is None
                    or (
                        not all_day
                        and end <= start
                    )
                ):
                    end = (
                        start + timedelta(hours=1)
                        if not all_day
                        else start
                    )

                location = (
                    jsonld_location(data)
                    or extract_visible_location(
                        page
                    )
                )

                geo = jsonld_geo(data)

                return {
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
                    "location": location,
                    "geo": geo,
                    "url": (
                        data.get("url")
                        or item["link"]
                    ),
                    "category": item["category"],
                }, None

        # ----------------------------------------------------
        # 3. Sichtbarer deutscher Veranstaltungstext
        # ----------------------------------------------------

        start, end, all_day = parse_page_dates(
            page,
            item["description"],
        )

        if start is not None:

            return {
                "uid": item["link"],
                "start": start,
                "end": end,
                "all_day": all_day,
                "summary": item["title"],
                "description": item["description"],
                "location": extract_visible_location(
                    page
                ),
                "geo": None,
                "url": item["link"],
                "category": item["category"],
            }, None

        # ----------------------------------------------------
        # 4. Datum aus URL
        # ----------------------------------------------------

        start, end, all_day = url_fallback(
            item
        )

        if start is not None:

            return {
                "uid": item["link"],
                "start": start,
                "end": end,
                "all_day": all_day,
                "summary": item["title"],
                "description": item["description"],
                "location": extract_visible_location(
                    page
                ),
                "geo": None,
                "url": item["link"],
                "category": item["category"],
            }, None

        return None, "kein Datum gefunden"

    except Exception as exc:
        return None, str(exc)


# ------------------------------------------------------------
# ISO-Datum
# ------------------------------------------------------------

def parse_iso(value):

    if not value:
        return None, False

    value = str(value).strip()

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
        return None, False


# ------------------------------------------------------------
# GEO-Cache
# ------------------------------------------------------------

def load_geo_cache():

    try:

        with open(
            GEO_CACHE_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        return (
            data
            if isinstance(data, dict)
            else {}
        )

    except (
        FileNotFoundError,
        json.JSONDecodeError,
    ):
        return {}


def save_geo_cache(cache):

    with open(
        GEO_CACHE_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            cache,
            file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        file.write("\n")


def normalize_location(location):

    return re.sub(
        r"\s+",
        " ",
        location or "",
    ).strip()


def geocode_location(
    location,
    cache,
):

    key = normalize_location(
        location
    )

    if not key:
        return None

    # Bereits vorhandener Cache-Eintrag.
    if key in cache:

        value = cache[key]

        if (
            isinstance(value, list)
            and len(value) == 2
        ):

            try:
                return (
                    float(value[0]),
                    float(value[1]),
                )

            except (
                TypeError,
                ValueError,
            ):
                return None

        return None

    query = key

    if "Leverkusen" not in query:
        query += (
            ", Leverkusen, Deutschland"
        )

    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "jsonv2",
            "limit": "1",
            "countrycodes": "de",
            "accept-language": "de",
        }
    )

    url = (
        NOMINATIM_URL
        + "?"
        + params
    )

    try:

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Connection": "close",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:

            raw = response.read()

            charset = (
                response.headers.get_content_charset()
                or "utf-8"
            )

            data = json.loads(
                raw.decode(
                    charset,
                    errors="replace",
                )
            )

        if data:

            latitude = float(
                data[0]["lat"]
            )

            longitude = float(
                data[0]["lon"]
            )

            if (
                -90 <= latitude <= 90
                and -180 <= longitude <= 180
            ):

                cache[key] = [
                    round(latitude, 6),
                    round(longitude, 6),
                ]

                return (
                    latitude,
                    longitude,
                )

        # Auch erfolglose Suchen speichern,
        # damit sie nicht jeden Tag wiederholt werden.
        cache[key] = None

        return None

    except Exception as exc:

        print(
            "GEO FEHLER:",
            key,
            "-",
            exc,
        )

        return None


def add_missing_geo(events):

    cache = load_geo_cache()

    missing = []
    seen = set()

    source_geo = 0

    for event in events:

        if event.get("geo"):
            source_geo += 1
            continue

        key = normalize_location(
            event.get(
                "location",
                "",
            )
        )

        if (
            key
            and key not in cache
            and key not in seen
        ):
            seen.add(key)
            missing.append(key)

    cache_geo = 0

    for event in events:

        if event.get("geo"):
            continue

        key = normalize_location(
            event.get(
                "location",
                "",
            )
        )

        if (
            key
            and key in cache
            and isinstance(
                cache[key],
                list,
            )
        ):
            cache_geo += 1

    print(
        "GEO bereits aus Quelle:",
        source_geo,
    )

    print(
        "GEO bereits im Cache:",
        cache_geo,
    )

    print(
        "Neue GEO-Abfragen:",
        len(missing),
    )

    cache_changed = False

    for index, location in enumerate(
        missing,
        1,
    ):

        print(
            f"  GEO [{index}/{len(missing)}]: "
            f"{location}"
        )

        geo = geocode_location(
            location,
            cache,
        )

        cache_changed = True

        if geo:

            print(
                "      -> "
                f"{geo[0]:.6f};"
                f"{geo[1]:.6f}"
            )

        else:

            print(
                "      -> keine "
                "Koordinaten gefunden"
            )

        if index < len(missing):
            time.sleep(
                NOMINATIM_DELAY
            )

    if cache_changed:
        save_geo_cache(cache)

    # Cache auf die Veranstaltungen anwenden.
    for event in events:

        if event.get("geo"):
            continue

        key = normalize_location(
            event.get(
                "location",
                "",
            )
        )

        if not key:
            continue

        value = cache.get(key)

        if (
            isinstance(value, list)
            and len(value) == 2
        ):

            try:

                event["geo"] = (
                    float(value[0]),
                    float(value[1]),
                )

            except (
                TypeError,
                ValueError,
            ):
                pass


# ------------------------------------------------------------
# iCalendar-Ausgabe
# ------------------------------------------------------------

def esc(value):

    return (
        html.unescape(
            str(value or "")
        )
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def fold_ics_line(
    line,
    limit=73,
):

    chunks = []
    current = ""

    for char in line:

        if (
            len(
                (current + char)
                .encode("utf-8")
            )
            > limit
        ):

            chunks.append(current)
            current = char

        else:
            current += char

    chunks.append(current)

    return chunks[0] + "".join(
        "\r\n " + chunk
        for chunk in chunks[1:]
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

        lines.extend(
            [
                "BEGIN:VEVENT",
                "UID:" + esc(
                    event["uid"]
                ),
                "DTSTAMP:" + stamp,
            ]
        )

        if event["all_day"]:

            lines.append(
                "DTSTART;VALUE=DATE:"
                + event["start"].strftime(
                    "%Y%m%d"
                )
            )

            # DTEND bei ganztägigen Ereignissen
            # ist im iCalendar-Standard exklusiv.
            lines.append(
                "DTEND;VALUE=DATE:"
                + (
                    event["end"]
                    + timedelta(days=1)
                ).strftime(
                    "%Y%m%d"
                )
            )

        else:

            start = (
                event["start"]
                if event["start"].tzinfo
                else event["start"].replace(
                    tzinfo=TZ
                )
            )

            end = (
                event["end"]
                if event["end"].tzinfo
                else event["end"].replace(
                    tzinfo=TZ
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

        description = (
            event["description"]
            or ""
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
            "SUMMARY:"
            + esc(event["summary"])
        )

        lines.append(
            "DESCRIPTION:"
            + esc(description)
        )

        if event.get("location"):

            lines.append(
                "LOCATION:"
                + esc(
                    event["location"]
                )
            )

        # Das ist der entscheidende neue Eintrag:
        # LATITUDE;LONGITUDE
        if event.get("geo"):

            latitude, longitude = (
                event["geo"]
            )

            lines.append(
                f"GEO:{latitude:.6f};"
                f"{longitude:.6f}"
            )

        lines.append(
            "URL:"
            + esc(event["url"])
        )

        lines.append(
            "CATEGORIES:"
            + esc(event["category"])
        )

        lines.append(
            "END:VEVENT"
        )

    lines.append(
        "END:VCALENDAR"
    )

    return "\r\n".join(
        fold_ics_line(line)
        for line in lines
    ) + "\r\n"


# ------------------------------------------------------------
# Hauptprogramm
# ------------------------------------------------------------

def main():

    items = []
    seen = set()

    # --------------------------------------------------------
    # RSS aller 11 Kategorien
    # --------------------------------------------------------

    for category_id, category in CATEGORIES:

        try:

            found = parse_rss(
                get(
                    rss_url(category_id),
                    20,
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

                    seen.add(
                        item["link"]
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

    print("=" * 80)

    print(
        "EINDEUTIGE VERANSTALTUNGEN:",
        len(items),
    )

    print("=" * 80)

    # --------------------------------------------------------
    # Veranstaltungen parallel abrufen
    # --------------------------------------------------------

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

                results.append(
                    result
                )

                print(
                    "[OK]",
                    result["summary"],
                )

                if result.get("location"):
                    print(
                        "    Ort:",
                        result["location"],
                    )

                if result.get("geo"):
                    print(
                        "    GEO:"
                        f"{result['geo'][0]:.6f};"
                        f"{result['geo'][1]:.6f}"
                    )

            else:

                failures += 1

                print(
                    "FEHLER:",
                    jobs[job]["title"],
                    "-",
                    error,
                )

    # --------------------------------------------------------
    # Doppelte Veranstaltungen entfernen
    # --------------------------------------------------------

    unique = {
        (
            event["uid"],
            event["start"],
        ): event
        for event in results
    }

    events = list(
        unique.values()
    )

    # --------------------------------------------------------
    # GEO ergänzen
    # --------------------------------------------------------

    print("=" * 80)
    print("GEO KOORDINATEN")
    print("=" * 80)

    add_missing_geo(
        events
    )

    # --------------------------------------------------------
    # ICS schreiben
    # --------------------------------------------------------

    with open(
        OUT,
        "w",
        encoding="utf-8",
        newline="",
    ) as file:

        file.write(
            make_ics(events)
        )

    # --------------------------------------------------------
    # Statistik
    # --------------------------------------------------------

    counts = {}
    geo_count = 0

    for event in events:

        category = event["category"]

        counts[category] = (
            counts.get(
                category,
                0,
            )
            + 1
        )

        if event.get("geo"):
            geo_count += 1

    print("=" * 80)
    print("FERTIG!")
    print("=" * 80)

    print(
        "Veranstaltungen geschrieben:",
        len(events),
    )

    print(
        "Nicht verarbeitet:",
        failures,
    )

    print(
        "Mit GEO:",
        geo_count,
    )

    print(
        "Ohne GEO:",
        len(events) - geo_count,
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
        "Datei:",
        OUT,
    )

    print(
        "GEO-Cache:",
        GEO_CACHE_FILE,
    )


if __name__ == "__main__":
    main()