"""Tiny FET exports that mimic the structure of the real GTU pages."""

DAYS = ["ორშ./Mon.", "სამშ./Tues."]
SLOTS = ["1-9:00", "2-10:00", "3-11:00"]


def table(table_id: str, name: str, rows: list[list[str]]) -> str:
    """rows[slot] is the list of raw <td> strings for that slot (spans already omitted)."""
    head = "".join(f'<th class="xAxis">{d}</th>' for d in DAYS)
    body = "".join(
        f'<tr><th class="yAxis">{SLOTS[i]}</th>{"".join(cells)}</tr>' for i, cells in enumerate(rows)
    )
    return (
        f'<table id="{table_id}" border="1"><thead><tr><td rowspan="2"></td>'
        f'<th colspan="{len(DAYS)}">{name}</th></tr><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
    )


def page(index: str, tables: list[str]) -> str:
    return (
        "﻿<html><body><table><tr><th>TimeTable</th></tr></table>"
        f"<ul>{index}</ul>{''.join(tables)}</body></html>"
    )


def link(table_id: str, label: str) -> str:
    return (
        f'<li><a href="#{table_id}">{label}</a><span>&nbsp;/&nbsp;</span>'
        f"<a href=\"javascript:tableToExcel('{table_id}', 'x')\"><img src=\"x.gif\"></a></li>"
    )


E = "<td>---</td>"
LECTURE = "მექანიკა (MEC10101G1-L) ლექცია"
LAB = "ფიზიკა (PHY20202G1-LB) ლაბორატორიული "
NO_CODE = "სამართლის პრაქტიკა სემინარი..."

# Rooms view: students / teacher / course.
ROOMS = page(
    link("table_1", "01-101") + link("table_2", "01-L1") + link("table_3", "13 - საველე"),
    [
        table("table_1", "01-101", [
            [f'<td rowspan="2">100, 101<br>ბერიძე ნინო<br>{LECTURE}<br></td>', E],
            [E],
            [E, E],
        ]),
        table("table_2", "01-L1", [
            [E, f"<td>100.1-1<br>კაპანაძე  ირაკლი Kapanadze Irakli<br>{LAB}<br></td>"],
            [E, f"<td>100.2-1<br>ბერიძე ნინო<br>{LAB}<br></td>"],
            [E, E],
        ]),
        table("table_3", "13 - საველე", [
            [E, E],
            [E, E],
            [f"<td>200<br>---- -----<br>{NO_CODE}<br></td>", E],
        ]),
    ],
)

# Teachers view: students / course / room.
TEACHERS = page(
    link("table_1", "---- -----") + link("table_2", "ბერიძე ნინო")
    + link("table_3", "კაპანაძე \tირაკლი Kapanadze Irakli"),
    [
        table("table_1", "---- -----", [
            [E, E],
            [E, E],
            [f"<td>200<br>{NO_CODE}<br>13 - საველე<br></td>", E],
        ]),
        table("table_2", "ბერიძე ნინო", [
            [f'<td rowspan="2">100, 101<br>{LECTURE}<br>01-101<br></td>', E],
            [f"<td>100.2-1<br>{LAB}<br>01-L1<br></td>"],
            [E, "<td>. (.) -<br></td>"],
        ]),
        table("table_3", "კაპანაძე ირაკლი Kapanadze Irakli", [
            [E, f"<td>100.1-1<br>{LAB}<br>01-L1<br></td>"],
            [E, E],
            [E, E],
        ]),
    ],
)


def _nested(*cols: tuple[str, str, str, str]) -> str:
    rows = zip(*cols)
    return '<td><table class="detailed">' + "".join(
        "<tr>" + "".join(f'<td class="detailed">{v}</td>' for v in row) + "</tr>" for row in rows
    ) + "</table></td>"


# Groups view: course / teacher / room; nested cells add the subgroup first.
# Group 100 shows the 2-slot lecture as two 1-slot cells, as FET sometimes does.
GROUPS = page(
    "<li>ბაკალავრიატი - პროგრამა მექანიკა<ul>"
    + link("table_1_DETAILED", "100") + link("table_3_DETAILED", "101")
    + "</ul></li><li>პროფესიული-სამართალი<ul>" + link("table_5_DETAILED", "200") + "</ul></li>",
    [
        table("table_1_DETAILED", "100", [
            [f"<td>{LECTURE}<br>ბერიძე ნინო<br>01-101<br></td>",
             _nested(("100.1-1", LAB, "კაპანაძე ირაკლი Kapanadze Irakli", "01-L1"))],
            [f"<td>{LECTURE}<br>ბერიძე ნინო<br>01-101<br></td>",
             _nested(("100.2-1", LAB, "ბერიძე ნინო", "01-L1"))],
            [E, E],
        ]),
        table("table_3_DETAILED", "101", [
            [f'<td rowspan="2">{LECTURE}<br>ბერიძე ნინო<br>01-101<br></td>', E],
            [E],
            [E, E],
        ]),
        table("table_5_DETAILED", "200", [
            [E, E],
            [E, E],
            [f"<td>{NO_CODE}<br>---- -----<br>13 - საველე<br></td>", E],
        ]),
    ],
)

INFO = """<html><body>
<a href="https://leqtori.gtu.ge/2026__2027/I/B/info">ინფორმაცია</a>
<b>VII კვირის ცხრილი / Week VII Timetable</b>
<a href="http://leqtori.gtu.ge:9000/public/teachers%202026_2027_I_7.html">პედაგოგები / Teache</a>
<a href="http://leqtori.gtu.ge:9000/public/prof_teachers.html">rs</a>
<a href="http://leqtori.gtu.ge:9000/public/groups%202026_2027_I_7.html">ჯგუფები / Groups</a>
<a href="http://leqtori.gtu.ge:9000/public/rooms%202026_2027_I_7.html">აუდიტორიები / Rooms</a>
</body></html>"""
