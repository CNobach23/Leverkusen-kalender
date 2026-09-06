import gzip
import html
import re
import ssl
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from collections import OrderedDict


CATEGORIES = OrderedDict([
    ("Familie & Kinder", 42023),
    ("Feste & Brauchtum", 42081),
    ("Führungen & Touren", 42030),
    ("Festivals & Open-Airs", 42082),
    ("Karneval", 42078),
    ("Lesungen & Literatur", 42029),
    ("Märkte & Messen", 42032),
    ("Partys & Nachtleben", 42036),
    ("Spitzensport", 42031),
    ("Tipps", 42037),
    ("Warntage", 34821),
])

BASE_RSS = (
    "https://www.leverkusen.de/"
    "stadt-erleben/veranstaltungskalender/index.php"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.leverkusen.de/",
}


def fetch(url, timeout=30):
    request = urllib.request.Request(url, headers=HEADERS)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()

            content_type = response.headers.get("Content-Type", "")
            content_encoding = response.headers.get("Content-Encoding", "")
            final_url = response.geturl()
            status = response.status

            # ------------------------------------------------
            # WICHTIG:
            # GitHub bekommt teilweise gzip-komprimierte Daten.
            # Diese müssen vor dem HTML-Parsing entpackt werden.
            # ------------------------------------------------
            if content_encoding.lower() == "gzip" or data[:2] == b"\x1f\x8b":
                try:
                    data = gzip.decompress(data)
                except Exception as exc:
                    return {
                        "ok": False,
                        "status": status,
                        "content_type": content_type,
                        "content_encoding": content_encoding,
                        "final_url": final_url,
                        "text": "",
                        "error": f"gzip-Dekompression fehlgeschlagen: {exc}",
                    }

            charset = "utf-8"

            match = re.search(
                r"charset=([^\s;]+)",
                content_type,
                re.IGNORECASE
            )

            if match:
                charset = match.group(1).strip('"')

            try:
                text = data.decode(charset, errors="replace")
            except LookupError:
                text = data.decode("utf-8", errors="replace")

            return {
                "ok": True,
                "status": status,
                "content_type": content_type,
                "content_encoding": content_encoding,
                "final_url": final_url,
                "text": text,
                "error": None,
            }

    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "content_type": "",
            "content_encoding": "",
            "final_url": url,
            "text": "",
            "error": repr(exc),
        }


def rss_url(category_id):
    params = [
        ("sp:categories[13495][0]", "-"),
        ("sp:categories[13495][1]", "__last__"),
        ("sp:categories[13459][0]", str(category_id)),
        ("sp:categories[13459][1]", "__last__"),
        ("sp:dateFrom[0]", "2026-09-05"),
        ("sp:dateTo[0]", ""),
        ("sp:fulltext[0]", ""),
        ("sp:out", "rss"),
        ("sp:cmp", "eventSearch-1-0-searchResult"),
        ("action", "submit"),
    ]

    return BASE_RSS + "?" + urllib.parse.urlencode(params)


def clean_text(value):
    value = html.unescape(value or "")

    value = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        value,
        flags=re.I | re.S,
    )

    value = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        value,
        flags=re.I | re.S,
    )

    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def parse_rss(xml_text):
    root = ET.fromstring(xml_text)

    items = []

    for item in root.findall(".//item"):
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        description = item.findtext("description", default="")

        title = html.unescape(title).strip()
        link = html.unescape(link).strip()
        description = html.unescape(description).strip()

        if title and link:
            items.append({
                "title": title,
                "link": link,
                "description": description,
            })

    return items


def show_diagnosis(item):

    print()
    print("=" * 80)
    print("DIAGNOSE VERANSTALTUNG")
    print("=" * 80)

    print("TITEL:")
    print(item["title"])

    print()
    print("RSS-LINK:")
    print(item["link"])

    result = fetch(item["link"])

    print()
    print("HTTP-STATUS:")
    print(result["status"])

    print()
    print("CONTENT-TYPE:")
    print(result["content_type"])

    print()
    print("CONTENT-ENCODING:")
    print(result["content_encoding"])

    print()
    print("ENDGÜLTIGE URL:")
    print(result["final_url"])

    if not result["ok"]:
        print()
        print("FEHLER:")
        print(result["error"])
        return

    text = result["text"]

    print()
    print("HTML-LÄNGE NACH DEKOMPRESSION:")
    print(len(text))

    print()
    print("HTML-BEGINN NACH DEKOMPRESSION:")
    print("-" * 80)
    print(text[:500])
    print("-" * 80)

    plain = clean_text(text)

    print()
    print("TEXT – ERSTE 3000 ZEICHEN:")
    print("-" * 80)
    print(plain[:3000])
    print("-" * 80)

    print()
    print("SUCHTESTELLEN:")

    patterns = [
        r"Termin-Details",
        r"Beginn",
        r"Ende",
        r"\d{1,2}\.\s*(?:Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)",
        r"\d{1,2}:\d{2}",
        r"2026",
        r"2027",
    ]

    for pattern in patterns:
        found = re.search(pattern, plain, re.IGNORECASE)
        print(
            f"  {pattern!r}: "
            f"{'JA' if found else 'NEIN'}"
        )


def main():

    print("=" * 80)
    print("LEVERKUSEN KALENDER – GZIP-DIAGNOSE")
    print("=" * 80)

    all_events = OrderedDict()

    for category, category_id in CATEGORIES.items():

        url = rss_url(category_id)

        print()
        print(f"Lade RSS: {category}")

        result = fetch(url)

        if not result["ok"]:
            print("  FEHLER:", result["error"])
            continue

        print(
            f"  HTTP {result['status']} – "
            f"{len(result['text'])} Zeichen"
        )

        try:
            events = parse_rss(result["text"])
        except Exception as exc:
            print("  RSS-PARSE-FEHLER:", repr(exc))
            continue

        print(f"  Veranstaltungen: {len(events)}")

        for event in events:

            key = (
                event["title"],
                event["link"],
            )

            if key not in all_events:
                all_events[key] = event

    events = list(all_events.values())

    print()
    print("=" * 80)
    print(f"EINDEUTIGE RSS-VERANSTALTUNGEN: {len(events)}")
    print("=" * 80)

    targets = [
        "Familientag im Sensenhammer",
        "Lesen verleiht Flügel",
        "Karnevalszug Hitdorf",
    ]

    selected = []

    for target in targets:

        for event in events:

            if target.lower() in event["title"].lower():

                if event not in selected:
                    selected.append(event)

                break

    if len(selected) < 3:

        for event in events:

            if event not in selected:
                selected.append(event)

            if len(selected) >= 3:
                break

    print()
    print("Es werden folgende Veranstaltungen untersucht:")

    for i, event in enumerate(selected, 1):

        print(f"{i}. {event['title']}")
        print(f"   {event['link']}")

    for event in selected:
        show_diagnosis(event)

    print()
    print("=" * 80)
    print("RSS-BESCHREIBUNGEN – VERGLEICH")
    print("=" * 80)

    shown = 0

    for event in events:

        description = clean_text(event["description"])

        if (
            re.search(r"\d{1,2}:\d{2}", description)
            or re.search(r"\d{1,2}\.\s*\w+", description)
        ):

            print()
            print("TITEL:", event["title"])
            print("LINK:", event["link"])
            print(
                "RSS-BESCHREIBUNG:",
                description[:1000]
            )

            shown += 1

            if shown >= 5:
                break

    print()
    print("=" * 80)
    print("DIAGNOSE BEENDET")
    print("=" * 80)
    print()
    print("Es wurde absichtlich KEINE veranstaltungen.ics geschrieben.")


if __name__ == "__main__":
    main()