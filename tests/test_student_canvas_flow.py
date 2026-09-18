import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import student_canvas_flow
from generate_summary import (
    load_latest_class_preparation,
    load_latest_student_assignments,
    load_latest_student_grades,
    render_class_preparation,
    student_assignment_records,
    student_grade_display,
)


class StudentCanvasFlowTests(unittest.TestCase):
    def test_load_env_supports_comments_and_quoted_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "# ignored\nCANVAS_STUDENT_ACCT='student name'\n"
                'CANVAS_STUDENT_PASSWORD="secret value"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                student_canvas_flow.load_env(path),
                {
                    "CANVAS_STUDENT_ACCT": "student name",
                    "CANVAS_STUDENT_PASSWORD": "secret value",
                },
            )

    def test_process_environment_overrides_file_values(self):
        with patch.object(
            student_canvas_flow,
            "load_env",
            return_value={
                "CANVAS_STUDENT_ACCT": "file-user",
                "CANVAS_STUDENT_PASSWORD": "file-password",
            },
        ), patch.dict(
            os.environ,
            {
                "CANVAS_STUDENT_ACCT": "process-user",
                "CANVAS_STUDENT_PASSWORD": "process-password",
            },
        ):
            self.assertEqual(
                student_canvas_flow.student_credentials(),
                ("process-user", "process-password"),
            )

    def test_canvas_home_requires_expected_https_origin_and_root(self):
        self.assertTrue(student_canvas_flow.is_canvas_home("https://byui.instructure.com/"))
        self.assertTrue(
            student_canvas_flow.is_canvas_home(
                "https://byui.instructure.com/?login_success=1"
            )
        )
        self.assertFalse(
            student_canvas_flow.is_canvas_home("https://byui.instructure.com/login/saml")
        )
        self.assertFalse(student_canvas_flow.is_canvas_home("https://example.com/"))

    def test_expected_destination_allows_canvas_child_route_only(self):
        requested = "https://byui.instructure.com/courses/431290/grades"
        self.assertTrue(student_canvas_flow.is_expected_destination(requested, requested))
        self.assertTrue(
            student_canvas_flow.is_expected_destination(
                f"{requested}/student/123?semester=fall", requested
            )
        )
        self.assertFalse(
            student_canvas_flow.is_expected_destination(
                "https://byui.instructure.com/courses/431290/assignments", requested
            )
        )
        self.assertFalse(
            student_canvas_flow.is_expected_destination(
                "https://example.com/courses/431290/grades", requested
            )
        )

    def test_course_order_matches_requested_sequence(self):
        self.assertEqual(
            student_canvas_flow.COURSE_URLS,
            [
                "https://byui.instructure.com/courses/420672",
                "https://byui.instructure.com/courses/424344",
                "https://byui.instructure.com/courses/431290",
                "https://byui.instructure.com/courses/431292",
                "https://byui.instructure.com/courses/424334",
                "https://byui.instructure.com/courses/420634",
            ],
        )
        self.assertEqual(
            student_canvas_flow.COURSE_SECTIONS,
            ("assignments", "grades", "pages", "quizzes"),
        )

    def test_save_landing_page_uses_course_specific_path(self):
        class FakePage:
            def evaluate(self, expression):
                self.expression = expression
                return "<!DOCTYPE html>\n<html><body>Course</body></html>"

        with tempfile.TemporaryDirectory() as directory:
            page = FakePage()
            destination = student_canvas_flow.save_landing_page(
                page, "420672", Path(directory)
            )
            self.assertEqual(destination.relative_to(directory).as_posix(), "course_420672/landing.html")
            self.assertEqual(
                destination.read_text(encoding="utf-8"),
                "<!DOCTYPE html>\n<html><body>Course</body></html>",
            )
            self.assertIn("csrf-token", page.expression)

    def test_save_manifest_records_sources(self):
        courses = [
            {
                "course_id": "420672",
                "landing_url": "https://byui.instructure.com/courses/420672",
                "landing_html": "data/student_canvas_html/run/course_420672/landing.html",
                "section_urls": [
                    "https://byui.instructure.com/courses/420672/assignments",
                    "https://byui.instructure.com/courses/420672/grades",
                    "https://byui.instructure.com/courses/420672/pages",
                    "https://byui.instructure.com/courses/420672/quizzes",
                ],
                "syllabus_url": (
                    "https://byui.instructure.com/courses/420672/assignments/syllabus"
                ),
                "syllabus_html": (
                    "data/student_canvas_html/run/course_420672/syllabus.html"
                ),
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = student_canvas_flow.save_manifest(Path(directory), courses)
            saved = __import__("json").loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["courses"], courses)
            self.assertIn("captured_at", saved)

    def test_save_course_html_uses_requested_filename(self):
        class FakePage:
            def evaluate(self, expression):
                return "<!DOCTYPE html>\n<html><body>Syllabus</body></html>"

        with tempfile.TemporaryDirectory() as directory:
            destination = student_canvas_flow.save_course_html(
                FakePage(), "431290", Path(directory), "syllabus.html"
            )
            self.assertEqual(
                destination.relative_to(directory).as_posix(),
                "course_431290/syllabus.html",
            )
            self.assertIn("Syllabus", destination.read_text(encoding="utf-8"))

    def test_extract_and_save_class_preparation_from_landing_table(self):
        class Page:
            def evaluate(self, expression):
                self.expression = expression
                return {
                    "table_found": True,
                    "entries": [{
                        "day_text": "Tue",
                        "date_text": "Sep 22",
                        "before_class": {
                            "text": "Read the case",
                            "lines": ["Read the case"],
                            "links": [{
                                "title": "Case reading",
                                "url": "/courses/420672/pages/case?token=secret#top",
                            }],
                        },
                        "in_class": {
                            "text": "Case discussion",
                            "lines": ["Case discussion"],
                            "links": [],
                        },
                    }],
                }

        page = Page()
        captured = student_canvas_flow.extract_class_preparation(
            page, "420672", "https://byui.instructure.com/courses/420672"
        )
        self.assertIn("pre.?class", page.expression)
        self.assertEqual(captured["status"], "ok")
        self.assertEqual(captured["entries"][0]["date"], "2026-09-22")
        self.assertEqual(
            captured["entries"][0]["before_class"]["links"][0]["url"],
            "https://byui.instructure.com/courses/420672/pages/case",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = student_canvas_flow.save_class_preparation(
                captured, "420672", Path(directory)
            )
            self.assertEqual(path.name, "class_preparation.json")
            self.assertEqual(
                __import__("json").loads(path.read_text())["entries"][0]["date"],
                "2026-09-22",
            )

    def test_latest_class_preparation_combines_newest_success_per_course(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "2026-09-16_120000"
            newer = root / "2026-09-17_120000"
            older.mkdir()
            newer.mkdir()
            (older / "manifest.json").write_text(__import__("json").dumps({
                "courses": [{
                    "course_id": "420634",
                    "class_preparation": {"status": "ok", "entries": [{"date": "2026-09-21"}]},
                }],
            }))
            (newer / "manifest.json").write_text(__import__("json").dumps({
                "courses": [{
                    "course_id": "420672",
                    "class_preparation": {"status": "ok", "entries": [{"date": "2026-09-22"}]},
                }],
            }))
            captured, manifest = load_latest_class_preparation(root)
            self.assertEqual(set(captured), {"420634", "420672"})
            self.assertEqual(manifest, newer / "manifest.json")

    def test_class_preparation_renders_next_class_when_week_has_none(self):
        captured = {
            "status": "ok",
            "source_url": "https://byui.instructure.com/courses/420672",
            "entries": [{
                "date": "2026-09-22",
                "before_class": {"lines": ["Read DAX resources"], "links": []},
                "in_class": {"lines": ["Review syllabus"], "links": []},
            }],
        }
        html = render_class_preparation(
            captured,
            __import__("datetime").datetime(2026, 9, 12),
            __import__("datetime").datetime(2026, 9, 18),
        )
        self.assertIn("Class Preparation", html)
        self.assertIn("Next scheduled class", html)
        self.assertIn("Tuesday", html)
        self.assertIn("September 22", html)
        self.assertIn("Read DAX resources", html)
        self.assertIn("Review syllabus", html)

    def test_extract_and_save_student_grade(self):
        class Locator:
            def __init__(self, text="", children=None):
                self.text = text
                self.children = children or {}

            @property
            def first(self):
                return self

            def wait_for(self, **kwargs):
                return None

            def locator(self, selector):
                return self.children.get(selector, Locator())

            def count(self):
                return 1 if self.text else 0

            def inner_text(self):
                return self.text

        class Page:
            def locator(self, selector):
                self.selector = selector
                return Locator(
                    children={
                        ".grade": Locator("94.5%"),
                        ".letter_grade": Locator("A"),
                    }
                )

        page = Page()
        grade = student_canvas_flow.extract_student_grade(
            page,
            "420672",
            "https://byui.instructure.com/courses/420672/grades",
        )
        self.assertEqual(page.selector, "div.student_assignment.final_grade")
        self.assertEqual(grade["grade"], "94.5%")
        self.assertEqual(grade["letter_grade"], "A")
        self.assertEqual(grade["status"], "ok")
        with tempfile.TemporaryDirectory() as directory:
            path = student_canvas_flow.save_student_grade(
                grade, "420672", Path(directory)
            )
            self.assertEqual(path.name, "grade.json")
            self.assertEqual(__import__("json").loads(path.read_text())["grade"], "94.5%")

    def test_extract_and_save_student_assignments_from_grades_page(self):
        class Page:
            def evaluate(self, expression, values):
                self.expression = expression
                self.values = values
                return {
                    "table_found": True,
                    "group_weights": {"Project work": 35},
                    "items": [{
                        "canvas_id": "55",
                        "title": "Student-visible task",
                        "url": "/courses/420672/assignments/55/submissions/247157?preview=1",
                        "due_at": "2026-09-17T23:59:00-06:00",
                        "due_text": "Sep 17 at 11:59pm",
                        "points_text": "10 pts",
                        "group_name": "Project work",
                        "submission_status": "graded",
                        "completed": True,
                    }, {
                        "canvas_id": "99",
                        "title": "Wrong course",
                        "url": "/courses/999/assignments/99",
                        "points_text": "5",
                    }],
                }

        page = Page()
        captured = student_canvas_flow.extract_student_assignments(
            page, "420672", "https://byui.instructure.com/courses/420672/grades"
        )
        self.assertEqual(page.values, {"courseId": "420672"})
        self.assertIn("#grades_summary tr.student_assignment", page.expression)
        self.assertNotIn("table.summary tr", page.expression)
        self.assertEqual(len(captured["assignments"]), 1)
        item = captured["assignments"][0]
        self.assertEqual(item["canvas_id"], "55")
        self.assertEqual(item["points"], 10)
        self.assertEqual(item["submission_status"], "graded")
        self.assertTrue(item["completed"])
        self.assertEqual(captured["group_weights"], {"Project work": 35.0})
        self.assertEqual(
            item["url"],
            "https://byui.instructure.com/courses/420672/assignments/55",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = student_canvas_flow.save_student_assignments(
                captured, "420672", Path(directory)
            )
            self.assertEqual(path.name, "assignments_from_grades.json")
            self.assertEqual(
                __import__("json").loads(path.read_text())["assignments"][0]["title"],
                "Student-visible task",
            )

    def test_latest_completed_manifest_supplies_report_grades(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "2026-09-16_100000"
            newer = root / "2026-09-16_110000"
            incomplete = root / "2026-09-16_120000"
            older.mkdir()
            newer.mkdir()
            incomplete.mkdir()
            (older / "manifest.json").write_text(
                '{"courses":[{"course_id":"1","grade":{"grade":"80%"}}]}'
            )
            (newer / "manifest.json").write_text(
                '{"courses":[{"course_id":"1","grade":{"grade":"95%",'
                '"letter_grade":"A"}}]}'
            )
            grades, source = load_latest_student_grades(root)
            self.assertEqual(grades["1"]["grade"], "95%")
            self.assertEqual(source, newer / "manifest.json")
            self.assertEqual(student_grade_display(grades["1"]), "95% (A)")
            self.assertEqual(
                student_grade_display({"grade": "N/A", "letter_grade": "N/A"}),
                "N/A (N/A)",
            )
            self.assertEqual(student_grade_display({"grade": None}), "Not available")

    def test_latest_student_scrape_supplies_assignments_and_ol_does_not_add_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "2026-09-17_100000"
            run.mkdir()
            (run / "manifest.json").write_text(
                '{"courses":[{"course_id":"1","student_assignments":'
                '{"status":"ok","group_weights":{"Project work":25},'
                '"assignments":[{"canvas_id":"55",'
                '"title":"Student task","url":"https://byui.instructure.com/'
                'courses/1/assignments/55","points":4,'
                '"group_name":"Project work","submission_status":"submitted",'
                '"completed":true}]}}]}'
            )
            captured, source = load_latest_student_assignments(root)
            self.assertEqual(source, run / "manifest.json")
            records = student_assignment_records(
                captured["1"],
                [
                    {"id":55,"name":"OL title","due_at":"2026-09-17T23:59:00Z"},
                    {"id":77,"name":"OL-only row","due_at":"2026-09-17T23:59:00Z"},
                ],
                None,
                "1",
            )
            self.assertEqual([item["id"] for item in records], ["55"])
            self.assertEqual(records[0]["name"], "Student task")
            self.assertTrue(records[0]["_student_grade_source"])
            self.assertEqual(records[0]["_group_weight"], 25)
            self.assertTrue(records[0]["_canvas_completed"])
            self.assertEqual(records[0]["_canvas_status"], "submitted")


if __name__ == "__main__":
    unittest.main()
