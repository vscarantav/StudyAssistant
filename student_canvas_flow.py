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
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

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
CLASS_PREP_COURSE_IDS = {"420634", "420672"}
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


def extract_student_assignments(page, course_id: str, source_url: str) -> dict[str, object]:
    """Extract the assignments visible to the student on Canvas's Grades page."""
    captured_at = datetime.now(timezone.utc).isoformat()
    raw_result = page.evaluate(
        r"""({courseId}) => {
            const items = [];
            for (const row of document.querySelectorAll('#grades_summary tr.student_assignment')) {
                const link = row.querySelector(
                    'th.title a[href*="/assignments/"], .title a[href*="/assignments/"]'
                );
                if (!link) continue;
                const href = link.getAttribute('href') || '';
                const match = href.match(/\/courses\/(\d+)\/assignments\/(\d+)/);
                if (!match || match[1] !== String(courseId)) continue;
                const due = row.querySelector('.due, td.due');
                const dueTime = due && due.querySelector('time[datetime]');
                const possible = row.querySelector(
                    '.points_possible, td.possible, .possible, [data-testid="points-possible"]'
                );
                const score = row.querySelector('.assignment_score');
                const scoreText = score ? score.textContent : '';
                const scoreMatch = scoreText.match(/\/\s*(-?[\d,.]+)/);
                const context = row.querySelector('.context');
                const submissionStatus = (
                    row.querySelector('.submission_status')?.textContent || ''
                ).trim().toLowerCase();
                const completed = row.classList.contains('assignment_graded') ||
                    ['submitted', 'graded', 'pending_review', 'complete', 'completed']
                        .includes(submissionStatus);
                items.push({
                    canvas_id: match[2],
                    title: link.textContent.trim(),
                    url: href,
                    due_at: dueTime ? dueTime.getAttribute('datetime') : null,
                    due_text: due ? due.textContent.trim() : '',
                    points_text: possible
                        ? possible.textContent.trim()
                        : (scoreMatch ? scoreMatch[1] : ''),
                    group_name: context ? context.textContent.trim() : '',
                    submission_status: submissionStatus,
                    completed,
                });
            }
            const groupWeights = {};
            const weightTable = Array.from(document.querySelectorAll('table.summary')).find((table) => {
                const headers = Array.from(table.querySelectorAll('thead th')).map(
                    (cell) => cell.textContent.trim().toLowerCase()
                );
                return headers[0] === 'group' && headers[1] === 'weight';
            });
            if (weightTable) {
                for (const row of weightTable.querySelectorAll('tbody tr')) {
                    const name = row.querySelector('th')?.textContent.trim() || '';
                    const text = row.querySelector('td')?.textContent.trim() || '';
                    const match = text.match(/-?[\d,.]+/);
                    if (name && name.toLowerCase() !== 'total' && match) {
                        groupWeights[name] = Number(match[0].replace(/,/g, ''));
                    }
                }
            }
            return {
                table_found: Boolean(document.querySelector('#grades_summary')),
                items,
                group_weights: groupWeights,
            };
        }""",
        {"courseId": str(course_id)},
    )

    table_found = isinstance(raw_result, dict) and raw_result.get("table_found") is True
    raw_items = raw_result.get("items", []) if isinstance(raw_result, dict) else []
    raw_weights = raw_result.get("group_weights", {}) if isinstance(raw_result, dict) else {}
    group_weights = {}
    if isinstance(raw_weights, dict):
        for name, value in raw_weights.items():
            try:
                group_weights[str(name).strip()] = float(value)
            except (TypeError, ValueError):
                continue
    assignments = {}
    for raw in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw, dict):
            continue
        canvas_id = str(raw.get("canvas_id", ""))
        title = str(raw.get("title", "")).strip()
        url = urljoin(source_url, str(raw.get("url", "")))
        parts = urlsplit(url)
        expected = urlsplit(CANVAS_BASE_URL)
        match = re.fullmatch(
            rf"/courses/{re.escape(str(course_id))}/assignments/(\d+)"
            r"(?:/submissions/\d+)?/?",
            parts.path,
        )
        if not title or not match or match.group(1) != canvas_id:
            continue
        if parts.scheme != expected.scheme or parts.netloc != expected.netloc:
            continue
        url = f"{CANVAS_BASE_URL}/courses/{course_id}/assignments/{canvas_id}"
        points_text = str(raw.get("points_text", "")).replace(",", "")
        points_match = re.search(r"-?\d+(?:\.\d+)?", points_text)
        points = float(points_match.group()) if points_match else None
        if points is not None and points.is_integer():
            points = int(points)
        assignments[canvas_id] = {
            "canvas_id": canvas_id,
            "title": title,
            "url": url,
            "due_at": raw.get("due_at") or None,
            "due_text": str(raw.get("due_text", "")).strip(),
            "points": points,
            "group_name": str(raw.get("group_name", "")).strip(),
            "submission_status": str(raw.get("submission_status", "")).strip().lower(),
            "completed": raw.get("completed") is True,
        }
    return {
        "course_id": str(course_id),
        "status": "ok" if table_found else "not_found",
        "source_url": source_url,
        "captured_at": captured_at,
        "group_weights": group_weights,
        "assignments": list(assignments.values()),
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


def save_student_assignments(
    assignments: dict[str, object], course_id: str, snapshot_dir: Path
) -> Path:
    """Save structured Grades-page assignment rows beside the HTML capture."""
    course_dir = snapshot_dir / f"course_{course_id}"
    course_dir.mkdir(parents=True, exist_ok=True)
    destination = course_dir / "assignments_from_grades.json"
    destination.write_text(json.dumps(assignments, indent=2) + "\n", encoding="utf-8")
    return destination


def extract_class_preparation(page, course_id: str, source_url: str) -> dict[str, object]:
    """Extract dated preclass and in-class plans from a course landing-page table."""
    captured_at = datetime.now(timezone.utc).isoformat()
    raw_result = page.evaluate(
        r"""() => {
            const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
            const tables = Array.from(document.querySelectorAll('table'));
            for (const table of tables) {
                const rows = Array.from(table.querySelectorAll('tr'));
                if (!rows.length) continue;
                const headerCells = Array.from(rows[0].children).filter(
                    (cell) => ['TH', 'TD'].includes(cell.tagName)
                );
                const headers = [];
                for (const cell of headerCells) {
                    const label = clean(cell.textContent).toLowerCase();
                    const span = Number(cell.getAttribute('colspan') || 1);
                    for (let i = 0; i < span; i += 1) headers.push(label);
                }
                const prepIndex = headers.findIndex((label) =>
                    /pre.?class|preparation|before class/.test(label)
                );
                const classIndex = headers.findIndex((label) =>
                    /in.?class|class activit|class content/.test(label)
                );
                if (prepIndex < 0 || classIndex < 0) continue;
                const dateIndexes = headers.map((label, index) =>
                    /date|day/.test(label) ? index : -1
                ).filter((index) => index >= 0);
                const cellData = (cell) => ({
                    text: clean(cell?.innerText || cell?.textContent),
                    lines: (cell?.innerText || cell?.textContent || '').split(/\n+/)
                        .map(clean).filter(Boolean),
                    links: Array.from(cell?.querySelectorAll('a[href]') || []).map((link) => ({
                        title: clean(link.textContent || link.getAttribute('title')),
                        url: link.getAttribute('href') || '',
                    })).filter((link) => link.title && link.url),
                });
                const entries = [];
                for (const row of rows.slice(1)) {
                    const cells = Array.from(row.children).filter(
                        (cell) => ['TH', 'TD'].includes(cell.tagName)
                    );
                    if (cells.length <= Math.max(prepIndex, classIndex)) continue;
                    const dateCandidates = (dateIndexes.length ? dateIndexes : cells.map((_, i) => i))
                        .map((index) => clean(cells[index]?.textContent));
                    const dateText = dateCandidates.find((text) =>
                        /\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}\b/i.test(text)
                    ) || '';
                    if (!dateText) continue;
                    const dayText = dateCandidates.find((text) =>
                        /\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\b/i.test(text)
                    ) || '';
                    entries.push({
                        day_text: dayText,
                        date_text: dateText,
                        before_class: cellData(cells[prepIndex]),
                        in_class: cellData(cells[classIndex]),
                    });
                }
                return {table_found: true, entries};
            }
            return {table_found: false, entries: []};
        }"""
    )

    table_found = isinstance(raw_result, dict) and raw_result.get("table_found") is True
    entries = []
    seen = set()
    for raw in raw_result.get("entries", []) if isinstance(raw_result, dict) else []:
        date_text = str(raw.get("date_text", ""))
        match = re.search(
            r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
            r"Dec(?:ember)?)\s+(\d{1,2})\b",
            date_text,
            re.IGNORECASE,
        )
        if not match:
            continue
        try:
            parsed = datetime.strptime(
                f"{match.group(1)[:3].title()} {match.group(2)} 2026", "%b %d %Y"
            )
        except ValueError:
            continue

        def clean_cell(value):
            value = value if isinstance(value, dict) else {}
            links = []
            for link in value.get("links", []):
                if not isinstance(link, dict):
                    continue
                url = urljoin(source_url, str(link.get("url", "")))
                parts = urlsplit(url)
                if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username:
                    continue
                sensitive_keys = {
                    "access_token", "api_key", "authenticity_token", "session",
                    "session_id", "token", "verifier",
                }
                safe_query = urlencode([
                    (key, item_value)
                    for key, item_value in parse_qsl(parts.query, keep_blank_values=True)
                    if key.casefold() not in sensitive_keys
                ])
                links.append({
                    "title": str(link.get("title", "")).strip(),
                    "url": parts._replace(query=safe_query, fragment="").geturl(),
                })
            lines = [str(line).strip() for line in value.get("lines", []) if str(line).strip()]
            return {
                "text": str(value.get("text", "")).strip(),
                "lines": lines,
                "links": links,
            }

        before = clean_cell(raw.get("before_class"))
        in_class = clean_cell(raw.get("in_class"))
        key = (parsed.date().isoformat(), before["text"], in_class["text"])
        if key in seen:
            continue
        seen.add(key)
        entries.append({
            "date": parsed.date().isoformat(),
            "day_text": str(raw.get("day_text", "")).strip(),
            "date_text": date_text.strip(),
            "before_class": before,
            "in_class": in_class,
        })
    entries.sort(key=lambda item: item["date"])
    return {
        "course_id": str(course_id),
        "status": "ok" if table_found else "not_found",
        "source_url": source_url,
        "captured_at": captured_at,
        "entries": entries,
    }


def save_class_preparation(
    preparation: dict[str, object], course_id: str, snapshot_dir: Path
) -> Path:
    course_dir = snapshot_dir / f"course_{course_id}"
    course_dir.mkdir(parents=True, exist_ok=True)
    destination = course_dir / "class_preparation.json"
    destination.write_text(json.dumps(preparation, indent=2) + "\n", encoding="utf-8")
    return destination


def wait_for_class_preparation_table(page, timeout_ms: int = 20_000) -> bool:
    """Wait for Canvas to finish injecting the landing-page schedule table."""
    try:
        page.wait_for_function(
            r"""() => Array.from(document.querySelectorAll('table')).some((table) => {
                const text = (table.rows?.[0]?.innerText || '').replace(/\s+/g, ' ').toLowerCase();
                return /pre.?class|preparation|before class/.test(text)
                    && /in.?class|class activit|class content/.test(text);
            })""",
            timeout=timeout_ms,
        )
        return True
    except PlaywrightTimeoutError:
        return False


def capture_grade_page(page, course_url: str, snapshot_dir: Path) -> dict[str, object]:
    """Visit and capture one course's Grades page for a grades-only run."""
    course_id = course_url.rstrip("/").rsplit("/", 1)[-1]
    grades_url = f"{course_url}/grades"
    print(f"Opening Grades: {grades_url}")
    navigate_to_canvas(page, grades_url, f"course {course_id} Grades")
    grade = extract_student_grade(page, course_id, grades_url)
    grade_destination = save_student_grade(grade, course_id, snapshot_dir)
    student_assignments = extract_student_assignments(page, course_id, grades_url)
    assignments_destination = save_student_assignments(
        student_assignments, course_id, snapshot_dir
    )
    grades_html_destination = save_course_html(
        page, course_id, snapshot_dir, "grades.html"
    )
    grade_text = grade.get("grade") or "not found"
    if grade.get("letter_grade"):
        grade_text = f"{grade_text} ({grade['letter_grade']})"
    print(f"Captured Canvas total: {grade_text}")
    print(
        f"Captured Grades-page assignments: {len(student_assignments['assignments'])} "
        f"(status: {student_assignments['status']})"
    )
    return {
        "course_id": course_id,
        "section_urls": [grades_url],
        "grade": grade,
        "grade_json": str(grade_destination.relative_to(SCRIPT_DIR)),
        "student_assignments": student_assignments,
        "assignments_json": str(assignments_destination.relative_to(SCRIPT_DIR)),
        "grades_html": str(grades_html_destination.relative_to(SCRIPT_DIR)),
    }


def visit_grade_pages(page, snapshot_dir: Path) -> list[dict[str, object]]:
    """Capture only the student Grades page for every configured course."""
    return [capture_grade_page(page, url, snapshot_dir) for url in COURSE_URLS]


def capture_class_preparation_page(
    page, course_url: str, snapshot_dir: Path
) -> dict[str, object]:
    """Capture one course landing page and its class-preparation schedule."""
    course_id = course_url.rstrip("/").rsplit("/", 1)[-1]
    print(f"Opening class preparation source: {course_url}")
    navigate_to_canvas(page, course_url, f"course {course_id} landing page")
    if not wait_for_class_preparation_table(page):
        print("Landing-page schedule table did not appear before the wait expired.")
    landing_destination = save_landing_page(page, course_id, snapshot_dir)
    preparation = extract_class_preparation(page, course_id, course_url)
    preparation_destination = save_class_preparation(
        preparation, course_id, snapshot_dir
    )
    print(
        f"Captured class-preparation rows: {len(preparation['entries'])} "
        f"(status: {preparation['status']})"
    )
    return {
        "course_id": course_id,
        "landing_url": course_url,
        "landing_html": str(landing_destination.relative_to(SCRIPT_DIR)),
        "class_preparation": preparation,
        "class_preparation_json": str(
            preparation_destination.relative_to(SCRIPT_DIR)
        ),
    }


def visit_class_preparation_pages(page, snapshot_dir: Path) -> list[dict[str, object]]:
    """Capture landing-page class schedules for BA 300 and BA 315 only."""
    return [
        capture_class_preparation_page(page, url, snapshot_dir)
        for url in COURSE_URLS
        if url.rstrip("/").rsplit("/", 1)[-1] in CLASS_PREP_COURSE_IDS
    ]


def visit_courses(page, snapshot_dir: Path) -> list[dict[str, object]]:
    """Save each landing page, then visit the requested course sections."""
    manifest = []
    for index, url in enumerate(COURSE_URLS, start=1):
        course_id = url.rstrip("/").rsplit("/", 1)[-1]
        print(f"Opening course {index}/{len(COURSE_URLS)} landing page: {url}")
        navigate_to_canvas(page, url, f"course {course_id} landing page")
        if course_id in CLASS_PREP_COURSE_IDS:
            wait_for_class_preparation_table(page)
        destination = save_landing_page(page, course_id, snapshot_dir)
        print(f"Saved landing HTML: {destination}")
        class_preparation = None
        class_preparation_destination = None
        if course_id in CLASS_PREP_COURSE_IDS:
            class_preparation = extract_class_preparation(page, course_id, url)
            class_preparation_destination = save_class_preparation(
                class_preparation, course_id, snapshot_dir
            )
            print(
                f"Captured class-preparation rows: {len(class_preparation['entries'])} "
                f"(status: {class_preparation['status']})"
            )

        section_urls = []
        grade = None
        grade_destination = None
        student_assignments = None
        assignments_destination = None
        grades_html_destination = None
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
                student_assignments = extract_student_assignments(
                    page, course_id, section_url
                )
                assignments_destination = save_student_assignments(
                    student_assignments, course_id, snapshot_dir
                )
                grades_html_destination = save_course_html(
                    page, course_id, snapshot_dir, "grades.html"
                )
                grade_text = grade.get("grade") or "not found"
                letter_text = grade.get("letter_grade")
                if letter_text:
                    grade_text = f"{grade_text} ({letter_text})"
                print(f"Captured Canvas total: {grade_text}")
                print(
                    "Captured Grades-page assignments: "
                    f"{len(student_assignments['assignments'])}"
                )

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
                "class_preparation": class_preparation,
                "class_preparation_json": (
                    str(class_preparation_destination.relative_to(SCRIPT_DIR))
                    if class_preparation_destination else None
                ),
                "section_urls": section_urls,
                "grade": grade,
                "grade_json": str(grade_destination.relative_to(SCRIPT_DIR)),
                "student_assignments": student_assignments,
                "assignments_json": str(assignments_destination.relative_to(SCRIPT_DIR)),
                "grades_html": str(grades_html_destination.relative_to(SCRIPT_DIR)),
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
    """Regenerate the report from local course data and the student scrape."""
    command = [
        sys.executable,
        str(SCRIPT_DIR / "generate_summary.py"),
        "--no-fetch",
        "--no-email",
        "--no-ai",
    ]
    print("Updating the current local report with captured student grades and assignments ...")
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
    parser.add_argument(
        "--grades-only",
        action="store_true",
        help="Visit and capture only each configured course's Grades page",
    )
    parser.add_argument(
        "--class-prep-only",
        action="store_true",
        help="Capture only BA 300 and BA 315 landing-page preparation tables",
    )
    args = parser.parse_args()
    if args.grades_only and args.class_prep_only:
        parser.error("--grades-only and --class-prep-only cannot be combined")
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
            if args.grades_only:
                courses = visit_grade_pages(page, snapshot_dir)
            elif args.class_prep_only:
                courses = visit_class_preparation_pages(page, snapshot_dir)
            else:
                courses = visit_courses(page, snapshot_dir)
            manifest_path = save_manifest(snapshot_dir, courses)
            if args.grades_only:
                print("All six Grades pages and their structured assignment rows were saved.")
            elif args.class_prep_only:
                print("BA 300 and BA 315 class-preparation tables were saved.")
            else:
                print(
                    "All six landing pages were saved and Assignments, Grades, Pages, "
                    "and Quizzes were opened in order. Grades-page assignments and all "
                    "six syllabi were saved."
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
