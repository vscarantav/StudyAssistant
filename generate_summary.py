"""
Weekly Homework Summary Generator

Fetches course content with the OL_Account Canvas session, determines the
current week, and generates a polished HTML report. The report can also be
emailed as a zipped HTML attachment.

Usage:
    1. Fill in the Gmail and Gemini settings in .env as needed
    2. Run every Monday: python3 generate_summary.py
    
    Optional flags:
        --week N          Override week number (e.g. --week 3)
        --no-fetch        Reuse the existing OL_Account Canvas fetch
        --no-email        Generate report only, don't send email
"""

import os
import sys
import json
import re
import zipfile
import smtplib
import argparse
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from course_plans.sources import date as course_date, eligible as course_eligible
from course_plans.render import report_fragment, toolbar as learning_toolbar, control as learning_control

try:
    from google import genai
except ImportError:
    genai = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
SNAPSHOTS_DIR = SCRIPT_DIR / "snapshots"
OUTPUT_DIR = SCRIPT_DIR / "output"
ENV_FILE = SCRIPT_DIR / ".env"
STUDENT_CANVAS_HTML_DIR = SCRIPT_DIR / "data" / "student_canvas_html"

# Course semester start date (first day of Week 01)
SEMESTER_START = datetime(2026, 9, 12)  # Saturday Sep 12 — Week 01 starts here
SEMESTER_END = datetime(2026, 12, 17)

from course_plans.sources import COURSES

COURSE_IDS = list(COURSES)

GEMINI_MODEL = "gemini-3.8-flash"

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def load_env():
    """Load .env file into a dict."""
    env = {}
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env[key.strip()] = val.strip()
    return env


def load_latest_student_grades(root=STUDENT_CANVAS_HTML_DIR):
    """Load grades only from the newest completed student-browser manifest."""
    root = Path(root)
    for manifest_path in sorted(root.glob("*/manifest.json"), reverse=True):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            courses = payload.get("courses", [])
            if not isinstance(courses, list):
                continue
            grades = {}
            for course in courses:
                course_id = str(course.get("course_id", ""))
                grade = course.get("grade")
                if course_id and isinstance(grade, dict):
                    grades[course_id] = grade
            if grades:
                return grades, manifest_path
        except (OSError, ValueError, TypeError):
            continue
    return {}, None


def load_latest_student_assignments(root=STUDENT_CANVAS_HTML_DIR):
    """Load assignment rows only from the newest completed student Grades scrape."""
    root = Path(root)
    for manifest_path in sorted(root.glob("*/manifest.json"), reverse=True):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            courses = payload.get("courses", [])
            if not isinstance(courses, list):
                continue
            assignments = {}
            for course in courses:
                course_id = str(course.get("course_id", ""))
                captured = course.get("student_assignments")
                if (
                    course_id
                    and isinstance(captured, dict)
                    and captured.get("status") == "ok"
                    and isinstance(captured.get("assignments"), list)
                ):
                    assignments[course_id] = captured
            if assignments:
                return assignments, manifest_path
        except (OSError, ValueError, TypeError):
            continue
    return {}, None


def load_latest_class_preparation(root=STUDENT_CANVAS_HTML_DIR):
    """Load BA 300/315 preparation rows from the newest successful landing scrape."""
    root = Path(root)
    preparation = {}
    newest_manifest = None
    for manifest_path in sorted(root.glob("*/manifest.json"), reverse=True):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            for course in payload.get("courses", []):
                course_id = str(course.get("course_id", ""))
                captured = course.get("class_preparation")
                if (
                    course_id in {"420634", "420672"}
                    and course_id not in preparation
                    and isinstance(captured, dict)
                    and captured.get("status") == "ok"
                    and isinstance(captured.get("entries"), list)
                ):
                    preparation[course_id] = captured
                    if newest_manifest is None:
                        newest_manifest = manifest_path
            if len(preparation) == 2:
                break
        except (OSError, ValueError, TypeError):
            continue
    return preparation, newest_manifest


def student_grade_display(grade):
    """Format Canvas's displayed total without interpreting or calculating it."""
    if not isinstance(grade, dict):
        return None
    total = grade.get("grade")
    letter = grade.get("letter_grade")
    if not total:
        return "Not available"
    if letter:
        return f"{total} ({letter})"
    return str(total)


def student_assignment_records(captured, exported_assignments, plan, course_id):
    """Enrich student Grades-page rows for week matching without adding OL-only rows."""
    exported = {str(item.get("id", "")): item for item in exported_assignments}
    requirements = {
        str(item.get("canvas_id", "")): item
        for item in (plan or {}).get("requirements", [])
        if item.get("canvas_id")
    }
    records = []
    group_weights = (captured or {}).get("group_weights", {})
    for item in (captured or {}).get("assignments", []):
        canvas_id = str(item.get("canvas_id", ""))
        if not canvas_id or not item.get("title") or not item.get("url"):
            continue
        exported_item = exported.get(canvas_id, {})
        requirement = requirements.get(canvas_id, {})
        points = item.get("points")
        if points is None:
            points = requirement.get("points", exported_item.get("points_possible"))
        records.append({
            "id": canvas_id,
            "course_id": str(course_id),
            "name": item["title"],
            "published": True,
            "due_at": item.get("due_at") or requirement.get("due_at") or exported_item.get("due_at"),
            "lock_at": None,
            "points_possible": points,
            "assignment_group_id": exported_item.get("assignment_group_id"),
            "submission_types": [],
            "description": "",
            "html_url": item["url"],
            "_group_name": item.get("group_name", ""),
            "_group_weight": group_weights.get(item.get("group_name", "")),
            "_canvas_completed": item.get("completed") is True,
            "_canvas_status": item.get("submission_status", ""),
            "_plan_notes": requirement.get("notes", []),
            "_student_grade_source": True,
        })
    return records


class HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and extract plain text."""
    def __init__(self):
        super().__init__()
        self._text = []
    
    def handle_data(self, data):
        self._text.append(data)
    
    def get_text(self):
        return " ".join(self._text).strip()


def html_to_text(html_str):
    """Convert HTML to plain text."""
    if not html_str:
        return ""
    extractor = HTMLTextExtractor()
    try:
        extractor.feed(html_str)
    except Exception:
        return html_str
    return extractor.get_text()


def truncate(text, max_len=200):
    """Truncate text to max_len with ellipsis."""
    text = text.replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        return text[:max_len].rsplit(" ", 1)[0] + "…"
    return text


class SummaryHTMLSanitizer(HTMLParser):
    """Keep the small, safe HTML subset used in a Gemini summary."""

    allowed_tags = {"p", "strong", "em", "ul", "ol", "li", "br", "code"}

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.allowed_tags:
            self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in self.allowed_tags and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(escape(data))

    def get_html(self):
        return "".join(self.parts).strip()


def sanitize_summary_html(summary):
    """Remove markup other than simple formatting permitted in the report."""
    sanitizer = SummaryHTMLSanitizer()
    sanitizer.feed(summary)
    sanitizer.close()
    return sanitizer.get_html()


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

def get_latest_snapshot_dir():
    """Find the most recent snapshot directory."""
    if not SNAPSHOTS_DIR.exists():
        return None
    subdirs = sorted(
        [d for d in SNAPSHOTS_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")],
        reverse=True,
    )
    return subdirs[0] if subdirs else None


def load_course_data(snapshot_dir, course_id):
    """Load all relevant JSON files for a course."""
    course_dir = snapshot_dir / f"course_{course_id}"
    if not course_dir.exists():
        print(f"  ⚠️  Course directory not found: {course_dir}")
        return None

    data = {}
    for fname in [
        "course_info",
        "modules",
        "assignments",
        "assignment_groups",
        "quizzes",
        "pages",
        "announcements",
    ]:
        fpath = course_dir / f"{fname}.json"
        if fpath.exists():
            with open(fpath, encoding="utf-8") as f:
                data[fname] = json.load(f)
        else:
            data[fname] = [] if fname != "course_info" else {}
    return data


# ---------------------------------------------------------------------------
# Week Detection
# ---------------------------------------------------------------------------

def get_current_week_number(override=None):
    """Determine the current course week number (1-14)."""
    if override:
        return override

    now = datetime.now()
    
    # Calculate week number based on semester start
    # Each week starts on Saturday and ends on Friday
    # Week 01 starts on SEMESTER_START (Sep 12, a Saturday)
    delta = now - SEMESTER_START
    week_num = (delta.days // 7) + 1
    
    if week_num < 1:
        print(f"  ℹ️  Semester hasn't started yet. Defaulting to Week 1.")
        return 1
    if week_num > 14:
        print(f"  ℹ️  Semester is over (week {week_num}). Showing Week 14.")
        return 14
    
    return week_num


def get_week_date_range(week_num):
    """Get the Saturday-Friday date range for a given week number."""
    week_start = SEMESTER_START + timedelta(weeks=week_num - 1)
    week_end = min(week_start + timedelta(days=6), SEMESTER_END)
    return week_start, week_end


def parse_date(date_str):
    """Convert to Mountain wall time for this report's local calendar comparisons."""
    value = course_date(date_str)
    return value.replace(tzinfo=None) if value else None


# ---------------------------------------------------------------------------
# Content Extraction
# ---------------------------------------------------------------------------

def extract_week_modules(modules, week_num):
    """Extract module items for the given week number."""
    week_prefix = f"W{week_num:02d}" if week_num >= 10 else f"W{week_num:02d}"
    week_pattern = re.compile(rf"Week\s+0?{week_num}\b", re.IGNORECASE)
    
    result = []
    for mod in modules:
        if not mod.get("published", False):
            continue
        if week_pattern.search(mod.get("name", "")):
            result.append(mod)
    
    return result


def extract_learning_items(module_items, pages=None):
    """Extract source material for Gemini's concept guide."""
    page_lookup = {
        str(page.get("url", "")): page
        for page in (pages or [])
        if isinstance(page, dict)
    }
    learning = []
    for item in module_items:
        if item.get("type") in ("Page", "ExternalUrl", "ExternalTool", "File"):
            # Skip unpublished and instructor/TA items
            if not item.get("published", True):
                continue
            title = item.get("title", "")
            if any(skip in title.lower() for skip in ["teaching notes", "do not publish"]):
                continue
            page = page_lookup.get(str(item.get("page_url", "")), {})
            learning.append({
                "title": title,
                "type": item["type"],
                "url": item.get("html_url") or item.get("external_url", ""),
                "content": truncate(html_to_text(page.get("body", "")), 2400),
            })
    return learning


def extract_week_announcements(announcements, week_start, week_end):
    """Return published announcements relevant to this report week."""
    relevant = []
    window_start = week_start - timedelta(days=3)
    window_end = week_end + timedelta(days=1)
    released_before = min(
        window_end,
        datetime.now(timezone.utc).replace(tzinfo=None),
    )

    for announcement in announcements or []:
        if not announcement.get("published", False):
            continue

        # Scheduled announcements carry their real release time here. The
        # designer view may expose their original creation/posted date early.
        effective_at = announcement.get("delayed_post_at") or announcement.get("posted_at")
        effective_dt = parse_date(effective_at)
        if not effective_dt or not (window_start <= effective_dt <= released_before):
            continue

        author = (
            announcement.get("user_name")
            or (announcement.get("author") or {}).get("display_name")
            or "Course instructor"
        )
        relevant.append({
            "title": announcement.get("title") or "Course announcement",
            "author": author,
            "posted_at": effective_at,
            "message": truncate(html_to_text(announcement.get("message", "")), 600),
            "url": announcement.get("html_url", ""),
            "effective_dt": effective_dt,
        })

    relevant.sort(key=lambda item: item["effective_dt"], reverse=True)
    return relevant


CAREER_READINESS_GROUPS = {
    "ds community",
    "being reading career: non-perusall being activities copied in i-learn",
}
CAREER_READINESS_TERMS = (
    "career development",
    "digital profile",
    "informational interview",
    "career success mentor",
    "linkedin",
    "handshake",
    "peoplegrove",
    "vmock",
    "me in 30 seconds",
)


def is_career_readiness_assignment(assignment, group_name):
    """Return whether an assignment belongs to career-readiness work."""
    group_name = (group_name or "").strip().lower()
    assignment_text = " ".join([
        assignment.get("name", ""),
        html_to_text(assignment.get("description", "")),
    ]).lower()
    return (
        group_name in CAREER_READINESS_GROUPS
        or "career" in group_name
        or any(term in assignment_text for term in CAREER_READINESS_TERMS)
    )


def extract_week_assignments(assignments, assignment_groups, week_num, week_start, week_end,
                             mapped_assignment_ids=None):
    """
    Extract assignments due in the given week.
    Also detects assignments by week prefix in name (e.g. 'W03 ...').
    Groups them by assignment group.
    """
    # Build group lookup
    group_map = {g["id"]: g["name"] for g in assignment_groups}
    
    # Week prefix pattern
    week_prefix_pattern = re.compile(rf"^W0?{week_num}\b", re.IGNORECASE)
    
    # Filter assignments
    week_assignments = []
    for a in assignments:
        if not a.get("published", False) or not course_eligible(a):
            continue
        # Zero-point placeholders and administrative check-ins are not useful
        # in the learner's weekly action list or calendar.
        try:
            if a.get("points_possible") is not None and float(a["points_possible"]) == 0:
                continue
        except (TypeError, ValueError):
            pass
        
        # Check by due date
        due_dt = parse_date(a.get("due_at"))
        name = a.get("name", "")
        
        # Match by due date within the week range
        in_range = False
        if due_dt:
            in_range = week_start <= due_dt < (week_end + timedelta(days=1))
        
        # Match by name prefix
        name_match = bool(week_prefix_pattern.match(name))
        
        if in_range or name_match or str(a.get("id")) in (mapped_assignment_ids or set()):
            group_name = a.get("_group_name") or group_map.get(a.get("assignment_group_id"), "Other")
            if is_career_readiness_assignment(a, group_name):
                continue

            assignment_id = str(a.get("id"))
            # Determine submission type label
            sub_types = a.get("submission_types", [])
            if a.get("_student_grade_source"):
                type_badge = "Canvas Item"
            elif "online_quiz" in sub_types:
                type_badge = "Quiz"
            elif "external_tool" in sub_types:
                type_badge = "External Tool"
            elif "online_upload" in sub_types or "online_url" in sub_types:
                type_badge = "Submission"
            elif "online_text_entry" in sub_types:
                type_badge = "Text Entry"
            elif "none" in sub_types:
                type_badge = "No Submission"
            else:
                type_badge = "Other"
            
            # Keep a display-length description and richer context for the AI lesson plan.
            full_description = html_to_text(a.get("description", ""))
            desc_text = truncate(full_description, 150)
            
            week_assignments.append({
                "canvas_id": assignment_id,
                "course_id": str(a.get("course_id", "")),
                "plan_notes": a.get("_plan_notes", []),
                "name": name,
                "due_at": a.get("due_at"),
                "due_dt": due_dt,
                "lock_at": a.get("lock_at"),
                "points": a.get("points_possible"),
                "group_name": group_name,
                "group_weight": a.get("_group_weight"),
                "canvas_completed": a.get("_canvas_completed") is True,
                "canvas_status": a.get("_canvas_status", ""),
                "type_badge": type_badge,
                "description": desc_text,
                "ai_context": truncate(full_description, 900),
                "url": a.get("html_url", ""),
            })
    
    # Canvas does not expose the final denominator here, so points × group
    # weight is a relevance ranking rather than an official grade calculation.
    def relevance(item):
        try: points=float(item.get("points") or 0)
        except (TypeError,ValueError): points=0
        try: weight=float(item.get("group_weight") or 0)
        except (TypeError,ValueError): weight=0
        return (-(points*weight),-weight,-points,item["due_dt"] or datetime.max,item["name"].casefold())
    week_assignments.sort(key=relevance)
    
    # Group by assignment group
    grouped = {}
    for a in week_assignments:
        grp = a["group_name"]
        if grp not in grouped:
            grouped[grp] = []
        grouped[grp].append(a)
    
    return grouped, week_assignments


def generate_ai_summary(week_num, courses_data, env):
    """Generate one concept-first learning guide per course with Gemini."""
    api_key = env.get("GEMINI_API_KEY")
    if not api_key or api_key == "your_gemini_api_key_here":
        print("  ⚠️  No valid GEMINI_API_KEY found, skipping AI summary.")
        return {}

    if genai is None:
        print("  ⚠️  Gemini SDK is not installed; skipping AI summary.")
        print("     Install it with: python3 -m pip install google-genai")
        return {}

    print(f"  🤖 Generating weekly concepts with {GEMINI_MODEL}...")
    try:
        client = genai.Client(api_key=api_key)
        summaries = {}
        for cd in courses_data:
            learning_sources = [
                " — ".join(
                    part for part in [item["title"], item.get("content", "")]
                    if part
                )
                for item in cd["learning_items"]
            ]
            assignments = [
                " — ".join(part for part in [
                    f"{item['name']} ({item['type_badge']}; due {format_short_date(item['due_at'])})",
                    item.get("ai_context") or truncate(item.get("description", ""), 250),
                ] if part)
                for item in cd["all_assignments"]
            ]
            course_detail = "\n".join([
                f"Course: {cd['course_code']} — {cd['course_name']}",
                f"Weekly topic: {cd['module_topic'] or 'Not specified'}",
                "Weekly learning sources:\n- " + ("\n- ".join(learning_sources) or "None listed"),
                "Assignments: " + ("; ".join(assignments) or "None due"),
            ])

            prompt = f"""Create the concept guide for the report section “What to Learn This Week” for Week {week_num} in this one course.

Teach the small, learnable concepts that unlock this course's work. Infer them only from the supplied source titles, page excerpts, weekly topic, and assignment descriptions. Do not return links, resource titles as list items, assignment counts, or generic study advice. Explain each concept in plain language, why it matters, and give a tiny concrete example. An example may use a simple invented data value or short inline expression; it must not claim that invented material is a course requirement.

Use this exact structure:
1. <p><strong>Weekly focus:</strong> one direct sentence naming the central ideas. No greeting, encouragement, or filler.</p>
2. <ul> with exactly 2 to 4 <li> items. Each begins with a concise concept name in <strong> tags and includes a plain-language explanation plus one tiny concrete example. Do not use a Canvas item or assignment title as the concept name.</li>
3. Finish with one <p><strong>Apply it:</strong> sentence connecting the concepts to this week's work without merely repeating its titles.</p>

Keep it between 120 and 220 words. Do not invent due dates, assignment requirements, data results, or unsupported concepts. Return raw HTML only, using only <p>, <strong>, <em>, <ul>, <ol>, <li>, <br>, and <code> tags—no Markdown, code fences, headings, links, styles, or scripts.

{course_detail}"""

            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            text = (response.text or "").strip()
            text = re.sub(r"^```(?:html)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
            summary = sanitize_summary_html(text)
            if summary:
                summaries[cd["course_code"]] = summary
            else:
                print(f"  ⚠️  Gemini returned an empty concept guide for {cd['course_code']}.")
        return summaries
    except Exception as e:
        print(f"  ⚠️  Course concept generation failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# HTML Report Generation
# ---------------------------------------------------------------------------

def format_due_date(date_str):
    """Format an ISO date string to a human-readable format."""
    dt = parse_date(date_str)
    if not dt:
        return "No due date"
    dt_mt = dt
    return dt_mt.strftime("%A, %b %d at %I:%M %p MT")


def format_short_date(date_str):
    """Short format: Mon Sep 14."""
    dt = parse_date(date_str)
    if not dt:
        return "TBD"
    dt_mt = dt
    return dt_mt.strftime("%a %b %d, %I:%M %p")


def render_class_preparation(captured, week_start, week_end, show_missing=False):
    """Render the relevant BA 300/315 landing-page schedule rows."""
    if not isinstance(captured, dict) or captured.get("status") != "ok":
        if not show_missing:
            return ""
        return """
        <div class="section-card class-preparation-section source-missing">
            <h3 class="section-title"><span class="section-icon">🏫</span> Class Preparation</h3>
            <p class="class-prep-empty">The secondary account has not captured this course’s landing-page schedule yet.</p>
        </div>"""

    dated_entries = []
    for entry in captured.get("entries", []):
        try:
            entry_date = datetime.strptime(str(entry.get("date", "")), "%Y-%m-%d")
        except (TypeError, ValueError):
            continue
        dated_entries.append((entry_date, entry))
    dated_entries.sort(key=lambda pair: pair[0])

    selected = [
        pair for pair in dated_entries
        if week_start.date() <= pair[0].date() <= week_end.date()
    ]
    selection_label = "This report week"
    if not selected:
        future = [pair for pair in dated_entries if pair[0].date() > week_end.date()]
        selected = future[:1]
        selection_label = "Next scheduled class"

    source_url = str(captured.get("source_url", ""))
    source_parts = urlsplit(source_url)
    source_link = ""
    if source_parts.scheme in {"http", "https"} and source_parts.netloc and not source_parts.username:
        clean_source = source_parts._replace(query="", fragment="").geturl()
        source_link = (
            f'<a class="class-prep-source" href="{escape(clean_source, quote=True)}" '
            'target="_blank" rel="noopener noreferrer">View course landing page ↗</a>'
        )

    def cell_html(cell, empty_text):
        cell = cell if isinstance(cell, dict) else {}
        valid_links = []
        seen_urls = set()
        for link in cell.get("links", []):
            if not isinstance(link, dict):
                continue
            url = str(link.get("url", ""))
            parts = urlsplit(url)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.netloc
                or parts.username
                or url in seen_urls
            ):
                continue
            seen_urls.add(url)
            valid_links.append({
                "title": str(link.get("title", "")).strip() or "Open resource",
                "url": url,
            })
        lines = []
        seen = set()
        for value in cell.get("lines", []):
            line = str(value).strip()
            key = line.casefold()
            if line and key not in seen:
                seen.add(key)
                lines.append(line)
        if not lines and str(cell.get("text", "")).strip():
            lines.append(str(cell["text"]).strip())
        used_urls = set()
        line_items = []
        for line in lines:
            exact_link = next(
                (link for link in valid_links if link["title"].casefold() == line.casefold()),
                None,
            )
            if exact_link:
                used_urls.add(exact_link["url"])
                line_items.append(
                    f'<li><a href="{escape(exact_link["url"], quote=True)}" '
                    f'target="_blank" rel="noopener noreferrer">{escape(line)} ↗</a></li>'
                )
            else:
                line_items.append(f"<li>{escape(line)}</li>")
        list_html = (
            '<ul class="class-prep-list">'
            + "".join(line_items)
            + "</ul>"
            if line_items else ""
        )
        links = []
        for link in valid_links:
            if link["url"] in used_urls:
                continue
            links.append(
                f'<a href="{escape(link["url"], quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{escape(link["title"])} ↗</a>'
            )
        links_html = (
            f'<div class="class-prep-links">{"".join(links)}</div>' if links else ""
        )
        if not list_html and not links_html:
            return f'<p class="class-prep-empty">{escape(empty_text)}</p>'
        return list_html + links_html

    cards = []
    for entry_date, entry in selected:
        before_html = cell_html(entry.get("before_class"), "No preparation listed.")
        in_class_html = cell_html(entry.get("in_class"), "No in-class activity listed.")
        cards.append(f"""
        <article class="class-prep-card">
            <header class="class-prep-date">
                <span>{escape(entry_date.strftime('%A'))}</span>
                <time datetime="{escape(entry_date.date().isoformat(), quote=True)}">{escape(entry_date.strftime('%B %d').replace(' 0', ' '))}</time>
            </header>
            <div class="class-prep-columns">
                <section>
                    <h4>Before class</h4>
                    {before_html}
                </section>
                <section>
                    <h4>In class</h4>
                    {in_class_html}
                </section>
            </div>
        </article>""")

    if not cards:
        cards.append(
            '<p class="class-prep-empty">No upcoming class-preparation rows were found on the landing page.</p>'
        )
    return f"""
    <div class="section-card class-preparation-section">
        <div class="class-prep-heading">
            <h3 class="section-title"><span class="section-icon">🏫</span> Class Preparation</h3>
            {source_link}
        </div>
        <p class="section-intro">{escape(selection_label)} · Source of truth: course landing page</p>
        <div class="class-prep-cards">{"".join(cards)}</div>
    </div>"""


def generate_html_report(week_num, week_start, week_end, courses_data, ai_summaries=None):
    """Generate a polished, self-contained HTML report."""
    ai_summaries = ai_summaries or {}
    
    week_start_str = week_start.strftime("%B %d")
    week_end_str = week_end.strftime("%B %d, %Y")
    
    # Count totals
    total_assignments = sum(
        len(cd["all_assignments"]) for cd in courses_data
    )
    
    # Course accent colors
    course_colors = {
        "DS 250": {"primary": "#0d9488", "light": "#ccfbf1", "dark": "#134e4a", "gradient": "linear-gradient(135deg, #0d9488, #06b6d4)"},
        "DS 350": {"primary": "#d97706", "light": "#fef3c7", "dark": "#78350f", "gradient": "linear-gradient(135deg, #d97706, #f59e0b)"},
    }
    default_colors = {"primary": "#6366f1", "light": "#e0e7ff", "dark": "#312e81", "gradient": "linear-gradient(135deg, #6366f1, #818cf8)"}
    
    # Build course sections
    course_sections_html = ""
    calendar_assignments = []
    for cd in courses_data:
        cname = cd["course_name"]
        ccode = cd["course_code"]
        colors = course_colors.get(ccode, default_colors)
        grade_display = student_grade_display(cd.get("student_grade"))
        grade_stat_html = (
            f'''<div class="stat grade-stat">
                        <span class="stat-number">{escape(grade_display)}</span>
                        <span class="stat-label">Canvas total</span>
                    </div>'''
            if grade_display is not None
            else ""
        )

        # Module topic
        module_topic = cd.get("module_topic", "")
        module_badge = f'<span class="module-badge">{escape(module_topic)}</span>' if module_topic else ""
        
        # Gemini turns the Canvas source material into actual concepts; raw
        # module links and page titles are intentionally not shown here.
        course_summary = ai_summaries.get(ccode)
        if course_summary:
            learning_html = f"""
            <div class="section-card">
                <h3 class="section-title">
                    <span class="section-icon">📚</span> What to Learn This Week
                </h3>
                <div class="concept-content">
                    {course_summary}
                </div>
            </div>"""
        else:
            learning_html = """
            <div class="section-card">
                <h3 class="section-title">
                    <span class="section-icon">📚</span> What to Learn This Week
                </h3>
                <p class="concept-unavailable">The concept guide could not be generated for this course.</p>
            </div>"""

        if cd.get("course_plan"):
            learning_html = report_fragment(
                cd["course_plan"], week_num, len(cd["all_assignments"])
            )

        class_preparation_html = render_class_preparation(
            cd.get("class_preparation"),
            week_start,
            week_end,
            show_missing=ccode in {"BA 300", "BA 315"},
        )

        # Professor announcements
        announcements_html = ""
        if cd.get("announcements"):
            announcement_cards = ""
            for announcement in cd["announcements"]:
                announcement_url = announcement.get("url", "")
                announcement_link = (
                    f'<a class="canvas-link" href="{escape(announcement_url, quote=True)}" '
                    'target="_blank" rel="noopener noreferrer">Read in Canvas <span aria-hidden="true">↗</span></a>'
                    if announcement_url else ""
                )
                message_html = (
                    f'<p class="announcement-message">{escape(announcement["message"])}</p>'
                    if announcement.get("message") else ""
                )
                announcement_cards += f"""
                <article class="announcement-card">
                    <h4 class="announcement-title">{escape(announcement["title"])}</h4>
                    <p class="announcement-meta">
                        {escape(announcement["author"])} · {format_short_date(announcement["posted_at"])} MT
                    </p>
                    {message_html}
                    {announcement_link}
                </article>"""

            announcements_html = f"""
            <details class="section-card announcements-section">
                <summary class="section-title">
                    <span><span class="section-icon">📢</span> Professor Announcements</span>
                    <span class="announcement-count">{len(cd["announcements"])}</span>
                </summary>
                <div class="announcement-list">{announcement_cards}</div>
            </details>"""
        
        # Homework section
        homework_html = ""
        if cd["grouped_assignments"]:
            groups_html = ""
            for group_name, items in cd["grouped_assignments"].items():
                items_html = ""
                for a in items:
                    due_formatted = format_due_date(a["due_at"])
                    until_formatted = (
                        format_due_date(a.get("lock_at"))
                        if a.get("lock_at")
                        else "Not set in Canvas"
                    )
                    points_html = f'<span class="points-badge">{escape(str(a["points"]))} pts</span>' if a["points"] else ""
                    weight = a.get("group_weight")
                    weight_html = (
                        f'<span class="weight-badge">{escape(f"{float(weight):g}")}% weight</span>'
                        if weight is not None else ""
                    )
                    type_class = a["type_badge"].lower().replace(" ", "-")
                    desc_html = f'<p class="hw-description">{escape(a["description"])}</p>' if a["description"] else ""
                    # Assignment URLs come from the other account's Grades-page scrape.
                    assignment_url = a.get("url", "")
                    open_link_html = (
                        f'<a class="canvas-link" href="{escape(assignment_url, quote=True)}" '
                        'target="_blank" rel="noopener noreferrer">Open in Canvas <span aria-hidden="true">↗</span></a>'
                        if assignment_url else ""
                    )
                    progress_id = f'canvas:{cd.get("course_id")}:assignment:{a.get("canvas_id")}'
                    saved_control = learning_control({
                        "id": progress_id,
                        "canvas_completed": a.get("canvas_completed") is True,
                    }) if cd.get("course_plan") and a.get("canvas_id") else ""
                    plan_notes_html = ''.join(f'<p class="lp-warning">{escape(note)}</p>' for note in a.get("plan_notes", []))
                    
                    items_html += f"""
                    <div class="hw-card{' canvas-completed' if a.get('canvas_completed') else ''}">
                        <div class="hw-header">
                            <h5 class="hw-name">{escape(a['name'])}</h5>
                            <div class="hw-badges">
                                <span class="type-badge type-{escape(type_class, quote=True)}">{escape(a['type_badge'])}</span>
                                {weight_html}
                                {points_html}
                            </div>
                        </div>
                        <div class="hw-dates">
                            <div class="hw-due">
                                <span class="due-icon">⏰</span>
                                <strong>Due:</strong> {due_formatted}
                            </div>
                            <div class="hw-until">
                                <span class="due-icon">🔒</span>
                                <strong>Until:</strong> {until_formatted}
                            </div>
                        </div>
                        {desc_html}
                        {plan_notes_html}
                        {open_link_html}
                        {saved_control}
                    </div>"""
                
                groups_html += f"""
                <div class="hw-group">
                    <h4 class="group-title">{escape(group_name)} <span class="group-count">{len(items)}</span></h4>
                    {items_html}
                </div>"""
            
            homework_html = f"""
            <div class="section-card assignments-section" id="course-{escape(str(cd.get('course_id')), quote=True)}-assignments">
                <h3 class="section-title">
                    <span class="section-icon">✓</span> Assignments This Week
                </h3>
                <p class="section-intro">Ordered by approximate grade impact (points × assignment-group weight). Zero-point items are hidden.</p>
                {groups_html}
            </div>"""
        elif not cd.get("assignment_source_available", True):
            homework_html = f"""
            <div class="section-card assignment-source-missing" id="course-{escape(str(cd.get('course_id')), quote=True)}-assignments">
                <h3 class="section-title">
                    <span class="section-icon">↻</span> Student Assignment Scrape Needed
                </h3>
                <p>The other account’s Grades page has not been captured yet. Run <code>python3 student_canvas_flow.py</code>; this report will not substitute OL_Account assignments.</p>
            </div>"""
        else:
            homework_html = f"""
            <div class="section-card" id="course-{escape(str(cd.get('course_id')), quote=True)}-assignments">
                <h3 class="section-title">
                    <span class="section-icon">✓</span> No Assignments This Week
                </h3>
                <p style="color: #94a3b8; padding: 1rem;">No graded assignments are mapped to this week.</p>
            </div>"""
        
        assignment_count = len(cd["all_assignments"])
        concept_count = 0
        if cd.get("course_plan"):
            concept_count = len(cd["course_plan"]["weeks"][week_num - 1]["concepts"])
        for assignment in cd["all_assignments"]:
            calendar_assignments.append({
                **assignment,
                "course_code": ccode,
                "color": colors["primary"],
            })
        
        course_sections_html += f"""
        <details class="course-section" style="--course-primary: {colors['primary']}; --course-light: {colors['light']}; --course-dark: {colors['dark']}; --course-gradient: {colors['gradient']};">
            <summary class="course-header">
                <div class="course-info">
                    <h2 class="course-code">{ccode}</h2>
                    <p class="course-name">{cname}</p>
                    {module_badge}
                </div>
                <div class="course-stats">
                    {grade_stat_html}
                    <div class="stat">
                        <span class="stat-number">{concept_count}</span>
                        <span class="stat-label">concepts</span>
                    </div>
                    <div class="stat">
                        <span class="stat-number">{assignment_count}</span>
                        <span class="stat-label">assignments</span>
                    </div>
                    <span class="accordion-chevron" aria-hidden="true"></span>
                </div>
            </summary>
            <div class="course-body">
                {learning_html}
                {class_preparation_html}
                {homework_html}
                {announcements_html}
            </div>
        </details>
        """

    # The report's source calendar uses Saturday-Friday boundaries, while this
    # compact agenda intentionally presents the user's requested Monday-Saturday
    # reading order. Dates remain the actual exported due dates.
    calendar_start = week_start + timedelta(days=(7 - week_start.weekday()) % 7)
    calendar_end = calendar_start + timedelta(days=5)
    calendar_days = []
    for weekday, label in enumerate(("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")):
        display_date = calendar_start + timedelta(days=weekday)
        items = [a for a in calendar_assignments if a.get("due_dt") and a["due_dt"].weekday() == weekday
                 and calendar_start <= a["due_dt"] < calendar_end + timedelta(days=1)]
        items.sort(key=lambda a: a["due_dt"])
        date_text = display_date.strftime("%b %d") if display_date else ""
        item_html = "".join(
            f'''<a class="calendar-assignment" href="{escape(a.get("url", ""), quote=True)}" target="_blank" rel="noopener noreferrer" style="--assignment-color:{escape(a["color"], quote=True)}">
                <span class="calendar-course">{escape(a["course_code"])}</span>
                <span class="calendar-name">{escape(a["name"])}</span>
                <time>{escape(a["due_dt"].strftime("%I:%M %p").lstrip("0"))}</time>
            </a>'''
            for a in items
        ) or '<p class="calendar-empty">No assignments due</p>'
        calendar_days.append(f'''<details class="calendar-day">
            <summary><span class="calendar-day-name">{label}</span><time>{escape(date_text)}</time><span class="calendar-day-count">{len(items)} assignment{'s' if len(items) != 1 else ''}</span><span class="calendar-day-chevron" aria-hidden="true"></span></summary>
            <div class="calendar-items">{item_html}</div>
        </details>''')
    unscheduled = [a for a in calendar_assignments if not a.get("due_dt") or a["due_dt"].weekday() == 6
                   or not (calendar_start <= a["due_dt"] < calendar_end + timedelta(days=1))]
    unscheduled_html = ""
    if unscheduled:
        unscheduled_html = '<details class="calendar-other"><summary>Other mapped work (' + str(len(unscheduled)) + ')</summary><ul>' + ''.join(
            f'<li>{escape(a["course_code"])} · {escape(a["name"])}</li>' for a in unscheduled
        ) + '</ul></details>'
    calendar_html = f'''<aside class="week-calendar" aria-label="Week assignment calendar">
        <div class="calendar-heading"><div><span class="calendar-eyebrow">Due this week</span><h2>Week calendar</h2></div><span class="calendar-count">{len(calendar_assignments)}</span></div>
        <p class="calendar-hint">Monday through Saturday · times shown in Mountain Time</p>
        {''.join(calendar_days)}
        {unscheduled_html}
    </aside>'''
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Study Assistant Lesson Plan · Week {week_num:02d}</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            line-height: 1.6;
            min-height: 100vh;
        }}

        .container {{
            max-width: 1380px;
            margin: 0 auto;
            padding: 2rem 1.5rem;
        }}

        .report-layout {{
            display: grid;
            grid-template-columns: minmax(0, 900px) minmax(300px, 390px);
            align-items: start;
            gap: 1.5rem;
        }}

        .course-list {{
            min-width: 0;
        }}

        .week-calendar {{
            position: sticky;
            top: 1rem;
            overflow: hidden;
            border: 1px solid #334155;
            border-radius: 1.25rem;
            background: #1e293b;
            box-shadow: 0 18px 45px rgba(2, 6, 23, 0.28);
        }}

        .calendar-heading {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            padding: 1.15rem 1.2rem 0.4rem;
        }}

        .calendar-heading h2 {{
            color: #f8fafc;
            font-size: 1.2rem;
            line-height: 1.2;
        }}

        .calendar-eyebrow {{
            display: block;
            color: #a5b4fc;
            font-size: 0.65rem;
            font-weight: 700;
            letter-spacing: 0.1em;
            text-transform: uppercase;
        }}

        .calendar-count {{
            display: grid;
            place-items: center;
            min-width: 2.2rem;
            height: 2.2rem;
            padding: 0 0.55rem;
            border-radius: 999px;
            background: #4f46e5;
            color: white;
            font-weight: 800;
        }}

        .calendar-hint {{
            padding: 0 1.2rem 1rem;
            color: #94a3b8;
            font-size: 0.72rem;
        }}

        .calendar-day {{
            border-top: 1px solid #334155;
            padding: 0.8rem 0.9rem 0.9rem;
        }}

        .calendar-day > summary {{
            display: grid;
            grid-template-columns: 1fr auto auto auto;
            align-items: center;
            gap: 0.75rem;
            color: #e2e8f0;
            font-size: 0.78rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            cursor: pointer;
            list-style: none;
        }}

        .calendar-day > summary::-webkit-details-marker {{
            display: none;
        }}

        .calendar-day > summary:focus-visible {{
            outline: 2px solid #a5b4fc;
            outline-offset: 4px;
            border-radius: 0.25rem;
        }}

        .calendar-day > summary time {{
            color: #64748b;
            font-weight: 600;
            letter-spacing: 0;
            text-transform: none;
        }}

        .calendar-day-count {{
            min-width: 5.9rem;
            padding: 0.16rem 0.45rem;
            border-radius: 999px;
            background: #0f172a;
            color: #a5b4fc;
            font-size: 0.62rem;
            text-align: center;
            letter-spacing: 0.02em;
            text-transform: none;
        }}

        .calendar-day-chevron {{
            width: 0.5rem;
            height: 0.5rem;
            border-right: 2px solid #94a3b8;
            border-bottom: 2px solid #94a3b8;
            transform: rotate(45deg);
            transition: transform 160ms ease;
        }}

        .calendar-day[open] .calendar-day-chevron {{
            transform: rotate(225deg);
        }}

        .calendar-items {{
            display: grid;
            gap: 0.45rem;
            margin-top: 0.55rem;
        }}

        .calendar-assignment {{
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 0.1rem 0.5rem;
            padding: 0.6rem 0.7rem;
            border: 1px solid #334155;
            border-left: 4px solid var(--assignment-color);
            border-radius: 0.65rem;
            background: #0f172a;
            color: #e2e8f0;
            text-decoration: none;
        }}

        .calendar-assignment:hover {{
            border-color: #64748b;
            border-left-color: var(--assignment-color);
            background: #111c31;
        }}

        .calendar-course {{
            color: var(--assignment-color);
            font-size: 0.65rem;
            font-weight: 800;
        }}

        .calendar-name {{
            grid-column: 1 / -1;
            color: #f8fafc;
            font-size: 0.78rem;
            font-weight: 650;
            line-height: 1.35;
        }}

        .calendar-assignment time {{
            grid-column: 2;
            grid-row: 1;
            justify-self: end;
            color: #94a3b8;
            font-size: 0.66rem;
        }}

        .calendar-empty {{
            color: #64748b;
            font-size: 0.75rem;
            font-style: italic;
        }}

        .calendar-other {{
            border-top: 1px solid #334155;
            padding: 0.8rem 1rem 1rem;
            color: #94a3b8;
            font-size: 0.75rem;
        }}

        .calendar-other ul {{
            padding: 0.6rem 0 0 1.1rem;
        }}

        /* ---- Hero Header ---- */
        .hero {{
            background: linear-gradient(135deg, #1e293b 0%, #0f172a 50%, #1e1b4b 100%);
            border: 1px solid #334155;
            border-radius: 1rem;
            padding: 0.85rem 1rem;
            margin-bottom: 1.25rem;
            position: relative;
            overflow: hidden;
        }}

        .hero::before {{
            content: '';
            position: absolute;
            top: -50%;
            right: -20%;
            width: 400px;
            height: 400px;
            background: radial-gradient(circle, rgba(99, 102, 241, 0.15) 0%, transparent 70%);
            border-radius: 50%;
        }}

        .hero::after {{
            content: '';
            position: absolute;
            bottom: -30%;
            left: -10%;
            width: 300px;
            height: 300px;
            background: radial-gradient(circle, rgba(14, 165, 233, 0.1) 0%, transparent 70%);
            border-radius: 50%;
        }}

        .hero-content {{
            position: relative;
            z-index: 1;
            display: flex;
            align-items: center;
            gap: 1rem;
            white-space: nowrap;
        }}

        .hero-label {{
            display: inline-block;
            background: linear-gradient(135deg, #6366f1, #8b5cf6);
            color: white;
            padding: 0.25rem 0.65rem;
            border-radius: 2rem;
            font-size: 0.62rem;
            font-weight: 600;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            flex: 0 0 auto;
        }}

        .hero-period {{
            display: flex;
            align-items: baseline;
            gap: 0.55rem;
            min-width: 0;
        }}

        .hero h1 {{
            font-size: 1.25rem;
            font-weight: 800;
            background: linear-gradient(135deg, #f8fafc, #94a3b8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            line-height: 1;
        }}

        .hero-dates {{
            font-size: 0.72rem;
            color: #94a3b8;
            font-weight: 400;
        }}

        .hero-stats {{
            display: flex;
            align-items: center;
            gap: 0.9rem;
            margin-left: auto;
        }}

        .hero-stat {{
            display: flex;
            align-items: baseline;
            gap: 0.25rem;
        }}

        .hero-stat-num {{
            font-size: 1.1rem;
            font-weight: 800;
            color: #f8fafc;
            line-height: 1;
        }}

        .hero-stat-label {{
            font-size: 0.55rem;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 500;
        }}

        /* ---- AI Summary ---- */
        .ai-summary {{
            position: relative;
            overflow: hidden;
            margin: 0 0 2rem;
            padding: 1.75rem 2rem;
            border: 1px solid rgba(167, 139, 250, 0.42);
            border-radius: 1.25rem;
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.26), rgba(30, 41, 59, 0.88) 55%, rgba(14, 165, 233, 0.16));
            box-shadow: 0 18px 45px rgba(15, 23, 42, 0.28), inset 0 1px 0 rgba(255, 255, 255, 0.10);
            backdrop-filter: blur(14px);
        }}

        .course-lesson {{
            padding: 1.5rem 2rem 0;
            border-top: 1px solid #334155;
        }}

        .course-lesson .ai-summary {{
            margin: 0;
        }}

        .ai-summary::after {{
            content: '';
            position: absolute;
            width: 180px;
            height: 180px;
            right: -70px;
            top: -85px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(196, 181, 253, 0.26), transparent 70%);
            pointer-events: none;
        }}

        .ai-summary-heading {{
            position: relative;
            z-index: 1;
            display: flex;
            align-items: center;
            gap: 0.65rem;
            margin-bottom: 0.8rem;
            color: #f8fafc;
            font-size: 1.15rem;
            font-weight: 750;
        }}

        .ai-summary-icon {{
            display: grid;
            place-items: center;
            width: 2rem;
            height: 2rem;
            border-radius: 0.65rem;
            background: rgba(255, 255, 255, 0.13);
        }}

        .ai-summary-content {{
            position: relative;
            z-index: 1;
            color: #dbeafe;
            line-height: 1.7;
        }}

        .ai-summary-content p + p, .ai-summary-content ul, .ai-summary-content ol {{
            margin-top: 0.7rem;
        }}

        .ai-summary-content ul, .ai-summary-content ol {{
            padding-left: 1.3rem;
        }}

        .ai-summary-content li + li {{
            margin-top: 0.8rem;
        }}

        .ai-summary-content code {{
            padding: 0.1rem 0.35rem;
            border-radius: 0.3rem;
            background: rgba(15, 23, 42, 0.65);
            color: #c4b5fd;
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
            font-size: 0.88em;
        }}

        /* ---- Course Section ---- */
        .course-section {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 1.25rem;
            margin-bottom: 2rem;
            overflow: hidden;
        }}

        .course-header {{
            background: var(--course-gradient);
            padding: 1rem 1.35rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 1rem;
            cursor: pointer;
            list-style: none;
            user-select: none;
        }}

        .course-header::-webkit-details-marker {{
            display: none;
        }}

        .course-header:focus-visible {{
            outline: 3px solid rgba(255, 255, 255, 0.9);
            outline-offset: -4px;
        }}

        .course-header:hover {{
            filter: brightness(1.05);
        }}

        .course-code {{
            font-size: 1.35rem;
            font-weight: 800;
            color: white;
            letter-spacing: -0.02em;
        }}

        .course-name {{
            font-size: 0.88rem;
            color: rgba(255, 255, 255, 0.85);
            font-weight: 400;
            margin-top: 0.05rem;
        }}

        .module-badge {{
            display: inline-block;
            background: rgba(255, 255, 255, 0.2);
            color: white;
            padding: 0.2rem 0.65rem;
            border-radius: 1rem;
            font-size: 0.72rem;
            font-weight: 500;
            margin-top: 0.4rem;
            backdrop-filter: blur(4px);
        }}

        .course-stats {{
            text-align: right;
            display: flex;
            align-items: center;
            gap: 1rem;
        }}

        .stat {{
            display: flex;
            flex-direction: column;
            align-items: center;
        }}

        .stat-number {{
            font-size: 1.55rem;
            font-weight: 800;
            color: white;
            line-height: 1.1;
        }}

        .stat-label {{
            font-size: 0.65rem;
            color: rgba(255, 255, 255, 0.75);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}

        .grade-stat .stat-number {{
            max-width: 10rem;
            font-size: 1rem;
            overflow-wrap: anywhere;
        }}

        .accordion-chevron {{
            width: 0.72rem;
            height: 0.72rem;
            border-right: 2px solid white;
            border-bottom: 2px solid white;
            transform: rotate(45deg);
            transition: transform 180ms ease;
            margin: -0.25rem 0.15rem 0 0;
        }}

        .course-section[open] .accordion-chevron {{
            transform: rotate(225deg);
            margin-top: 0.3rem;
        }}

        /* ---- Compact lesson plan ---- */
        .report-lesson-plan {{
            border-top: 1px solid #334155;
            background: #172033;
        }}

        .course-utility-strip {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.7rem 1.35rem;
            border-bottom: 1px solid #334155;
            background: #0f172a;
            color: #94a3b8;
            font-size: 0.75rem;
        }}

        .course-utility-strip a {{
            color: #cbd5e1;
            text-decoration: none;
        }}

        .course-utility-strip a:hover {{
            color: #fff;
            text-decoration: underline;
        }}

        .course-plan-link {{
            margin-right: auto;
            color: var(--course-primary) !important;
            font-weight: 750;
        }}

        .course-utility-strip > span,
        .course-utility-strip > a:not(.course-plan-link) {{
            padding: 0.25rem 0.55rem;
            border: 1px solid #334155;
            border-radius: 999px;
            white-space: nowrap;
        }}

        .course-utility-strip strong {{
            color: #f8fafc;
        }}

        .lesson-plan-heading {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            padding: 1.25rem 1.35rem 0.85rem;
        }}

        .lesson-eyebrow {{
            display: block;
            color: var(--course-primary);
            font-size: 0.65rem;
            font-weight: 800;
            letter-spacing: 0.11em;
            text-transform: uppercase;
        }}

        .lesson-plan-heading h3 {{
            margin-top: 0.15rem;
            color: #f8fafc;
            font-size: 1.08rem;
        }}

        .lesson-plan-button {{
            flex: 0 0 auto;
            padding: 0.5rem 0.75rem;
            border: 1px solid color-mix(in srgb, var(--course-primary), white 15%);
            border-radius: 0.6rem;
            color: #f8fafc;
            font-size: 0.76rem;
            font-weight: 750;
            text-decoration: none;
        }}

        .lesson-plan-button:hover {{
            background: var(--course-primary);
        }}

        .lesson-concepts {{
            display: grid;
            gap: 0.55rem;
            padding: 0 1.35rem 1.25rem;
        }}

        .lesson-concept {{
            padding: 0.8rem 0.9rem;
            border: 1px solid #334155;
            border-left: 3px solid var(--course-primary);
            border-radius: 0.75rem;
            background: #0f172a;
        }}

        .lesson-concept-heading {{
            display: flex;
            align-items: baseline;
            gap: 0.65rem;
        }}

        .lesson-concept-heading a {{
            color: #f8fafc;
            font-size: 0.9rem;
            font-weight: 750;
            text-decoration: none;
        }}

        .lesson-concept-heading a:hover {{
            color: var(--course-primary);
            text-decoration: underline;
        }}

        .lesson-number {{
            flex: 0 0 auto;
            color: var(--course-primary);
            font-size: 0.59rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }}

        .lesson-hook {{
            margin: 0.18rem 0 0 4.8rem;
            color: #94a3b8;
            font-size: 0.78rem;
        }}

        .lesson-preview {{
            margin: 0.55rem 0 0 4.8rem;
            color: #cbd5e1;
            font-size: 0.78rem;
        }}

        .lesson-preview > summary {{
            color: #94a3b8;
            cursor: pointer;
            font-weight: 650;
        }}

        .lesson-preview > summary:hover {{
            color: #e2e8f0;
        }}

        .lesson-preview-grid {{
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.65rem;
            margin-top: 0.65rem;
        }}

        .lesson-preview-grid section {{
            min-width: 0;
            padding: 0.7rem;
            border-radius: 0.55rem;
            background: #172033;
        }}

        .lesson-preview-grid h5 {{
            margin-bottom: 0.3rem;
            color: var(--course-primary);
            font-size: 0.68rem;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}

        .lesson-preview-grid p,
        .lesson-preview-grid pre {{
            color: #cbd5e1;
            font: inherit;
            line-height: 1.5;
            white-space: pre-wrap;
            overflow-wrap: anywhere;
        }}

        .lesson-empty {{
            padding: 0.8rem;
            color: #94a3b8;
            font-size: 0.82rem;
        }}

        /* ---- Section Cards ---- */
        .section-card {{
            padding: 1.5rem 2rem;
            border-top: 1px solid #334155;
        }}

        .section-title {{
            font-size: 1.15rem;
            font-weight: 700;
            color: #f1f5f9;
            margin-bottom: 1.25rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}

        .announcements-section > summary.section-title {{
            justify-content: space-between;
            margin-bottom: 0;
            cursor: pointer;
            list-style: none;
        }}

        .announcements-section > summary::-webkit-details-marker {{
            display: none;
        }}

        .announcements-section[open] > summary.section-title {{
            margin-bottom: 1.25rem;
        }}

        .announcement-count {{
            display: inline-grid;
            place-items: center;
            min-width: 1.65rem;
            height: 1.65rem;
            padding: 0 0.4rem;
            border-radius: 999px;
            background: #0f172a;
            color: #94a3b8;
            font-size: 0.72rem;
        }}

        .section-intro {{
            margin: -0.7rem 0 1.1rem;
            color: #94a3b8;
            font-size: 0.78rem;
        }}

        .assignments-section {{
            scroll-margin-top: 1rem;
        }}

        .section-icon {{
            font-size: 1.2rem;
        }}

        /* ---- BA 300 / BA 315 class preparation ---- */
        .class-preparation-section {{
            background: #172033;
        }}

        .class-prep-heading {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
        }}

        .class-prep-heading .section-title {{
            margin-bottom: 1.25rem;
        }}

        .class-prep-source {{
            margin-bottom: 1.25rem;
            color: var(--course-primary);
            font-size: 0.76rem;
            font-weight: 750;
            text-decoration: none;
        }}

        .class-prep-source:hover {{
            text-decoration: underline;
        }}

        .class-prep-cards {{
            display: grid;
            gap: 0.75rem;
        }}

        .class-prep-card {{
            display: grid;
            grid-template-columns: 9rem minmax(0, 1fr);
            border: 1px solid #334155;
            border-left: 3px solid var(--course-primary);
            border-radius: 0.8rem;
            background: #0f172a;
            overflow: hidden;
        }}

        .class-prep-date {{
            display: flex;
            flex-direction: column;
            justify-content: center;
            gap: 0.15rem;
            padding: 1rem;
            border-right: 1px solid #334155;
        }}

        .class-prep-date span {{
            color: var(--course-primary);
            font-size: 0.68rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }}

        .class-prep-date time {{
            color: #f8fafc;
            font-size: 0.92rem;
            font-weight: 750;
        }}

        .class-prep-columns {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }}

        .class-prep-columns section {{
            min-width: 0;
            padding: 0.9rem 1rem;
        }}

        .class-prep-columns section + section {{
            border-left: 1px solid #334155;
        }}

        .class-prep-columns h4 {{
            margin-bottom: 0.45rem;
            color: #e2e8f0;
            font-size: 0.74rem;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }}

        .class-prep-list {{
            padding-left: 1.1rem;
            color: #cbd5e1;
            font-size: 0.8rem;
            line-height: 1.5;
        }}

        .class-prep-list li + li {{
            margin-top: 0.25rem;
        }}

        .class-prep-list a {{
            color: #93c5fd;
            text-decoration: none;
        }}

        .class-prep-list a:hover {{
            color: #dbeafe;
            text-decoration: underline;
        }}

        .class-prep-links {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            margin-top: 0.65rem;
        }}

        .class-prep-links a {{
            padding: 0.25rem 0.45rem;
            border: 1px solid #334155;
            border-radius: 0.4rem;
            color: #93c5fd;
            font-size: 0.7rem;
            text-decoration: none;
        }}

        .class-prep-links a:hover {{
            border-color: #60a5fa;
            color: #dbeafe;
        }}

        .class-prep-empty {{
            color: #94a3b8;
            font-size: 0.8rem;
        }}

        /* ---- Weekly Concepts ---- */
        .concept-content {{
            padding: 1rem 1.15rem;
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 0.85rem;
            color: #cbd5e1;
            font-size: 0.9rem;
        }}

        .concept-content p + p,
        .concept-content ul,
        .concept-content ol {{
            margin-top: 0.75rem;
        }}

        .concept-content ul,
        .concept-content ol {{
            padding-left: 1.3rem;
        }}

        .concept-content li + li {{
            margin-top: 0.65rem;
        }}

        .concept-unavailable {{
            padding: 0.85rem 1rem;
            border-radius: 0.75rem;
            background: #0f172a;
            color: #94a3b8;
            font-size: 0.9rem;
        }}

        /* ---- Professor Announcements ---- */
        .announcement-list {{
            display: grid;
            gap: 0.75rem;
        }}

        .announcement-card {{
            padding: 1rem 1.1rem;
            border: 1px solid #334155;
            border-left: 3px solid var(--course-primary);
            border-radius: 0.75rem;
            background: #0f172a;
        }}

        .announcement-title {{
            color: #f8fafc;
            font-size: 0.95rem;
        }}

        .announcement-meta {{
            margin-top: 0.15rem;
            color: #64748b;
            font-size: 0.72rem;
        }}

        .announcement-message {{
            margin-top: 0.65rem;
            color: #cbd5e1;
            font-size: 0.85rem;
            line-height: 1.55;
        }}

        /* ---- Homework Groups ---- */
        .hw-group {{
            margin-bottom: 1.5rem;
        }}

        .hw-group:last-child {{
            margin-bottom: 0;
        }}

        .group-title {{
            font-size: 0.95rem;
            font-weight: 600;
            color: var(--course-primary);
            margin-bottom: 0.75rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding-bottom: 0.5rem;
            border-bottom: 1px solid #1e293b;
        }}

        .group-count {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: var(--course-primary);
            color: white;
            width: 1.5rem;
            height: 1.5rem;
            border-radius: 50%;
            font-size: 0.75rem;
            font-weight: 700;
        }}

        .hw-card {{
            background: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 0.75rem;
            padding: 1rem 1.25rem;
            margin-bottom: 0.6rem;
            transition: border-color 0.2s, transform 0.15s;
        }}

        .hw-card:hover {{
            border-color: #475569;
            transform: translateY(-1px);
        }}

        .hw-card.canvas-completed {{
            border-color: #166534;
            box-shadow: inset 3px 0 0 #22c55e;
        }}

        .hw-card.canvas-completed .lp-controls label {{
            color: #86efac;
            font-weight: 700;
        }}

        .hw-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 1rem;
            flex-wrap: wrap;
        }}

        .hw-name {{
            font-size: 0.95rem;
            font-weight: 600;
            color: #f1f5f9;
            flex: 1;
            min-width: 200px;
        }}

        .hw-badges {{
            display: flex;
            gap: 0.4rem;
            flex-shrink: 0;
        }}

        .type-badge {{
            padding: 0.2rem 0.6rem;
            border-radius: 1rem;
            font-size: 0.7rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }}

        .type-quiz {{
            background: #312e81;
            color: #a5b4fc;
        }}

        .type-submission {{
            background: #064e3b;
            color: #6ee7b7;
        }}

        .type-external-tool {{
            background: #78350f;
            color: #fcd34d;
        }}

        .type-text-entry {{
            background: #1e3a5f;
            color: #7dd3fc;
        }}

        .type-no-submission {{
            background: #374151;
            color: #9ca3af;
        }}

        .type-other {{
            background: #374151;
            color: #d1d5db;
        }}

        .type-canvas-item {{
            background: #1e3a5f;
            color: #7dd3fc;
        }}

        .assignment-source-missing p {{
            color: #94a3b8;
            font-size: 0.85rem;
        }}

        .assignment-source-missing code {{
            color: #c4b5fd;
        }}

        .points-badge,
        .weight-badge {{
            background: #1e293b;
            padding: 0.2rem 0.6rem;
            border-radius: 1rem;
            font-size: 0.7rem;
            font-weight: 600;
            border: 1px solid #374151;
        }}

        .points-badge {{
            color: #fbbf24;
        }}

        .weight-badge {{
            color: #c4b5fd;
        }}

        .hw-due {{
            font-size: 0.82rem;
            color: #94a3b8;
            display: flex;
            align-items: center;
            gap: 0.35rem;
        }}

        .hw-dates {{
            display: grid;
            gap: 0.3rem;
            margin-top: 0.5rem;
        }}

        .hw-until {{
            font-size: 0.82rem;
            color: #94a3b8;
            display: flex;
            align-items: center;
            gap: 0.35rem;
        }}

        .due-icon {{
            font-size: 0.85rem;
        }}

        .hw-description {{
            font-size: 0.82rem;
            color: #64748b;
            margin-top: 0.5rem;
            line-height: 1.5;
        }}

        .canvas-link {{
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            margin-top: 0.85rem;
            color: var(--course-primary);
            font-size: 0.82rem;
            font-weight: 700;
            text-decoration: none;
        }}

        .canvas-link:hover {{
            color: var(--course-dark);
            text-decoration: underline;
        }}

        /* ---- Footer ---- */
        .footer {{
            text-align: center;
            padding: 2rem;
            color: #475569;
            font-size: 0.8rem;
        }}

        .footer a {{
            color: #6366f1;
            text-decoration: none;
        }}

        /* ---- Responsive ---- */
        @media (max-width: 1040px) {{
            .report-layout {{
                grid-template-columns: 1fr;
            }}
            .week-calendar {{
                position: static;
                grid-row: 1;
            }}
        }}

        @media (max-width: 640px) {{
            .container {{
                padding: 1rem;
            }}
            .hero {{
                padding: 0.75rem;
            }}
            .hero-content {{
                gap: 0.65rem;
                overflow-x: auto;
                scrollbar-width: thin;
            }}
            .hero-stats {{
                gap: 0.65rem;
            }}
            .course-header {{
                padding: 0.9rem 1rem;
                gap: 0.75rem;
            }}
            .course-stats {{
                gap: 0.75rem;
            }}
            .course-code {{
                font-size: 1.15rem;
            }}
            .course-name {{
                font-size: 0.78rem;
            }}
            .course-utility-strip {{
                align-items: flex-start;
                flex-wrap: wrap;
                padding: 0.7rem 1rem;
            }}
            .course-plan-link {{
                flex-basis: 100%;
            }}
            .lesson-plan-heading {{
                align-items: flex-start;
                flex-direction: column;
                padding: 1rem 1rem 0.75rem;
            }}
            .lesson-concepts {{
                padding: 0 1rem 1rem;
            }}
            .lesson-concept-heading {{
                align-items: flex-start;
                flex-direction: column;
                gap: 0.15rem;
            }}
            .lesson-hook,
            .lesson-preview {{
                margin-left: 0;
            }}
            .class-prep-heading {{
                align-items: flex-start;
                flex-direction: column;
                gap: 0;
            }}
            .class-prep-card {{
                grid-template-columns: 1fr;
            }}
            .class-prep-date {{
                border-right: 0;
                border-bottom: 1px solid #334155;
            }}
            .class-prep-columns {{
                grid-template-columns: 1fr;
            }}
            .class-prep-columns section + section {{
                border-top: 1px solid #334155;
                border-left: 0;
            }}
            .lesson-preview-grid {{
                grid-template-columns: 1fr;
            }}
            .hw-header {{
                flex-direction: column;
                gap: 0.5rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="hero">
            <div class="hero-content">
                <span class="hero-label">Study Assistant</span>
                <div class="hero-period">
                    <h1>Lesson Plan · Week {week_num:02d}</h1>
                    <p class="hero-dates">{week_start_str} – {week_end_str}</p>
                </div>
                <div class="hero-stats">
                    <div class="hero-stat">
                        <span class="hero-stat-num">{total_assignments}</span>
                        <span class="hero-stat-label">Total Assignments</span>
                    </div>
                    <div class="hero-stat">
                        <span class="hero-stat-num">{len(courses_data)}</span>
                        <span class="hero-stat-label">Courses</span>
                    </div>
                    <div class="hero-stat">
                        <span class="hero-stat-num">{week_num}/14</span>
                        <span class="hero-stat-label">Semester Calendar</span>
                    </div>
                </div>
            </div>
        </div>

        <div class="report-layout">
            <div class="course-list">{course_sections_html}</div>
            {calendar_html}
        </div>

        <div class="footer">
            <p>Generated on {datetime.now().strftime("%B %d, %Y at %I:%M %p")} · Homework Assistant</p>
        </div>
    </div>
</body>
</html>"""
    
    if any(cd.get("course_plan") for cd in courses_data):
        assets = '<link rel="stylesheet" href="assets/learning.css"><script src="assets/learning-manifest.js" defer></script><script src="assets/progress.js" defer></script>'
        html = html.replace('</head>', assets + '</head>')
        html = html.replace('<div class="container">', '<div class="container">' + learning_toolbar(), 1)
    return html


# ---------------------------------------------------------------------------
# Course-design artifacts and packaging
# ---------------------------------------------------------------------------

def write_test_artifact(root, relative_path, content):
    """Write a course-design fixture and return its filesystem path."""
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def create_week_2_course_design_artifacts(week_num):
    """Create valid, synthetic Week 2 fixtures limited to taught concepts."""
    if week_num != 2:
        return []

    root = OUTPUT_DIR / f"week_{week_num:02d}_course_design_test_artifacts"
    root.mkdir(parents=True, exist_ok=True)
    artifacts = []

    def add(relative_path, content):
        artifacts.append(write_test_artifact(root, relative_path, content))

    add("DS250/core_task_2_testing_test_submission.html", """<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"UTF-8\"><title>TEST ONLY — Core Task 2</title></head><body><h1>TEST ONLY — Code and Output Fixture</h1><p>This synthetic HTML upload verifies the Canvas file-upload path; it is not learner work.</p><h2>Code</h2><pre>first_number = 5\nsecond_number = 7\ntotal = first_number + second_number\nprint(total)</pre><h2>Output</h2><pre>12</pre><p>Verification: 5 + 7 equals 12, so the displayed output matches the calculation.</p></body></html>\n""")
    add("DS250/icebreaker_test_text.md", """# TEST ONLY — Icebreaker Paste-in Fixture\n\nI am Test Student, a fictional account used to verify the Week 2 workflow. I am interested in learning how clear questions and small programs can turn data into useful answers. Outside class, this sample learner enjoys science-fiction and number puzzles.\n\nThis fixture is only for a test account. Verify separately that the activity asks for a profile picture and two replies with a recommendation and reason.\n""")
    add("DS250/critical_questioning_test_text.md", """# TEST ONLY — Critical Questioning Response\n\n**Question:** When a short Python calculation prints an unexpected value, how can I tell whether the input values or the calculation caused the problem?\n\n**Reasoning:** Use values with a known answer first. For example, `5 + 7` should produce `12`. If it does not, inspect the assigned values and then the expression. This isolates one checkable part of the program and tests the output.\n""")
    add("DS350/asking_good_questions/readme.md", """# TEST ONLY — Potential Semester-Project Questions\n\nThis synthetic `readme.md` fixture tests the GitHub-link submission workflow. Replace the placeholder links and fictional feedback before using it outside a test account.\n\n## 1. Flight departure delays\n\n**Question:** How does the distribution of departure delay differ by month for flights leaving New York City in 2013?\n\n**Comparable work:** `[TEST: insert a verified public URL]`\n\n**Fictional feedback:** Reviewer A said the population, measure, and comparison are clear. Reviewer B requested an explanation of missing delays. Reviewer C found the question feasible.\n\n## 2. Arrival and departure delays\n\n**Question:** What relationship appears between departure delay and arrival delay for New York City flights in 2013?\n\n**Comparable work:** `[TEST: insert a verified public URL]`\n\n**Fictional feedback:** Reviewer A recommended a scatterplot. Reviewer B said the variables support the question. Reviewer C asked that early arrivals remain visible.\n\n## 3. Airport traffic\n\n**Question:** How does the number of departing flights vary across the three New York City airports in 2013?\n\n**Comparable work:** `[TEST: insert a verified public URL]`\n\n**Fictional feedback:** Reviewer A said the categorical comparison is clear. Reviewer B recommended reporting counts. Reviewer C found it useful for a travel audience.\n\n## 4. Distance and delay\n\n**Question:** Does the distribution of arrival delay differ for shorter and longer flights departing New York City in 2013?\n\n**Comparable work:** `[TEST: insert a verified public URL]`\n\n**Fictional feedback:** Reviewer A requested a definition of shorter and longer. Reviewer B said the listed data can answer it. Reviewer C judged it feasible.\n""")
    add("DS350/visualizing_large_distributions_test.qmd", """---\ntitle: \"TEST ONLY — Visualizing Large Distributions\"\nformat: gfm\n---\n\n```{r}\n#| message: false\nlibrary(tidyverse)\nflights_data <- nycflights13::flights\n```\n\n## Two univariate distributions\n\n```{r}\nggplot(flights_data, aes(x = dep_delay)) +\n  geom_histogram()\n\nggplot(flights_data, aes(x = arr_delay)) +\n  geom_histogram()\n```\n\n## A bivariate view\n\n```{r}\nggplot(flights_data, aes(x = dep_delay, y = arr_delay)) +\n  geom_point()\n```\n\n## Test-fixture interpretation\n\nThis fixture renders two separate distributions and one plot containing both selected variables. Inspect the rendered plots before making any claims: the text here does not assert results that have not been observed. Histograms show each delay separately; the point plot displays the two numerical variables together.\n""")
    add("DS350/gapminder_part_1_test.qmd", """---\ntitle: \"TEST ONLY — Gapminder Part 1\"\nformat: gfm\n---\n\n```{r}\n#| message: false\nlibrary(tidyverse)\nlibrary(gapminder)\n\ngapminder_without_kuwait <- gapminder |>\n  filter(country != \"Kuwait\")\n```\n\n```{r}\nggplot(\n  gapminder_without_kuwait,\n  aes(x = gdpPercap, y = lifeExp, size = pop, color = continent)\n) +\n  geom_point() +\n  theme_bw() +\n  scale_y_continuous(trans = \"sqrt\")\n```\n\n## Test-fixture note\n\nThis fixture uses the required Gapminder data, removes Kuwait, maps data to visual aesthetics, adds a point layer, applies the requested theme, and uses the specified square-root y scale. Compare the rendered chart with the assignment reference before evaluating the workflow.\n""")
    add("README.md", """# Week 02 Course-Design Test Artifacts\n\nThese are **synthetic fixtures** for course-design testing. They use only Week 1–2 programming, question-framing, and grammar-of-graphics ideas. They must not be represented as learner work.\n\n## Local files\n\n| Assignment | Fixture | Test submission path |\n| --- | --- | --- |\n| DS 250 Core Task 2 | `DS250/core_task_2_testing_test_submission.html` | Upload the HTML fixture to test file handling. |\n| DS 250 Icebreaker | `DS250/icebreaker_test_text.md` | Copy into a Slack test account. |\n| DS 250 Critical Questioning | `DS250/critical_questioning_test_text.md` | Paste into an online-text test response. |\n| DS 250 Digital Profiles | `DS250/digital_profiles_test_response.md` | Verify the external-account workflow. |\n| DS 350 Asking Good Questions | `DS350/asking_good_questions/readme.md` | Push to test GitHub; submit its `readme.md` URL. |\n| DS 350 Large Distributions | `DS350/visualizing_large_distributions_test.qmd` | Render, push generated files to test GitHub; submit the `.md` URL. |\n| DS 350 Gapminder Part 1 | `DS350/gapminder_part_1_test.qmd` | Render, push generated files to test GitHub; submit the `.md` URL. |\n\n## Account-only cases\n\nCanvas quizzes, Perusall comments, Slack replies, and actual profile creation require a dedicated test account. The Course Goals Letter needs the exact competency table for the chosen grade from the current syllabus; no fixture is generated because the snapshot does not contain that table.\n""")

    try:
        from docx import Document
        letter_path = root / "DS250" / "course_goals_letter_test_fixture.docx"
        letter_path.parent.mkdir(parents=True, exist_ok=True)
        document = Document()
        document.add_heading("TEST ONLY — Course Goals Letter", level=1)
        document.add_paragraph("This is a file-format and workflow fixture, not learner work.")
        document.add_heading("Final grade goal", level=2)
        document.add_paragraph("[TEST: select a grade for the test scenario]")
        document.add_heading("Course competency table", level=2)
        document.add_paragraph("[TEST: paste the exact current-syllabus competency table for that grade; do not invent requirements]")
        document.add_heading("Plan", level=2)
        document.add_paragraph("[TEST: map a fictional learner plan to the pasted competencies]")
        document.save(letter_path)
        artifacts.append(letter_path)
    except ImportError:
        add("DS250/course_goals_letter_test_fixture.txt", "DOCX fixture unavailable: install python-docx. Use the exact syllabus competency table; do not invent one.\n")

    try:
        from PIL import Image, ImageDraw
        screenshot_path = root / "DS250" / "core_task_2_code_output_screenshot.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1000, 480), "#0f172a")
        draw = ImageDraw.Draw(image)
        draw.text((32, 30), "TEST ONLY — W02 U0: Core Task 2", fill="#f8fafc")
        draw.text((32, 90), "first_number = 5\nsecond_number = 7\ntotal = first_number + second_number\nprint(total)", fill="#c4b5fd", spacing=10)
        draw.text((32, 285), "Output\n12", fill="#86efac", spacing=10)
        draw.text((32, 390), "Synthetic screenshot for Canvas workflow testing; not learner work.", fill="#94a3b8")
        image.save(screenshot_path, "PNG")
        artifacts.append(screenshot_path)
    except ImportError:
        pass

    readme_path = root / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8")
    readme_text = readme_text.replace(
        "| DS 250 Core Task 2 | `DS250/core_task_2_testing_test_submission.html` | Upload the HTML fixture to test file handling. |\n",
        "| DS 250 Core Task 2 | HTML fixture and `DS250/core_task_2_code_output_screenshot.png` | Test the requested code/output screenshot workflow. |\n",
    ).replace(
        "| DS 250 Digital Profiles | `DS250/digital_profiles_test_response.md` | Verify the external-account workflow. |\n",
        "",
    ).replace(
        "The Course Goals Letter needs the exact competency table for the chosen grade from the current syllabus; no fixture is generated because the snapshot does not contain that table.",
        "The Course Goals Letter DOCX fixture contains placeholders: paste the exact competency table from the current syllabus before evaluating submission content.",
    )
    readme_path.write_text(readme_text, encoding="utf-8")

    return artifacts


def zip_html(html_content, week_num, artifacts=None):
    """Zip the HTML report and optional course-design artifacts."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    html_filename = f"week_{week_num:02d}_summary_{date_str}.html"
    zip_filename = f"week_{week_num:02d}_summary_{date_str}.zip"
    zip_path = OUTPUT_DIR / zip_filename
    
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(html_filename, html_content)
        for folder in (OUTPUT_DIR / "course_plans", OUTPUT_DIR / "assets"):
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(OUTPUT_DIR).as_posix())
        readme = OUTPUT_DIR / "LEARNING_README.txt"
        if readme.exists():
            zf.write(readme, readme.name)
        # The dashboard may link older reports, so include them to keep navigation intact.
        for path in sorted(OUTPUT_DIR.glob("week_*_summary_*.html")):
            if path.name != html_filename:
                zf.write(path, path.name)
        for artifact in artifacts or []:
            zf.write(artifact, artifact.relative_to(OUTPUT_DIR).as_posix())
    
    print(f"  📦 Zipped report: {zip_path}")
    return zip_path, html_filename


def send_email(zip_path, html_filename, week_num, env):
    """Send the zipped HTML report via Gmail SMTP."""
    gmail_address = env.get("GMAIL_ADDRESS")
    gmail_password = env.get("GMAIL_APP_PASSWORD")
    recipient = env.get("RECIPIENT_EMAIL", gmail_address)
    
    if not gmail_address or not gmail_password:
        print("  ❌ Missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD in .env")
        print("     Copy .env.example to .env and fill in your credentials.")
        return False
    
    # Build email
    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = recipient
    msg["Subject"] = f"📚 Week {week_num:02d} — Homework Summary"
    
    body = f"""Hi!

Here's your weekly homework summary for Week {week_num:02d}.

Open the attached HTML file in your browser to see your assignments and learning materials for the week.

— Homework Assistant 🤖
"""
    msg.attach(MIMEText(body, "plain"))
    
    # Attach zip
    with open(zip_path, "rb") as f:
        part = MIMEBase("application", "zip")
        part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename={zip_path.name}",
        )
        msg.attach(part)
    
    # Send
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_password)
            server.send_message(msg)
        print(f"  ✅ Email sent to {recipient}")
        return True
    except Exception as e:
        print(f"  ❌ Failed to send email: {e}")
        return False


# ---------------------------------------------------------------------------
# Canvas Data Refresh
# ---------------------------------------------------------------------------

def refresh_canvas_data(anonymous_ol_account=False):
    """Run the fetch_course.py script to get fresh data."""
    import subprocess
    
    fetch_script = SCRIPT_DIR / "fetch_course.py"
    if not fetch_script.exists():
        print("  ⚠️  fetch_course.py not found. Using existing snapshots.")
        raise RuntimeError("fetch_course.py was not found")
    
    print("🔄 Refreshing Canvas data...")
    print("   A browser window will open. Please log in if needed.")
    print()
    
    try:
        command = [sys.executable, str(fetch_script)]
        if anonymous_ol_account:
            command.append("--anonymous")
        result = subprocess.run(
            command,
            cwd=str(SCRIPT_DIR),
            timeout=900,
        )
        if result.returncode == 0:
            print("\n  ✅ Canvas data refreshed successfully!")
            return True
        else:
            raise RuntimeError(f"OL_Account Canvas fetch exited with code {result.returncode}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("OL_Account Canvas fetch timed out after 15 minutes")
    except Exception as e:
        raise RuntimeError(f"OL_Account Canvas fetch failed: {e}") from e


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate weekly homework summary")
    parser.add_argument("--week", type=int, choices=range(1, 15), help="Override week number (1-14)")
    parser.add_argument("--no-fetch", action="store_true", help="Reuse the existing OL_Account Canvas fetch")
    parser.add_argument("--no-email", action="store_true", help="Generate report only, don't send email")
    parser.add_argument("--no-ai", action="store_true", help="Skip external AI lesson-plan generation")
    parser.add_argument(
        "--anonymous-ol-account",
        action="store_true",
        help="Use a fresh OL_Account Chrome context without loading or saving cookies",
    )
    args = parser.parse_args()
    
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║         📚 Weekly Homework Summary              ║")
    print("╚══════════════════════════════════════════════════╝")
    print()
    
    # Step 1: Refresh OL_Account Canvas course content.
    if not args.no_fetch:
        try:
            refresh_canvas_data(anonymous_ol_account=args.anonymous_ol_account)
        except RuntimeError as error:
            print(f"\n❌ {error}")
            sys.exit(1)
    else:
        print("⏭️  Reusing the existing OL_Account Canvas fetch (--no-fetch)")
    
    # Step 2: Find latest snapshot
    snapshot_dir = get_latest_snapshot_dir()
    if not snapshot_dir:
        print("\n❌ No snapshots found! Run the script without --no-fetch first.")
        sys.exit(1)
    
    print(f"\n📂 Using snapshot: {snapshot_dir.name}")
    from build_course_plans import build as build_learning_plans
    course_plans = {p["course_id"]: p for p in build_learning_plans(snapshot_dir, OUTPUT_DIR)}

    print("🔗 Using OL_Account content for lessons; assignment cards require the student Grades-page scrape")
    
    # Step 3: Determine current week
    week_num = get_current_week_number(args.week)
    week_start, week_end = get_week_date_range(week_num)
    print(f"📅 Week {week_num:02d}: {week_start.strftime('%B %d')} – {week_end.strftime('%B %d, %Y')}")
    
    # Step 4: Process each course
    courses_data = []
    student_grades, student_grade_manifest = load_latest_student_grades()
    student_assignments, student_assignment_manifest = load_latest_student_assignments()
    class_preparation, class_preparation_manifest = load_latest_class_preparation()
    if student_grade_manifest:
        print(f"🎓 Using student grades from: {student_grade_manifest.parent.name}")
    else:
        print("🎓 No completed student grade capture is available")
    if student_assignment_manifest:
        print(
            "📋 Using student Grades-page assignments from: "
            f"{student_assignment_manifest.parent.name}"
        )
    else:
        print("📋 No completed student Grades-page assignment scrape is available")
    if class_preparation_manifest:
        print(
            "🏫 Using landing-page class preparation from: "
            f"{class_preparation_manifest.parent.name}"
        )
    else:
        print("🏫 No completed BA 300/315 class-preparation scrape is available")
    
    for course_id in COURSE_IDS:
        print(f"\n{'─' * 50}")
        data = load_course_data(snapshot_dir, course_id)
        if not data:
            plan = course_plans.get(course_id)
            if plan:
                print(f"⚠️  {plan['course_code']}: no Canvas export; including source-gap plan")
                captured = student_assignments.get(course_id)
                records = student_assignment_records(captured, [], plan, course_id)
                mapped_ids = {
                    str(r.get("canvas_id"))
                    for r in plan["requirements"]
                    if week_num in r["learning_weeks"] or r["due_week"] == week_num
                }
                grouped_assignments, all_assignments = extract_week_assignments(
                    records, [], week_num, week_start, week_end,
                    mapped_assignment_ids=mapped_ids,
                ) if captured else ({}, [])
                courses_data.append({
                    "course_id": course_id,
                    "course_plan": plan,
                    "course_name": plan["name"],
                    "course_code": plan["course_code"],
                    "module_topic": "",
                    "learning_items": [],
                    "announcements": [],
                    "grouped_assignments": grouped_assignments,
                    "all_assignments": all_assignments,
                    "assignment_source_available": captured is not None,
                    "class_preparation": class_preparation.get(course_id),
                    "student_grade": student_grades.get(course_id),
                })
            continue

        plan = course_plans.get(course_id)
        mapped_ids = set()
        if plan:
            normalized = {r["canvas_id"]: r for r in plan["requirements"]}
            for assignment in data["assignments"]:
                req = normalized.get(str(assignment.get("id")))
                if req:
                    assignment["_plan_notes"] = req["notes"]
                    if "exported_due_at" in req:
                        assignment["due_at"] = req["due_at"]
                    if week_num in req["learning_weeks"] or req["due_week"] == week_num:
                        mapped_ids.add(req["canvas_id"])
        
        course_name = data["course_info"].get("name", f"Course {course_id}")
        course_code = data["course_info"].get("course_code", f"Course {course_id}")
        print(f"📖 {course_code}: {course_name}")
        
        # Extract week modules
        week_modules = extract_week_modules(data["modules"], week_num)
        
        # Get module topic from module name
        module_topic = ""
        if week_modules:
            mod_name = week_modules[0]["name"]
            # Extract topic part (after "Week XX" prefix)
            topic_match = re.search(r"Week\s+\d+\s*[:\-—]?\s*(.*)", mod_name, re.IGNORECASE)
            if topic_match:
                module_topic = topic_match.group(1).strip()
        
        # Extract learning items from all matching modules
        learning_items = []
        for mod in week_modules:
            learning_items.extend(extract_learning_items(mod.get("items", []), data["pages"]))
        
        print(f"  📚 Learning items: {len(learning_items)}")

        announcements = extract_week_announcements(
            data["announcements"], week_start, week_end
        )
        print(f"  📢 Professor announcements: {len(announcements)}")
        
        # The report's actionable assignment list comes only from the other
        # account's student Grades-page scrape. OL data may enrich matching
        # rows with scheduling metadata, but it never contributes extra rows.
        captured = student_assignments.get(course_id)
        if captured is not None:
            assignment_records = student_assignment_records(
                captured, data["assignments"], plan, course_id
            )
            grouped_assignments, all_assignments = extract_week_assignments(
                assignment_records,
                data["assignment_groups"],
                week_num,
                week_start,
                week_end,
                mapped_assignment_ids=mapped_ids,
            )
        else:
            grouped_assignments, all_assignments = {}, []
        
        print(f"  ✏️  Assignments: {len(all_assignments)}")
        for group_name, items in grouped_assignments.items():
            print(f"     • {group_name}: {len(items)}")
        
        courses_data.append({
            "course_id": course_id,
            "course_plan": course_plans.get(course_id),
            "course_name": course_name,
            "course_code": course_code,
            "module_topic": module_topic,
            "learning_items": learning_items,
            "announcements": announcements,
            "grouped_assignments": grouped_assignments,
            "all_assignments": all_assignments,
            "assignment_source_available": captured is not None,
            "class_preparation": class_preparation.get(course_id),
            "student_grade": student_grades.get(course_id),
        })
    
    # Step 5: Generate HTML
    print(f"\n{'─' * 50}")
    
    env = load_env()
    if args.no_ai or all(cd.get("course_plan") for cd in courses_data):
        print("  📚 Using reviewed local concept lessons; no external AI call needed")
        ai_summaries = {}
    else:
        ai_summaries = generate_ai_summary(week_num, courses_data, env)
    
    print("📝 Generating HTML report...")
    html = generate_html_report(week_num, week_start, week_end, courses_data, ai_summaries=ai_summaries)
    
    # Step 6: Create course-design fixtures and package the report
    artifacts = create_week_2_course_design_artifacts(week_num)
    if artifacts:
        print(f"  🧪 Created {len(artifacts)} course-design test artifacts")
    # Make the new report visible to the dashboard before packaging its links.
    preview_name = f"week_{week_num:02d}_summary_{datetime.now().strftime('%Y-%m-%d')}.html"
    (OUTPUT_DIR / preview_name).write_text(html, encoding="utf-8")
    from course_plans.render import render_all
    render_all(list(course_plans.values()), OUTPUT_DIR)
    zip_path, html_filename = zip_html(html, week_num, artifacts=artifacts)
    
    # Also save the raw HTML for convenience
    html_path = OUTPUT_DIR / html_filename
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  📄 HTML report: {html_path}")

    # Step 7: Send email
    if not args.no_email:
        print()
        send_email(zip_path, html_filename, week_num, env)
    else:
        print("\n⏭️  Skipping email (--no-email)")
    
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║              ✅ All done!                       ║")
    print("╚══════════════════════════════════════════════════╝")
    print()


if __name__ == "__main__":
    main()
