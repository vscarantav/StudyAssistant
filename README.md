# Homework Assistant — course plans and learning progress

## Open the student Canvas workflow

Add `CANVAS_STUDENT_ACCT` and `CANVAS_STUDENT_PASSWORD` to the ignored `.env`
file, then run:

```sh
python3 student_canvas_flow.py
```

The script opens Chrome with a fresh, non-persistent browser context, enters
the student credentials, and pauses for you to complete MFA. After Canvas
redirects to its home page, it saves static copies of the six rendered course
landing pages under `data/student_canvas_html/<timestamp>/`, visits each
course's Assignments, Grades, Pages, and Quizzes sections, and waits with the
browser open for the next interactive steps. Saved HTML has scripts and
CSRF/authenticity-token values removed. It also visits each course syllabus at
`/assignments/syllabus` and saves it as `syllabus.html` beside the landing-page
file. On each Grades page it captures the displayed Canvas total and letter
grade into `grade.json`. After a complete run, the current local weekly report
is regenerated and shows that value in each course header. The flow does not
save cookies or submit coursework.

## Open your learning dashboard

```sh
python3 serve_learning.py --open
```

Open <http://127.0.0.1:8765/course_plans/index.html> while the server is running. It serves only the generated `output/` site on your computer. Stop it with Ctrl+C.

The dashboard covers all six configured Canvas courses: DS 350, BA 300, DS 250, FIN 398R, BA 315, and GESCI 201 Natural Disasters. It contains 84 weekly pages and reviewed course-specific concept pages. The current Week 01 and Week 02 reports link to the plans and lessons. If any configured course is missing from all local snapshots, it remains visible with an explicit source gap instead of disappearing.

Each course covers its overview, suggested learning outcomes, roadmap, exams, projects, attendance/proctoring evidence, grading metadata, tools, complete exported coursework and source gaps. Concept lessons include original worked examples, real-world uses, common mistakes and practice questions with explanations.

## Progress

- Mark coursework completed manually from a weekly report, course plan or weekly plan.
- Mark concepts Not started, Learning, Practiced or Mastered, and add review flags or notes.
- Assignment counts include the course's exported assigned items. Optional, reference and conditional/stretch items are excluded from the denominator. This is a completion inventory, not an estimate of your final grade.
- Progress is saved in `data/learning_progress.json`. The previous version is kept as `data/learning_progress.previous.json`. Regenerating reports never resets these files.
- Use **Export progress backup** to move progress between computers; **Import** merges the backup and asks before replacing differing item states.
- Canvas submission state is not available in the current exports. The application does not claim automatic Canvas syncing or submit work.

You can open `output/course_plans/index.html` directly without running Python. All lesson content and navigation work offline. In this portable mode, edits stay on the current page: export before leaving, then import on the next page. Use the local server for shared persistent progress.

## Rebuild from existing exports

```sh
python3 build_course_plans.py --no-ai
python3 generate_summary.py --week 1 --no-fetch --no-email --no-ai
python3 generate_summary.py --week 2 --no-fetch --no-email --no-ai
python3 build_course_plans.py --no-ai
```

The report generator refreshes the dashboard's report list automatically; the standalone build is useful after editing lesson content. Source snapshot selection can be explicit:

```sh
python3 build_course_plans.py --snapshot snapshots/2026-09-15_133653 --no-ai
```

For an isolated build, pass `--output /tmp/learning-preview --data-dir /tmp/learning-models`. Building reviewed lessons requires Python 3.10+ and no external API key; the existing Canvas/browser workflows additionally require their existing Playwright dependencies.

ZIP reports include linked course plans, weekly lessons, assets and portable-use instructions. Extract the entire ZIP before opening its HTML. Progress backups are exported separately and are not automatically included in report ZIPs.

## Source limitations to understand

The September 15, 2026 exports contain a mix of fall schedules and older assignment deadlines. Original dates are preserved and conflicts are flagged; a learning week does not silently replace a deadline. Mountain-time conversion accounts for daylight saving time.

The existing session could not retrieve current syllabi during implementation (unauthorized/non-JSON responses). Official course-wide outcomes, general absence penalties, late-work rules and some grade thresholds remain explicitly unknown. The next normal Canvas fetch now requests syllabus text as well as existing course metadata. Check the original Canvas course when a requirement is marked unresolved.

Reviewed policy highlights are cited within each plan. For example, DS 350's coding challenges specify Proctorio; BA 300's schedule names testing-center exams; BA 315 has an exam-date conflict; FIN 398R's Total Hours instructions state its hour/week minimums. Supplementary documentation supports learning examples, not course-policy claims.

## Files and maintenance

- `IMPLEMENTATION_PLAN.md`: original specification, completion ledger and next-agent handoff.
- `course_plans/sources.py`: student-facing source filtering, source manifest, dates and schedule parsing.
- `course_plans/curriculum.py`: normalized requirements, reviewed policy summaries and suggested concept-to-week mappings.
- `course_plans/lessons.py`: original reviewed lessons and verified supplementary links.
- `course_plans/render.py`: templates, report fragments and navigation manifest.
- `course_plans/progress.py`, `serve_learning.py`: validated local persistence.
- `data/course_plans/*.json`: rebuildable normalized models; original snapshots remain unchanged.

Optional reviewed corrections can be saved in `data/course_plan_overrides.json`, keyed by Canvas course ID. `facts` entries require `label`, `status`, `value`, `reason`, and `source_url`; `deadlines` entries require the stable requirement `id`, ISO `due_at`, `reason`, and `source_url`. Original exported deadlines are retained alongside corrections.

The authored teaching sequence is intentionally inspectable. When the course changes, review the mappings and policy summaries against the new evidence before relying on the new semester's plan. No AI output is needed to rebuild existing lessons.

## Verification

```sh
python3 -m unittest discover -s tests -v
python3 tests/browser_learning.py
```

The browser check requires a working Playwright Chromium installation and permission to launch a browser and bind a local test port. It uses temporary progress, never real learner records. Do not run `test_mock.py`: that older script rewrites `generate_summary.py`.
