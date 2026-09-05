import datetime as dt
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo


CATEGORIES = {
    "42023": "Familie & Kinder",
    "42081": "Feste & Brauchtum",
    "42030": "Führungen & Touren",
    "42082": "Festivals & Open-Airs",
    "42078": "Karneval",
    "42029": "Lesungen & Literatur",
    "42032": "Märkte & Messen",
    "42036": "Partys & Nachtleben",
    "42031": "Spitzensport",
    "42037": "Tipps",
    "34821": "Warntage",
}

BASE_URL = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
TZ = ZoneInfo("Europe/Berlin")


def rss_url(category_id):
    today = dt.datetime.now(TZ).date().isoformat()

    params = [
        ("form", "eventSearch-1.form"),
        ("sp:fulltext[]", ""),
        ("sp:categories[13495][]", "-"),
        ("sp:categories[13495][]", "__last__"),
        ("sp:categories[13459][]", category_id),
        ("sp:categories[13459][]", "__last__"),
        ("sp:dateFrom[]", today),
        ("sp:dateTo[]", ""),
        ("sp:out", "rss"),
        ("sp:cmp", "eventSearch-1-0-searchResult"),
        ("action", "submit"),
    ]

    return BASE_URL + "?" + urllib.parse.urlencode(params)


def download(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def local_name(tag):
    return tag.split("}")[-1]


def text_of(element, name):
    for child in element:
        if local_name(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""


def parse_rss(data):
    root = ET.fromstring(data)
    events = []

    for item in root.iter():
        if local_name(item.tag) != "item":
            continue

        title = text_of(item, "title")
        link = text_of(item, "link")
        guid = text_of(item, "guid") or link
        description = text_of(item, "description")

        if title and link:
            events.append({
                "title": html.unescape(title),
                "link": link,
                "guid": guid,
                "description": html.unescape(description),
            })

    return events


def extract_jsonld(page):
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S,
    )

    for raw in scripts:
        try:
            data = json.loads(html.unescape(raw.strip()))
        except Exception:
            continue

        objects = data if isinstance(data, list) else [data]

        if isinstance(data, dict) and "@graph" in data:
            objects = data["@graph"]

        for obj in objects:
            if not isinstance(obj, dict):
                continue

            if obj.get("@type") == "Event" or (
                isinstance(obj.get("@type"), list)
                and "Event" in obj.get("@type")
            ):
                return obj

    return None


def fetch_event(item, category):
    try:
        page = download(item["link"]).decode("utf-8", errors="replace")
        event = extract_jsonld(page)

        if not event:
            return None

        start = event.get("startDate")
        end = event.get("endDate") or start

        if not start:
            return None

        return {
            "uid": item["guid"],
            "title": event.get("name") or item["title"],
            "description": event.get("description") or item["description"],
            "url": item["link"],
            "start": start,
            "end": end,
            "category": category,
            "location": get_location(event),
        }

    except Exception as exc:
        print("Fehler bei:", item["link"], exc)
        return None


def get_location(event):
    location = event.get("location")

    if isinstance(location, dict):
        name = location.get("name", "")
        address = location.get("address", "")

        if isinstance(address, dict):
            address = ", ".join(
                str(address.get(x, ""))
                for x in ["streetAddress", "postalCode", "addressLocality"]
                if address.get(x)
            )

        return ", ".join(x for x in [name, address] if x)

    return str(location or "")


def parse_datetime(value):
    value = value.replace("Z", "+00:00")

    try:
        d = dt.datetime.fromisoformat(value)
    except ValueError:
        d = dt.datetime.fromisoformat(value[:19])

    if d.tzinfo is None:
        d = d.replace(tzinfo=TZ)

    return d.astimezone(dt.timezone.utc)


def ics_escape(value):
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r", "")
        .replace("\n", "\\n")
    )


def make_ics(events):
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CNobach23//Leverkusen Kalender//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Leverkusen Veranstaltungen",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]

    for event in events:
        start = parse_datetime(event["start"])
        end = parse_datetime(event["end"])

        lines.extend([
            "BEGIN:VEVENT",
            "UID:" + ics_escape(event["uid"]),
            "DTSTAMP:" + stamp,
            "DTSTART:" + start.strftime("%Y%m%dT%H%M%SZ"),
            "DTEND:" + end.strftime("%Y%m%dT%H%M%SZ"),
            "SUMMARY:" + ics_escape(event["title"]),
            "DESCRIPTION:" + ics_escape(
                event["description"] + "\n\nKategorie: " + event["category"]
            ),
            "LOCATION:"