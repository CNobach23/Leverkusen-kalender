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


# ============================================================
# GRUNDEINSTELLUNGEN
# ============================================================

BASE = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
OUT = "veranstaltungen.ics"

TZ = ZoneInfo("Europe/Berlin")

UA = "Mozilla/5.0 (LeverkusenKalender/9.0)"

CURRENT_YEAR = datetime.now(TZ).year


# ============================================================
# GEWÜNSCHTE KATEGORIEN
# ============================================================

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


# ============================================================
# MONATE
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


# ============================================================
# HTTP-ABRUF
# ============================================================

def get(url, timeout=15, tries=3):
    """
    Lädt eine URL.
    Unterstützt gzip-komprimierte Antworten.
    """

    err = None

    for n in range(tries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": UA,
                    "Connection": "close",
                    "Accept-Encoding": "gzip",
                },
            )

            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()

                encoding = (
                    r.headers.get("Content-Encoding", "")
                    .lower()
                    .strip()
                )

                # gzip über Header erkennen
                if encoding == "gzip":
                    raw = gzip.decompress(raw)

                # gzip zusätzlich über Magic Bytes erkennen
                elif raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)

                charset = (
                    r.headers.get_content_charset()
                    or "utf-8"
                )

                return raw.decode(
                    charset,
                    errors="replace"
                )

        except Exception as e:
            err = e

            if n + 1 < tries:
                time.sleep(1)

    raise err


# ============================================================
# RSS-URL
# ============================================================

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

    return (
        BASE
        + "?"
        + urllib.parse.urlencode(p)
    )


# ============================================================
# RSS AUSWERTEN
# ============================================================

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

            out.append({
                "title": vals["title"],
                "link": vals["link"],
                "description": vals.get(
                    "description",
                    ""
                ),
                "category": category,
            })

    return out


# ============================================================
# ICS ESCAPING / UNESCAPING
# ============================================================

def unescape(v):
    return (
        html.unescape(v or "")
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def esc(v):
    return (
        html.unescape(str(v or ""))
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


# ============================================================
# ICS DATUM AUSWERTEN
# ============================================================

def ical_value(value):

    value = value.strip()

    # Nur Datum
    if re.fullmatch(r"\d{8}", value):

        return (
            datetime.strptime(
                value,
                "%Y%m%d"
            ).date(),
            True
        )

    # UTC
    if value.endswith("Z"):

        return (
            datetime.strptime(
                value,
                "%Y%m%dT%H%M%SZ"
            ).replace(
                tzinfo=timezone.utc
            ),
            False
        )

    # Lokale Zeit
    return (
        datetime.strptime(
            value[:15],
            "%Y%m%dT%H%M%S"
        ),
        False
    )


# ============================================================
# ICS PARSEN
# ============================================================

def parse_ics(text):

    lines = (
        text
        .replace("\r\n", "\n")
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

            left, value = line.split(
                ":",
                1
            )

            name = left.split(
                ";",
                1
            )[0].upper()

            ev[name] = unescape(value)

    return ev


# ============================================================
# iCAL-LINK AUF DER VERANSTALTUNGSSEITE
# ============================================================

def page_ical_link(page):

    page = html.unescape(page)

    links = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        page,
        re.I
    )

    for link in links:

        low = link.lower()

        if (
            "ical" in low
            or "termin-speichern" in low
        ):
            return urllib.parse.urljoin(
                BASE,
                link
            )

    return None


# ============================================================
# JSON-LD VERANSTALTUNG FINDEN
# ============================================================

def jsonld_event(page):

    pattern = (
        r'<script[^>]+'
        r'type=["\']application/ld\+json["\']'
        r'[^>]*>(.*?)</script>'
    )

    blocks = re.findall(
        pattern,
        page,
        re.I | re.S
    )

    for raw in blocks:

        try:

            obj = json.loads(
                html.unescape(
                    raw
                ).strip()
            )

        except Exception:
            continue

        stack = (
            obj
            if isinstance(obj, list)
            else [obj]
        )

        while stack:

            x = stack.pop()

            if isinstance(x, dict):

                typ = x.get(
                    "@type",
                    ""
                )

                types = (
                    [
                        str(t).lower()
                        for t in typ
                    ]
                    if isinstance(typ, list)
                    else [
                        str(typ).lower()
                    ]
                )

                if (
                    "event" in types
                    or "eventseries" in types
                ):
                    return x

                stack.extend(
                    x.values()
                )

            elif isinstance(x, list):

                stack.extend(x)

    return None


# ============================================================
# SICHTBAREN TEXT ERZEUGEN
# ============================================================

def visible_text(page):

    s = html.unescape(page)

    s = re.sub(
        r"(?is)<(script|style|noscript).*?</\1>",
        " ",
        s
    )

    s = re.sub(
        r"(?is)<[^>]+>",
        " ",
        s
    )

    s = re.sub(
        r"\s+",
        " ",
        s
    )

    return s.strip()


# ============================================================
# VERANSTALTUNGSORT AUS JSON-LD
# ============================================================

def location_from_jsonld(data):

    if not data:
        return ""

    loc = data.get(
        "location",
        ""
    )

    if isinstance(loc, list):

        if loc:
            loc = loc[0]

        else:
            loc = ""

    # location ist Objekt
    if isinstance(loc, dict):

        name = str(
            loc.get(
                "name",
                ""
            )
            or ""
        ).strip()

        address = loc.get(
            "address",
            ""
        )

        if isinstance(address, dict):

            street = str(
                address.get(
                    "streetAddress",
                    ""
                )
                or ""
            ).strip()

            postal = str(
                address.get(
                    "postalCode",
                    ""
                )
                or ""
            ).strip()

            city = str(
                address.get(
                    "addressLocality",
                    ""
                )
                or ""
            ).strip()

            parts = []

            if name:
                parts.append(name)

            if street:
                parts.append(street)

            if postal or city:

                city_part = " ".join(
                    x
                    for x in [
                        postal,
                        city
                    ]
                    if x
                )

                if city_part:
                    parts.append(
                        city_part
                    )

            if parts:
                return ", ".join(parts)

        if name:
            return name

    if isinstance(loc, str):

        return loc.strip()

    return ""


# ============================================================
# VERANSTALTUNGSORT AUS SICHTBAREM TEXT
# ============================================================

def location_from_page(page):

    text = visible_text(page)

    # Normalfall:
    #
    # Veranstaltungsort
    # Name des Veranstaltungsortes
    # Adresse
    #
    # Danach folgt normalerweise ein anderer Abschnitt.

    patterns = [

        r"\bVeranstaltungsort\b\s*"
        r"(.{2,400}?)"
        r"(?=\s+"
        r"(?:Veranstalter|"
        r"Termin-Details|"
        r"Ähnliche Veranstaltungen|"
        r"Kosten|"
        r"Kontakt)\b)",

        r"\bOrt\b\s*"
        r"(.{2,300}?)"
        r"(?=\s+"
        r"(?:Veranstalter|"
        r"Termin-Details|"
        r"Ähnliche Veranstaltungen)\b)",
    ]

    for pattern in patterns:

        m = re.search(
            pattern,
            text,
            re.I
        )

        if not m:
            continue

        result = re.sub(
            r"\s+",
            " ",
            m.group(1)
        ).strip()

        result = result.strip(
            " :-–—"
        )

        if result:
            return result[:400]

    return ""


# ============================================================
# BESTEN VERANSTALTUNGSORT ERMITTELN
# ============================================================

def extract_location(page, jsonld=None):

    # 1. JSON-LD mit Adresse
    loc = location_from_jsonld(
        jsonld
    )

    if loc:
        return loc

    # 2. Sichtbarer Veranstaltungsort
    loc = location_from_page(
        page
    )

    if loc:
        return loc

    return ""


# ============================================================
# DEUTSCHE DATUMSERKENNUNG
# ============================================================

def german_dates(text):

    month = (
        r"(Januar|Februar|März|Maerz|"
        r"April|Mai|Juni|Juli|August|"
        r"September|Oktober|November|Dezember)"
    )

    patterns = [

        # 6.–8. September 2026
        rf"(\d{{1,2}})\.\s*[–-]\s*"
        rf"(\d{{1,2}})\.\s*{month}\s*(\d{{4}})",

        # 6. bis 8. September 2026
        rf"(\d{{1,2}})\.\s*"
        rf"(?:bis|–|-)\s*"
        rf"(\d{{1,2}})\.\s*{month}\s*(\d{{4}})",

        # 6. September 2026
        rf"(\d{{1,2}})\.\s*"
        rf"{month}\s*(\d{{4}})",
    ]

    for pat in patterns:

        m = re.search(
            pat,
            text,
            re.I
        )

        if not m:
            continue

        groups = m.groups()

        try:

            if len(groups) == 4:

                d1, d2, mon, year = groups

                mo = MONTHS[
                    mon.lower()
                ]

                return (
                    date(
                        int(year),
                        mo,
                        int(d1)
                    ),
                    date(
                        int(year),
                        mo,
                        int(d2)
                    )
                )

            d1, mon, year = groups

            mo = MONTHS[
                mon.lower()
            ]

            d = date(
                int(year),
                mo,
                int(d1)
            )

            return d, d

        except Exception:
            pass

    return None, None


# ============================================================
# DEUTSCHE ZEITERKENNUNG
# ============================================================

def german_times(text):

    # Explizite Zeitspanne bevorzugen.
    patterns = [

        r"(\d{1,2}:\d{2})\s*"
        r"[–-]\s*"
        r"(\d{1,2}:\d{2})\s*Uhr",

        r"(\d{1,2}:\d{2})\s*"
        r"(?:bis|–|-)\s*"
        r"(\d{1,2}:\d{2})\s*Uhr",

        # "Zeit: 14:15 – 15:15"
        r"(?:Zeit|Uhrzeit)\s*:\s*"
        r"(\d{1,2}:\d{2})\s*"
        r"[–-]\s*"
        r"(\d{1,2}:\d{2})",

        r"(?:Beginn|Start)\s*:\s*"
        r"(\d{1,2}:\d{2})\s*"
        r"(?:Uhr)?\s*"
        r"(?:bis|–|-)\s*"
        r"(\d{1,2}:\d{2})\s*"
        r"Uhr",
    ]

    for pat in patterns:

        m = re.search(
            pat,
            text,
            re.I
        )

        if m:
            return (
                m.group(1),
                m.group(2)
            )

    # Nur Beginn
    m = re.search(
        r"(?:ab\s+|Start\s*:\s*|Beginn\s*:\s*)?"
        r"(\d{1,2}:\d{2})\s*Uhr",
        text,
        re.I
    )

    if m:
        return (
            m.group(1),
            None
        )

    return None, None


# ============================================================
# LOKALE DATETIME ERZEUGEN
# ============================================================

def make_local(d, hhmm):

    if not hhmm:

        return datetime.combine(
            d,
            dtime.min
        ).replace(
            tzinfo=TZ
        )

    h, m = map(
        int,
        hhmm.split(":")
    )

    return datetime.combine(
        d,
        dtime(h, m)
    ).replace(
        tzinfo=TZ
    )


# ============================================================
# DATUM + ZEIT VON VERANSTALTUNGSSEITE
# ============================================================

def parse_page_dates(
    page,
    fallback_text=""
):

    text = visible_text(
        page
    )

    d1, d2 = german_dates(
        text
    )

    if d1 is None and fallback_text:

        d1, d2 = german_dates(
            visible_text(
                fallback_text
            )
        )

    if d1 is None:

        return (
            None,
            None,
            False
        )

    start_t, end_t = german_times(
        text
    )

    if (
        not start_t
        and fallback_text
    ):

        start_t, end_t = german_times(
            visible_text(
                fallback_text
            )
        )

    # Datum ohne Uhrzeit =
    # Ganztagstermin
    if not start_t:

        return (
            d1,
            d2,
            True
        )

    start = make_local(
        d1,
        start_t
    )

    # Mehrtägiger Termin
    if d2 > d1:

        end = make_local(
            d2,
            end_t or start_t
        )

    else:

        if end_t:

            end = make_local(
                d1,
                end_t
            )

        else:

            # Nur Beginn vorhanden:
            # 1 Stunde Standarddauer
            end = (
                start
                + timedelta(hours=1)
            )

    if end <= start:

        end = (
            start
            + timedelta(hours=1)
        )

    return (
        start,
        end,
        False
    )


# ============================================================
# ISO-DATUM
# ============================================================

def parse_iso(v):

    if not v:

        return None, False

    v = str(v).strip()

    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}",
        v
    ):

        return (
            datetime.strptime(
                v,
                "%Y-%m-%d"
            ).date(),
            True
        )

    try:

        return (
            datetime.fromisoformat(
                v.replace(
                    "Z",
                    "+00:00"
                )
            ),
            False
        )

    except Exception:

        return None, False


# ============================================================
# URL-FALLBACK
# ============================================================

def url_fallback(item):

    m = re.search(
        r"/(20\d{2})-(\d{2})-(\d{2})"
        r"(?:-(\d{2})-(\d{2}))?/?$",
        item["link"]
    )

    if not m:

        return (
            None,
            None,
            False
        )

    y, mo, d = map(
        int,
        m.group(
            1,
            2,
            3
        )
    )

    if m.group(4):

        start = datetime(
            y,
            mo,
            d,
            int(m.group(4)),
            int(m.group(5))
        )

        return (
            start,
            start + timedelta(hours=1),
            False
        )

    start = date(
        y,
        mo,
        d
    )

    return (
        start,
        start,
        True
    )


# ============================================================
# VERANSTALTUNG ABRUFEN
# ============================================================

def fetch_event(item):

    try:

        page = get(
            item["link"]
        )

        # ----------------------------------------------------
        # JSON-LD frühzeitig auslesen
        # ----------------------------------------------------

        data = jsonld_event(
            page
        )

        # ----------------------------------------------------
        # VERANSTALTUNGSORT ERMITTELN
        # ----------------------------------------------------

        location = extract_location(
            page,
            data
        )

        # ----------------------------------------------------
        # 1. OFFIZIELLER iCAL-DATENSATZ
        # ----------------------------------------------------

        ical = page_ical_link(
            page
        )

        if ical:

            try:

                src = parse_ics(
                    get(
                        ical,
                        12,
                        2
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

                    if (
                        not all_day
                        and isinstance(
                            start,
                            datetime
                        )
                        and isinstance(
                            end,
                            datetime
                        )
                        and end <= start
                    ):

                        end = (
                            start
                            + timedelta(hours=1)
                        )

                    # Offizieller iCal-Ort
                    ical_location = src.get(
                        "LOCATION",
                        ""
                    )

                    if ical_location:
                        location = ical_location

                    return {
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
                        "location": location,
                        "url": (
                            src.get("URL")
                            or item["link"]
                        ),
                        "category": item["category"],
                    }, None

            except Exception:
                pass

        # ----------------------------------------------------
        # 2. JSON-LD
        # ----------------------------------------------------

        if data and data.get(
            "startDate"
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
                        start
                        if all_day
                        else start
                        + timedelta(hours=1)
                    )

                json_location = location_from_jsonld(
                    data
                )

                if json_location:
                    location = json_location

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
                    "url": (
                        data.get("url")
                        or item["link"]
                    ),
                    "category": item["category"],
                }, None

        # ----------------------------------------------------
        # 3. SICHTBARE DATUMS-/ZEITANGABEN
        # ----------------------------------------------------

        start, end, all_day = parse_page_dates(
            page,
            item["description"]
        )

        if start is not None:

            return {
                "uid": item["link"],
                "start": start,
                "end": end,
                "all_day": all_day,
                "summary": item["title"],
                "description": item["description"],
                "location": location,
                "url": item["link"],
                "category": item["category"],
            }, None

        # ----------------------------------------------------
        # 4. DATUM AUS URL
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
                "location": location,
                "url": item["link"],
                "category": item["category"],
            }, None

        return (
            None,
            "kein Datum gefunden"
        )

    except Exception as e:

        return (
            None,
            str(e)
        )


# ============================================================
# SORTIERUNG
# ============================================================

def sort_key(e):

    x = e["start"]

    if (
        isinstance(x, date)
        and not isinstance(x, datetime)
    ):

        x = datetime.combine(
            x,
            datetime.min.time(),
            tzinfo=TZ
        )

    elif x.tzinfo is None:

        x = x.replace(
            tzinfo=TZ
        )

    return x.astimezone(
        timezone.utc
    ).timestamp()


# ============================================================
# ICS ERZEUGEN
# ============================================================

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
        key=sort_key
    ):

        lines += [
            "BEGIN:VEVENT",
            "UID:" + esc(
                e["uid"]
            ),
            "DTSTAMP:" + stamp,
        ]

        # ----------------------------------------------------
        # DATUM / ZEIT
        # ----------------------------------------------------

        if e["all_day"]:

            lines.append(
                "DTSTART;VALUE=DATE:"
                + e["start"].strftime(
                    "%Y%m%d"
                )
            )

            # DTEND bei Ganztagsterminen
            # ist exklusiv.
            end_date = (
                e["end"]
                + timedelta(days=1)
            )

            lines.append(
                "DTEND;VALUE=DATE:"
                + end_date.strftime(
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

        # ----------------------------------------------------
        # BESCHREIBUNG
        # ----------------------------------------------------

        desc = e.get(
            "description",
            ""
        )

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

        # ----------------------------------------------------
        # VERANSTALTUNGSORT
        # ----------------------------------------------------

        location = e.get(
            "location",
            ""
        )

        if location:

            lines.append(
                "LOCATION:"
                + esc(location)
            )

        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        lines.append(
            "URL:"
            + esc(e["url"])
        )

        # ----------------------------------------------------
        # KATEGORIE
        # ----------------------------------------------------

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


# ============================================================
# HAUPTPROGRAMM
# ============================================================

def main():

    print("=" * 80)
    print("LEVERKUSEN WEB-CAL KALENDER")
    print("=" * 80)
    print()

    items = []
    seen = set()

    # --------------------------------------------------------
    # ALLE RSS-KATEGORIEN ABRUFEN
    # --------------------------------------------------------

    for cid, category in CATEGORIES:

        try:

            found = parse_rss(
                get(
                    rss_url(cid),
                    15,
                    3
                ),
                category
            )

            print(
                "Kategorie:",
                category,
                "RSS-Veranstaltungen:",
                len(found)
            )

            for item in found:

                if item["link"] not in seen:

                    seen.add(
                        item["link"]
                    )

                    items.append(
                        item
                    )

        except Exception as e:

            print(
                "RSS FEHLER:",
                category,
                e
            )

    print()
    print("=" * 80)
    print(
        "EINDEUTIGE VERANSTALTUNGEN:",
        len(items)
    )
    print("=" * 80)
    print()

    # --------------------------------------------------------
    # VERANSTALTUNGSSEITEN PARALLEL ABRUFEN
    # --------------------------------------------------------

    results = []
    failures = 0

    with ThreadPoolExecutor(
        max_workers=4
    ) as pool:

        jobs = {
            pool.submit(
                fetch_event,
                item
            ): item
            for item in items
        }

        completed = 0
        total = len(jobs)

        for job in as_completed(jobs):

            completed += 1

            result, error = job.result()

            if result:

                results.append(
                    result
                )

                print(
                    f"[{completed}/{total}] "
                    + result["summary"]
                )

                if result.get("location"):

                    print(
                        "    Ort:",
                        result["location"]
                    )

            else:

                failures += 1

                print(
                    "FEHLER:",
                    jobs[job]["title"],
                    "-",
                    error
                )

    # --------------------------------------------------------
    # DUPLIKATE ENTFERNEN
    # --------------------------------------------------------

    unique = {
        (
            e["uid"],
            e["start"]
        ): e
        for e in results
    }

    # --------------------------------------------------------
    # ICS SCHREIBEN
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("SCHREIBE KALENDER")
    print("=" * 80)
    print()

    with open(
        OUT,
        "w",
        encoding="utf-8",
        newline=""
    ) as f:

        f.write(
            make_ics(
                list(
                    unique.values()
                )
            )
        )

    # --------------------------------------------------------
    # STATISTIK
    # --------------------------------------------------------

    counts = {}

    for e in unique.values():

        counts[e["category"]] = (
            counts.get(
                e["category"],
                0
            )
            + 1
        )

    print()
    print("=" * 80)
    print("FERTIG!")
    print("=" * 80)

    print(
        "Veranstaltungen geschrieben:",
        len(unique)
    )

    print(
        "Nicht verarbeitet:",
        failures
    )

    print()

    for _, category in CATEGORIES:

        print(
            category
            + ": "
            + str(
                counts.get(
                    category,
                    0
                )
            )
        )

    print()
    print(
        "Datei:",
        OUT
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()