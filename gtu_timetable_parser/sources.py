"""Discover and download the current teachers/groups/rooms timetable pages.

https://leqtori.gtu.ge/ redirects to the page of the current period, for example
https://leqtori.gtu.ge/2026__2027/I/B/info. That page links the weekly FET exports
(hosted on leqtori.gtu.ge:9000) whose file names change every week, so the links are
found by their visible labels instead of being hard-coded.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .fet import clean

START_URL = "https://leqtori.gtu.ge/"
USER_AGENT = "gtu_timetable_parser/1.0 (+https://leqtori.gtu.ge/)"

# Link labels on the info page ("პედაგოგები / Teachers" etc.).
VIEW_LABELS = {
    "teachers": ("პედაგოგები", "teachers"),
    "groups": ("ჯგუფები", "groups"),
    "rooms": ("აუდიტორიები", "rooms"),
}
VIEWS = tuple(VIEW_LABELS)

ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


@dataclass
class Page:
    url: str
    html: str
    last_modified: str | None = None


@dataclass
class Sources:
    info: Page
    views: dict[str, Page]
    period: str  # e.g. "2026__2027/I/B"
    week: int | None
    week_label: str | None
    links: dict[str, str] = field(default_factory=dict)


def _quote(url: str) -> str:
    # Hrefs on the info page mix pre-encoded (%20) and raw non-ASCII characters.
    return urllib.parse.quote(url, safe=":/?&=#%+,;@")


def fetch(url: str, retries: int = 3, timeout: float = 120) -> Page:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(_quote(url), headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                if charset.lower().replace("-", "") == "utf8":
                    charset = "utf-8-sig"  # the FET exports start with a BOM
                return Page(resp.geturl(), raw.decode(charset), resp.headers.get("Last-Modified"))
        except OSError as e:
            last_error = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to download {url}: {last_error}")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, clean("".join(self._text))))
            self._href = None

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)


def period_from_url(url: str) -> str:
    """'https://leqtori.gtu.ge/2026__2027/I/B/info' -> '2026__2027/I/B'."""
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    if parts and parts[-1] == "info":
        parts = parts[:-1]
    if not parts or not re.fullmatch(r"\d{4}_+\d{4}", parts[0]):
        raise ValueError(f"cannot read the academic period from {url!r}")
    return "/".join(parts)


def _roman(s: str) -> int | None:
    total = 0
    for i, ch in enumerate(s):
        v = ROMAN.get(ch)
        if v is None:
            return None
        total += -v if i + 1 < len(s) and ROMAN.get(s[i + 1], 0) > v else v
    return total


def find_view_links(info: Page) -> dict[str, str]:
    parser = _LinkParser()
    parser.feed(info.html)
    found: dict[str, str] = {}
    for view, (label_ka, label_en) in VIEW_LABELS.items():
        for href, text in parser.links:
            if label_ka in text or text.lower().startswith(label_en):
                found[view] = urllib.parse.urljoin(info.url, href)
                break
    missing = [v for v in VIEWS if v not in found]
    if missing:
        raise RuntimeError(f"links not found on {info.url}: {', '.join(missing)}")
    return found


def find_week(info: Page, links: dict[str, str]) -> tuple[int | None, str | None]:
    text = clean(re.sub(r"<[^>]+>", " ", info.html))
    m = re.search(r"\b([IVXLC]+)\s+კვირის ცხრილი", text) or re.search(
        r"Week\s+([IVXLC]+)\b", text
    )
    if m:
        return _roman(m.group(1)), m.group(0)
    m = re.search(r"_(\d+)\.html$", urllib.parse.unquote(links["groups"]))
    return (int(m.group(1)), None) if m else (None, None)


def download(start_url: str = START_URL) -> Sources:
    info = fetch(start_url)
    links = find_view_links(info)
    views = {view: fetch(url) for view, url in links.items()}
    week, week_label = find_week(info, links)
    return Sources(info, views, period_from_url(info.url), week, week_label, links)


def save(sources: Sources, directory: Path) -> None:
    """Store the downloaded pages so a run can be repeated offline with load()."""
    directory.mkdir(parents=True, exist_ok=True)
    meta = asdict(sources)
    for name, page in [("info", sources.info), *sources.views.items()]:
        (directory / f"{name}.html").write_text(page.html, encoding="utf-8")
    for page in [meta["info"], *meta["views"].values()]:
        del page["html"]
    (directory / "sources.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load(directory: Path) -> Sources:
    meta = json.loads((directory / "sources.json").read_text(encoding="utf-8"))

    def page(name: str, m: dict) -> Page:
        return Page(m["url"], (directory / f"{name}.html").read_text(encoding="utf-8-sig"),
                    m.get("last_modified"))

    return Sources(
        info=page("info", meta["info"]),
        views={v: page(v, m) for v, m in meta["views"].items()},
        period=meta["period"],
        week=meta["week"],
        week_label=meta["week_label"],
        links=meta["links"],
    )
