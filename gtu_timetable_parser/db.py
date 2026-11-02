"""Write a normalized Timetable into a single SQLite file."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .normalize import (
    BUILDING_RE,
    Timetable,
    is_placeholder,
    known_types,
    program_level,
    split_course,
    split_day,
    split_slot,
)
from .sources import Sources

SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE day (
    id      INTEGER PRIMARY KEY,  -- 1 = Monday
    label   TEXT NOT NULL,        -- as in the source, e.g. 'ორშ./Mon.'
    name_ka TEXT NOT NULL,
    name_en TEXT
);
CREATE TABLE slot (
    id         INTEGER PRIMARY KEY,  -- lesson number, 1 = first lesson of the day
    label      TEXT NOT NULL,        -- as in the source, e.g. '1-9:00'
    start_time TEXT                  -- 'HH:MM'
);
CREATE TABLE program (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    level TEXT                       -- bachelor, master, doctoral, vocational, preparatory
);
CREATE TABLE student_group (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    program_id INTEGER REFERENCES program(id)
);
CREATE TABLE teacher (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE room (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    building TEXT                    -- building number from the room code, e.g. '01'
);
CREATE TABLE course (
    id   INTEGER PRIMARY KEY,
    code TEXT,
    name TEXT NOT NULL,
    UNIQUE (code, name)
);
CREATE TABLE lesson (
    id         INTEGER PRIMARY KEY,
    day_id     INTEGER NOT NULL REFERENCES day(id),
    slot_id    INTEGER NOT NULL REFERENCES slot(id),
    duration   INTEGER NOT NULL,     -- in slots
    course_id  INTEGER NOT NULL REFERENCES course(id),
    type       TEXT,                 -- ლექცია, პრაქტიკული, ლაბორატორიული, ...
    teacher_id INTEGER REFERENCES teacher(id),
    room_id    INTEGER REFERENCES room(id),
    students   TEXT NOT NULL         -- FET student sets as written, comma separated
);
CREATE TABLE lesson_group (
    lesson_id INTEGER NOT NULL REFERENCES lesson(id),
    group_id  INTEGER NOT NULL REFERENCES student_group(id),
    subgroup  TEXT,                  -- subgroup student set, NULL = whole group
    PRIMARY KEY (lesson_id, group_id)
) WITHOUT ROWID;

CREATE INDEX lesson_teacher ON lesson(teacher_id);
CREATE INDEX lesson_room ON lesson(room_id);
CREATE INDEX lesson_course ON lesson(course_id);
CREATE INDEX lesson_day_slot ON lesson(day_id, slot_id);
CREATE INDEX lesson_group_group ON lesson_group(group_id);
CREATE INDEX student_group_program ON student_group(program_id);
CREATE INDEX room_building ON room(building);

CREATE VIEW lesson_view AS
SELECT l.id, l.day_id, d.label AS day, l.slot_id, s.start_time, l.duration,
       l.course_id, c.code AS course_code, c.name AS course_name, l.type,
       l.teacher_id, t.name AS teacher, l.room_id, r.name AS room, r.building,
       l.students
FROM lesson l
JOIN day d ON d.id = l.day_id
JOIN slot s ON s.id = l.slot_id
JOIN course c ON c.id = l.course_id
LEFT JOIN teacher t ON t.id = l.teacher_id
LEFT JOIN room r ON r.id = l.room_id;
"""


def output_name(period: str) -> str:
    """'2026__2027/I/B' -> '2026__2027_I_B.sqlite'."""
    return period.replace("/", "_") + ".sqlite"


def _ids(con: sqlite3.Connection, table: str, cols: str, rows: list[tuple]) -> dict:
    """Insert rows (first column is the natural key) and return key -> id."""
    marks = ", ".join("?" * len(cols.split(",")))
    ids = {}
    for row in rows:
        cur = con.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", row)
        ids[row[0]] = cur.lastrowid
    return ids


def _meta(sources: Sources, tt: Timetable, counts: dict[str, int]) -> dict[str, str]:
    parts = sources.period.split("/")
    meta = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "period": sources.period,
        "academic_year": parts[0].replace("__", "-").replace("_", "-"),
        "semester": parts[1] if len(parts) > 1 else "",
        "week": "" if sources.week is None else str(sources.week),
        "week_label": sources.week_label or "",
        "source_info_url": sources.info.url,
        "counts": json.dumps(counts),
        "warnings": json.dumps(dict(tt.warnings), ensure_ascii=False),
    }
    for view, page in sources.views.items():
        meta[f"source_{view}_url"] = page.url
        meta[f"source_{view}_last_modified"] = page.last_modified or ""
    return meta


def write(tt: Timetable, sources: Sources, out_dir: Path) -> tuple[Path, dict[str, int]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / output_name(sources.period)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)

    con = sqlite3.connect(tmp)
    try:
        con.executescript(SCHEMA)

        for i, label in enumerate(tt.days, start=1):
            con.execute("INSERT INTO day VALUES (?, ?, ?, ?)", (i, label, *split_day(label)))
        slot_ids = []
        for i, label in enumerate(tt.slots, start=1):
            number, start = split_slot(label)
            slot_id = number if number is not None and number not in slot_ids else i
            slot_ids.append(slot_id)
            con.execute("INSERT INTO slot VALUES (?, ?, ?)", (slot_id, label, start))

        programs = sorted({p for _, p in tt.groups if p})
        program_ids = _ids(con, "program", "name, level", [(p, program_level(p)) for p in programs])
        group_ids = {}
        for name, program in tt.groups:
            cur = con.execute("INSERT INTO student_group (name, program_id) VALUES (?, ?)",
                              (name, program_ids.get(program)))
            group_ids[name] = cur.lastrowid

        teachers = list(dict.fromkeys(
            [*tt.teachers, *(l.key.teacher for l in tt.lessons if not is_placeholder(l.key.teacher))]
        ))
        teacher_ids = _ids(con, "teacher", "name", [(t,) for t in teachers])
        rooms = list(dict.fromkeys(
            [*tt.rooms, *(l.key.room for l in tt.lessons if not is_placeholder(l.key.room))]
        ))
        room_ids = _ids(con, "room", "name, building", [
            (r, m.group(1) if (m := BUILDING_RE.match(r)) else None) for r in rooms
        ])

        types = known_types([l.key.course for l in tt.lessons])
        course_ids: dict[tuple, int] = {}
        for lesson in tt.lessons:
            k = lesson.key
            course = split_course(k.course, types)
            ck = (course.code, course.name)
            if ck not in course_ids:
                cur = con.execute("INSERT INTO course (code, name) VALUES (?, ?)", ck)
                course_ids[ck] = cur.lastrowid
            cur = con.execute(
                "INSERT INTO lesson (day_id, slot_id, duration, course_id, type, teacher_id,"
                " room_id, students) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (k.day + 1, slot_ids[k.slot], k.duration, course_ids[ck], course.type,
                 teacher_ids.get(k.teacher), room_ids.get(k.room), lesson.students),
            )
            con.executemany(
                "INSERT INTO lesson_group VALUES (?, ?, ?)",
                [(cur.lastrowid, group_ids[g], sub) for g, sub in lesson.groups.items()],
            )

        counts = {
            table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("lesson", "lesson_group", "teacher", "student_group", "program",
                          "room", "course")
        }
        con.executemany("INSERT INTO meta VALUES (?, ?)", _meta(sources, tt, counts).items())
        con.commit()
        con.execute("VACUUM")
    except BaseException:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    con.close()
    os.replace(tmp, path)
    return path, counts
