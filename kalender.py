import gzip
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


# ============================================================
# EINSTELLUNGEN
# ============================================================

TZ = ZoneInfo("Europe/Berlin")

RSS_BASE = (
    "https://www.leverkusen.de/"
    "stadt-erleben/veranstaltungskalender/index.php"
)

ICS_FILE = Path("veranstaltungen.ics")
GEO_CACHE_FILE = Path("geo_cache.json")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

USER_AGENT = (
    "Leverkusen-Kalender/1.0 "
    "(https://github.com/CNobach23/Leverkusen-kalender)"
)

# Nominatim maximal 1 Anfrage pro Sekunde.
# Durch den Cache werden die meisten Anfragen vermieden.
GEOCODE_DELAY = 15.0


# ============================================================
# KATEGORIEN
# ============================================================

CATEGORIES = {
    "Familie & Kinder": 42023,
    "Feste & Brauchtum": 42081,
    "Führungen & Touren": 42030,
    "Festivals & Open-Airs": 42082,
    "Karneval": 42078,
    "Lesungen & Literatur": 42029,
    "Märkte & Messen": 42032,
    "Partys & Nachtleben": 42036,
    "Spitzensport": 42031,
    "Tipps": 42037,
    "Warntage": 34821,
}


# ============================================================
# HTTP
# ============================================================

def http_get(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml,text/xml,text/calendar,*/*"
            ),
            "Accept-Encoding": "gzip",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read()
        encoding = response.headers.get("Content-Encoding", "")

        if "gzip" in encoding.lower():
            try:
                data = gzip.decompress(data)
            except Exception:
                pass

        return data


def get_text(url, timeout=30):
    data = http_get(url, timeout)

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(
            "iso-8859-1",
            errors="replace"
        )


# ============================================================
# HILFSFUNKTIONEN
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = html.unescape(str(value))

    value = re.sub(
        r"<br\s*/?>",
        "\n",
        value,
        flags=re.I
    )

    value = re.sub(
        r"</p\s*>",
        "\n",
        value,
        flags=re.I
    )

    value = re.sub(
        r"<[^>]+>",
        " ",
        value
    )

    value = value.replace(
        "\xa0",
        " "
    )

    value = re.sub(
        r"[ \t]+",
        " ",
        value
    )

    value = re.sub(
        r"\n[ \t]+",
        "\n",
        value
    )

    return value.strip()


def esc(value):
    """
    iCalendar-Escaping.
    """

    value = html.unescape(
        str(value or "")
    )

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


def fold_ics_line(line, limit=75):
    """
    RFC-5545-kompatibles Zeilen-Folding.
    """

    result = []

    current = ""

    for char in line:

        if (
            len(current.encode("utf-8"))
            + len(char.encode("utf-8"))
            > limit
        ):
            result.append(current)
            current = " " + char

        else:
            current += char

    if current:
        result.append(current)

    return "\r\n".join(result)


def parse_iso(value):

    if not value:
        return None, False

    value = str(value).strip()

    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}",
        value
    ):
        try:
            return (
                datetime.strptime(
                    value,
                    "%Y-%m-%d"
                ).date(),
                True
            )
        except Exception:
            return None, False

    try:

        value2 = value.replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(
            value2
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=TZ
            )

        return dt, False

    except Exception:
        return None, False


def sort_key(event):

    value = event["start"]

    if (
        isinstance(value, date)
        and not isinstance(value, datetime)
    ):

        value = datetime.combine(
            value,
            datetime.min.time(),
            tzinfo=TZ
        )

    elif value.tzinfo is None:

        value = value.replace(
            tzinfo=TZ
        )

    return value.astimezone(
        timezone.utc
    ).timestamp()


# ============================================================
# RSS
# ============================================================

def build_rss_url(category_id):

    today = date.today().isoformat()

    params = {
        "sp:categories[13495][0]": "-",
        "sp:categories[13495][1]": "__last__",

        "sp:categories[13459][0]": str(
            category_id
        ),
        "sp:categories[13459][1]": "__last__",

        "sp:dateFrom[0]": today,
        "sp:dateTo[0]": "",

        "sp:fulltext[0]": "",

        "sp:out": "rss",

        "sp:cmp": "eventSearch-1-0-searchResult",

        "action": "submit",
    }

    return (
        RSS_BASE
        + "?"
        + urllib.parse.urlencode(params)
    )


def parse_rss(xml_text, category):

    root = ET.fromstring(
        xml_text
    )

    items = []

    for item in root.iter():

        tag = item.tag.split(
            "}"
        )[-1]

        if tag != "item":
            continue

        values = {}

        for child in item:

            child_tag = child.tag.split(
                "}"
            )[-1]

            text = child.text or ""

            if child_tag not in values:
                values[child_tag] = text

        title = clean_text(
            values.get(
                "title",
                ""
            )
        )

        link = clean_text(
            values.get(
                "link",
                ""
            )
        )

        description = clean_text(
            values.get(
                "description",
                ""
            )
        )

        if not link or not title:
            continue

        items.append(
            {
                "title": title,
                "link": link,
                "description": description,
                "category": category,
            }
        )

    return items


def fetch_category(category, category_id):

    url = build_rss_url(
        category_id
    )

    print()
    print(
        f"Kategorie: {category}"
    )

    try:

        xml_text = get_text(
            url
        )

        items = parse_rss(
            xml_text,
            category
        )

        print(
            f"  RSS: {len(items)}"
        )

        return items

    except Exception as exc:

        print(
            f"  RSS FEHLER: {exc}"
        )

        return []


# ============================================================
# iCAL DES OFFIZIELLEN LEVERKUSENER TERMINS
# ============================================================

def unfold_ics(text):

    text = text.replace(
        "\r\n",
        "\n"
    ).replace(
        "\r",
        "\n"
    )

    lines = text.split(
        "\n"
    )

    result = []

    for line in lines:

        if (
            line.startswith(
                (" ", "\t")
            )
            and result
        ):

            result[-1] += line[1:]

        else:

            result.append(
                line
            )

    return result


def parse_ics(text):

    values = {}

    for line in unfold_ics(
        text
    ):

        if ":" not in line:
            continue

        left, value = line.split(
            ":",
            1
        )

        property_name = (
            left.split(
                ";",
                1
            )[0]
            .upper()
        )

        if property_name not in values:
            values[property_name] = value

    return values


def parse_ics_date(value):

    if not value:
        return None, False

    value = value.strip()

    # Ganztägiger Termin
    if re.fullmatch(
        r"\d{8}",
        value
    ):

        try:

            return (
                datetime.strptime(
                    value,
                    "%Y%m%d"
                ).date(),
                True
            )

        except Exception:
            return None, False

    # UTC
    if value.endswith("Z"):

        try:

            dt = datetime.strptime(
                value,
                "%Y%m%dT%H%M%SZ"
            ).replace(
                tzinfo=timezone.utc
            )

            return dt, False

        except Exception:
            return None, False

    # Lokale Zeit
    for fmt in (
        "%Y%m%dT%H%M%S",
        "%Y%m%dT%H%M",
    ):

        try:

            dt = datetime.strptime(
                value,
                fmt
            )

            dt = dt.replace(
                tzinfo=TZ
            )

            return dt, False

        except Exception:
            pass

    return None, False


# ============================================================
# iCAL-LINK AUF DER LEVERKUSENER EVENT-SEITE
# ============================================================

def find_ical_link(page):

    page2 = html.unescape(
        page
    )

    matches = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        page2,
        flags=re.I
    )

    for href in matches:

        href = href.strip()

        if (
            "iCalFromParameters"
            not in href
        ):
            continue

        return urllib.parse.urljoin(
            "https://www.leverkusen.de/",
            href
        )

    match = re.search(
        r'https?://[^"\'>\s]*'
        r'iCalFromParameters[^"\'>\s]*',
        page2,
        flags=re.I
    )

    if match:

        return html.unescape(
            match.group(0)
        )

    return None


# ============================================================
# JSON-LD
# ============================================================

def find_jsonld_events(page):

    result = []

    scripts = re.findall(
        r'<script[^>]+type=["\']'
        r'application/ld\+json'
        r'["\'][^>]*>'
        r'(.*?)</script>',
        page,
        flags=re.I | re.S
    )

    for raw in scripts:

        raw = html.unescape(
            raw
        ).strip()

        if not raw:
            continue

        try:

            data = json.loads(
                raw
            )

        except Exception:
            continue

        objects = []

        if isinstance(
            data,
            list
        ):

            objects.extend(
                data
            )

        elif isinstance(
            data,
            dict
        ):

            if isinstance(
                data.get("@graph"),
                list
            ):

                objects.extend(
                    data["@graph"]
                )

            else:

                objects.append(
                    data
                )

        for obj in objects:

            if not isinstance(
                obj,
                dict
            ):
                continue

            event_type = obj.get(
                "@type"
            )

            if event_type == "Event":

                result.append(
                    obj
                )

            elif (
                isinstance(
                    event_type,
                    list
                )
                and "Event"
                in event_type
            ):

                result.append(
                    obj
                )

    return result


def jsonld_event(page):

    events = find_jsonld_events(
        page
    )

    if not events:
        return None

    return events[0]


def jsonld_location(data):

    if not isinstance(
        data,
        dict
    ):
        return ""

    loc = data.get(
        "location"
    )

    if isinstance(
        loc,
        str
    ):

        return clean_text(
            loc
        )

    if isinstance(
        loc,
        dict
    ):

        name = clean_text(
            loc.get(
                "name",
                ""
            )
        )

        address = loc.get(
            "address"
        )

        if isinstance(
            address,
            dict
        ):

            parts = []

            for key in (
                "streetAddress",
                "postalCode",
                "addressLocality",
            ):

                value = clean_text(
                    address.get(
                        key,
                        ""
                    )
                )

                if value:
                    parts.append(
                        value
                    )

            if name and parts:

                return (
                    name
                    + ", "
                    + ", ".join(parts)
                )

            if name:
                return name

            return ", ".join(
                parts
            )

        if name:
            return name

    return ""


def jsonld_geo(data):

    if not isinstance(
        data,
        dict
    ):
        return None

    loc = data.get(
        "location"
    )

    if isinstance(
        loc,
        dict
    ):

        geo = loc.get(
            "geo"
        )

        if isinstance(
            geo,
            dict
        ):

            lat = geo.get(
                "latitude"
            )

            lon = geo.get(
                "longitude"
            )

            try:

                if (
                    lat is not None
                    and lon is not None
                ):

                    return (
                        float(lat),
                        float(lon)
                    )

            except Exception:
                pass

    geo = data.get(
        "geo"
    )

    if isinstance(
        geo,
        dict
    ):

        try:

            return (
                float(
                    geo.get(
                        "latitude"
                    )
                ),
                float(
                    geo.get(
                        "longitude"
                    )
                )
            )

        except Exception:
            pass

    return None


# ============================================================
# SICHTBARE DATUMS-/ZEITANGABEN
# ============================================================

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


def parse_german_datetime(text):

    text = clean_text(
        text
    )

    pattern1 = re.search(
        r"\b(\d{1,2})\.\s*"
        r"(Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|"
        r"September|Oktober|November|Dezember)"
        r"\s+(\d{4})"
        r"(?:[^\d]{0,30})"
        r"(\d{1,2}):(\d{2})",
        text,
        flags=re.I
    )

    if pattern1:

        day = int(
            pattern1.group(1)
        )

        month_name = (
            pattern1.group(2)
            .lower()
        )

        year = int(
            pattern1.group(3)
        )

        hour = int(
            pattern1.group(4)
        )

        minute = int(
            pattern1.group(5)
        )

        month = MONTHS.get(
            month_name
        )

        if month:

            try:

                return datetime(
                    year,
                    month,
                    day,
                    hour,
                    minute,
                    tzinfo=TZ
                )

            except Exception:
                pass

    pattern2 = re.search(
        r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})"
        r"(?:[^\d]{0,30})"
        r"(\d{1,2}):(\d{2})",
        text
    )

    if pattern2:

        day = int(
            pattern2.group(1)
        )

        month = int(
            pattern2.group(2)
        )

        year = int(
            pattern2.group(3)
        )

        hour = int(
            pattern2.group(4)
        )

        minute = int(
            pattern2.group(5)
        )

        try:

            return datetime(
                year,
                month,
                day,
                hour,
                minute,
                tzinfo=TZ
            )

        except Exception:
            pass

    return None


def parse_page_dates(
    page,
    description=""
):

    text = clean_text(
        page
    )

    start = parse_german_datetime(
        text
    )

    if start is None:

        start = parse_german_datetime(
            description
        )

    if start is None:
        return None, None, False

    times = re.findall(
        r"\b(\d{1,2}):(\d{2})\s*Uhr",
        text,
        flags=re.I
    )

    if len(times) >= 2:

        hour = int(
            times[1][0]
        )

        minute = int(
            times[1][1]
        )

        end = start.replace(
            hour=hour,
            minute=minute
        )

        if end < start:
            end += timedelta(
                days=1
            )

        return (
            start,
            end,
            False
        )

    times2 = re.findall(
        r"\b(\d{1,2}):(\d{2})\b",
        text
    )

    if len(times2) >= 2:

        hour = int(
            times2[1][0]
        )

        minute = int(
            times2[1][1]
        )

        end = start.replace(
            hour=hour,
            minute=minute
        )

        if end < start:
            end += timedelta(
                days=1
            )

        return (
            start,
            end,
            False
        )

    end = start + timedelta(
        hours=1
    )

    return (
        start,
        end,
        False
    )


# ============================================================
# ORT AUS SICHTBAREM HTML
# ============================================================

def visible_location(page):

    text = clean_text(
        page
    )

    patterns = [

        r"Veranstaltungsort\s*"
        r"(.{5,250}?)(?="
        r"Google Maps|Anfahrt|Termin speichern|"
        r"Veranstalter|Weitere Informationen|$)",

        r"Veranstaltungsort\s*"
        r"[:\-]?\s*(.{5,250}?)(?="
        r"Google Maps|Anfahrt|Veranstalter|$)",

        r"Ort\s*[:\-]\s*(.{5,200}?)(?="
        r"Google Maps|Anfahrt|Veranstalter|$)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.I | re.S
        )

        if match:

            value = clean_text(
                match.group(1)
            )

            value = re.sub(
                r"\s+",
                " ",
                value
            ).strip(
                " -:;"
            )

            if value:
                return value

    return ""


# ============================================================
# ORT FÜR 149 LIVE
# ============================================================

def short_location(location):
    """
    WICHTIGER TEST:

    Wir verwenden jetzt NICHT mehr nur den kurzen
    Veranstaltungsnamen.

    Stattdessen wird die vollständige Adresse als
    LOCATION an 149 Live übergeben.

    Beispiel:

    Industriemuseum Freudenthaler Sensenhammer,
    Freudenthal 68, 51375 Leverkusen

    bleibt vollständig erhalten.

    Dadurch kann 149 Live versuchen, die Adresse
    selbst als Karten-/Ortseintrag zu erkennen.
    """

    location = clean_text(
        location
    )

    return location


# ============================================================
# GEO AUS iCAL
# ============================================================

def parse_geo(value):

    if not value:
        return None

    value = value.strip()

    match = re.match(
        r"^\s*(-?\d+(?:\.\d+)?)\s*;"
        r"\s*(-?\d+(?:\.\d+)?)\s*$",
        value
    )

    if match:

        try:

            return (
                float(
                    match.group(1)
                ),
                float(
                    match.group(2)
                )
            )

        except Exception:
            pass

    return None


# ============================================================
# NOMINATIM
# ============================================================

def load_geo_cache():

    if not GEO_CACHE_FILE.exists():
        return {}

    try:

        data = json.loads(
            GEO_CACHE_FILE.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(
            data,
            dict
        ):
            return data

    except Exception:
        pass

    return {}


def save_geo_cache(cache):

    GEO_CACHE_FILE.write_text(
        json.dumps(
            cache,
            ensure_ascii=False,
            indent=2,
            sort_keys=True
        ),
        encoding="utf-8"
    )


def geocode(
    location,
    cache
):

    location = clean_text(
        location
    )

    if not location:
        return None

    query = location

    if "Leverkusen" not in query:

        query += (
            ", Leverkusen, Deutschland"
        )

    cache_key = query

    if cache_key in cache:

        value = cache[
            cache_key
        ]

        try:

            return (
                float(
                    value["lat"]
                ),
                float(
                    value["lon"]
                )
            )

        except Exception:
            return None

    params = {
        "q": query,
        "format": "jsonv2",
        "limit": "1",
        "countrycodes": "de",
        "accept-language": "de",
    }

    url = (
        NOMINATIM_URL
        + "?"
        + urllib.parse.urlencode(
            params
        )
    )

    print(
        f"    Geocoding: {query}"
    )

    try:

        time.sleep(
            GEOCODE_DELAY
        )

        data = http_get(
            url,
            timeout=30
        )

        result = json.loads(
            data.decode(
                "utf-8"
            )
        )

        if not result:

            print(
                "    GEO nicht gefunden"
            )

            return None

        lat = float(
            result[0]["lat"]
        )

        lon = float(
            result[0]["lon"]
        )

        cache[cache_key] = {
            "lat": lat,
            "lon": lon,
        }

        save_geo_cache(
            cache
        )

        return (
            lat,
            lon
        )

    except Exception as exc:

        print(
            f"    GEO FEHLER: {exc}"
        )

        return None


# ============================================================
# EINZELNES EVENT
# ============================================================

def fetch_event(
    item,
    geo_cache
):

    try:

        page = get_text(
            item["link"]
        )

        # ----------------------------------------------------
        # 1. OFFIZIELLER iCAL
        # ----------------------------------------------------

        ical_link = find_ical_link(
            page
        )

        if ical_link:

            try:

                ical_text = get_text(
                    ical_link,
                    timeout=20
                )

                src = parse_ics(
                    ical_text
                )

                if "DTSTART" in src:

                    start, all_day = (
                        parse_ics_date(
                            src["DTSTART"]
                        )
                    )

                    end = None

                    if "DTEND" in src:

                        end, _ = (
                            parse_ics_date(
                                src["DTEND"]
                            )
                        )

                    if start is not None:

                        if end is None:

                            if all_day:

                                end = start

                            else:

                                end = (
                                    start
                                    + timedelta(
                                        hours=1
                                    )
                                )

                        location = (
                            src.get(
                                "LOCATION",
                                ""
                            )
                        )

                        geo = parse_geo(
                            src.get(
                                "GEO",
                                ""
                            )
                        )

                        return {
                            "uid": item["link"],
                            "start": start,
                            "end": end,
                            "all_day": all_day,

                            "summary": (
                                src.get(
                                    "SUMMARY"
                                )
                                or item["title"]
                            ),

                            "description": (
                                src.get(
                                    "DESCRIPTION"
                                )
                                or item["description"]
                            ),

                            "location": clean_text(
                                location
                            ),

                            "geo": geo,

                            "url": (
                                src.get(
                                    "URL"
                                )
                                or item["link"]
                            ),

                            "category": (
                                item["category"]
                            ),
                        }, None

            except Exception:
                pass

        # ----------------------------------------------------
        # 2. JSON-LD
        # ----------------------------------------------------

        data = jsonld_event(
            page
        )

        if data:

            start, all_day = (
                parse_iso(
                    data.get(
                        "startDate"
                    )
                )
            )

            end, _ = parse_iso(
                data.get(
                    "endDate"
                )
            )

            if start is not None:

                if end is None:

                    if all_day:

                        end = start

                    else:

                        end = (
                            start
                            + timedelta(
                                hours=1
                            )
                        )

                location = jsonld_location(
                    data
                )

                geo = jsonld_geo(
                    data
                )

                return {
                    "uid": item["link"],
                    "start": start,
                    "end": end,
                    "all_day": all_day,

                    "summary": (
                        data.get(
                            "name"
                        )
                        or item["title"]
                    ),

                    "description": (
                        data.get(
                            "description"
                        )
                        or item["description"]
                    ),

                    "location": location,
                    "geo": geo,

                    "url": (
                        data.get(
                            "url"
                        )
                        or item["link"]
                    ),

                    "category": (
                        item["category"]
                    ),
                }, None

        # ----------------------------------------------------
        # 3. SICHTBARES DATUM
        # ----------------------------------------------------

        start, end, all_day = (
            parse_page_dates(
                page,
                item["description"]
            )
        )

        if start is not None:

            location = visible_location(
                page
            )

            return {
                "uid": item["link"],
                "start": start,
                "end": end,
                "all_day": all_day,

                "summary": item["title"],
                "description": item["description"],

                "location": location,
                "geo": None,

                "url": item["link"],
                "category": item["category"],
            }, None

        # ----------------------------------------------------
        # 4. URL-FALLBACK
        # ----------------------------------------------------

        start, end, all_day = (
            url_fallback(
                item
            )
        )

        if start is not None:

            location = visible_location(
                page
            )

            return {
                "uid": item["link"],
                "start": start,
                "end": end,
                "all_day": all_day,

                "summary": item["title"],
                "description": item["description"],

                "location": location,
                "geo": None,

                "url": item["link"],
                "category": item["category"],
            }, None

        return (
            None,
            "kein Datum gefunden"
        )

    except Exception as exc:

        return (
            None,
            str(exc)
        )


# ============================================================
# URL-FALLBACK
# ============================================================

def url_fallback(item):

    url = item["link"]

    patterns = [
        r"/(\d{4})/(\d{2})/(\d{2})",

        r"-(\d{4})-(\d{2})-(\d{2})(?:/|$)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            url
        )

        if not match:
            continue

        try:

            year = int(
                match.group(1)
            )

            month = int(
                match.group(2)
            )

            day = int(
                match.group(3)
            )

            start = datetime(
                year,
                month,
                day,
                0,
                0,
                tzinfo=TZ
            )

            return (
                start,
                start + timedelta(
                    days=1
                ),
                True
            )

        except Exception:
            pass

    return (
        None,
        None,
        False
    )


# ============================================================
# DOPPELTE EVENTS ENTFERNEN
# ============================================================

def unique_events(events):

    result = {}
    category_sets = {}

    for event in events:

        uid = event["uid"]

        if uid not in result:

            result[uid] = event

            category_sets[uid] = {
                event["category"]
            }

        else:

            category_sets[uid].add(
                event["category"]
            )

            if (
                not result[uid].get(
                    "location"
                )
                and event.get(
                    "location"
                )
            ):

                result[uid]["location"] = (
                    event["location"]
                )

            if (
                not result[uid].get(
                    "geo"
                )
                and event.get(
                    "geo"
                )
            ):

                result[uid]["geo"] = (
                    event["geo"]
                )

    for uid, event in result.items():

        cats = sorted(
            category_sets[uid]
        )

        event["category"] = cats[0]

        event["categories"] = cats

    return list(
        result.values()
    )


# ============================================================
# ICS ERZEUGEN
# ============================================================

def make_ics(
    events,
    geo_cache
):

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    raw_lines = [
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
        key=sort_key
    ):

        raw_lines.extend(
            [
                "BEGIN:VEVENT",
                "UID:"
                + esc(
                    event["uid"]
                ),
                "DTSTAMP:"
                + stamp,
            ]
        )

        # ----------------------------------------------------
        # DATUM / ZEIT
        # ----------------------------------------------------

        if event["all_day"]:

            raw_lines.append(
                "DTSTART;VALUE=DATE:"
                + event["start"].strftime(
                    "%Y%m%d"
                )
            )

            end_date = event["end"]

            if isinstance(
                end_date,
                datetime
            ):

                end_date = (
                    end_date.date()
                )

            if end_date <= event["start"]:

                end_date = (
                    event["start"]
                    + timedelta(
                        days=1
                    )
                )

            raw_lines.append(
                "DTEND;VALUE=DATE:"
                + end_date.strftime(
                    "%Y%m%d"
                )
            )

        else:

            start = event["start"]

            if start.tzinfo is None:

                start = start.replace(
                    tzinfo=TZ
                )

            end = event["end"]

            if end.tzinfo is None:

                end = end.replace(
                    tzinfo=TZ
                )

            raw_lines.append(
                "DTSTART:"
                + start.astimezone(
                    timezone.utc
                ).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
            )

            raw_lines.append(
                "DTEND:"
                + end.astimezone(
                    timezone.utc
                ).strftime(
                    "%Y%m%dT%H%M%SZ"
                )
            )

        # ----------------------------------------------------
        # BESCHREIBUNG
        # ----------------------------------------------------

        description = clean_text(
            event.get(
                "description",
                ""
            )
        )

        if description:
            description += "\n\n"

        description += (
            "Kategorie: "
            + event["category"]
            + "\n\n"
            + event["url"]
        )

        # ----------------------------------------------------
        # TITEL
        # ----------------------------------------------------

        raw_lines.append(
            "SUMMARY:"
            + esc(
                event["summary"]
            )
        )

        raw_lines.append(
            "DESCRIPTION:"
            + esc(
                description
            )
        )

        # ----------------------------------------------------
        # LOCATION
        #
        # WICHTIG:
        # HIER wird jetzt die vollständige Adresse
        # ausgegeben.
        # ----------------------------------------------------

        full_location = clean_text(
            event.get(
                "location",
                ""
            )
        )

        if full_location:

            location_value = (
                short_location(
                    full_location
                )
            )

            if location_value:

                raw_lines.append(
                    "LOCATION:"
                    + esc(
                        location_value
                    )
                )

        # ----------------------------------------------------
        # GEO
        # ----------------------------------------------------

        geo = event.get(
            "geo"
        )

        if (
            geo is None
            and full_location
        ):

            geo = geocode(
                full_location,
                geo_cache
            )

            event["geo"] = geo

        if geo is not None:

            try:

                lat = float(
                    geo[0]
                )

                lon = float(
                    geo[1]
                )

                raw_lines.append(
                    "GEO:"
                    + f"{lat:.6f}"
                    + ";"
                    + f"{lon:.6f}"
                )

            except Exception:
                pass

        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        raw_lines.append(
            "URL:"
            + esc(
                event["url"]
            )
        )

        # ----------------------------------------------------
        # KATEGORIEN
        # ----------------------------------------------------

        categories = event.get(
            "categories",
            [event["category"]]
        )

        raw_lines.append(
            "CATEGORIES:"
            + esc(
                ";".join(
                    categories
                )
            )
        )

        raw_lines.append(
            "END:VEVENT"
        )

    raw_lines.append(
        "END:VCALENDAR"
    )

    folded = []

    for line in raw_lines:

        folded.append(
            fold_ics_line(
                line
            )
        )

    return (
        "\r\n".join(
            folded
        )
        + "\r\n"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=================================================="
    )

    print(
        " LEVERKUSEN KALENDER"
    )

    print(
        "=================================================="
    )

    # --------------------------------------------------------
    # GEO-CACHE
    # --------------------------------------------------------

    geo_cache = load_geo_cache()

    print(
        f"GEO-Cache: {len(geo_cache)} Einträge"
    )

    # --------------------------------------------------------
    # RSS-EVENTS EINLESEN
    # --------------------------------------------------------

    all_items = []

    for category, category_id in (
        CATEGORIES.items()
    ):

        items = fetch_category(
            category,
            category_id
        )

        all_items.extend(
            items
        )

    print()

    print(
        f"RSS-EINTRÄGE GESAMT: "
        f"{len(all_items)}"
    )

    # --------------------------------------------------------
    # DOPPELTE RSS-EINTRÄGE ENTFERNEN
    # --------------------------------------------------------

    unique_items = {}

    for item in all_items:

        unique_items[
            item["link"]
        ] = item

    items = list(
        unique_items.values()
    )

    print(
        f"UNIQUE RSS-EINTRÄGE: "
        f"{len(items)}"
    )

    # --------------------------------------------------------
    # EVENTS HOLEN
    # --------------------------------------------------------

    events = []

    errors = []

    for index, item in enumerate(
        items,
        start=1
    ):

        print()

        print(
            f"[{index}/{len(items)}] "
            f"{item['title']}"
        )

        event, error = fetch_event(
            item,
            geo_cache
        )

        if event is None:

            errors.append(
                (
                    item["title"],
                    error
                )
            )

            print(
                f"    [FEHLER] {error}"
            )

            continue

        # ----------------------------------------------------
        # FEHLENDER ORT
        # ----------------------------------------------------

        if not event.get(
            "location"
        ):

            try:

                page = get_text(
                    item["link"],
                    timeout=20
                )

                event["location"] = (
                    visible_location(
                        page
                    )
                )

            except Exception:
                pass

        # ----------------------------------------------------
        # GEO ERZEUGEN
        # ----------------------------------------------------

        geo = event.get(
            "geo"
        )

        if (
            geo is None
            and event.get(
                "location"
            )
        ):

            geo = geocode(
                event["location"],
                geo_cache
            )

            event["geo"] = geo

        # ----------------------------------------------------
        # AUSGABE
        # ----------------------------------------------------

        print(
            "    [OK]"
        )

        print(
            "    Ort: "
            + (
                event.get(
                    "location"
                )
                or "(kein Ort)"
            )
        )

        if event.get(
            "geo"
        ):

            print(
                "    GEO: "
                f"{event['geo'][0]:.6f};"
                f"{event['geo'][1]:.6f}"
            )

        else:

            print(
                "    GEO: nicht gefunden"
            )

        events.append(
            event
        )

    # --------------------------------------------------------
    # DUPLIKATE ENTFERNEN
    # --------------------------------------------------------

    events = unique_events(
        events
    )

    print()

    print(
        "=================================================="
    )

    print(
        f"EINDEUTIGE VERANSTALTUNGEN: "
        f"{len(events)}"
    )

    print(
        "=================================================="
    )

    # --------------------------------------------------------
    # ICS
    # --------------------------------------------------------

    ics = make_ics(
        events,
        geo_cache
    )

    ICS_FILE.write_text(
        ics,
        encoding="utf-8",
        newline=""
    )

    # Cache speichern
    save_geo_cache(
        geo_cache
    )

    print()

    print(
        f"Veranstaltungen geschrieben: "
        f"{len(events)}"
    )

    print(
        f"ICS-Datei: {ICS_FILE}"
    )

    print(
        f"GEO-Cache: {GEO_CACHE_FILE}"
    )

    # --------------------------------------------------------
    # FEHLER
    # --------------------------------------------------------

    print()

    if errors:

        print(
            f"Nicht verarbeitet: "
            f"{len(errors)}"
        )

        for title, error in errors:

            print(
                f"  - {title}: {error}"
            )

    else:

        print(
            "Nicht verarbeitet: 0"
        )

    print()

    print(
        "Fertig."
    )


if __name__ == "__main__":
    main()