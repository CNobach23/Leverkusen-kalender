import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser


BASE_URL = "https://www.leverkusen.de/stadt-erleben/veranstaltungskalender/"
OUTPUT_FILE = "veranstaltungen.ics"
TIMEZONE = "Europe/Berlin"

USER_AGENT = "Mozilla/5.0 (compatible; LeverkusenKalender/2.0)"


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


def http_get(url, timeout=15, attempts=2):
    last_error = None

    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": (
                        "text/html,application/xhtml+xml,"
                        "application/xml,text/xml,*/*;q=0.8"
                    ),
                    "Connection": "close",
                },
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout,
            ) as response:

                data = response.read()

                charset = (
                    response.headers.get_content_charset()
                    or "utf-8"
                )

                return data.decode(
                    charset,
                    errors="replace",
                )

        except Exception as exc:

            last_error = exc

            if attempt + 1 < attempts:
                time.sleep(1)

    raise last_error


def build_rss_url(category_id):

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
            date.today().isoformat(),
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
        BASE_URL
        + "?"
        + urllib.parse.urlencode(params)
    )


def parse_rss(xml_text, category_name):

    root = ET.fromstring(xml_text)

    events = []

    for item in root.findall(".//item"):

        def get_value(tag):

            node = item.find(tag)

            if node is None or node.text is None:
                return ""

            return html.unescape(
                node.text
            ).strip()

        title = get_value("title")
        link = get_value("link")
        guid = get_value("guid")
        description = get_value("description")

        if not title or not link:
            continue

        events.append(
            {
                "title": title,
                "link": link,
                "guid": guid or link,
                "description": description,
                "category": category_name,
            }
        )

    return events


class TextParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):

        text = " ".join(
            data.split()
        )

        if text:
            self.parts.append(text)

    def get_text(self):

        return "\n".join(
            self.parts
        )


def html_to_text(html_text):

    parser = TextParser()

    parser.feed(html_text)

    return parser.get_text()


def get_jsonld_objects(html_text):

    objects = []

    pattern = (
        r'<script[^>]+'
        r'type=["\']application/ld\+json["\']'
        r'[^>]*>(.*?)</script>'
    )

    matches = re.findall(
        pattern,
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for raw in matches:

        raw = html.unescape(
            raw
        ).strip()

        raw = re.sub(
            r"^\s*<!--",
            "",
            raw,
        )

        raw = re.sub(
            r"-->\s*$",
            "",
            raw,
        )

        raw = raw.strip()

        try:

            obj = json.loads(
                raw
            )

            if isinstance(
                obj,
                list,
            ):
                objects.extend(
                    obj
                )
            else:
                objects.append(
                    obj
                )

        except Exception:
            continue

    return objects


def walk_json(obj):

    if isinstance(
        obj,
        dict,
    ):

        yield obj

        for value in obj.values():

            yield from walk_json(
                value
            )

    elif isinstance(
        obj,
        list,
    ):

        for value in obj:

            yield from walk_json(
                value
            )


def parse_iso(value):

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

        normalized = value.replace(
            "Z",
            "+00:00",
        )

        dt = datetime.fromisoformat(
            normalized
        )

        return (
            dt,
            False,
        )

    except Exception:
        pass

    match = re.search(
        r"(\d{4}-\d{2}-\d{2})"
        r"(?:[T ](\d{2}):(\d{2}))?",
        value,
    )

    if not match:
        return None, False

    if match.group(2):

        dt = datetime.strptime(
            (
                f"{match.group(1)} "
                f"{match.group(2)}:"
                f"{match.group(3)}"
            ),
            "%Y-%m-%d %H:%M",
        )

        return (
            dt,
            False,
        )

    return (
        datetime.strptime(
            match.group(1),
            "%Y-%m-%d",
        ).date(),
        True,
    )


def extract_event_data(
    html_text,
    rss_event,
):

    candidates = []

    objects = get_jsonld_objects(
        html_text
    )

    for obj in objects:

        for data in walk_json(
            obj
        ):

            event_type = data.get(
                "@type",
                "",
            )

            if isinstance(
                event_type,
                list,
            ):

                types = event_type

            else:

                types = [
                    event_type
                ]

            normalized_types = [
                str(x).lower()
                for x in types
            ]

            if (
                "event" in normalized_types
                or "eventseries" in normalized_types
            ):

                candidates.append(
                    data
                )

    start = None
    end = None
    all_day = False
    location = ""

    for data in candidates:

        start_value, start_day = (
            parse_iso(
                data.get(
                    "startDate"
                )
            )
        )

        end_value, end_day = (
            parse_iso(
                data.get(
                    "endDate"
                )
            )
        )

        if start_value is None:
            continue

        start = start_value
        all_day = start_day

        if end_value is not None:
            end = end_value

        location_data = data.get(
            "location"
        )

        if isinstance(
            location_data,
            dict,
        ):

            location = str(
                location_data.get(
                    "name",
                    "",
                )
            )

        elif isinstance(
            location_data,
            str,
        ):

            location = location_data

        break

    # Fallback: datetime/content-Angaben im HTML
    if start is None:

        values = re.findall(
            r'(?:datetime|content)=["\']'
            r'([^"\']*20\d{2}-\d{2}-\d{2}'
            r'[^"\']*)["\']',
            html_text,
            flags=re.IGNORECASE,
        )

        parsed_values = []

        for value in values:

            parsed, is_day = parse_iso(
                value
            )

            if parsed is not None:

                parsed_values.append(
                    (
                        parsed,
                        is_day,
                    )
                )

        if parsed_values:

            start = parsed_values[0][0]
            all_day = parsed_values[0][1]

            if len(parsed_values) > 1:

                end = parsed_values[1][0]

    # Letzter Fallback:
    # Datum direkt aus der Veranstaltungs-URL
    if start is None:

        match = re.search(
            r"/(20\d{2})-"
            r"(\d{2})-"
            r"(\d{2})"
            r"(?:-(\d{2})-(\d{2}))?/?$",
            rss_event["link"],
        )

        if match:

            year = int(
                match.group(1)
            )

            month = int(
                match.group(2)
            )

            day =