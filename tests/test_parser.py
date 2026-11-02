import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gtu_timetable_parser import db, fet, normalize, sources  # noqa: E402
from tests import fixtures  # noqa: E402


def make_sources() -> sources.Sources:
    info = sources.Page("https://leqtori.gtu.ge/2026__2027/I/B/info", fixtures.INFO)
    links = sources.find_view_links(info)
    week, label = sources.find_week(info, links)
    pages = {"teachers": fixtures.TEACHERS, "groups": fixtures.GROUPS, "rooms": fixtures.ROOMS}
    return sources.Sources(
        info=info,
        views={v: sources.Page(links[v], pages[v], "Thu, 29 Oct 2026 19:08:17 GMT") for v in pages},
        period=sources.period_from_url(info.url),
        week=week,
        week_label=label,
        links=links,
    )


class FetParseTest(unittest.TestCase):
    def test_rowspan_and_nested_cells(self):
        index, tables = fet.parse(fixtures.GROUPS)
        self.assertEqual([t.name for t in tables], ["100", "101", "200"])
        self.assertEqual(tables[0].days, fixtures.DAYS)
        self.assertEqual(tables[0].slots, fixtures.SLOTS)

        lecture = tables[1].cells[0]
        self.assertEqual((lecture.day, lecture.slot, lecture.duration), (0, 0, 2))
        self.assertEqual(lecture.lines, [fixtures.LECTURE, "ბერიძე ნინო", "01-101"])

        nested = [c for c in tables[0].cells if c.columns]
        self.assertEqual(len(nested), 2)
        self.assertEqual(nested[0].columns[0][0], "100.1-1")
        self.assertEqual(nested[0].day, 1)

    def test_index_tree(self):
        index, _ = fet.parse(fixtures.GROUPS)
        self.assertEqual(
            [(e.depth, e.label, e.table_id) for e in index],
            [
                (1, "ბაკალავრიატი - პროგრამა მექანიკა", None),
                (2, "100", "table_1_DETAILED"),
                (2, "101", "table_3_DETAILED"),
                (1, "პროფესიული-სამართალი", None),
                (2, "200", "table_5_DETAILED"),
            ],
        )

    def test_wrong_cell_count_is_an_error(self):
        broken = fixtures.page("", [fixtures.table("table_1", "x", [[fixtures.E], [], []])])
        with self.assertRaises(ValueError):
            fet.parse(broken)


class HelpersTest(unittest.TestCase):
    def test_split_course(self):
        c = normalize.split_course("გეგმილური ხაზვა (3კრ.) (EET70205G1-P) პრაქტიკული....", [])
        self.assertEqual((c.name, c.code, c.type), ("გეგმილური ხაზვა (3კრ.)", "EET70205G1-P", "პრაქტიკული"))
        c = normalize.split_course("ინფორმაციული ტექნოლოგიების (IT) საფუძვლები ლექცია", ["ლექცია"])
        self.assertEqual((c.name, c.code, c.type), ("ინფორმაციული ტექნოლოგიების (IT) საფუძვლები", None, "ლექცია"))
        c = normalize.split_course("უცხოური ენა (ინგლისური) - B2+1 (LEH12312G2 -P) პრაქტიკული", ["პრაქტიკული"])
        self.assertEqual((c.name, c.code, c.type), ("უცხოური ენა (ინგლისური) - B2+1", "LEH12312G2-P", "პრაქტიკული"))
        c = normalize.split_course("ციფრული წიგნიერება (CIF WIG) ლექცია", ["ლექცია"])
        self.assertEqual((c.code, c.type), ("CIF WIG", "ლექცია"))
        c = normalize.split_course("Use LabView (EET76305E2- B) Laboratory work", [])
        self.assertEqual((c.code, c.type), ("EET76305E2-B", "Laboratory work"))

    def test_slot_day_level(self):
        self.assertEqual(normalize.split_slot("10-18:00"), (10, "18:00"))
        self.assertEqual(normalize.split_slot("1-9:00"), (1, "09:00"))
        self.assertEqual(normalize.split_day("ორშ./Mon."), ("ორშ.", "Mon."))
        self.assertEqual(normalize.program_level("Bachelor's Program - Civil Engineering"), "bachelor")
        self.assertEqual(normalize.program_level("მაგისტრატურა- პროგრამა დიზაინი"), "master")
        self.assertEqual(normalize.program_level("პროფესიულო-მექატრონიკა"), "vocational")
        self.assertIsNone(normalize.program_level("სხვა"))

    def test_sources_discovery(self):
        src = make_sources()
        self.assertEqual(src.period, "2026__2027/I/B")
        self.assertEqual(src.week, 7)
        self.assertTrue(src.links["teachers"].endswith("teachers%202026_2027_I_7.html"))
        self.assertEqual(db.output_name(src.period), "2026__2027_I_B.sqlite")


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.src = make_sources()
        views = {v: fet.parse(self.src.views[v].html) for v in sources.VIEWS}
        self.tt = normalize.build(views)
        self.tmp = tempfile.TemporaryDirectory()
        self.path, self.counts = db.write(self.tt, self.src, Path(self.tmp.name))
        self.con = sqlite3.connect(self.path)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def q(self, sql, *args):
        return self.con.execute(sql, args).fetchall()

    def test_lessons(self):
        self.assertEqual(self.counts["lesson"], 4)
        self.assertEqual(dict(self.tt.warnings), {"teachers view: empty placeholder cell": 1})
        rows = self.q(
            "SELECT day, start_time, duration, course_code, course_name, type, teacher, room, building"
            " FROM lesson_view ORDER BY day_id, slot_id, room"
        )
        self.assertEqual(rows, [
            ("ორშ./Mon.", "09:00", 2, "MEC10101G1-L", "მექანიკა", "ლექცია", "ბერიძე ნინო", "01-101", "01"),
            ("ორშ./Mon.", "11:00", 1, None, "სამართლის პრაქტიკა", "სემინარი", None, "13 - საველე", "13"),
            ("სამშ./Tues.", "09:00", 1, "PHY20202G1-LB", "ფიზიკა", "ლაბორატორიული",
             "კაპანაძე ირაკლი Kapanadze Irakli", "01-L1", "01"),
            ("სამშ./Tues.", "10:00", 1, "PHY20202G1-LB", "ფიზიკა", "ლაბორატორიული", "ბერიძე ნინო", "01-L1", "01"),
        ])

    def test_filter_by_group_teacher_room(self):
        by_group = self.q(
            "SELECT lv.start_time, lv.day, lg.subgroup FROM lesson_view lv"
            " JOIN lesson_group lg ON lg.lesson_id = lv.id"
            " JOIN student_group g ON g.id = lg.group_id WHERE g.name = ? ORDER BY lv.day_id, lv.slot_id",
            "100",
        )
        self.assertEqual(by_group, [
            ("09:00", "ორშ./Mon.", None),
            ("09:00", "სამშ./Tues.", "100.1-1"),
            ("10:00", "სამშ./Tues.", "100.2-1"),
        ])
        self.assertEqual(
            self.q("SELECT g.name FROM lesson_group lg JOIN student_group g ON g.id = lg.group_id"
                   " JOIN lesson l ON l.id = lg.lesson_id WHERE l.duration = 2 ORDER BY g.name"),
            [("100",), ("101",)],
        )
        self.assertEqual(
            self.q("SELECT count(*) FROM lesson l JOIN teacher t ON t.id = l.teacher_id WHERE t.name = ?",
                   "ბერიძე ნინო"),
            [(2,)],
        )
        self.assertEqual(
            self.q("SELECT count(*) FROM lesson l JOIN room r ON r.id = l.room_id WHERE r.name = '01-L1'"),
            [(2,)],
        )

    def test_programs_and_meta(self):
        self.assertEqual(
            self.q("SELECT g.name, p.name, p.level FROM student_group g"
                   " JOIN program p ON p.id = g.program_id ORDER BY g.name"),
            [
                ("100", "ბაკალავრიატი - პროგრამა მექანიკა", "bachelor"),
                ("101", "ბაკალავრიატი - პროგრამა მექანიკა", "bachelor"),
                ("200", "პროფესიული-სამართალი", "vocational"),
            ],
        )
        meta = dict(self.q("SELECT key, value FROM meta"))
        self.assertEqual(meta["academic_year"], "2026-2027")
        self.assertEqual(meta["semester"], "I")
        self.assertEqual(meta["week"], "7")
        self.assertEqual(self.q("SELECT count(*) FROM teacher"), [(2,)])  # placeholder dropped

    def test_rewrite_replaces_file(self):
        self.con.close()
        path, _ = db.write(self.tt, self.src, self.path.parent)
        self.assertEqual(path, self.path)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [path.name])
        self.con = sqlite3.connect(path)


if __name__ == "__main__":
    unittest.main()
