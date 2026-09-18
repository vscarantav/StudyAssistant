# Homework Assistant Application Architecture

## 1. Purpose and scope

Homework Assistant is a local-first Python application that turns Canvas course exports into an offline learning site and weekly homework reports. OL_Account exports supply course content, schedules, announcements, and lesson evidence. A separate student-account browser scrape supplies the assignment rows and links shown in weekly report containers. The application combines these with source-backed course plans, weekly roadmaps, concept lessons, manual progress tracking, portable ZIP packages, and optional email delivery.

This document is the behavioral and technical contract for the repository as implemented. It distinguishes current behavior from limitations and intentionally unsupported behavior. The application is configured for the Fall 2026 BYU-Idaho term and six learning-dashboard courses:

| Course ID | Course |
| --- | --- |
| `431292` | DS 350 — Data Wrangling & Visualization |
| `420634` | BA 300 — SQL for Analysts |
| `431290` | DS 250 — Data Science Programming |
| `424344` | FIN 398R — Business Finance Internship |
| `420672` | BA 315 — Business Analytics |
| `424334` | GESCI 201 — Natural Disasters |

Course `424334` is included end-to-end. Its Canvas identity, coursework, deadlines, official syllabus outcomes, policy evidence, week mapping, and reviewed natural-disaster concept sequence are handled by the same pipeline as the other courses. If any configured course is absent from all snapshots, the generated site and weekly reports retain a source-gap plan instead of silently omitting it.

## 2. Product boundaries

The application is expected to:

- Fetch configured Canvas course data through the user-authenticated OL_Account browser session.
- Preserve fetched data as timestamped, immutable-style JSON snapshots.
- Normalize eligible student-facing content into course requirements, evidence, weeks, facts, concepts, and source gaps.
- Generate six full-semester course plans, 14 weekly pages per course, reviewed concept lesson pages where mappings exist, and a dashboard.
- Generate a selected week's report with assignments, announcements, learning items, plan links, lessons, and milestone context.
- Let a learner manually mark coursework complete and self-assess concept mastery.
- Persist progress locally when served by `serve_learning.py` and support JSON export/import.
- Package weekly reports with every linked plan, lesson, asset, instruction file, older report, and optional Week 02 test artifact.
- Optionally send the resulting ZIP through Gmail SMTP.
- Explicitly expose missing, stale, conflicting, inferred, or unavailable source information instead of silently inventing facts.

The application is not expected to:

- Submit assignments, quizzes, discussions, grades, or any other changes to Canvas.
- Automatically infer Canvas completion; current exports contain no student `submission` objects.
- Calculate an official course grade.
- Keep portable `file://` pages synchronized with one another without export/import.
- Automatically generalize reviewed policy summaries or concept mappings to a different term.
- Treat supplementary lesson links as evidence for course policy.
- Publish the generated site to the internet.
- Include browser profiles, raw snapshots, credentials, or progress backups in report ZIP files.
- Authenticate, switch to, validate, or maintain a second Canvas account.
- Remap assignment URLs through another Canvas identity or open reports in an account-specific browser profile.

## 3. System context

```text
Canvas + OL_Account interactive login
          |
          v
 fetch_course.py -----------------------> snapshots/<timestamp>/course_<id>/*.json
                                                              |
                                                              v
                                                     course_plans.sources
                                                              |
                                                              v
                                                     course_plans.curriculum
                                                              |
                         +------------------------------------+------------------+
                         |                                                       |
                         v                                                       v
              build_course_plans.py                                  generate_summary.py
                         |                                                       |
                         +--------------------> output/ <-------------------------+
                                                   |
                             +---------------------+------------------+
                             |                                        |
                             v                                        v
                   serve_learning.py                           weekly ZIP / email
                             |
                             v
                  data/learning_progress.json
```

The build path is deterministic from local snapshots, reviewed curriculum data, lesson content, and optional overrides. Network or model access is not required to rebuild the learning site.

## 4. Repository responsibilities

### Root commands

| File | Responsibility |
| --- | --- |
| `fetch_course.py` | Opens an authenticated OL_Account Canvas session and exports complete course datasets. |
| `build_course_plans.py` | Builds normalized course models and renders the complete learning site. |
| `generate_summary.py` | Orchestrates optional OL_Account refresh, learning-site build, weekly extraction, report rendering, test-artifact generation, ZIP packaging, and optional email. |
| `serve_learning.py` | Serves only the generated `output/` tree and exposes same-origin progress APIs. |
| `test_mock.py` | Obsolete/destructive development helper; it rewrites `generate_summary.py` and must not be used as a test. |

### `course_plans` package

| File | Responsibility |
| --- | --- |
| `sources.py` | Reads snapshot JSON, filters student-visible evidence, sanitizes URLs, parses dates/schedules, and reports source gaps. |
| `curriculum.py` | Holds reviewed course profiles and policy facts; normalizes requirements, weeks, concepts, sources, conflicts, and overrides. |
| `lessons.py` | Loads reviewed teaching content from `data/lesson_content.json` and defines verified supplementary resources. |
| `render.py` | Produces escaped static HTML, navigation, course/week/lesson pages, report fragments, and the learning manifest. |
| `progress.py` | Validates progress payloads and performs revision-controlled atomic persistence with a previous-version backup. |
| `assets/progress.js` | Implements browser-side progress loading, editing, summaries, review detection, continuation links, import, and export. |
| `assets/learning.css` | Supplies the shared responsive visual design. |

### Data and generated artifacts

| Path | Role |
| --- | --- |
| `snapshots/<timestamp>/course_<id>/` | Raw timestamped Canvas API exports and the source of truth for builds. |
| `data/course_plans/<id>.json` | Rebuildable normalized course models. |
| `data/lesson_content.json` | Reviewed concept explanations, examples, mistakes, exercises, and answers. |
| `data/course_plan_overrides.json` | Optional reviewed corrections to facts or requirement deadlines. |
| `data/learning_progress.json` | Durable local learner progress. It is never overwritten by a report build. |
| `data/learning_progress.previous.json` | Backup of the immediately preceding progress state. |
| `data/ol_account_canvas_state.json` | Saved OL_Account Playwright storage state used by `fetch_course.py`. It stays local and outside generated bundles. |
| `data/navigation_logs/` | Redacted OL_Account navigation diagnostics. These stay local and outside generated bundles. |
| `output/course_plans/` | Generated dashboard, course plans, week pages, and lesson pages. |
| `output/assets/` | Generated/copy-on-build CSS, JavaScript, and manifest files. |
| `output/week_*_summary_*.html` | Generated weekly reports. |
| `output/week_*_summary_*.zip` | Portable weekly report packages. |

## 5. Canvas acquisition behavior

### Single OL_Account flow

Running `fetch_course.py` opens a visible Playwright Chromium session. The user is expected to authenticate interactively and complete MFA. The script verifies that Canvas returns the configured course rather than relying only on a role label.

The fetcher requests, as available:

- Course metadata, permissions, and `syllabus_body`.
- Course settings.
- Modules with items.
- Assignments and rubric assessments.
- Full page bodies.
- Quizzes and their questions.
- Discussion topics and announcements.
- Rubrics and associations.
- File metadata, not file contents.
- Question banks and questions.
- Assignment groups.
- Calendar events.
- Grading standards.

List endpoints use Canvas pagination with 100 records per request. A Canvas rate-limit response causes a five-second retry. Other HTTP, HTML, or non-JSON failures are reported and result in partial data for that endpoint rather than fabricated content.

Successful data is saved under a timestamped snapshot directory. Each course receives individual category JSON files and a `full_snapshot.json`. Navigation diagnostics redact query values before logging. Saved browser state and navigation logs remain in `data/` and are not application output.

The login redirect guard prevents a completed local Canvas login from being handed unexpectedly into SAML. Calling `fetch_course.py --anonymous` uses a fresh OL_Account browser context without loading or saving cookies; the orchestrator exposes this as `generate_summary.py --anonymous-ol-account`.

OL_Account remains the course-content acquisition flow. The separate `student_canvas_flow.py` browser session signs into the student account, captures each Grades page, and stores structured assignment rows plus the displayed course total. Weekly assignment cards are created only from that student scrape. Matching OL_Account records may add week/date metadata, but they cannot add assignment rows absent from the student Grades page. If no completed student scrape is available, the report displays a capture-needed message instead of substituting OL_Account assignments. The same secondary-account flow also treats the BA 300 and BA 315 landing-page schedule tables as the source of truth for dated preclass preparation and in-class activities; these rows are stored separately and rendered under **Class Preparation**.

Legacy account/profile artifacts may still exist under an ignored local `data/` directory from older versions. They are not read by the current code, are not part of the architecture, and are never packaged into output.

## 6. Source ingestion and evidence rules

`course_plans.sources.load_course()` reads these categories for each learning course: course info, modules, pages, assignments, quizzes, assignment groups, announcements, files, rubrics, and calendar events.

Expected source behavior:

- Each file must decode as JSON and have the expected top-level type. Missing, unreadable, or incorrectly typed files become explicit source gaps.
- If a course is absent from the selected snapshot, the loader searches newest-to-oldest for a usable export of that course and reports that an older export was used.
- A SHA-256 hash and record count are retained in the source manifest for each loaded category.
- Unpublished, student-hidden, obsolete, backup, instructor-only, TA-only, answer-key, and solution material is excluded from student evidence using title and visibility checks.
- HTML is reduced to readable text; scripts and styles are ignored.
- Only absolute `http` and `https` links without embedded usernames are allowed.
- Query strings are removed from evidence URLs so authenticated download tokens do not leak into generated pages.
- Canvas source links fall back to a canonical course/category URL if an exported `html_url` is absent or unsafe.
- Missing `syllabus_body` produces a source-gap warning about outcomes, grade thresholds, attendance, and late-work rules.
- Missing assignment submission objects produces a warning that progress is manual, not Canvas-synced.
- Modules whose `items_count` exceeds the exported items generate an incomplete-module warning.

All generated policy claims should resolve to eligible source records. Facts carry a status such as `confirmed`, `conflicting`, or `not_documented`; uncertainty must remain visible.

## 7. Time and scheduling model

The configured term uses `America/Denver` and spans September 12 through December 17, 2026, represented internally with an exclusive end boundary of December 18. There are 14 Saturday-based learning weeks.

Scheduling behavior:

- ISO timestamps are converted to Mountain time, including daylight-saving behavior.
- Naive timestamps are interpreted as Mountain time.
- A due date belongs to a week only when it falls inside the half-open interval `[week start, next week start)`.
- Titles such as `W09` or `Week 9` provide a fallback week clue.
- Module placement supplies learning weeks; due dates separately supply due weeks.
- A requirement can therefore be learned in one or more weeks and due in another.
- HTML schedule tables are parsed for September–December dates. The year is inferred as 2026.
- An included weekday label must match the inferred calendar date; otherwise the row does not receive a mapped week.
- Exported deadline conflicts are preserved. A reviewed override may change the active due date while retaining the original exported value and correction evidence.
- FIN 398R is phase/milestone-oriented; its weekly focus may be explicitly labeled as a suggested study checkpoint rather than an official weekly schedule.

## 8. Normalized course model

`course_plans.curriculum` produces one model per configured learning course and optionally writes it to `data/course_plans/<course-id>.json`.

Each model contains, at minimum:

- Course identity, code, name, overview, suggested learning goals, and tool notes.
- Source evidence, source hashes, snapshot identity, and source gaps.
- Student-visible module phases.
- Deduplicated requirements from assignments and quizzes.
- Reviewed course facts and their evidence links.
- Fourteen week records with focus, dates, concept IDs, learning requirement IDs, due requirement IDs, schedule rows, evidence IDs, and suggestion flags.
- Course-specific concepts selected from `data/lesson_content.json`.
- Stable progress IDs and content hashes.

### Requirement behavior

Assignments and quizzes are combined without double-counting a quiz already represented by its assignment ID. Each normalized requirement may include:

- Stable ID and Canvas record ID.
- Title, sanitized Canvas URL, kind, and category.
- Description text and notes.
- Points, due timestamp, due week, and one or more learning weeks.
- Source IDs and a content hash.
- Original exported deadline when a reviewed override exists.

Requirements are classified so optional, extra-credit, reference, conditional, or stretch items can remain visible without inflating the assigned-progress denominator. Personal internship checklist tasks are tracked separately.

### Reviewed curriculum content

Course overviews, suggested outcomes, tool descriptions, concept-to-week mappings, and policy summaries are deliberately authored in `curriculum.py`. They are not generic AI extraction. Official outcomes must not be implied when exports do not contain them.

Lesson bodies are reviewed local JSON. Every lesson is expected to contain a title, hook, plain-language explanation, observable goal, invented worked example, result/interpretation, common mistake, practice question, and expandable answer. Supplementary resources come from a small reviewed URL registry; the learning site remains usable without them.

### Overrides

`data/course_plan_overrides.json` may add reviewed corrections without modifying snapshots. Fact overrides require a label, status, value, reason, and source URL. Deadline overrides require a stable requirement ID, ISO due date, reason, and source URL. Invalid or unevidenced override structures must not silently alter models.

## 9. Static learning-site behavior

`build_course_plans.py` calls `build_models()` and `render_all()`. With no explicit snapshot, the model builder selects the latest snapshot. `--snapshot`, `--output`, and `--data-dir` support reproducible and isolated builds. `--no-ai` is retained for command compatibility; this build never calls an external model.

Expected output:

- One dashboard at `output/course_plans/index.html`.
- One full plan per course.
- Fourteen weekly pages per course, including weeks with no mapped work.
- One page per mapped concept.
- Shared CSS and progress JavaScript.
- `learning-manifest.json` and its JavaScript equivalent.
- `LEARNING_README.txt` with portable-use and progress instructions.

All source-derived text is HTML-escaped. Links are accepted only after URL validation. Generated pages use relative internal links so the tree works both through the local server and from an extracted ZIP.

### Dashboard

The dashboard displays all six configured courses, their overviews, progress summaries, concept counts, weekly-page counts, and a context-sensitive “Continue learning” link. A configured course with no available export remains visible with explicit source gaps and zero exported requirements rather than being omitted. The dashboard also lists the newest generated report for each week and explains persistence and portable mode.

“Continue learning” prioritizes the first concept marked for review, then the first concept not mastered. When all planned concepts are mastered, it says so.

### Full course plan

Each plan is expected to expose:

- Overview and suggested outcomes.
- Documented facts with status and evidence.
- Tools and prerequisites/context.
- A 14-week roadmap.
- Exams/quizzes and projects with known metadata.
- Attendance, proctoring/testing-center, grading, and other policy information when supported.
- Explicit unknowns, conflicts, source gaps, and freshness information.
- Assignment and concept progress with transparent denominators.
- The source inventory and workspace audit references.

### Weekly pages

Each week page shows its date range, focus, concept lessons, schedule rows, learning items, deadlines, upcoming assessment/project milestones, and evidence. Suggested internship checkpoints are explicitly labeled. A week without mapped concepts or work says so rather than disappearing.

### Concept lessons

Each lesson links back to the course and relevant weeks and includes the reviewed explanation, goal, example, result, mistake, exercise, answer, progress controls, related assessment/project links, supplementary reading, and course evidence.

Examples use invented data and are never represented as answers to actual course assessments.

## 10. Weekly report behavior

`generate_summary.py` is the full orchestration command.

Unless disabled, it:

1. Runs `fetch_course.py` and stops if the OL_Account fetch fails.
2. Selects the newest snapshot.
3. Rebuilds the complete learning site from that snapshot.
4. Resolves the requested or current learning week.
5. Loads each configured report course.
6. Extracts matching modules, learning items, announcements, and assignments, including their OL_Account URLs.
7. Incorporates normalized plan notes and mappings.
8. Uses reviewed local lessons; it calls Gemini only as a legacy fallback when a course lacks a normalized plan and `--no-ai` is not set.
9. Renders the report, creates Week 02 fixtures when applicable, refreshes dashboard report links, writes HTML, and builds a ZIP.
10. Sends the ZIP by email unless `--no-email` is passed.

### Week selection

`--week` accepts 1–14. Without an override, the configured semester dates determine the current week. Reports preserve the Saturday–Friday model and Mountain-time due-date formatting.

### Assignment selection

Assignments may enter a report through an in-range due date, title/module week mapping, or reviewed normalized mapping. Adjacent weeks must not double-count boundary deadlines. Assignment-group labels are retained. Existing career-readiness filtering rules affect weekly display, while full plans preserve the broader eligible course inventory. The report uses each exported assignment's `html_url` directly; there is no account-based override layer.

Each course section appears even if it has no due assignments. It includes course-plan and current-week links, manual progress counts, relevant concept lessons, the next exported milestone, and important proctoring/testing-center/conflict/hour requirements when available.

On desktop, the report places a sticky weekly assignment calendar to the right of the course list. It displays Monday at the top through Saturday at the bottom, marks each assignment on its Mountain-time due day, uses the course accent color, and links directly to the Canvas item. Every day shows its due-assignment count and is independently collapsible. All weekday panels are collapsed by default so the learner can scan the entire week, then expand only the days they need. Mapped work without a usable Monday–Saturday due date remains visible under “Other mapped work.” On narrower screens, the calendar moves above the course list.

### Announcements and learning material

Announcements are selected by the configured week range. Module items resolve to readable page/assignment links when available. Existing Canvas links, due dates, collapsible course sections, and visual identity are preserved.

### Week 02 fixtures

Week 02 generation creates clearly labeled synthetic course-design artifacts under `output/week_02_course_design_test_artifacts/`. These are workflow/file-format fixtures, not learner work or course evidence. Optional DOCX and PNG artifacts depend on `python-docx` and Pillow; text fallbacks or omissions are allowed when those libraries are unavailable.

### ZIP packaging

The report ZIP includes:

- The current report HTML.
- The complete `output/course_plans/` tree.
- The complete `output/assets/` tree.
- `LEARNING_README.txt` when present.
- Older report HTML files needed by dashboard navigation.
- Week 02 fixtures when generated.

It does not include raw snapshots, browser profiles, session state, `.env`, progress files, or arbitrary `data/` content. Users must extract the whole ZIP before opening the report so relative links resolve.

### Email

Email uses Gmail SMTP over SSL on port 465. Configuration is loaded from `.env`:

- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`
- `RECIPIENT_EMAIL` (defaults to the Gmail address)

The ZIP is attached to a plain-text weekly summary email. Missing credentials or SMTP errors are reported; they do not change Canvas or learner progress. Use `--no-email` for safe local generation.

## 11. Progress architecture

Progress is manual and separate from generated content. Rebuilding plans or reports must never reset `data/learning_progress.json`.

### Stable IDs

Allowed item IDs are:

- `canvas:<course-id>:assignment:<record-id>`
- `canvas:<course-id>:quiz:<record-id>`
- `concept:<course-id>:<slug>`
- `task:<course-id>:<slug>`

Assignments and tasks support `not_started` or `completed`. Concepts support `not_started`, `learning`, `practiced`, or `mastered`. A concept may also have a review flag and notes. Notes are limited to 5,000 characters. Progress source must be `manual`, and the term must be `2026-fall`.

Each entry may carry a content hash. If the current manifest hash differs from a saved hash, the UI counts the concept as needing review.

### Manifest

The generated learning manifest is the browser/server contract. For each course it declares known items, assigned-denominator IDs, concept IDs, personal task IDs, display titles, hashes, and target paths. Normal saves may update only IDs present in this manifest.

### Local-server mode

When pages are loaded from `localhost` or `127.0.0.1`, `progress.js` uses:

- `GET /api/progress` to load state.
- `POST /api/progress` to update current manifest items.
- `POST /api/progress/import` to merge a validated backup, including archived IDs.

The browser submits its current revision. A stale revision receives HTTP 409; the client reloads current state and asks the user to retry. This prevents silent last-writer-wins updates across tabs.

Writes are validated, written to a temporary file in the progress directory, flushed and `fsync`ed, then atomically replaced. Before replacement, the prior state is copied to `learning_progress.previous.json`.

### Portable file mode

When opened via `file://`, progress exists only in JavaScript memory on that page. Controls work, but navigating to another page creates a separate state. The UI must clearly instruct the user to export before leaving and import on the next page, or use the local server.

### Import and export

Export downloads the complete version-1 state as `learning-progress.json`. Import validates schema, IDs, statuses, notes, and source. If imported values differ from existing values, the browser asks for confirmation before replacement. Backups larger than 2 MB are rejected.

## 12. Local server and security boundary

`serve_learning.py` binds only to `127.0.0.1`, default port `8765`, and serves only the configured output directory. `/` redirects internally to the dashboard.

Expected safeguards:

- The resolved file path must remain under the output root; traversal resolves to a forbidden placeholder.
- Directory listings return 404.
- API GET requests require an allowed `Host` header.
- API POST requests require both an allowed `Host` and an exact same-origin `Origin`.
- Request bodies must be greater than zero and no more than 2 MB.
- Unknown API routes return JSON 404 responses.
- Static/API responses set `X-Content-Type-Options: nosniff` and disable caching as appropriate.
- The server has no Canvas credentials, external network requirement, upload endpoint, or arbitrary file-write endpoint.

`--open` asks the operating system to open the dashboard URL. Ctrl+C cleanly stops and closes the server.

## 13. Error and degradation behavior

The application prefers visible degradation over invented data:

- Partial/missing snapshot categories become model source gaps.
- A missing course in the chosen snapshot can fall back to the newest older course export and is labeled.
- Invalid dates are ignored for week mapping, not coerced.
- Conflicting schedule/deadline evidence remains visible.
- Missing syllabi leave official policies unknown.
- Missing submission objects leave completion manual.
- Missing optional artifact libraries reduce only optional fixture output.
- Invalid progress files make the API return an error; they are not silently reset.
- Invalid progress updates/imports return HTTP 400.
- Concurrent progress edits return HTTP 409.
- Filesystem persistence failures return HTTP 500 and retain the prior durable file when atomic replacement has not occurred.
- Canvas refresh failure stops the default report workflow; `--no-fetch` deliberately uses existing snapshots.
- Email failure is reported after local artifacts have already been generated.

## 14. Commands and side effects

### Open the learning dashboard

```sh
python3 serve_learning.py --open
```

Reads `output/`; may write progress files when the learner changes state. It performs no Canvas fetch and sends no email.

### Rebuild plans offline

```sh
python3 build_course_plans.py --no-ai
```

Reads local snapshots and reviewed content; writes normalized models and generated site files. It does not write learner progress.

### Generate a safe local weekly report

```sh
python3 generate_summary.py --week 1 --no-fetch --no-email --no-ai
```

Reuses existing snapshots, rebuilds plans, writes report/ZIP output, and performs no login, model call, browser opening, or email. Report generation never launches a separate browser profile.

### Full weekly workflow

```sh
python3 generate_summary.py --week 1
```

May open the OL_Account Canvas login browser, refresh snapshots, use a legacy model fallback if needed, create local artifacts, and send email. It does not open the generated report or launch another Canvas identity. This is the broadest-side-effect command.

### Fetch Canvas only

```sh
python3 fetch_course.py
```

Opens a visible authenticated browser and writes a new snapshot and browser/session diagnostics.

## 15. Configuration and dependencies

The core build requires Python 3.10+ because the implementation uses modern type syntax and `zoneinfo`. Standard-library modules provide HTML parsing/rendering support, HTTP serving, JSON, ZIP creation, SMTP, and atomic filesystem behavior.

Feature dependencies include:

- Playwright and a compatible Chromium/Chrome installation for the OL_Account Canvas fetch and browser verification.
- `google-genai` only for the legacy AI-summary fallback.
- `python-docx` and Pillow for optional Week 02 fixture formats.
- Gmail app credentials for email delivery.

Important hard-coded configuration currently lives in source code: Canvas base URL, term dates, timezone, Gemini model name, reviewed mappings, and local-server default port. The shared configured course list lives in `course_plans.sources.COURSES` and is consumed by acquisition, normalization, and weekly reporting so those pipelines cannot silently diverge. A future multi-term version should externalize and validate these values.

## 16. Verification contract

Run the offline automated suite with:

```sh
python3 -m unittest discover -s tests -v
```

The unit/integration suite is expected to cover evidence eligibility, date/DST/week behavior, partial exports, assignment/quiz deduplication, unknown policies, overrides, progress validation and persistence, backup/corruption/conflict behavior, worked examples, generated manifests, and internal links in output/ZIPs.

Run the browser workflow with:

```sh
python3 tests/browser_learning.py
```

It exercises dashboard navigation, shared report/lesson progress, notes, review flags, import/export, rebuild persistence, mobile layout, origin protection, and portable file mode using temporary progress rather than the real learner record.

`test_mock.py` is explicitly excluded from verification because it modifies production code.

## 17. Architectural invariants

Future changes should preserve these rules:

1. Raw Canvas snapshots remain separate from normalized and generated artifacts.
2. Generated builds never overwrite learner progress.
3. The system never claims automatic Canvas completion without actual submission evidence.
4. Policy statements are source-backed or explicitly unknown/conflicting/provisional.
5. Student-hidden, obsolete, answer-key, and instructor-only material does not become learner content.
6. Authenticated query values and local session artifacts never enter portable output.
7. Official assignments remain separate from invented practice examples.
8. Assignment due weeks and learning weeks remain distinct.
9. Progress IDs stay stable across rebuilds, and changed content is detectable through hashes.
10. Portable output remains navigable through relative paths and honestly describes its page-local progress behavior.
11. Local progress writes remain validated, revision-controlled, and atomic.
12. Canvas writes and submissions remain outside the application's authority.
13. Canvas acquisition and exported Canvas links use only OL_Account; no alternate-account authentication or URL remapping is introduced implicitly.

## 18. Known constraints and extension points

- The current curriculum and term dates are Fall 2026-specific.
- Current syllabus retrieval has been incomplete, so several official outcomes and policies remain unknown.
- The normalized course model covers a fixed list of six configured courses, each with a reviewed course-specific profile. The honest fallback profile remains available so a newly configured or temporarily unavailable course is not silently omitted.
- Weekly email/report generation and full learning-site generation live in one large module; separating orchestration, report selection, rendering, packaging, and delivery would reduce coupling.
- Some configuration and reviewed curriculum data are Python constants rather than versioned declarative configuration.
- The static browser client has no automated Canvas sync, authentication, or multi-user identity model.
- The local HTTP server is intentionally small and single-workspace; it is not a production web server.

Suitable future extensions include explicit term configuration, refreshed syllabus ingestion, a formal normalized-model schema, opt-in submission-state synchronization with provenance, configurable delivery providers, and clearer separation between report generation and test-fixture creation. Each extension must preserve the invariants above.
