# gtu_timetable_parser

Builds one SQLite file with the current GTU timetable from
[leqtori.gtu.ge](https://leqtori.gtu.ge/), ready to be queried by a website that filters
by teacher, group or room.

## How it works

1. `https://leqtori.gtu.ge/` redirects to the current period, e.g.
   `https://leqtori.gtu.ge/2026__2027/I/B/info`. The path (`2026__2027/I/B`) names the
   output file: `2026__2027_I_B.sqlite`. When the site moves to a new year or semester,
   the next run writes a new file; within a period each run replaces the file.
2. The info page links three weekly pages (file names change every week, so they are
   found by their labels):
   **პედაგოგები / Teachers**, **ჯგუფები / Groups**, **აუდიტორიები / Rooms**.
3. All three are HTML exports of [FET](https://lalescu.ro/liviu/fet/): one table per
   teacher / group / room, days as columns, lesson slots as rows, `rowspan` for
   multi-slot lessons and nested tables for parallel subgroup lessons. They show the
   same lessons from three sides:

   | view     | table per | cell lines                                   |
   |----------|-----------|----------------------------------------------|
   | rooms    | room      | students / teacher / course                  |
   | teachers | teacher   | students / course / room                     |
   | groups   | group     | (subgroup) / course / teacher / room         |

   The rooms view gives every lesson, the teachers view must agree with it (differences
   are reported as warnings), and the groups view adds study programs and which group
   and subgroup each lesson belongs to.

Only the Python standard library is used (Python 3.10+).

## Usage

```bash
python3 -m gtu_timetable_parser                 # download and write output/<period>.sqlite
python3 -m gtu_timetable_parser -o ../web/data  # choose the output directory
python3 -m gtu_timetable_parser --save-sources sources      # keep the downloaded HTML
python3 -m gtu_timetable_parser --from-sources sources      # rebuild offline from it
python3 -m unittest discover -s tests -t .      # tests
```

The site asks students to re-check the timetable every Saturday, so a weekly cron job
(e.g. Saturday evening) keeps the file current:

```cron
0 20 * * 6  cd /path/to/gtu_timetable_parser && python3 -m gtu_timetable_parser -o /path/to/web/data
```

The file is written under a temporary name and swapped in at the end, so a failed run
never leaves a broken file behind.

## Output schema

```
meta(key, value)                  period, academic_year, semester, week, source URLs and
                                  Last-Modified dates, generated_at, counts, warnings
day(id, label, name_ka, name_en)  1 = Monday ... 6 = Saturday
slot(id, label, start_time)       lesson number, '09:00' ... '22:00' (one slot = one hour)
program(id, name, level)          study program; level = bachelor | master | doctoral |
                                  vocational | preparatory | NULL (guessed from the name)
student_group(id, name, program_id)
teacher(id, name)
room(id, name, building)          building = number prefix of the room, e.g. '01'
course(id, code, name)            code as written in the source (may be NULL)
lesson(id, day_id, slot_id, duration, course_id, type, teacher_id, room_id, students)
lesson_group(lesson_id, group_id, subgroup)   subgroup NULL = whole group
lesson_view                       lesson joined with day, slot, course, teacher, room
```

`duration` is in slots; a lesson occupies slots `slot_id .. slot_id + duration - 1`.
`students` keeps the FET student sets exactly as written (e.g. `12400.1-1, 12401.1-1`).

### Example queries

```sql
-- a teacher's week
SELECT * FROM lesson_view
WHERE teacher_id = (SELECT id FROM teacher WHERE name = 'ბერიძე თამარ')
ORDER BY day_id, slot_id;

-- a room's week
SELECT * FROM lesson_view
WHERE room_id = (SELECT id FROM room WHERE name = '01-529')
ORDER BY day_id, slot_id;

-- a group's week, with the subgroup each lesson is for
SELECT lv.*, lg.subgroup FROM lesson_view lv
JOIN lesson_group lg ON lg.lesson_id = lv.id
WHERE lg.group_id = (SELECT id FROM student_group WHERE name = '12300')
ORDER BY lv.day_id, lv.slot_id;

-- dropdown contents
SELECT id, name FROM teacher ORDER BY name;
SELECT g.id, g.name, p.name AS program FROM student_group g
LEFT JOIN program p ON p.id = g.program_id ORDER BY p.name, g.name;
SELECT id, name, building FROM room ORDER BY building, name;
```

### Using it from a website

- **Server side** (e.g. a Next.js route handler): open the file read-only with
  `better-sqlite3` or `node:sqlite` and run the queries above.
- **In the browser**: load the file with [sql.js](https://github.com/sql-js/sql.js)
  (the whole file is ~2.5 MB, ~0.8 MB gzipped), or with
  [sql.js-httpvfs](https://github.com/phiresky/sql.js-httpvfs) to fetch only the pages
  a query needs via HTTP range requests.

## Known quirks of the source

- Seven pairs of teachers have byte-identical names in FET; the HTML cannot tell their
  lessons apart, so each pair becomes one teacher (counted in `meta.warnings`).
- About 40 teacher cells contain only `. (.) -`; they are empty placeholders and skipped.
- Course codes are typed by hand and inconsistent (`ICT11508G4-LPB.`, `LEH12312G2 -P`,
  `CIF WIG`, `258963`); they are tidied (trailing dots, spaces around `-`) but otherwise
  kept as written. About 1% of lessons have no code at all.
- The teacher placeholder `---- -----` becomes a lesson with `teacher_id` NULL.
