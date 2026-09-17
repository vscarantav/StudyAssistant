"""
Canvas API Course Fetcher (Live Browser Session)
Opens a visible browser, lets you log in + MFA, then fetches all course
content via fetch() calls from inside the authenticated page.

Usage:
    python fetch_course.py
    
    1. A browser window will open to Canvas login
    2. Log in and complete MFA
    3. The script detects the completed login and fetches automatically
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from playwright.sync_api import Error as PlaywrightError, sync_playwright
from course_plans.sources import COURSES

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COURSE_IDS = list(COURSES)
BASE_URL = "https://byui.instructure.com"
API_BASE = f"{BASE_URL}/api/v1"
OL_ACCOUNT_LOGIN_URL = f"{BASE_URL}/login/canvas"
OL_ACCOUNT_LOGIN_SUCCESS_URL = f"{BASE_URL}/?login_success=1"

PER_PAGE = 100
SCRIPT_DIR = Path(__file__).parent.resolve()
OL_ACCOUNT_STATE_PATH = SCRIPT_DIR / "data" / "ol_account_canvas_state.json"
NAVIGATION_LOG_DIR = SCRIPT_DIR / "data" / "navigation_logs"


def redact_url(raw_url):
    """Keep destinations useful while removing OAuth/session query values."""
    parts = urlsplit(raw_url)
    redacted_query = urlencode([(key, "<redacted>") for key, _ in parse_qsl(parts.query)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, redacted_query, ""))


class NavigationTracker:
    """Record OL_Account browser navigations for redirect troubleshooting."""

    def __init__(self):
        NAVIGATION_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.path = NAVIGATION_LOG_DIR / f"ol_account_navigation_{timestamp}.log"
        self.sequence = 0

    def write(self, event, url, status=None):
        self.sequence += 1
        status_text = f" status={status}" if status is not None else ""
        line = f"{self.sequence:03d} {event}{status_text} {redact_url(url)}"
        print(f"   🧭 {line}")
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(line + "\n")

    def attach(self, page):
        page.on(
            "framenavigated",
            lambda frame: self.write("COMMIT", frame.url) if frame == page.main_frame else None,
        )
        page.on(
            "response",
            lambda response: self.write("RESPONSE", response.url, response.status)
            if response.request.is_navigation_request() and response.request.frame == page.main_frame
            else None,
        )

# ---------------------------------------------------------------------------
# In-browser API caller
# ---------------------------------------------------------------------------

def browser_fetch(page, url: str) -> dict:
    """
    Makes a fetch() call from inside the browser page.
    Returns dict with 'status', 'ok', 'body', 'is_json', 'link_header'.
    """
    try:
        return page.evaluate('''async (url) => {
            try {
                const resp = await fetch(url, {
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' }
                });
                const text = await resp.text();
                let body = null;
                let isJson = false;
                try {
                    body = JSON.parse(text);
                    isJson = true;
                } catch(e) {
                    body = text.substring(0, 500);
                }
                const linkHeader = resp.headers.get('Link') || '';
                return {
                    status: resp.status,
                    ok: resp.ok,
                    body: body,
                    is_json: isJson,
                    link_header: linkHeader
                };
            } catch(e) {
                return { status: 0, ok: false, body: e.message, is_json: false, link_header: '' };
            }
        }''', url)
    except PlaywrightError as error:
        # Login redirects briefly destroy the page's JavaScript context. Treat
        # that as a transient unauthenticated check and retry from the caller.
        return {
            "status": 0,
            "ok": False,
            "body": str(error),
            "is_json": False,
            "link_header": "",
        }


def show_login_notice(page, message):
    """Place a blocking, high-contrast account reminder over the login page."""
    page.evaluate("""(message) => {
        const oldNotice = document.getElementById('homework-assistant-login-notice');
        if (oldNotice) oldNotice.remove();

        const overlay = document.createElement('div');
        overlay.id = 'homework-assistant-login-notice';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.style.cssText = [
            'position:fixed', 'inset:0', 'z-index:2147483647',
            'display:flex', 'align-items:center', 'justify-content:center',
            'background:rgba(15,23,42,.82)', 'font-family:Arial,sans-serif'
        ].join(';');
        overlay.innerHTML = `
            <div style="max-width:520px;margin:24px;padding:36px;border-radius:18px;
                        background:white;color:#0f172a;text-align:center;
                        box-shadow:0 24px 70px rgba(0,0,0,.45)">
                <div style="font-size:25px;font-weight:800;margin-bottom:14px">${message}</div>
                <div style="font-size:16px;color:#475569;margin-bottom:24px">
                    This account is used to fetch the course content for the report.
                </div>
                <button type="button" id="homework-assistant-login-continue" style="border:0;border-radius:10px;
                        padding:12px 22px;background:#2563eb;color:white;font-size:16px;
                        font-weight:700;cursor:pointer">Continue to login</button>
            </div>`;
        document.body.appendChild(overlay);
        document.getElementById('homework-assistant-login-continue').onclick = () => overlay.remove();
    }""", message)
    page.bring_to_front()


def install_ol_account_login_redirect_guard(page):
    """Keep a completed local Canvas login from being handed off to SAML SSO."""
    forced_redirect = {"used": False}

    def handle_route(route):
        parts = urlsplit(route.request.url)
        is_saml_navigation = (
            route.request.is_navigation_request()
            and parts.netloc == urlsplit(BASE_URL).netloc
            and parts.path.rstrip("/") == "/login/saml"
        )
        if is_saml_navigation:
            forced_redirect["used"] = True
            print(f"   ↪️  Canvas requested SSO; forcing {OL_ACCOUNT_LOGIN_SUCCESS_URL}")
            route.fulfill(
                status=302,
                headers={"Location": OL_ACCOUNT_LOGIN_SUCCESS_URL, "Cache-Control": "no-store"},
                body="",
            )
            return
        route.continue_()

    page.route("**/*", handle_route)
    return forced_redirect


def paginated_get(page, url: str, params: dict | None = None) -> list:
    """Paginated GET using in-browser fetch."""
    results = []
    params = params or {}
    params["per_page"] = str(PER_PAGE)

    query = "&".join(f"{k}={v}" for k, v in params.items())
    next_url = f"{url}?{query}" if query else url

    while next_url:
        resp = browser_fetch(page, next_url)

        if resp["status"] == 403 and "Rate Limit" in str(resp.get("body", "")):
            print(f"  ⏳ Rate limited. Waiting 5s ...")
            time.sleep(5)
            continue

        if not resp["ok"]:
            body_preview = str(resp.get("body", ""))[:150]
            if "<html" in body_preview.lower() or "<!doctype" in body_preview.lower():
                print(f"  ⚠️  {resp['status']} for ...{url.split('/api/v1')[-1]} (HTML error page)")
            else:
                print(f"  ⚠️  {resp['status']} for ...{url.split('/api/v1')[-1]}: {body_preview}")
            break

        if not resp["is_json"]:
            print(f"  ⚠️  Non-JSON response for ...{url.split('/api/v1')[-1]}")
            break

        data = resp["body"]
        if isinstance(data, list):
            results.extend(data)
        else:
            results.append(data)

        next_url = None
        link_header = resp.get("link_header", "")
        for part in link_header.split(","):
            if 'rel="next"' in part:
                next_url = part.split("<")[1].split(">")[0]
                break

    return results


def single_get(page, url: str, params: dict | None = None):
    """GET a single resource via in-browser fetch."""
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        full_url = f"{url}?{query}"
    else:
        full_url = url

    resp = browser_fetch(page, full_url)

    if resp["ok"] and resp["is_json"]:
        return resp["body"]
    else:
        if not resp["ok"]:
            body_preview = str(resp.get("body", ""))[:150]
            if "<html" in body_preview.lower() or "<!doctype" in body_preview.lower():
                print(f"  ⚠️  {resp['status']} for ...{url.split('/api/v1')[-1]} (HTML error)")
            else:
                print(f"  ⚠️  {resp['status']} for ...{url.split('/api/v1')[-1]}: {body_preview}")
        return None


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def fetch_course_info(course_id, page):
    print("  📋 Course info ...")
    return single_get(page, f"{API_BASE}/courses/{course_id}?include[]=permissions&include[]=syllabus_body")

def fetch_course_settings(course_id, page):
    print("  ⚙️  Course settings ...")
    return single_get(page, f"{API_BASE}/courses/{course_id}/settings")

def fetch_modules(course_id, page):
    print("  📦 Modules ...")
    modules = paginated_get(page, f"{API_BASE}/courses/{course_id}/modules", {"include[]": "items"})
    for mod in modules:
        if "items" not in mod or mod["items"] is None:
            mod["items"] = paginated_get(
                page, f"{API_BASE}/courses/{course_id}/modules/{mod['id']}/items"
            )
    return modules

def fetch_assignments(course_id, page):
    print("  📝 Assignments ...")
    return paginated_get(
        page, f"{API_BASE}/courses/{course_id}/assignments",
        {"include[]": "rubric_assessment", "order_by": "position"},
    )

def fetch_pages(course_id, page):
    print("  📄 Pages ...")
    page_list = paginated_get(page, f"{API_BASE}/courses/{course_id}/pages")
    full_pages = []
    for i, pg in enumerate(page_list):
        slug = pg.get("url", pg.get("page_id"))
        detail = single_get(page, f"{API_BASE}/courses/{course_id}/pages/{slug}")
        if detail:
            full_pages.append(detail)
        if (i + 1) % 25 == 0:
            print(f"    ... fetched {i + 1}/{len(page_list)} pages")
    return full_pages

def fetch_quizzes(course_id, page):
    print("  ❓ Quizzes ...")
    quizzes = paginated_get(page, f"{API_BASE}/courses/{course_id}/quizzes")
    for quiz in quizzes:
        quiz["questions"] = paginated_get(
            page, f"{API_BASE}/courses/{course_id}/quizzes/{quiz['id']}/questions"
        )
    return quizzes

def fetch_discussion_topics(course_id, page):
    print("  💬 Discussion topics ...")
    return paginated_get(page, f"{API_BASE}/courses/{course_id}/discussion_topics")

def fetch_announcements(course_id, page):
    print("  📢 Announcements ...")
    return paginated_get(
        page, f"{API_BASE}/courses/{course_id}/discussion_topics",
        {"only_announcements": "true"},
    )

def fetch_rubrics(course_id, page):
    print("  📊 Rubrics ...")
    return paginated_get(
        page, f"{API_BASE}/courses/{course_id}/rubrics",
        {"include[]": "associations"},
    )

def fetch_files(course_id, page):
    print("  📁 Files (metadata) ...")
    return paginated_get(page, f"{API_BASE}/courses/{course_id}/files")

def fetch_question_banks(course_id, page):
    print("  🏦 Question banks ...")
    banks = paginated_get(page, f"{API_BASE}/courses/{course_id}/question_banks")
    for bank in banks:
        bank["questions"] = paginated_get(
            page, f"{API_BASE}/courses/{course_id}/question_banks/{bank['id']}/questions"
        )
    return banks

def fetch_assignment_groups(course_id, page):
    print("  📂 Assignment groups ...")
    return paginated_get(page, f"{API_BASE}/courses/{course_id}/assignment_groups")

def fetch_calendar_events(course_id, page):
    print("  📅 Calendar events ...")
    return paginated_get(
        page, f"{API_BASE}/courses/{course_id}/calendar_events",
        {"type": "event", "all_events": "true"},
    )

def fetch_grading_standards(course_id, page):
    print("  🎓 Grading standards ...")
    return paginated_get(page, f"{API_BASE}/courses/{course_id}/grading_standards")


def has_required_course_access(course_info, course_id):
    """Confirm that Canvas returned the configured course, regardless of role label."""
    return (
        isinstance(course_info, dict)
        and str(course_info.get("id", "")) == str(course_id)
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_all_course_data(course_id, page, authenticated_user):
    print(f"\n{'='*60}")
    print(f"Fetching course {course_id} ...")
    print(f"{'='*60}")

    course_info = fetch_course_info(course_id, page)
    if not has_required_course_access(course_info, course_id):
        raise RuntimeError("Course info was not returned")

    snapshot = {
        "meta": {
            "course_id": course_id,
            "base_url": BASE_URL,
            "fetched_at": datetime.now().isoformat(),
            "fetched_by": {
                "id": authenticated_user.get("id"),
                "name": authenticated_user.get("name"),
                "login_id": authenticated_user.get("login_id"),
            },
        },
        "course_info": course_info,
        "course_settings": fetch_course_settings(course_id, page),
        "modules": fetch_modules(course_id, page),
        "assignments": fetch_assignments(course_id, page),
        "pages": fetch_pages(course_id, page),
        "quizzes": fetch_quizzes(course_id, page),
        "discussion_topics": fetch_discussion_topics(course_id, page),
        "announcements": fetch_announcements(course_id, page),
        "rubrics": fetch_rubrics(course_id, page),
        "files": fetch_files(course_id, page),
        "question_banks": fetch_question_banks(course_id, page),
        "assignment_groups": fetch_assignment_groups(course_id, page),
        "calendar_events": fetch_calendar_events(course_id, page),
        "grading_standards": fetch_grading_standards(course_id, page),
    }

    print(f"\n  ✅ Fetch complete for course {course_id}:")
    for key, val in snapshot.items():
        if key == "meta":
            continue
        if isinstance(val, list):
            print(f"     {key}: {len(val)} items")
        elif isinstance(val, dict):
            print(f"     {key}: ✓")
        else:
            print(f"     {key}: {'✓' if val else '✗ (empty/error)'}")

    return snapshot


def save_snapshot(snapshot, output_dir):
    course_id = snapshot["meta"]["course_id"]
    course_dir = os.path.join(output_dir, f"course_{course_id}")
    os.makedirs(course_dir, exist_ok=True)

    full_path = os.path.join(course_dir, "full_snapshot.json")
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)
    print(f"  💾 Full snapshot: {full_path}")

    for key, val in snapshot.items():
        if key == "meta":
            continue
        part_path = os.path.join(course_dir, f"{key}.json")
        with open(part_path, "w", encoding="utf-8") as f:
            json.dump(val, f, indent=2, ensure_ascii=False, default=str)

    print(f"  💾 Individual files saved to {course_dir}/")


def main():
    parser = argparse.ArgumentParser(description="Fetch configured Canvas courses")
    parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Use a fresh OL_Account browser context and do not load or save cookies",
    )
    args = parser.parse_args()

    OL_ACCOUNT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state_path = OL_ACCOUNT_STATE_PATH

    script_dir = str(SCRIPT_DIR)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    final_output_dir = os.path.join(script_dir, "snapshots", timestamp)
    output_dir = os.path.join(script_dir, "snapshots", f".{timestamp}.partial")
    os.makedirs(output_dir, exist_ok=True)

    with sync_playwright() as p:
        # Use the installed Google Chrome binary. BYU-I's local Canvas login and
        # OTP flow redirects incorrectly in Playwright's bundled Chromium.
        browser = p.chromium.launch(channel="chrome", headless=False)
        
        # Try loading saved state if available, otherwise start fresh
        has_saved_ol_account_session = state_path.exists() and not args.anonymous
        if has_saved_ol_account_session:
            context = browser.new_context(storage_state=str(state_path))
        else:
            context = browser.new_context()
        
        page = context.new_page()
        navigation_tracker = NavigationTracker()
        navigation_tracker.attach(page)
        forced_login_redirect = install_ol_account_login_redirect_guard(page)
        print(f"   Navigation log: {navigation_tracker.path}")
        if args.anonymous:
            print("   Anonymous OL_Account mode: saved cookies will not be loaded or written")

        print("🔐 Opening Canvas — please log in if needed ...")
        initial_url = f"{BASE_URL}/" if has_saved_ol_account_session else OL_ACCOUNT_LOGIN_URL
        page.goto(initial_url, wait_until="domcontentloaded", timeout=60000)
        
        # Verify a saved session via the API. A fresh interactive login is
        # detected only through page navigation so API requests cannot interfere
        # with Canvas's username/OTP forms.
        auth_check = (
            browser_fetch(page, f"{API_BASE}/users/self")
            if has_saved_ol_account_session
            else {"ok": False, "is_json": False}
        )
        if not auth_check["ok"] or not auth_check["is_json"]:
            page.goto(OL_ACCOUNT_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            print(f"   OL_Account login page: {OL_ACCOUNT_LOGIN_URL}")
            print(f"   Please log in and complete MFA in the browser window.")
            print("   Fetching will resume automatically after login.")
            show_login_notice(page, "Login with your OL_Account")
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                page.wait_for_timeout(250)
                current = urlsplit(page.url)
                is_canvas_page = current.netloc == urlsplit(BASE_URL).netloc
                is_login_page = current.path == "/login" or current.path.startswith("/login/")
                if is_canvas_page and not is_login_page:
                    break

        # Make the first API request only after Canvas has independently landed
        # outside its login pages (including the forced login-success landing).
        current = urlsplit(page.url)
        is_canvas_page = current.netloc == urlsplit(BASE_URL).netloc
        is_login_page = current.path == "/login" or current.path.startswith("/login/")
        if not (is_canvas_page and not is_login_page):
            print("❌ Canvas login did not reach an authenticated Canvas page within 5 minutes.")
            print(f"   Final page: {redact_url(page.url)}")
            browser.close()
            sys.exit(1)

        auth_check = browser_fetch(page, f"{API_BASE}/users/self")
        if auth_check["ok"] and auth_check["is_json"]:
            user = auth_check["body"]
            print(f"✅ Authenticated as: {user.get('name', 'Unknown')} ({user.get('login_id', '')})")
            if forced_login_redirect["used"]:
                print("   Authentication succeeded after the forced local-login landing")
        else:
            print(f"❌ API auth check failed (HTTP {auth_check['status']})")
            print(f"   Response: {str(auth_check.get('body', ''))[:300]}")
            browser.close()
            sys.exit(1)

        # Require real API access to every configured course before persisting
        # this as the OL_Account session. Canvas does not consistently expose the
        # custom Course Designer role through enrollment/permission labels.
        invalid_ol_account_courses = []
        for course_id in COURSE_IDS:
            course_info = fetch_course_info(course_id, page)
            if not has_required_course_access(course_info, course_id):
                invalid_ol_account_courses.append(course_id)
        if invalid_ol_account_courses:
            print("❌ This Canvas user cannot access all configured courses through the API.")
            print(f"   Failed course access check: {', '.join(invalid_ol_account_courses)}")
            print("   The session was not saved as OL_Account.")
            browser.close()
            sys.exit(1)

        # Save only the verified OL_Account course-designer session unless this is
        # an explicitly anonymous diagnostic run.
        if args.anonymous:
            print("   Anonymous OL_Account session will not be saved")
        else:
            context.storage_state(path=str(OL_ACCOUNT_STATE_PATH))
            print(f"   OL_Account session saved to {OL_ACCOUNT_STATE_PATH}")

        print(f"\nCanvas API Course Fetcher")
        print(f"Output: {output_dir}")
        print(f"Courses: {', '.join(COURSE_IDS)}")

        start = time.time()

        errors = []
        for course_id in COURSE_IDS:
            try:
                snapshot = fetch_all_course_data(course_id, page, user)
                save_snapshot(snapshot, output_dir)
            except Exception as e:
                print(f"\n❌ Error fetching course {course_id}: {e}")
                import traceback
                traceback.print_exc()
                errors.append(course_id)

        elapsed = time.time() - start
        browser.close()

    if errors:
        print(f"\n❌ OL_Account fetch incomplete; failed courses: {', '.join(errors)}")
        print("   The partial snapshot was not promoted for report generation.")
        sys.exit(1)

    os.rename(output_dir, final_output_dir)
    output_dir = final_output_dir

    print(f"\n{'='*60}")
    print(f"✅ All done in {elapsed:.1f}s")
    print(f"   Snapshots saved to: {output_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
