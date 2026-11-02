"""Parser for the HTML timetables exported by FET (https://lalescu.ro/liviu/fet/).

The GTU teachers/groups/rooms pages are FET "days horizontal" exports: an index of
<ul>/<li> links followed by one <table id="table_N[_DETAILED]"> per entity. Each table
has the days as columns, the lesson slots as rows, and one <td> per lesson; a lesson
longer than one slot uses rowspan and the covered slots are omitted from later rows.
A cell holding several parallel lessons (subgroups) contains a nested
<table class="detailed"> with one column per lesson.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

EMPTY_CELL = "---"


def clean(text: str) -> str:
    """Collapse all whitespace (tabs, nbsp, repeated spaces) into single spaces."""
    return " ".join(text.split())


@dataclass
class Cell:
    day: int  # 0-based column index into Table.days
    slot: int  # 0-based row index into Table.slots
    duration: int  # number of slots covered
    lines: list[str] | None = None  # simple cell: text split on <br>
    columns: list[list[str]] | None = None  # nested cell: one list of lines per parallel lesson


@dataclass
class Table:
    id: str
    name: str
    days: list[str] = field(default_factory=list)
    slots: list[str] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)


@dataclass
class IndexEntry:
    depth: int  # 1 = top level of the index list
    label: str
    table_id: str | None  # None for headings such as study programs


class _RawCell:
    def __init__(self, rowspan: int):
        self.rowspan = rowspan
        self.text: list[str] = []
        self.nested: list[list[str]] | None = None


class _FetHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.index: list[IndexEntry] = []
        self.tables: list[tuple[Table, list[tuple[str | None, list[_RawCell]]]]] = []
        self._table_depth = 0
        self._cur: tuple[Table, list[tuple[str | None, list[_RawCell]]]] | None = None
        self._in_thead = False
        self._th: dict[str, str | None] | None = None
        self._th_text: list[str] = []
        self._cell: _RawCell | None = None
        self._nested_depth = 0
        self._nested_text: list[str] | None = None
        # Index (<ul>/<li>/<a>) state; only used before the first table.
        self._ul_depth = 0
        self._li_text: list[str] | None = None
        self._a_href: str | None = None
        self._a_text: list[str] = []

    # -- index ---------------------------------------------------------------
    def _flush_li_heading(self) -> None:
        if self._li_text is not None:
            label = clean("".join(self._li_text))
            if label:
                self.index.append(IndexEntry(self._ul_depth, label, None))
            self._li_text = None

    # -- tags ----------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table":
            self._table_depth += 1
            table_id = a.get("id") or ""
            if self._cur is None and table_id.startswith("table_"):
                self._cur = (Table(table_id, ""), [])
                self.tables.append(self._cur)
            elif self._cell is not None:
                self._nested_depth += 1
                if self._cell.nested is None:
                    self._cell.nested = []
            return

        if self._table_depth == 0:
            if tag == "ul":
                self._flush_li_heading()
                self._ul_depth += 1
            elif tag == "li":
                self._li_text = []
            elif tag == "a":
                self._flush_li_heading()
                self._a_href = a.get("href") or ""
                self._a_text = []
            return

        if self._cur is None:
            return
        if self._nested_depth:
            if tag == "tr":
                self._cell.nested.append([])
            elif tag == "td":
                self._nested_text = []
            elif tag == "br" and self._nested_text is not None:
                self._nested_text.append("\n")
            return
        table, rows = self._cur
        if tag == "thead":
            self._in_thead = True
        elif tag == "tbody":
            self._in_thead = False
        elif tag == "tr" and not self._in_thead:
            rows.append((None, []))
        elif tag == "th":
            self._th = a
            self._th_text = []
        elif tag == "td" and not self._in_thead:
            self._cell = _RawCell(int(a.get("rowspan") or 1))
        elif tag == "br" and self._cell is not None:
            self._cell.text.append("\n")
        elif tag == "br" and self._th is not None:
            self._th_text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._table_depth -= 1
            if self._nested_depth:
                self._nested_depth -= 1
            elif self._table_depth == 0:
                self._cur = None
            return

        if self._table_depth == 0:
            if tag == "ul":
                self._flush_li_heading()
                self._ul_depth -= 1
            elif tag == "li":
                self._flush_li_heading()
            elif tag == "a" and self._a_href is not None:
                if self._a_href.startswith("#table_"):
                    self.index.append(
                        IndexEntry(self._ul_depth, clean("".join(self._a_text)), self._a_href[1:])
                    )
                self._a_href = None
            return

        if self._cur is None:
            return
        if self._nested_depth:
            if tag == "td" and self._nested_text is not None:
                self._cell.nested[-1].append("".join(self._nested_text))
                self._nested_text = None
            return
        table, rows = self._cur
        if tag == "th" and self._th is not None:
            text = clean("".join(self._th_text))
            cls = self._th.get("class")
            if cls == "xAxis":
                table.days.append(text)
            elif cls == "yAxis" and rows:
                rows[-1] = (text, rows[-1][1])
            elif self._in_thead and self._th.get("colspan"):
                table.name = text
            self._th = None
        elif tag == "td" and self._cell is not None:
            if rows:
                rows[-1][1].append(self._cell)
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._table_depth == 0:
            if self._a_href is not None:
                self._a_text.append(data)
            elif self._li_text is not None:
                self._li_text.append(data)
            return
        if self._nested_text is not None:
            self._nested_text.append(data)
        elif self._cell is not None and not self._nested_depth:
            self._cell.text.append(data)
        elif self._th is not None:
            self._th_text.append(data)


def _lines(text: str) -> list[str]:
    lines = [clean(x) for x in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def _resolve(table: Table, rows: list[tuple[str | None, list[_RawCell]]]) -> None:
    """Place each <td> on its day/slot, accounting for rowspans from earlier rows."""
    body = [(slot, cells) for slot, cells in rows if slot is not None]
    table.slots = [slot for slot, _ in body]
    ndays = len(table.days)
    covered: set[tuple[int, int]] = set()
    for row, (slot, cells) in enumerate(body):
        queue = iter(cells)
        for day in range(ndays):
            if (row, day) in covered:
                continue
            raw = next(queue, None)
            if raw is None:
                raise ValueError(f"{table.id} ({table.name}): row {slot!r} has too few cells")
            for k in range(1, raw.rowspan):
                covered.add((row + k, day))
            if raw.nested is not None:
                # Nested rows are fields, columns are parallel lessons: transpose.
                fields = [[clean(x) for x in r] for r in raw.nested if r]
                width = max((len(f) for f in fields), default=0)
                columns = [[f[i] if i < len(f) else "" for f in fields] for i in range(width)]
                table.cells.append(Cell(day, row, raw.rowspan, columns=columns))
                continue
            lines = _lines("".join(raw.text))
            if not lines or lines == [EMPTY_CELL]:
                continue
            table.cells.append(Cell(day, row, raw.rowspan, lines=lines))
        if next(queue, None) is not None:
            raise ValueError(f"{table.id} ({table.name}): row {slot!r} has too many cells")


def parse(html: str) -> tuple[list[IndexEntry], list[Table]]:
    """Parse a FET HTML export into its index entries and timetable tables."""
    parser = _FetHTMLParser()
    parser.feed(html)
    parser.close()
    tables = []
    for table, rows in parser.tables:
        _resolve(table, rows)
        tables.append(table)
    return parser.index, tables
