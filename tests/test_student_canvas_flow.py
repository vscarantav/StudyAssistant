import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import student_canvas_flow
from generate_summary import load_latest_student_grades, student_grade_display


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


if __name__ == "__main__":
    unittest.main()
