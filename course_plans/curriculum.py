"""Normalize exported requirements and map reviewed lessons to source-backed weeks."""
import hashlib
import json
import re
from pathlib import Path
from datetime import timedelta
from .sources import ROOT, START, END, COURSES, load_course, eligible, plain, safe_url, date, week_of, title_week, schedule_rows
from .lessons import LESSONS

UNKNOWN = 'Not documented in available exports.'
# These are suggested study objectives, not invented official syllabus outcomes.
PROFILES = {
 '431290': {
  'overview': 'Use Python and Polars to turn messy data into reproducible answers. Explore names and flights, connect baseball tables, evaluate predictions, clean survey text and publish a portfolio that shows how you reached your conclusions.',
  'goals': ['Write and debug reproducible Python analyses.', 'Filter, transform, summarize and visualize tabular data.', 'Use SQL and joins to connect related datasets.', 'Evaluate predictive models and clean text/category features.', 'Communicate findings in a Quarto report and a GitHub portfolio.'],
  'weeks': ['workflow python','workflow python questions','filter graphics','columns filter','columns aggregation','missing tidy','sql-basics questions','joins sql-groups','ml','ml regression','regex factors','factors ml','git reflection','python reflection'],
  'tools': 'Python, Polars, Quarto, GitHub, Slack, Lets-Plot and scikit-learn are referenced in the published resources. Follow course setup instructions for exact versions.',
 },
 '431292': {
  'overview': 'Build a complete analysis in R: import data, reshape it, join sources, visualize patterns and communicate an actionable finding. The semester moves from charts and tidy tables to dates, maps, interactive views, text and reusable functions.',
  'goals': ['Build and critique charts using the grammar of graphics.', 'Import, tidy, summarize and join data in R.', 'Analyze time, spatial, text and categorical data.', 'Create interactive views and reusable functions.', 'Publish and defend an original visualization-based semester project.'],
  'weeks': ['workflow git','graphics distributions questions','r-wrangle aggregation','import tidy','communication git','joins','dates','spatial','interactive questions','regex factors','git reflection','functions','communication reflection','reflection communication'],
  'tools': 'R, RStudio, Quarto, GitHub and R for Data Science (2e); course topics also reference dplyr, stringr, forcats, purrr, tidyquant and leaflet.',
 },
 '420634': {
  'overview': 'Learn to ask useful business questions of relational databases in MySQL. Start with precise filters and grouped summaries, then build multi-step queries, combine tables, rank observations and apply the techniques to team analysis projects.',
  'goals': ['Write reliable SELECT queries with clear filters.', 'Summarize business measures at the correct grouping level.', 'Use subqueries, CTEs and CASE for multi-step analysis.', 'Join tables and use window functions without inflating results.', 'Explain and defend team analyses of customer activity and promotion.'],
  'weeks': ['sql-basics sql-groups sql-functions','sql-filtering sql-groups','sql-basics sql-groups sql-filtering','subqueries','subqueries case','subqueries case','joins','windows sql-design','joins windows','questions sql-basics','tidy subqueries','communication sql-groups','sql-design','reflection sql-design'],
  'tools': 'MySQL Workbench and Server, the sakila/world sample databases, and Excel for the promotion project are referenced in the exports.',
 },
 '420672': {
  'overview': 'Turn business data into a persuasive decision. Build Power BI models, clean source tables, write DAX measures and create interactive views; then practice defending recommendations through cases and the final analysis work.',
  'goals': ['Import, clean and model data in Power BI.', 'Build interactive visuals that answer an audience’s question.', 'Evaluate DAX expressions under changing filter context.', 'Compare business alternatives using evidence and assumptions.', 'Present an actionable analytical story with explicit limitations.'],
  'weeks': ['power-query pbi-model', 'power-query pbi-model case-analysis','pbi-model communication','dax-context dax-iterators case-analysis','dax-context dax-iterators case-analysis','dax-tables case-analysis','dax-tables case-analysis','dax-context dax-tables','dax-iterators case-analysis','dax-context dax-iterators dax-tables','pbi-model case-analysis','communication case-analysis','interactive case-analysis','communication reflection'],
  'tools': 'Microsoft Power BI / Power Query / DAX. Project 1 also mentions Tableau, but this exported schedule emphasizes Power BI. The schedule links a VDI option for Mac computers.',
 },
 '424344': {
  'overview': 'Turn internship work into documented professional growth. Track actual work hours, obtain qualified résumé feedback, update your professional profile and complete the required end-of-internship evaluation.',
  'goals': ['Document work hours against internship requirements.', 'Explain your professional contribution using credible evidence.', 'Apply feedback from qualified résumé reviewers.', 'Reflect on growth and complete required internship records.'],
  'weeks': ['internship-hours reflection','internship-hours','internship-hours','career-evidence','career-evidence','career-evidence','career-evidence','career-evidence','career-evidence reflection','internship-hours','career-evidence','internship-hours reflection','internship-hours reflection','reflection'],
  'tools': 'Work-hour records, résumé PDF, LinkedIn screenshot and VMock. The course has a No Textbook module.',
 },
 '424334': {
  'overview': 'Examine why earthquakes, volcanoes, landslides, floods, hurricanes and tornadoes occur; compare their effects on communities; and use scientific evidence and historical cases to evaluate risk and mitigation.',
  'goals': ['Explain the science behind major natural hazards.', 'Describe the scale, timing and consequences of documented disasters.', 'Connect hazards to effects on communities, engineering, economics, culture and politics.', 'Use past events to evaluate preparedness and mitigation.', 'Compare natural-disaster risks for a selected city.'],
  'weeks': ['hazard-risk tectonics','tectonics hazard-risk','earthquakes hazard-risk','earthquakes case-analysis','volcanoes hazard-risk','volcanoes case-analysis','landslides hazard-risk','landslides case-analysis','floods hazard-risk','floods case-analysis','floods hazard-risk','severe-weather hazard-risk','severe-weather case-analysis','hazard-risk reflection'],
  'tools': 'Perusall, Canvas readiness quizzes, Google Earth guided labs, scientific hazard maps and credible historical sources are referenced in the exported course content.',
 }
}

# Reviewed facts: references resolve to eligible export sources, not hidden notes.
FACTS = {
 '431290': [
 ('Grading', 'confirmed', 'The end-of-course goals letter describes specifications grading and a final grade request. Beginning/mid/end letters review progress against syllabus competencies. The current competency thresholds are missing; do not use the example grade calculation as the official table.', 'assignments', 'Course Goals Letter'),
 ('Participation', 'confirmed', 'Slack is explicitly required. The published communication page directs questions to Slack and submissions to Canvas. General attendance enforcement and absence penalties are not documented.', 'pages', 'Slack vs Canvas'),
 ('Exams', 'confirmed', 'The Week 14 coding challenge asks for a rendered HTML file and the QMD source, with code, comments and outputs. Its instructions describe an hour of work. Proctorio/testing-center requirements are not stated in that assignment.', 'assignments', 'W14 Assignment: Coding Challenge'),
 ('Report format', 'confirmed', 'The setup unit explicitly requires charts to be embedded in a single submitted HTML file using embed-resources: true.', 'pages', 'W01 U0: Unit Overview'),
 ],
 '431292': [
 ('Proctorio', 'confirmed', 'W09, W11 and W13 coding challenges are remotely proctored with Proctorio. Instructions specify Chrome, the extension and photo ID. They allow notes, past homework and internet, but prohibit AI and communication/help from others. The descriptions specify 90 minutes; compare quiz metadata below for conflicts.', 'assignments', 'Coding Challenge'),
 ('Meetings and grading review', 'confirmed', 'The exported course includes recurring team reports and W13 exit-interview preparation. The linked preparation instructions require scheduling and attending the interview, an updated résumé and a one-page grade-proposal cover letter. General class absence penalties and current grade thresholds are not available.', 'assignments', 'Prepare for the Exit Interview'),
 ],
 '420634': [
 ('Testing center', 'confirmed', 'The current SQL schedule places Exams 1, 2 and 3 in the testing center and lists an Exam 1.1 retake. It also lists Exam 4 in December; that row does not explicitly repeat a testing-center location. Proctorio is not documented.', 'pages', 'SQL for Analysts'),
 ('Attendance', 'confirmed', 'The SQL schedule links an attendance tracker and the export includes attendance/participation assignments. The general absence penalty and makeup policy are not documented.', 'pages', 'SQL for Analysts'),
 ('Group participation and AI', 'confirmed', 'The three team project instructions require group work and say nonparticipants receive zero even if they submit correct work. They prohibit outside-class collaboration and tools that supply outcomes, while allowing syntax help, Google and the assigned group. Read the full project rules before working.', 'assignments', 'Project 1:'),
 ],
 '420672': [
 ('Testing center', 'confirmed', 'The Course Schedule explicitly lists the final exam in the testing center on December 17 (year inferred from this term). Project 7 also describes a testing-center final as a placeholder. The DAX exam location is not specified; Proctorio is not documented.', 'pages', 'Course Schedule'),
 ('Exam date conflict', 'conflicting', 'The schedule places the DAX PowerBI exam on November 19, while the exported assignment deadline is December 2 at 11:59 PM Mountain. Treat the exam date as unresolved and check the current course instructions.', 'assignments', 'DAX PowerBI Exam'),
 ('Attendance', 'confirmed', 'The schedule links an attendance tracker and contains group/class discussions. The general attendance penalty and absence/makeup rules are not documented.', 'pages', 'Course Schedule'),
 ('Case writeups', 'confirmed', 'The one-page case writeup template explicitly prohibits using AI to fill it out. It asks for the decision, key facts, analysis and a reasoned recommendation. The lessons here use unrelated invented examples for study.', 'pages', '1 Page Case Writeup Template'),
 ],
 '424344': [
 ('Work-hour requirement', 'confirmed', 'Total Hours states that FIN398 internships require at least 270 total hours, at least 7 weeks and at least 20 hours per week. These are separate minimums; the assignment describes a pass/fail category.', 'assignments', 'Total Hours'),
 ('Pass condition', 'confirmed', 'The Self-Evaluation assignment explicitly states that completing the self-evaluation is necessary to pass. The actual completion word must come from your own completed evaluation.', 'assignments', 'Self-Evaluation'),
 ('Résumé review', 'confirmed', 'Obtain reviews from two professionals; family members, current students and direct supervisors do not count. Submit the reviewers’ professional details, advice and your response, plus an updated résumé PDF. The instructions also require VMock.', 'assignments', 'Resume Reviews & Update'),
 ('Professional profile', 'confirmed', 'Update LinkedIn with your internship position and submit a screenshot.', 'assignments', 'Updated LinkedIn'),
 ('Attendance / exams', 'not_documented', 'No classroom attendance penalty, Proctorio or testing-center requirement is documented. The work-hour and evaluation requirements above still apply.', 'assignments', 'Total Hours'),
 ],
 '424334': [
 ('Official course outcomes', 'confirmed', 'The current syllabus asks students to explain the science and basic facts of disasters, discuss their effects across communities and disciplines, and apply lessons learned to future preparation.', 'syllabus', 'Current course syllabus'),
 ('Team-based participation', 'confirmed', 'The syllabus describes a flipped, team-based structure: preparation in Perusall, individual and team readiness checks, and in-class application activities. It recommends attending every meeting, participating actively with the team, and submitting every assignment.', 'syllabus', 'Current course syllabus'),
 ('Proctorio', 'confirmed', 'Published Tests 2–4 and the final exam are remotely proctored. Instructions require Chrome and the Proctorio extension; tests are closed to outside materials. Read each test’s current camera, microphone, environment and timing rules before starting.', 'assignments', 'Remotely Proctored'),
 ('Semester project', 'confirmed', 'The final project evaluates personal risk tolerance and the earthquake, volcano, landslide, flood, tornado and hurricane risk of one city using course resources, additional research, historical events, impacts and mitigation.', 'assignments', 'End of Semester Project'),
 ('Field trip', 'confirmed', 'The export includes field-trip sign-up and attendance items plus a documented research-report makeup option. Confirm the active dates and participation instructions in Canvas.', 'assignments', 'Field Trip'),
 ]
}


def fallback_profile(cid, info):
    """Keep a configured course visible until its curriculum is reviewed."""
    label = info.get('course_code') or info.get('name') or f'Course {cid}'
    return {
        'overview': (
            f'{label} is a configured Canvas course. Its exported coursework and '
            'deadlines are included below when available. Course-specific learning '
            'outcomes and concept lessons require a reviewed source mapping.'
        ),
        'goals': [
            'Review the exported modules, assignments, assessments, and deadlines.',
            'Confirm course-specific outcomes and policies in the current Canvas syllabus.',
        ],
        'weeks': [''] * 14,
        'tools': 'Not documented in the available reviewed course mapping.',
    }


def selected(sources, kind=None, title=None):
    return [s for s in sources if (kind is None or s['kind']==kind) and (title is None or title.lower() in s['title'].lower())]


def normalize(snapshot, cid):
    data, sources, gaps, manifest = load_course(snapshot,cid)
    info = data['course_info']
    profile = PROFILES.get(cid) or fallback_profile(cid, info)
    by_source = {s['id']:s for s in sources}
    module_assignment_weeks = {}; module_page_weeks = {}; phases = []
    for m in data['modules']:
        if not eligible(m): continue
        w = title_week(m['name'])
        phases.append({'name':m['name'],'items':[x.get('title','') for x in m.get('items',[]) if eligible(x)]})
        if w:
            for item in m.get('items',[]):
                if not eligible(item): continue
                module_assignment_weeks.setdefault(str(item.get('content_id')),[]).append(w)
                module_page_weeks.setdefault(item.get('page_url'),[]).append(w)
    requirements = []
    quizzes = {str(q.get('assignment_id')):q for q in data['quizzes'] if eligible(q)}
    records = [('assignments',a) for a in data['assignments'] if eligible(a)]
    assignment_ids = {str(a['id']) for _,a in records}
    records += [('quizzes',q) for q in data['quizzes'] if eligible(q) and str(q.get('assignment_id')) not in assignment_ids]
    for kind,a in records:
        rid = str(a['id']); title=a.get('name',a.get('title',''))
        text=plain(a.get('description','')); q=quizzes.get(rid,{}) if kind=='assignments' else a
        due=date(a.get('due_at')); due_week=week_of(due)
        learning_weeks=sorted(set(module_assignment_weeks.get(rid,[]) + ([title_week(title)] if title_week(title) else [])))
        if not learning_weeks and due_week: learning_weeks=[due_week]
        optional=bool(re.search(r'extra credit|\bbonus\b',title,re.I))
        reference=bool(re.search(r'study guide|practice test|practice question|problem description|prep solution',title,re.I))
        conditional=bool(re.search(r'stretch',title,re.I))
        note=[]
        if due and not due_week: note.append('Exported date is outside the configured fall term; current deadline needs confirmation.')
        if due_week and learning_weeks and due_week not in learning_weeks: note.append(f'Learning week {", ".join(map(str,learning_weeks))} differs from exported deadline week {due_week}.')
        minutes=re.search(r'(\d+)[ -]minute',text,re.I)
        if minutes and q.get('time_limit') and int(minutes[1])!=q['time_limit']: note.append('Time limit conflict between description and quiz settings.')
        source_id=f'{kind}:{rid}'
        req={'id':f'canvas:{cid}:{"assignment" if kind=="assignments" else "quiz"}:{rid}',
             'canvas_id':rid,'title':title,'source_ids':[source_id] if source_id in by_source else [],
             'url':safe_url(a.get('html_url','')) or f'https://byui.instructure.com/courses/{cid}/{kind}/{rid}',
             'description':text,'points':a.get('points_possible'),'due_at':a.get('due_at'),
             'lock_at':a.get('lock_at'), 'unlock_at':a.get('unlock_at'), 'due_week':due_week,
             'learning_weeks':learning_weeks,'notes':note,'category':'optional' if optional else 'reference' if reference else 'conditional' if conditional else 'assigned',
             'kind':'assessment' if q or re.search(r'exam|coding challenge|\bquiz\b',title,re.I) else 'project' if re.search(r'project|case study|core task|resume|résumé|self-evaluation|total hours|linkedin|goals letter',title,re.I) else 'assignment',
             'quiz': {k:q.get(k) for k in ('time_limit','allowed_attempts','unlock_at','lock_at')},
             'submission_types':a.get('submission_types',[]), 'group_assignment':bool(a.get('group_category_id')),
             'rubric':[{'criterion':r.get('description'),'points':r.get('points')} for r in a.get('rubric',[])],
             'proctoring':'Proctorio (confirmed in description)' if 'proctorio' in text.lower() and 'Remotely Proctored' in title else 'Testing center (provisional placeholder)' if 'testing center' in text.lower() and 'placeholder' in text.lower() else UNKNOWN}
        requirements.append(req)
    outside=sum(bool(r['due_at']) and not r['due_week'] for r in requirements)
    if outside: gaps.append(f'{outside} items have deadlines outside the configured fall term. Their original dates are preserved; learning-week labels are not replacement deadlines.')
    schedule=selected(sources,'pages','SQL for Analysts' if cid=='420634' else 'Course Schedule') if cid in ('420634','420672') else []
    rows=[]
    for s in schedule:
        page=next((p for p in data['pages'] if str(p.get('page_id'))==s['record_id']),{})
        rows+= [dict(r,source_id=s['id']) for r in schedule_rows(page.get('body',''))]
    facts=[]
    for label,status,value,kind,title in FACTS.get(cid, []):
        refs=selected(sources,kind,title)
        # Never retain a curated factual claim when its supporting source disappears.
        facts.append({'label':label,'status':status if refs else 'not_documented','value':value if refs else UNKNOWN,'source_ids':[s['id'] for s in refs]})
    if cid=='431292':
        facts.append({'label':'Semester project','status':'confirmed','value':'The linked semester-project instructions require a remote dataset; at least two basic wrangling verbs and two grouped-wrangling verbs; appropriate visual encodings and theme choices; interpretation and a hosted HTML link. Combining/reshaping data may be needed. Review the project during the exit interview. Suggested preparation: question → data → validated analysis → charts → feedback → publication.', 'source_ids':[s['id'] for s in selected(sources,'assignments','W14 Semester Project')], 'external_url':'https://byuistats.github.io/DS350_assignments/semester_project.html'})
        for f in facts:
            if f['label']=='Meetings and grading review': f['external_url']='https://byuistats.github.io/DS350_assignments/Task_33_interview.html'
    for label in ('General attendance enforcement','General late-work / makeup policy','Official course outcomes','Grade thresholds / competency table'):
        if not any(f['label'] == label for f in facts):
            facts.append({'label':label,'status':'not_documented','value':UNKNOWN,'source_ids':[]})
    weeks=[]; concept_weeks={}
    for w in range(1,15):
        mods=[s for s in sources if s['kind']=='modules' and title_week(s['title'])==w]
        relevant_rows=[r for r in rows if r['week']==w]
        refs=[s['id'] for s in mods]+[r['source_id'] for r in relevant_rows]
        refs += [s['id'] for s in sources if title_week(s['title'])==w and s['kind'] in ('pages','assignments')]
        weekreq=[r for r in requirements if w in r['learning_weeks'] or r['due_week']==w]
        refs += [sid for r in weekreq for sid in r['source_ids']]
        # Internship study sessions are suggestions tied to actual milestone instructions.
        keys=profile['weeks'][w-1].split()
        if cid=='424344': refs += [s['id'] for s in sources if s['kind']=='assignments' and any(t in s['title'] for t in ('Total Hours','Resume Reviews','Self-Evaluation','LinkedIn'))]
        if cid in ('420634','420672') and not relevant_rows and not weekreq:
            keys=[]  # Don't invent a calendar lesson if the schedule has no row.
        if not refs: keys=[]
        focus=' / '.join(m['title'] for m in mods) or ('Internship study checkpoint (suggested)' if cid=='424344' else 'Course schedule and preparation' if relevant_rows else 'Preparation from exported assignments (schedule differs)' if weekreq else 'No scheduled learning content in this export')
        for key in keys: concept_weeks.setdefault(key,[]).append(w)
        weeks.append({'number':w,'start':(START+timedelta(weeks=w-1)).isoformat(),'end':min(START+timedelta(weeks=w),END).isoformat(),
                      'focus':focus,'concepts':keys,'source_ids':list(dict.fromkeys(refs)), 'schedule':relevant_rows,
                      'learning_ids':[r['id'] for r in requirements if w in r['learning_weeks']], 'due_ids':[r['id'] for r in requirements if r['due_week']==w],
                      'suggested':cid=='424344'})
    concepts=[]
    for key, ws in concept_weeks.items():
        lesson = dict(LESSONS[key])
        if 'variants' in lesson:
            variant = lesson['variants'].get(cid)
            if variant:
                lesson.update(variant)
            del lesson['variants']
        refs=list(dict.fromkeys(sid for w in weeks if w['number'] in ws for sid in w['source_ids']))
        evidence_text = [(sid, by_source[sid]['text']) for sid in refs if sid in by_source]
        content_hash = hashlib.sha256(json.dumps([lesson,evidence_text],sort_keys=True).encode()).hexdigest()
        concepts.append(dict(lesson,id=f'concept:{cid}:{key}',weeks=ws,source_ids=refs,content_hash=content_hash))
    # Store only sanitized excerpts in normalized data; raw exports remain untouched.
    for s in sources:
        s['text']=re.sub(r'\b[0-9a-f]{24,}\b','[identifier omitted]',s['text'], flags=re.I)
    for r in requirements:
        r['description']=re.sub(r'\b[0-9a-f]{24,}\b','[identifier omitted]',r['description'], flags=re.I)
    return {'schema_version':1,'course_id':cid,'term':'2026-fall','course_code':info.get('course_code',cid),'name':info.get('name',cid),
            'snapshot':sources[0]['snapshot'] if sources else str(snapshot),'overview':profile['overview'],'suggested_outcomes':profile['goals'],
            'tools':profile['tools'],'facts':facts,'weeks':weeks,'concepts':concepts,'requirements':requirements,'sources':sources,'gaps':gaps,
            'manifest':manifest,'phases':phases,'grading_groups':[{'name':g['name'],'weight':g.get('group_weight')} for g in data['assignment_groups']],
            'weighted_grading':info.get('apply_assignment_group_weights',False),'calendar':{'start':START.isoformat(),'end_exclusive':END.isoformat(),'timezone':'America/Denver','basis':'Configured fall term; schedule years inferred where omitted.'}}


def apply_overrides(plan, changes):
    """Apply explicit evidence-backed corrections without touching exported records."""
    if not changes: return plan
    if not isinstance(changes,dict): raise ValueError('Course overrides must be an object.')
    for task in changes.get('personal_tasks', []):
        key = task.get('key', '')
        week = task.get('week')
        if not re.fullmatch(r'[a-z0-9-]+', key) or type(week) is not int or not 1 <= week <= 14:
            raise ValueError('Personal tasks need a stable key and week 1–14.')
        url = safe_url(task.get('url', ''))
        if not url or not task.get('title'):
            raise ValueError('Personal tasks need a title and resource URL.')
        item = {'id':f'task:{plan["course_id"]}:{key}', 'canvas_id':None,
                'title':task['title'], 'description':task.get('description',''),
                'url':url, 'category':'personal', 'kind':'personal task',
                'learning_weeks':[week], 'due_week':None, 'due_at':None,
                'source_ids':[], 'notes':['Added by you for this week.'],
                'quiz':{}, 'points':None, 'submission_types':[], 'rubric':[]}
        plan['requirements'] = [r for r in plan['requirements'] if r['id'] != item['id']] + [item]
        for w in plan['weeks']:
            w['learning_ids'] = [i for i in w['learning_ids'] if i != item['id']]
            if w['number'] == week:
                w['learning_ids'].append(item['id'])
    for change in changes.get('facts',[]):
        if not change.get('reason') or not change.get('source_url') or not safe_url(change['source_url']):
            raise ValueError('A policy correction needs a reason and an HTTP source URL.')
        if change.get('status') not in ('confirmed','provisional','conflicting','not_documented'):
            raise ValueError('Invalid policy status.')
        plan['facts']=[f for f in plan['facts'] if f['label']!=change['label']]
        plan['facts'].append({'label':change['label'],'status':change['status'],'value':change['value'],
                              'source_ids':[],'external_url':safe_url(change['source_url']),'override_reason':change['reason']})
    for change in changes.get('deadlines',[]):
        if not change.get('reason') or not safe_url(change.get('source_url','')) or not date(change.get('due_at')):
            raise ValueError('A deadline correction needs a valid date, reason and source URL.')
        req=next((r for r in plan['requirements'] if r['id']==change['id']),None)
        if req is None: raise ValueError('Unknown requirement in deadline override.')
        old=req['due_at'];req['exported_due_at']=old;req['due_at']=change['due_at'];req['due_week']=week_of(req['due_at'])
        req['notes'].append(f"Reviewed correction: {change['reason']}. Original export: {old}. Source: {safe_url(change['source_url'])}")
        for w in plan['weeks']:
            w['due_ids']=[i for i in w['due_ids'] if i!=req['id']]
            if w['number']==req['due_week']: w['due_ids'].append(req['id'])
    return plan


def build_models(snapshot=None, data_dir=None):
    snapshot=ROOT/'snapshots'/sorted(p.name for p in (ROOT/'snapshots').iterdir() if p.is_dir())[ -1] if snapshot is None else snapshot
    override_path=ROOT/'data/course_plan_overrides.json'
    overrides=json.loads(override_path.read_text()) if override_path.exists() else {}
    plans=[apply_overrides(normalize(snapshot,cid),overrides.get(cid,{})) for cid in COURSES]
    directory=Path(data_dir) if data_dir else ROOT/'data'/'course_plans'
    directory.mkdir(parents=True,exist_ok=True)
    for p in plans:
        (directory/f'{p["course_id"]}.json').write_text(json.dumps(p,indent=2,ensure_ascii=False))
    return plans
