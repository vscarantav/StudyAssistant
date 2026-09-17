"""Open the student Canvas account in a fresh interactive browser session.

This student-facing scraping workflow signs in through the Church Account
form, pauses while the user completes MFA, visits each configured Canvas
course, captures the requested HTML and grade data, and updates the report.

Run:
    python3 student_canvas_flow.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


SCRIPT_DIR = Path(__file__).parent.resolve()
ENV_FILE = SCRIPT_DIR / ".env"
CANVAS_BASE_URL = "https://byui.instructure.com"
CANVAS_HOME_URL = f"{CANVAS_BASE_URL}/"
COURSE_URLS = [
    f"{CANVAS_BASE_URL}/courses/420672",
    f"{CANVAS_BASE_URL}/courses/424344",
    f"{CANVAS_BASE_URL}/courses/431290",
    f"{CANVAS_BASE_URL}/courses/431292",
    f"{CANVAS_BASE_URL}/courses/424334",
    f"{CANVAS_BASE_URL}/courses/420634",
]
COURSE_SECTIONS = ("assignments", "grades", "pages", "quizzes")
HTML_SNAPSHOT_DIR = SCRIPT_DIR / "data" / "student_canvas_html"

USERNAME_SELECTOR = "#username-input"
PASSWORD_SELECTOR = "#password-input, input[name='password'], input[type='password']"
PRIMARY_BUTTON_SELECTOR = "#button-primary"


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Load a small .env file without placing secrets in logs."""
    values: dict[str, str] = {}
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            values[key.strip()] = value
    return values


def student_credentials() -> tuple[str, str]:
    """Read required credentials from .env, with process env as an override."""
    file_values = load_env()
    username = os.environ.get("CANVAS_STUDENT_ACCT", file_values.get("CANVAS_STUDENT_ACCT", ""))
    password = os.environ.get(
        "CANVAS_STUDENT_PASSWORD", file_values.get("CANVAS_STUDENT_PASSWORD", "")
    )
    missing = [
        key
        for key, value in (
            ("CANVAS_STUDENT_ACCT", username),
            ("CANVAS_STUDENT_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required .env value(s): {', '.join(missing)}")
    return username, password


def is_canvas_home(url: str) -> bool:
    """Return true only for the BYU-I Canvas origin's root page."""
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.netloc == urlsplit(CANVAS_BASE_URL).netloc
        and parts.path in {"", "/"}
    )


def click_primary_button(page, expected_label: str) -> None:
    """Click the primary button only after confirming the expected action."""
    button = page.locator(PRIMARY_BUTTON_SELECTOR)
    button.wait_for(state="visible", timeout=60_000)
    label = button.inner_text().strip()
    if label.casefold() != expected_label.casefold():
        raise RuntimeError(
            f"Expected the {expected_label!r} button, but the primary button was {label!r}"
        )
    button.click()


def is_expected_destination(current_url: str, requested_url: str) -> bool:
    """Allow Canvas to redirect a section URL to a child route."""
    current = urlsplit(current_url)
    requested = urlsplit(requested_url)
    requested_path = requested.path.rstrip("/")
    return (
        current.scheme == requested.scheme
        and current.netloc == requested.netloc
        and (
            current.path.rstrip("/") == requested_path
            or current.path.startswith(f"{requested_path}/")
        )
    )


def navigate_to_canvas(page, url: str, label: str) -> None:
    """Navigate with retries for Canvas routes that abort during a redirect."""
    last_error = None
    for attempt in range(1, 4):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.locator("body").wait_for(state="visible", timeout=10_000)
            if urlsplit(page.url).path.startswith("/login"):
                raise RuntimeError(f"Canvas returned to login while opening {label}")
            if not is_expected_destination(page.url, url):
                raise RuntimeError(
                    f"Canvas redirected {label} to an unexpected page: {page.url}"
                )
            return
        except RuntimeError:
            raise
        except PlaywrightError as error:
            last_error = error
            page.wait_for_timeout(750)
            if urlsplit(page.url).path.startswith("/login"):
                raise RuntimeError(f"Canvas returned to login while opening {label}") from error
            if is_expected_destination(page.url, url):
                try:
                    page.locator("body").wait_for(state="visible", timeout=10_000)
                    return
                except PlaywrightError:
                    pass
            if attempt < 3:
                print(f"Retrying {label} after interrupted navigation ({attempt}/3)")
    raise RuntimeError(f"Could not open {label} after 3 attempts: {last_error}")


def sign_in_and_wait_for_mfa(page, username: str, password: str, timeout_ms: int) -> None:
    """Submit username/password and wait for the user to complete MFA."""
    print(f"Opening {CANVAS_HOME_URL}")
    page.goto(CANVAS_HOME_URL, wait_until="domcontentloaded", timeout=60_000)

    username_input = page.locator(USERNAME_SELECTOR)
    username_input.wait_for(state="visible", timeout=60_000)
    username_input.fill(username)
    click_primary_button(page, "Next")

    password_input = page.locator(PASSWORD_SELECTOR).first
    password_input.wait_for(state="visible", timeout=60_000)
    password_input.fill(password)
    click_primary_button(page, "Verify")

    print("MFA is ready in Chrome. Complete it there; this flow will resume automatically.")
    page.wait_for_url(is_canvas_home, wait_until="domcontentloaded", timeout=timeout_ms)
    print("Canvas home reached. Resuming automation.")


def static_page_html(page) -> str:
    """Return rendered HTML without executable or authentication material."""
    return page.evaluate(
        """() => {
            const clone = document.documentElement.cloneNode(true);
            clone.querySelectorAll('script, meta[name="csrf-token"]').forEach((node) => node.remove());
            clone.querySelectorAll('input').forEach((input) => {
                const name = (input.getAttribute('name') || '').toLowerCase();
                const type = (input.getAttribute('type') || '').toLowerCase();
                if (type === 'password' || /csrf|authenticity|token/.test(name)) {
                    input.removeAttribute('value');
                }
            });
            return '<!DOCTYPE html>' + String.fromCharCode(10) + clone.outerHTML;
        }"""
    )


def save_course_html(
    page, course_id: str, snapshot_dir: Path, filename: str
) -> Path:
    """Save a static rendered copy of one course page."""
    course_dir = snapshot_dir / f"course_{course_id}"
    course_dir.mkdir(parents=True, exist_ok=True)
    destination = course_dir / filename
    destination.write_text(static_page_html(page), encoding="utf-8")
    return destination


def save_landing_page(page, course_id: str, snapshot_dir: Path) -> Path:
    """Save a static rendered copy of one course landing page."""
    return save_course_html(page, course_id, snapshot_dir, "landing.html")


def extract_student_grade(page, course_id: str, source_url: str) -> dict[str, str | None]:
    """Extract the Canvas total and letter grade shown to the current student."""
    container = page.locator("div.student_assignment.final_grade").first
    captured_at = datetime.now(timezone.utc).isoformat()
    try:
        container.wait_for(state="attached", timeout=15_000)
        grade_locator = container.locator(".grade").first
        grade = grade_locator.inner_text().strip() if grade_locator.count() else ""
        letter_locator = container.locator(".letter_grade").first
        letter_grade = (
            letter_locator.inner_text().strip() if letter_locator.count() else None
        )
        return {
            "course_id": course_id,
            "status": "ok" if grade else "not_available",
            "grade": grade or None,
            "letter_grade": letter_grade or None,
            "source_url": source_url,
            "captured_at": captured_at,
        }
    except PlaywrightTimeoutError:
        return {
            "course_id": course_id,
            "status": "not_found",
            "grade": None,
            "letter_grade": None,
            "source_url": source_url,
            "captured_at": captured_at,
        }


def save_student_grade(
    grade: dict[str, str | None], course_id: str, snapshot_dir: Path
) -> Path:
    """Save one course's structured student grade beside its HTML captures."""
    course_dir = snapshot_dir / f"course_{course_id}"
    course_dir.mkdir(parents=True, exist_ok=True)
    destination = course_dir / "grade.json"
    destination.write_text(json.dumps(grade, indent=2) + "\n", encoding="utf-8")
    return destination


def visit_courses(page, snapshot_dir: Path) -> list[dict[str, object]]:
    """Save each landing page, then visit the requested course sections."""
    manifest = []
    for index, url in enumerate(COURSE_URLS, start=1):
        course_id = url.rstrip("/").rsplit("/", 1)[-1]
        print(f"Opening course {index}/{len(COURSE_URLS)} landing page: {url}")
        navigate_to_canvas(page, url, f"course {course_id} landing page")
        destination = save_landing_page(page, course_id, snapshot_dir)
        print(f"Saved landing HTML: {destination}")

        section_urls = []
        grade = None
        grade_destination = None
        for section in COURSE_SECTIONS:
            section_url = f"{url}/{section}"
            print(f"Opening {section.title()}: {section_url}")
            navigate_to_canvas(page, section_url, f"course {course_id} {section.title()}")
            section_urls.append(section_url)
            if section == "grades":
                grade = extract_student_grade(page, course_id, section_url)
                grade_destination = save_student_grade(
                    grade, course_id, snapshot_dir
                )
                grade_text = grade.get("grade") or "not found"
                letter_text = grade.get("letter_grade")
                if letter_text:
                    grade_text = f"{grade_text} ({letter_text})"
                print(f"Captured Canvas total: {grade_text}")

        syllabus_url = f"{url}/assignments/syllabus"
        print(f"Opening Syllabus: {syllabus_url}")
        navigate_to_canvas(page, syllabus_url, f"course {course_id} Syllabus")
        syllabus_destination = save_course_html(
            page, course_id, snapshot_dir, "syllabus.html"
        )
        print(f"Saved syllabus HTML: {syllabus_destination}")
        manifest.append(
            {
                "course_id": course_id,
                "landing_url": url,
                "landing_html": str(destination.relative_to(SCRIPT_DIR)),
                "section_urls": section_urls,
                "grade": grade,
                "grade_json": str(grade_destination.relative_to(SCRIPT_DIR)),
                "syllabus_url": syllabus_url,
                "syllabus_html": str(syllabus_destination.relative_to(SCRIPT_DIR)),
            }
        )
    return manifest


def new_snapshot_dir() -> Path:
    """Create a timestamped local directory for this browser run."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return HTML_SNAPSHOT_DIR / timestamp


def save_manifest(snapshot_dir: Path, courses: list[dict[str, object]]) -> Path:
    """Record the source URLs and local HTML paths for later processing."""
    path = snapshot_dir / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "courses": courses,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def update_current_report() -> bool:
    """Regenerate the current report from local course data and student grades."""
    command = [
        sys.executable,
        str(SCRIPT_DIR / "generate_summary.py"),
        "--no-fetch",
        "--no-email",
        "--no-ai",
    ]
    print("Updating the current local report with captured student grades ...")
    try:
        result = subprocess.run(command, cwd=str(SCRIPT_DIR), timeout=300)
    except subprocess.TimeoutExpired:
        print(
            "Warning: report update timed out; the captured grade files were kept.",
            file=sys.stderr,
        )
        return False
    if result.returncode:
        print(
            f"Warning: report update exited with code {result.returncode}; "
            "the captured grade files were kept.",
            file=sys.stderr,
        )
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Log into the student Canvas account and open configured courses"
    )
    parser.add_argument(
        "--mfa-timeout",
        type=int,
        default=600,
        metavar="SECONDS",
        help="Seconds to wait for manual MFA (default: 600)",
    )
    parser.add_argument(
        "--close-after-open",
        action="store_true",
        help="Close Chrome after visiting all courses instead of waiting for Enter",
    )
    parser.add_argument(
        "--no-report-update",
        action="store_true",
        help="Capture student data without regenerating the current local report",
    )
    args = parser.parse_args()
    if args.mfa_timeout <= 0:
        parser.error("--mfa-timeout must be greater than zero")

    try:
        username, password = student_credentials()
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        print(f"Add the missing value(s) to {ENV_FILE}", file=sys.stderr)
        return 2

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
            # A normal non-persistent Playwright context starts without saved
            # cookies/cache and is discarded when Chrome closes.
            context = browser.new_context()
            page = context.new_page()
            sign_in_and_wait_for_mfa(page, username, password, args.mfa_timeout * 1_000)
            snapshot_dir = new_snapshot_dir()
            courses = visit_courses(page, snapshot_dir)
            manifest_path = save_manifest(snapshot_dir, courses)
            print(
                "All six landing pages were saved and Assignments, Grades, Pages, "
                "and Quizzes were opened in order. All six syllabi were saved."
            )
            print(f"Snapshot manifest: {manifest_path}")
            if not args.no_report_update:
                update_current_report()
            if not args.close_after_open:
                input("Browser is ready for the next steps. Press Enter to close it. ")
            browser.close()
    except PlaywrightTimeoutError:
        print("Error: login or MFA timed out before Canvas home was reached.", file=sys.stderr)
        return 1
    except (PlaywrightError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped; the anonymous browser session has been closed.")
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
