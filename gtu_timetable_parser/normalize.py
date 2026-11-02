"""Merge the teachers, groups and rooms views into one normalized timetable.

The three FET exports show the same lessons from three sides, with the cell lines in a
different order in each view:

    rooms view     (table = room):     students / teacher / course
    teachers view  (table = teacher):  students / course / room
    groups view    (table = group):    course / teacher / room
                   nested cells add a first line with the subgroup the lesson is for

A lesson is identified by day, start slot, duration, course line, teacher and room.
The rooms view provides the lessons; the teachers view is a cross-check that also adds
lessons without a room; the groups view links lessons to groups and study programs.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .fet import IndexEntry, Table, clean

# The course code is the last "(...)" of the course line, followed by the activity type:
# "name (CODE) type". Codes are typed by hand, e.g. "(ICT11508G4-LPB.)", "(LEH12312G2 -P)",
# "(258963)"; a well-formed one looks like "AAC80501G1-LS" or "WINSABPR01".
PAREN_RE = re.compile(r"\(([^()]*)\)")
WELL_FORMED_CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*")
NON_LATIN_RE = re.compile(r"[Ⴀ-ჿᲐ-ᲿЀ-ӿ]")  # Georgian, Cyrillic
BUILDING_RE = re.compile(r"^(\d+)\s*-")
SLOT_RE = re.compile(r"^(\d+)\s*-\s*(\d{1,2}:\d{2})$")
PLACEHOLDER_RE = re.compile(r"^[-\s.()]*$")  # "---- -----", ". (.) -"

# Activity types always recognised at the end of a course line without a code; more are
# learned from the lines that do have a code.
COMMON_TYPES = [
    "ლექცია", "პრაქტიკული", "ლაბორატორიული", "სემინარი", "საკურსო", "პრაქტიკა",
    "Lecture", "Practical Work", "Laboratory", "Seminar", "Practice",
    "Лекция", "Практическая работа", "Лабораторная работа", "Семинар", "Практика",
]

LEVELS = [
    ("bachelor", ("ბაკალავრ", "bachelor", "бакалавр")),
    ("master", ("მაგისტრ", "master", "магистр")),
    ("doctoral", ("დოქტორ", "doctor", "phd", "доктор")),
    ("vocational", ("პროფესიულ", "vocational")),
    ("preparatory", ("მომზადება", "preparatory")),
]


@dataclass(frozen=True)
class LessonKey:
    day: int
    slot: int
    duration: int
    course: str  # raw course line: "name (CODE) type"
    teacher: str
    room: str


@dataclass
class Lesson:
    key: LessonKey
    students: str  # FET student sets as written, comma separated
    # group name -> subgroup student set (None when the lesson is for the whole group)
    groups: dict[str, str | None] = field(default_factory=dict)


@dataclass
class Course:
    name: str
    code: str | None
    type: str | None


@dataclass
class Timetable:
    days: list[str]
    slots: list[str]
    teachers: list[str]
    rooms: list[str]
    groups: list[tuple[str, str | None]]  # (group, program)
    lessons: list[Lesson]
    warnings: Counter = field(default_factory=Counter)


def is_placeholder(value: str) -> bool:
    return not value or bool(PLACEHOLDER_RE.match(value))


def normalize_type(value: str) -> str:
    return re.sub(r"[\s.]+$", "", value)


def normalize_code(value: str) -> str:
    """'LEH12312G2 -P' -> 'LEH12312G2-P', 'ICT11508G4-LPB.' -> 'ICT11508G4-LPB'."""
    return re.sub(r"\s*-\s*", "-", clean(value).rstrip(".").strip())


def _code_candidate(line: str) -> tuple[re.Match, str, str] | None:
    """The last "(...)" of the line if it can be a code: (match, code, text after it)."""
    matches = list(PAREN_RE.finditer(line))
    if not matches or NON_LATIN_RE.search(matches[-1].group(1)):
        return None
    m = matches[-1]
    code = normalize_code(m.group(1))
    return (m, code, normalize_type(line[m.end():].strip())) if code else None


def split_course(line: str, known_types: list[str]) -> Course:
    """'შენობა-ნაგებობების აღდგენა (AAC32301G1-LPK) ლექცია' -> name, code, type."""
    candidate = _code_candidate(line)
    if candidate:
        m, code, rest = candidate
        # "(IT) საფუძვლები ლექცია" is part of a name, not a code followed by a type.
        if not rest or rest in known_types or WELL_FORMED_CODE_RE.fullmatch(code):
            return Course(clean(line[: m.start()]), code, rest or None)
    # No code: the type is the longest known type the line ends with.
    stripped = normalize_type(line)
    for t in known_types:
        if stripped.endswith(" " + t):
            return Course(stripped[: -len(t)].strip(), None, t)
    return Course(stripped, None, None)


def known_types(course_lines: list[str]) -> list[str]:
    """Activity types: the common ones plus whatever follows a well-formed code."""
    types = set(COMMON_TYPES)
    for line in course_lines:
        candidate = _code_candidate(line)
        if candidate and candidate[2] and WELL_FORMED_CODE_RE.fullmatch(candidate[1]):
            types.add(candidate[2])
    return sorted(types, key=len, reverse=True)


def split_slot(label: str) -> tuple[int | None, str | None]:
    """'2-10:00' -> (2, '10:00')."""
    m = SLOT_RE.match(label)
    if not m:
        return None, None
    hour, minute = m.group(2).split(":")
    return int(m.group(1)), f"{int(hour):02d}:{minute}"


def split_day(label: str) -> tuple[str, str | None]:
    """'ორშ./Mon.' -> ('ორშ.', 'Mon.')."""
    ka, _, en = label.partition("/")
    return ka.strip(), en.strip() or None


def program_level(program: str | None) -> str | None:
    if not program:
        return None
    head = program.lower().split("-")[0]
    for level, words in LEVELS:
        if any(w in head for w in words):
            return level
    return None


def _check_axes(views: dict[str, list[Table]]) -> tuple[list[str], list[str]]:
    days = slots = None
    for name, tables in views.items():
        for t in tables:
            if days is None:
                days, slots = t.days, t.slots
            elif t.days != days or t.slots != slots:
                raise ValueError(f"{name} table {t.name!r} has different days/slots")
    if days is None:
        raise ValueError("no timetable tables found")
    return days, slots


def build(views: dict[str, tuple[list[IndexEntry], list[Table]]]) -> Timetable:
    _, teacher_tables = views["teachers"]
    group_index, group_tables = views["groups"]
    _, room_tables = views["rooms"]
    days, slots = _check_axes(
        {"teachers": teacher_tables, "groups": group_tables, "rooms": room_tables}
    )
    warnings: Counter = Counter()
    lessons: dict[LessonKey, Lesson] = {}

    # FET can hold two teachers with the same display name; the HTML cannot tell
    # their lessons apart, so they become one teacher.
    names = Counter(t.name for t in teacher_tables)
    warnings["teachers view: same-name teachers merged"] += sum(n - 1 for n in names.values())

    for table in room_tables:
        for c in table.cells:
            if len(c.lines) != 3:
                warnings["rooms view: unexpected cell"] += 1
                continue
            students, teacher, course = c.lines
            key = LessonKey(c.day, c.slot, c.duration, course, teacher, table.name)
            lessons.setdefault(key, Lesson(key, students))

    from_teachers = set()
    for table in teacher_tables:
        for c in table.cells:
            if len(c.lines) != 3 or is_placeholder(c.lines[1]):
                warnings["teachers view: empty placeholder cell"] += 1
                continue
            students, course, room = c.lines
            key = LessonKey(c.day, c.slot, c.duration, course, table.name, room)
            from_teachers.add(key)
            if key not in lessons:
                warnings["teachers view: lesson missing from rooms view"] += 1
                lessons[key] = Lesson(key, students)
    warnings["rooms view: lesson missing from teachers view"] += sum(
        1 for k in lessons if k not in from_teachers
    )

    # Groups view: a lesson may be drawn as several shorter cells, so match on
    # containment of the cell's slots instead of equal duration.
    by_place: dict[tuple, list[Lesson]] = defaultdict(list)
    for lesson in lessons.values():
        k = lesson.key
        by_place[(k.day, k.course, k.teacher, k.room)].append(lesson)

    program_of: dict[str, str | None] = {}
    program = None
    for entry in group_index:
        if entry.table_id is None:
            program = entry.label
        else:
            program_of[entry.table_id] = program if entry.depth > 1 else None

    for table in group_tables:
        for c in table.cells:
            if c.columns is not None:
                entries = [(col[0] or None, *col[1:4]) for col in c.columns if len(col) >= 4]
            elif len(c.lines) == 3:
                entries = [(None, *c.lines)]
            else:
                warnings["groups view: unexpected cell"] += 1
                continue
            for subgroup, course, teacher, room in entries:
                found = [
                    lesson for lesson in by_place.get((c.day, course, teacher, room), [])
                    if lesson.key.slot <= c.slot
                    and c.slot + c.duration <= lesson.key.slot + lesson.key.duration
                ]
                if len(found) != 1:
                    warnings["groups view: cell not matched to a lesson"] += 1
                    continue
                lesson = found[0]
                if subgroup is not None or table.name not in lesson.groups:
                    lesson.groups[table.name] = subgroup

    # Lessons the groups view did not show: link by student set name.
    group_names = {t.name for t in group_tables}
    for lesson in lessons.values():
        if lesson.groups:
            continue
        for token in lesson.students.split(","):
            token = token.strip()
            base = token.split(".")[0]
            if token in group_names:
                lesson.groups[token] = None
            elif base in group_names:
                lesson.groups[base] = token
        if not lesson.groups:
            warnings["lesson without a group"] += 1

    return Timetable(
        days=days,
        slots=slots,
        teachers=[t.name for t in teacher_tables if not is_placeholder(t.name)],
        rooms=[t.name for t in room_tables],
        groups=[(t.name, program_of.get(t.id)) for t in group_tables],
        lessons=sorted(lessons.values(), key=lambda x: (x.key.day, x.key.slot, x.key.room)),
        warnings=+warnings,
    )
