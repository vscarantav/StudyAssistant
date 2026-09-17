# Full course plans and weekly learning progress

## Start here — agent handoff

**Created:** September 15, 2026. **Status:** implemented and verified against the available exports. Current syllabus gaps remain explicitly documented.

The user requested this implementation plan first so another agent can continue if the conversation ends. The implementation checkpoint at the end of this file describes the delivered application. This file is the durable specification and work ledger. Read it before implementing, update the checkboxes after verified work, and leave precise next steps in the handoff log. Do not treat planned files, commands, or features below as already implemented.

### User's intended result

For every course, create a complete, source-backed semester learning plan with course overview, outcomes, major concepts, exams, large projects, attendance expectations, Proctorio/testing-center requirements, and other relevant course information. Every course in the weekly report must link to its full plan, show progress, and link to separate concept lessons with simple explanations, useful worked examples, and interesting real-world applications. Use the exported course content to determine what belongs in each week.

### Continuation prompt

> Read `IMPLEMENTATION_PLAN.md` in `/Users/vinny/Desktop/Homework_assistant`. Continue from the first unfinished phase. Inspect the current files and handoff log before editing. Implement the five course plans, export-backed weekly concept lessons, persistent progress, report links, and portable packaging described here. Use the existing snapshots first; distinguish documented requirements, suggested study activities, and missing information. Validate the implementation without emailing or submitting anything. Update this file with changes, verification results, and remaining work before ending the session.

## 1. Verified project baseline

This is a local Python project producing HTML reports and ZIP attachments. No Git repository, project README, or applicable `AGENTS.md` was found during inspection. Preserve existing files and unrelated behavior; use targeted changes or backups before major refactoring. Do not execute `test_mock.py`: it rewrites `generate_summary.py` and is not a verification suite.

Current export: `snapshots/2026-09-15_133653/`. Recheck for newer snapshots on resumption. Treat these JSON snapshots as the supplied exports; no Common Cartridge import is necessary for the initial implementation.

| Course | Canvas ID | Modules | Pages | Assignments | Quizzes | Scheduling considerations |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| DS 350 — Data Wrangling & Visualization | 431292 | 18 | 60 | 95 | 15 | Inspect weekly modules and coding challenges |
| BA 300 — SQL for Analysts | 420634 | 2 | 7 | 53 | 24 | Modules are career activities; main page contains a dated course schedule |
| DS 250 — Data Science Programming | 431290 | 20 | 67 | 75 | 38 | Week 01–14 modules, including two-week units |
| FIN 398R — Business Finance Internship | 424344 | 6 | 1 | 7 | 3 | Start/middle/end internship milestones rather than weekly modules |
| BA 315 — Business Analytics | 420672 | 3 | 25 | 79 | 22 | Project modules and a separate Course Schedule page |

Counts include unpublished/exported records, so they are inventory counts, not student workload totals. Quizzes may also appear as assignments and must be deduplicated.

### Existing integration points

| File / function | Current behavior | Required extension |
| --- | --- | --- |
| `fetch_course.py`: `fetch_course_info()` | Requests course metadata with `include[]=permissions` | Also request syllabus content when refreshing exports; preserve existing permission handling |
| `fetch_course.py`: `fetch_files()` | Collects file metadata | Selectively obtain linked current syllabi/schedules when necessary; record unavailable sources |
| `generate_summary.py`: `load_course_data()` | Loads seven JSON categories | Share a normalized reader that also exposes file references, rubrics, calendar and grading information where useful |
| `extract_week_modules()` | Recognizes published `Week N` modules | Resolve dated schedules, project/milestone modules and explicit week labels |
| `parse_date()`, `get_week_date_range()` | Removes timezone information; global Saturday–Friday calendar | Use timezone-aware course dates and explicit mapping rules |
| `extract_week_assignments()` | Uses due date OR title prefix; filters career-readiness work | Avoid adjacent-week double counting and preserve existing report preferences while exposing full course requirements |
| `generate_ai_summary()` | Gemini creates 120–220 words per course; explicitly disallows links | Consume/cache structured concept content; generate lesson links in trusted templates |
| `generate_html_report()` | Collapsible course sections and weekly homework | Add full-plan links, concept lesson links, counts/progress and next milestones |
| `zip_html()` | Includes the report and optional Week 02 test artifacts | Include the full tree of linked plans, lessons, assets and portable progress instructions |
Existing flags: `--week`, `--no-fetch`, `--no-email`, `--no-ai`. Canvas acquisition and report links use only the OL_Account flow.

### Source gaps and traps discovered

- None of the five `course_info.json` files contains `syllabus_body`. File metadata is not downloaded document content.
- None of the exported assignments contains a student `submission` object. Actual Canvas completion cannot currently be calculated.
- DS 250 has a page titled **Syllabus (Backup - Do Not Use)** and unpublished instructor material. These are not current student policy authorities.
- Other courses contain old pages and historical internship syllabi. Published status alone does not establish that a source applies to this term.
- BA 300's current main page and BA 315's Course Schedule link attendance trackers. This establishes a tracking mechanism, not an attendance penalty or mandatory-attendance rule.
- BA 315's published **Project 7** description says `Placeholder: Final Exam (Case in the testing center)`. Report this as a provisional exam-location indication until current instructions confirm it.
- DS 350 has published W09, W11 and W13 remotely proctored coding challenges with Proctorio descriptions, plus a W09 practice Proctorio quiz. Assignment IDs: `17374961`, `17374965`, `17374967`, `17374963`. Use their actual current descriptions for requirements; do not infer them from unpublished teaching notes.
- FIN 398R's published Self-Evaluation/Total Hours descriptions contain pass-condition language. Extract the exact relevant requirements and evidence; do not invent an hours threshold.
- The report calendar currently assumes September 12–December 17, 2026, 14 weeks, Saturday–Friday. Course timezone is `America/Denver`; BA/FIN course metadata has no start/end dates. Confirm schedule alignment, and label configured fallback dates.
- The existing weekly career-readiness filter is broad. A full semester plan must inventory required coursework even when the weekly report intentionally hides a category; identify the exclusion in progress denominators.
- Week 02 output includes synthetic course-design fixtures. Never use these as course evidence or learner completion evidence.

## 2. Product specification

### A. One full plan for each course

Use consistent sections with course-specific content:

1. **Overview:** what the course teaches, why it is useful, and what the student should be able to build or do by the end.
2. **Course outcomes:** official outcomes with sources; keep any suggested learning objectives visibly separate when official outcomes are missing.
3. **Major concepts and prerequisites:** short descriptions and links to detailed concept lessons.
4. **Semester roadmap:** every supported week or internship phase, its learning goals, concepts, readings, assignments, deliverables, and upcoming assessments. Show gaps explicitly instead of inventing an official weekly schedule.
5. **Exams and quizzes:** name, type, coverage if documented, dates/windows, duration, attempts, points/weight, permitted materials, location, and preparation links. Mark missing fields as unknown.
6. **Proctoring:** course summary plus exam-specific requirements for Proctorio, testing center, or other documented arrangements. Distinguish confirmed, provisional, conflicting and not documented. Absence of a Proctorio mention is not evidence of no proctoring.
7. **Major projects:** purpose, expected output, milestones, due dates, individual/group status, rubric, relevant concepts and a suggested preparation sequence. Mark suggested milestones as suggestions.
8. **Attendance and participation:** whether attendance is enforced, evidence of tracking, penalties, absence/makeup rules, required meetings/presentations or internship obligations. If enforcement is unknown, say so directly.
9. **Other useful information:** grading/competency rules, pass conditions, late-work/retake policy, required tools/textbooks, instructor and office-hour links, workload if documented, submission formats, and documented AI/collaboration rules.
10. **Progress and next steps:** separate assignment and concept progress; upcoming milestones and concepts needing review.
11. **Sources and freshness:** links back to the course evidence, snapshot timestamp, known gaps and conflicts.

Display an explicit “Not documented in available exports” value for unavailable required sections. This is a completed review of available evidence, not a fabricated answer. Keep a source-gap list so future exports can fill these sections.

### B. Separate concept lesson pages

Each lesson must include:

- A concrete title and a short hook explaining a useful problem it helps solve.
- A plain-language explanation that defines new terms before using them.
- At least one step-by-step worked example with inputs, reasoning, output and interpretation.
- A practical application related to the actual subject; clearly identify invented example data.
- A common mistake and how to recognize or fix it.
- A small “try it yourself” exercise with an expandable answer/explanation.
- Observable “I can…” learning goals and progress controls.
- Links to the relevant week, full plan, exported course sources, and one or two useful external explanations when verified.

Use the languages/tools actually taught in the exports. Do not assume every data science course uses the same language. For SQL, possible examples include customers/orders; visualization could compare distributions; business analytics could explain a dashboard decision. These are illustrative formats, not confirmed curriculum assignments.

Keep simple topics concise; split complex units into linked concepts. Reuse the same concept ID when revisiting it in later weeks. Official assignments remain linked separately from optional practice.

External links must be opened and checked when authoring lessons, preferably using official documentation or reputable university/open educational material. Record title, URL, relevance and verification date. Do not invent URLs or treat supplementary material as evidence for course policies. Core explanations and examples must remain usable without those links.

### C. Weekly report integration

Every course section, including a course with no assignments due, must provide:

- **Full course plan** link and an anchor to **This week's plan**.
- A brief focus statement and linked concept lessons derived from that week's evidence.
- Assignment completion and concept mastery counts with denominator labels.
- Next exam/project milestone, with any relevant documented proctoring/location requirement.
- Useful “Continue learning” or “Review” links based on saved progress.

Preserve existing assignment links, announcements, due dates, visual identity and collapsible course sections. Place navigable links outside the `<summary>` toggle where possible. Do not rely on generated model HTML to supply links: render validated URLs and IDs in the templates.

## 3. Evidence and scheduling rules

### Source selection

1. Start with current student-facing syllabus, active schedules, published modules/pages, assignment instructions, quiz metadata and applicable announcements.
2. Exclude unpublished instructor/TA resources, answer keys, explicitly obsolete/backup pages and synthetic fixtures from student lessons and policy extraction.
3. Follow course-linked current syllabi/schedules when exports lack their contents. For restricted sources, use the existing authorized read workflow if available; keep unavailable content as a gap. Do not substitute a similarly named public course.
4. Store evidence references for each policy, outcome, assessment, project and week mapping: snapshot, relative file, record ID, field/JSON pointer, short excerpt, source URL and source dates.
5. Handle authority and recency together. An explicitly applicable instructor change can supersede a syllabus detail; a more recent generic page cannot automatically do so. Preserve conflicting evidence and show the uncertainty.
6. Redact access codes, authenticated download tokens, session details and irrelevant personal data from generated pages and evidence bundles. Only publish the needed source excerpt and canonical course link.

### Week mapping

- Build the whole semester mapping once; the report selects a week from it.
- Keep `learning_week` separate from `due_week`; a project can be studied over several weeks and submitted later.
- Prefer explicit current module/week or dated schedule evidence for learning sequence. Use dates to place deadlines. Use title week prefixes as a fallback or supporting clue; record disagreements.
- For BA 300, parse the current SQL schedule table; for BA 315, parse Course Schedule and project dependencies. Check term/year/day-of-week consistency before accepting embedded dates.
- For FIN 398R, retain internship phases and dated milestones; suggested weekly preparation must be labeled and not imply official weekly assignments.
- Resolve module page slugs, content IDs, quiz-to-assignment relationships and external resource references. Fetch all module items when an embedded `items` list is incomplete.
- Use timezone-aware `zoneinfo.ZoneInfo`, converting UTC timestamps to the course timezone before week assignment. Use half-open ranges `[week_start, next_week_start)` to avoid counting boundary deadlines twice. Bound the final week by the supported course/term end.
- Do not silently place undated requirements into Week 1. Keep them in an “Unscheduled / date not provided” section with links from relevant concepts.
- Select the newest usable snapshot per course, report missing categories and stale fallback data, and avoid treating an incomplete fetch as deletion of coursework.

## 4. Proposed implementation structure

Use small Python modules and static HTML/CSS/JavaScript, with a small local server for reliable shared progress. Avoid a framework migration for this feature.

```text
IMPLEMENTATION_PLAN.md                 # this specification + work ledger
course_plans/
  __init__.py
  sources.py                           # snapshot loading, eligibility, provenance
  models.py                            # versioned validation/data structures
  curriculum.py                        # outcomes, policies, assessments, week mapping
  lessons.py                           # structured authoring/cache and fallback content
  render.py                            # plan/lesson HTML and link manifest
  progress.py                          # state validation, merge, persistence
  assets/learning.css
  assets/progress.js
build_course_plans.py                  # offline-first plan builder CLI
serve_learning.py                     # local site + progress API; Python stdlib
data/course_plans/<course_id>.json     # normalized source-backed curriculum
data/lesson_content/<course_id>.json   # reviewed/cached lesson content
data/course_plan_overrides.json       # explicit corrections with evidence/reason
data/learning_progress.json           # durable user-owned state; never regenerated
output/course_plans/index.html
output/course_plans/<course_id>/index.html
output/course_plans/<course_id>/weeks/week-01.html
output/course_plans/<course_id>/concepts/<concept_id>.html
output/assets/...
tests/test_course_plans.py
tests/test_week_mapping.py
tests/test_progress.py
tests/test_report_bundle.py
```

Names may change if inspection reveals a simpler fit; record any change here. Do not create empty scaffolding and call a phase complete.

### Data contracts

- **Course plan:** `schema_version`, stable `course_id`, term identity, title/code, source snapshot and hashes, calendar/timezone, overview, outcomes, policies, exams, projects, requirements, weeks, concepts, sources and gaps.
- **Evidence-backed fact:** `value`, `status` (`confirmed`, `provisional`, `not_documented`, `conflicting`), `source_ids`; support a short explanation. For a boolean policy, `false` requires explicit negative evidence; missing is `null`.
- **Requirement:** Canvas IDs, kind, description, points/weight where provided, due/unlock/lock timestamps, required/optional/unknown designation, publication/currentness, course link and evidence. Link assignment and quiz records instead of counting twice.
- **Week:** stable key, local start/end, mapping evidence, focus, outcome/concept IDs, learning items, due requirement IDs and suggested preparation.
- **Concept:** stable ID, title, source IDs, related outcomes, prerequisite concepts, applicable weeks, linked requirements, lesson content, verified resources and content hash.
- **Progress:** `schema_version`, course/term identity, per-item IDs, status, update timestamp, provenance (`manual` or actual Canvas evidence), optional notes and review flag. Keep content hashes separate from item identity.

Prefer IDs such as `canvas:<course_id>:assignment:<assignment_id>` and `concept:<course_id>:<stable_key>`. Do not derive progress keys from report date, week filename, list position or AI-rewritten titles. Deduplicate Canvas quizzes using their `assignment_id` relationship.

Validate generated structured content before rendering. A failed model request must affect only its course/concept. Cache by source/content hash and authoring version. Offline builds should use reviewed cached lessons; when none exist, show factual available topics and source gaps, without pretending a detailed lesson was generated.

## 5. Progress behavior and persistence

### User-facing meaning

- **Assignments:** show manual completion counts initially. The inspected exports lack submission data, so do not label these counts “synced from Canvas.” Unknown Canvas completion remains unknown.
- **Concepts:** `not_started`, `learning`, `practiced`, `mastered`, with a separate `needs_review` flag and optional notes. A click on a lesson never marks it mastered. Display mastery as self-assessed.
- **Progress formulas:** assignment completion = completed / known required trackable assignments; concept mastery = mastered / planned unique concepts. Show counts and coverage. Optional/excused items are excluded or reported separately; unknown requiredness is identified. A zero denominator displays “No items” or “Not available,” never 100%.
- **Semester elapsed:** if retained, label it as calendar progress; it is not academic completion. Do not estimate a grade from completion percentages.
- **Across weeks:** one saved item can appear in several weeks; completing it updates every view. Source changes can flag review without erasing prior work. Retain archived progress when items disappear and require explicit matching for genuinely new IDs.

### Storage decision

The normal local experience should serve `output/` at a stable loopback origin and use `data/learning_progress.json` as the durable record. A minimal server provides read/update/export/import endpoints. Validate item IDs and statuses, serialize writes and use atomic replacement; preserve state during regeneration and handle conflicting imports visibly. Restrict file serving to the output tree and progress writes to the intended file; bind to loopback and validate modifying-request origins.

This solves the current `file://` issue: browser storage behavior across separate local HTML files cannot be assumed to share progress reliably. Browser local storage may be a cache but must not be the only durable record.

ZIP/file-open mode must still support reading every plan and lesson offline. Provide explicit JSON progress export/import and, if supported, a clearly labeled browser-only temporary state. Show that portable copies do not automatically sync with the workspace. Do not silently show a saved indicator after a failed write. Include a small README in the bundle explaining unzip, navigation, and progress portability.

Automatic Canvas progress syncing is a later enhancement dependent on actual student-specific submission/module evidence. Do not read instructor/test-account state as learner achievement. The initial feature is complete with reliable manual progress and explicit data coverage.

## 6. Ordered implementation phases

### Phase 1 — Inventory and source normalization

- [x] Implement reusable snapshot loading, per-course completeness checks and source manifest.
- [x] Inventory all five courses, active schedules, linked syllabi and available policies.
- [x] Enrich syllabus/file content only where needed; keep provenance and gap records.
- [x] Filter current student-facing sources; deduplicate assignments/quizzes and identify missing module items.
- [x] Validate source selection against obsolete pages, unpublished teaching resources and partial exports.

**Done when:** all five course inventories exist and every source is classified with a traceable origin; unavailable syllabus/policy facts are explicitly listed.

### Phase 2 — Semester curricula and policy summaries

- [x] Implement validated plan schema and configurable term/timezone metadata.
- [x] Extract summaries, outcomes, concepts, projects, assessment details, grading and policy facts.
- [x] Map all supported weeks and internship phases using the rules above.
- [x] Add evidence-backed override support for ambiguous schedules; preserve manual corrections across rebuilds.
- [x] Review each course's findings, especially proctoring, attendance and pass conditions.

**Done when:** each course has a complete structured plan covering all available semester content, with no unsupported policy claims or invented deadlines.

### Phase 3 — Lesson content and static course pages

- [x] Build shared templates, course-plan index, five full plans and week pages.
- [x] Inventory all unique semester concepts; author sourced explanations/examples/practice pages.
- [x] Verify supplementary links while authoring and cache content for offline regeneration.
- [x] Deliver current week first as an integration slice, then complete every supported semester week/concept before calling this phase done.
- [x] Check examples for correct outputs, accessible language and relation to course concepts.

**Done when:** every concept linked from a supported week has an actual useful lesson page, every plan has all specified sections, and navigation works without AI calls.

### Phase 4 — Persistent progress

- [x] Implement progress schema, atomic storage, import/export, stable ID reconciliation and backup/error behavior.
- [x] Add local server and accessible controls for assignments, concepts, review flags and notes.
- [x] Update week/course progress from the same stored state and refresh other open views.
- [x] Implement documented portable/file-open behavior.
- [x] Verify progress survives regeneration, browser/server restart and source-content changes.

**Done when:** a changed status appears consistently in the weekly report and full plan, remains after rebuilding, and can be backed up/restored.

### Phase 5 — Weekly report and ZIP integration

- [x] Include course IDs and plan/concept references in report input; use shared semester mapping.
- [x] Add plan links, this-week links, concept previews, meaningful progress and next milestones to every course.
- [x] Preserve current homework/announcement behavior and document any intentional schedule correction.
- [x] Package every required linked file with valid relative paths, excluding private raw exports/session files.
- [x] Keep `--no-fetch`, `--no-email`, and `--no-ai` effective; add an explicit output directory/snapshot override if needed for isolated builds.

**Done when:** the normal local report and extracted ZIP navigate to all course/week/lesson pages and have clearly defined progress behavior.

### Phase 6 — Verification and handoff

- [x] Run focused data, week mapping, progress and bundle tests.
- [x] Build all five courses and all supported weeks with the inspected snapshot, without network/AI dependency for cached content.
- [x] Visually inspect desktop/mobile views and keyboard navigation; exercise progress controls in a browser.
- [x] Review source-backed policy summaries course by course; list unresolved source gaps.
- [x] Document build/open/backup instructions and update the handoff ledger below with exact commands/results.

**Done when:** all acceptance criteria pass and the user can open the report, follow full-plan/lesson links and retain progress through future runs.

## 7. Verification plan

Create small representative fixtures rather than copying private exports into tests. Test meaningful failure modes:

1. **Evidence:** missing syllabus and negated/provisional policy statements remain unknown/provisional; attendance tracking alone does not imply enforcement; obsolete and instructor-only sources stay excluded.
2. **Coverage:** five configured courses always appear, including weeks with no due work; partial exports report gaps; assignment/quiz pairs count once.
3. **Schedules:** week prefixes, date conflicts, Saturday boundary, UTC conversion, DST, final partial week, undated requirements, project modules, and internship milestones.
4. **Progress:** stable IDs across regenerated reports, unique concept denominator, zero denominators, manual versus synced provenance, source changes, atomic-write failure and validated import/merge.
5. **Content/rendering:** required lesson sections, safe escaping and URLs, correct tiny worked-example outputs, external-source verification metadata, no model dependency when cached content exists.
6. **Packaging:** traverse HTML links and anchors in a generated tree and an extracted ZIP; ensure all internal pages/assets exist and excluded private files are absent.
7. **End-to-end:** mark an assignment complete and a concept mastered, navigate report → plan → lesson, rebuild, reopen and verify persistence. Export/import into portable mode and verify its stated limitations.

Existing offline report command, to use in a controlled validation directory or after protecting same-date outputs:

```sh
python3 generate_summary.py --week 1 --no-fetch --no-email --no-ai
```

Proposed commands after implementation (not available yet):

```sh
python3 build_course_plans.py --snapshot snapshots/2026-09-15_133653 --no-ai
python3 -m unittest discover -s tests
python3 serve_learning.py
```

Do not run the unflagged report generator for validation: it can fetch live Canvas data, call an external model and send email. Creating plans and local reports does not require sending messages or making submissions.

## 8. Acceptance checklist

- [x] DS 350, BA 300, DS 250, FIN 398R and BA 315 each have a readable full plan.
- [x] Each plan covers outcomes, overview, concepts, exams, projects, Proctorio/testing center, attendance, grading and other documented relevant information.
- [x] Every policy and official requirement has evidence or an explicit unknown/conflict label.
- [x] Full semester coverage is present wherever exports support it; missing future content and suggested scheduling are clearly marked.
- [x] Every course in a weekly report links to its full plan and relevant weekly lessons.
- [x] Lessons teach concepts with understandable explanations, worked examples, useful applications and practice.
- [x] Assignment completion and concept mastery have accurate, transparent denominators and persisted state.
- [x] Regenerating reports never overwrites the user's progress or silently changes stable concept identity.
- [x] Plans/lessons work offline; shared progress works through the local server; portable behavior is explained and verified.
- [x] An extracted ZIP has no broken internal links and contains no session files, access codes or raw private export bundles.
- [x] Current assignment links, announcements and existing report controls continue working.

## 9. Handoff ledger

### September 15, 2026 — planning session

- Completed: inspected root scripts, report-generation integration points, current snapshot inventory, policy/source leads and completion-data coverage; wrote this implementation plan.
- Created: `IMPLEMENTATION_PLAN.md` only.
- Not performed: application implementation, live Canvas refresh, external resource selection, full syllabus review, report regeneration, email sending or progress initialization.
- Validation: read-only inspection; no feature tests apply to the planning document.
- Next action: Phase 1 — implement the source reader/manifest and inspect each course's current student-facing schedule/syllabus links. Continue through subsequent phases, updating this ledger after each meaningful checkpoint.

### Template for the next agent

- Date / phase:
- Files changed:
- Completed checklist items:
- Exact verification commands and results:
- Course-source gaps / decisions / limitations:
- Remaining work and first concrete next action:


### September 15, 2026 — implementation and verification

**Delivered:** 5 full course plans, 70 weekly pages, 61 concept lesson pages, updated Week 01/02 reports and ZIP bundles, durable local progress, portable backup/import, and documentation.

Files added: `course_plans/{sources,curriculum,lessons,render,progress}.py`, package initializer, CSS/JS assets, `build_course_plans.py`, `serve_learning.py`, `README.md`, `tests/test_learning.py`, and `tests/browser_learning.py`. Normalized models are in `data/course_plans/`; the generated site starts at `output/course_plans/index.html`.

Files updated: `generate_summary.py` integrates full-plan/weekly/lesson links, manual progress controls, source date warnings, shared requirement mappings, complete ZIP navigation, local-time conversion, half-open week boundaries and the final partial week. It uses reviewed local lessons without an external AI call. `fetch_course.py` now requests syllabus content on the next normal fetch. Original snapshots and saved Canvas sessions were not modified.

Implemented structure differs modestly from the original proposal: schema/data contracts live in `curriculum.py` and `progress.py`, and reviewed original lesson content lives in `lessons.py` rather than an AI-generated lesson cache. No external model is required. Term configuration is centralized in `sources.py`; it currently targets fall 2026. Course-specific mappings and policy summaries are explicitly reviewed authoring data, not a claim of automatic interpretation of every future syllabus. Optional evidence-backed policy/deadline corrections are supported in `data/course_plan_overrides.json`; no corrections were invented to reconcile conflicting dates.

Source completion is bounded by available evidence: current syllabus retrieval was attempted with the saved session. Canvas returned unauthorized or non-JSON responses, so no current syllabus supplement was obtained. Existing historical internship files and obsolete syllabus backups were not promoted to current policies. Missing official course-wide outcomes, general attendance/late-work rules and grade thresholds are shown as unknown; suggested study outcomes are clearly labeled. All modules in the inspected export had their expected item counts. No source-gap claim should be read as a verified absence of a course requirement.

Verified commands/results:

- `python3 generate_summary.py --week 1 --no-fetch --no-email --no-ai` — updated report and ZIP.
- Same command for `--week 2` — updated report and ZIP.
- `python3 build_course_plans.py --no-ai` — 5 plans / 61 lessons / 70 weeks.
- Isolated build with `--output /tmp/learning-isolated-check --data-dir /tmp/learning-isolated-models` — passed.
- `python3 -m unittest discover -s tests -v` — 15 tests passed: eligibility, dates/DST, partial exports, deduplication, unknown policies, overrides, persistence/backup/corruption/conflicts, example SQL results, manifests and extracted ZIP links.
- `python3 tests/browser_learning.py` — passed with temporary progress only: dashboard, shared report/lesson completion, notes, review flags, export/import, rebuild persistence, mobile width, origin guard and file-open mode. Chromium required an unsandboxed local test launch. No real learner state was created or modified by verification.
- Python compilation checks — passed.
- Visual review: `/tmp/learning-dashboard.png` and `/tmp/learning-lesson-mobile.png`; desktop and mobile inspected.

Progress is intentionally manual: there are no student submission objects in the export. The local server saves to `data/learning_progress.json` with atomic replacement and a previous-version backup. File-open mode is explicitly page-only and offers import/export; it does not imply cross-file browser storage synchronization.

No emails, messages, submissions, grade changes, or external publishing occurred.

**Next-agent handoff:** use README commands to rebuild/open. If the user supplies a refreshed authenticated export, review the source gaps first, then update official outcomes/policies and resolve the fall-schedule versus old-deadline conflicts with evidence. Recheck the reviewed concept mappings if course content changes. Treat future-semester generalization and automatic student Canvas syncing as separate enhancements; the current fall-course learning site is delivered.

### September 16, 2026 — course 424334 inclusion

Course `424334` was already fetched by the Canvas acquisition configuration but was omitted from the separate learning-site and weekly-report course lists. It is now included in source normalization, model generation, the dashboard, all 14 weekly pages, the learning manifest, and weekly reports. No `course_424334` export exists in the current workspace, so the application generates an explicit source-gap plan instead of hiding the course or inventing its identity and curriculum. The next successful full Canvas refresh will populate its exported identity, requirements, deadlines, sources, and week mappings; its course-specific outcomes and concept mapping should then be reviewed.
