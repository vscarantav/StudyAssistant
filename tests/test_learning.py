import json
import tempfile
import unittest
import sqlite3
import zipfile
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, unquote
from datetime import datetime
from course_plans.sources import eligible, date, week_of, schedule_rows, safe_url, ROOT
from course_plans.progress import ProgressStore, validate
from course_plans.curriculum import normalize, apply_overrides
from course_plans.render import control, manifest, report_fragment
from generate_summary import extract_week_assignments

class SourceTests(unittest.TestCase):
 def test_student_sources(self):
  for r in [{'title':'Syllabus (Backup - Do Not Use)'},{'title':'Course guide','published':False},{'title':'Old','hide_from_students':True},{'title':'Exam 3 prep solution'}]: self.assertFalse(eligible(r))
  self.assertTrue(eligible({'title':'W09 Coding Challenge','published':True}))
 def test_timezone_and_half_open_weeks(self):
  self.assertEqual(date('2026-09-19T05:59:59Z').hour,23)
  self.assertEqual(week_of('2026-09-19T05:59:59Z'),1)
  self.assertEqual(week_of('2026-09-19T06:00:00Z'),2)
  self.assertEqual(date('2026-12-03T06:59:59Z').strftime('%m-%d %H:%M'),'12-02 23:59')
  self.assertIsNone(week_of('2026-05-01T06:00:00Z'))
  self.assertIsNone(week_of('2026-12-18T07:00:00Z'))
 def test_schedule_year_and_weekday(self):
  rows=schedule_rows('<table><tr><td>Tue Sep 22</td><td>DAX</td></tr><tr><td>Mon Sep 22</td><td>Old</td></tr></table>')
  self.assertEqual(rows[0]['week'],2);self.assertIsNone(rows[1]['week'])
 def test_url_safety(self):
  self.assertEqual(safe_url('javascript:alert(1)'),'')
  self.assertEqual(safe_url('https://x.test/a?access_token=secret#b'),'https://x.test/a#b')

class ProgressTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'progress.json';self.store=ProgressStore(self.path)
 def tearDown(self):self.tmp.cleanup()
 def test_restart_conflict_and_backup(self):
  key='concept:431292:joins';item={'status':'mastered','notes':'Check duplicates','needs_review':True}
  self.assertFalse(self.path.exists())
  self.store.update({key:item},0)
  self.assertEqual(ProgressStore(self.path).read()['items'][key]['status'],'mastered')
  with self.assertRaises(RuntimeError):self.store.update({key:{'status':'learning'}},0)
  self.assertEqual(self.store.read()['items'][key]['status'],'mastered')
  self.store.update({key:{'status':'learning'}},1)
  self.assertTrue(self.path.with_suffix('.previous.json').exists())
 def test_malformed_import_does_not_overwrite(self):
  with self.assertRaises(ValueError):self.store.update({'../../elsewhere':{'status':'completed'}},0)
  self.assertFalse(self.path.exists())
  with self.assertRaises(ValueError):validate({'schema_version':1,'items':{'canvas:1:assignment:2':{'status':'mastered'}}})
 def test_wrong_account_evidence_rejected(self):
  with self.assertRaises(ValueError):self.store.update({'canvas:1:assignment:2':{'status':'completed','source':'canvas'}},0)
 def test_corrupt_state_not_erased(self):
  self.path.write_text('broken')
  with self.assertRaises(ValueError):self.store.update({'canvas:1:assignment:2':{'status':'completed'}},0)
  self.assertEqual(self.path.read_text(),'broken')

class FixtureTests(unittest.TestCase):
 def test_weekly_assignment_preserves_its_source_url(self):
  assignment={'id':9,'course_id':1,'name':'W01 Example','published':True,'due_at':'2026-09-15T18:00:00Z','lock_at':'2026-09-17T18:00:00Z','assignment_group_id':3,'submission_types':['online_upload'],'html_url':'https://canvas.test/courses/1/assignments/9'}
  grouped,items=extract_week_assignments([assignment],[{'id':3,'name':'Homework'}],1,datetime(2026,9,12),datetime(2026,9,18))
  self.assertEqual(items[0]['url'],assignment['html_url'])
  self.assertEqual(items[0]['lock_at'],assignment['lock_at'])
  self.assertEqual(grouped['Homework'][0]['url'],assignment['html_url'])

 def test_weekly_assignment_omits_zero_point_items(self):
  assignments=[
   {'id':9,'course_id':1,'name':'Graded work','published':True,'due_at':'2026-09-15T18:00:00Z','points_possible':5,'assignment_group_id':3,'submission_types':['online_upload']},
   {'id':10,'course_id':1,'name':'Administrative placeholder','published':True,'due_at':'2026-09-15T18:00:00Z','points_possible':'0','assignment_group_id':3,'submission_types':['none']},
  ]
  grouped,items=extract_week_assignments(assignments,[{'id':3,'name':'Homework'}],1,datetime(2026,9,12),datetime(2026,9,18))
  self.assertEqual([item['name'] for item in items],['Graded work'])
  self.assertEqual(len(grouped['Homework']),1)

 def test_weekly_assignments_are_ranked_by_points_times_group_weight(self):
  assignments=[
   {'id':1,'course_id':1,'name':'Many points, low weight','published':True,'due_at':'2026-09-15T18:00:00Z','points_possible':20,'assignment_group_id':1,'submission_types':[],'_group_weight':10},
   {'id':2,'course_id':1,'name':'Fewer points, high weight','published':True,'due_at':'2026-09-16T18:00:00Z','points_possible':8,'assignment_group_id':2,'submission_types':[],'_group_weight':40},
  ]
  groups=[{'id':1,'name':'Practice'},{'id':2,'name':'Exams'}]
  grouped,items=extract_week_assignments(assignments,groups,1,datetime(2026,9,12),datetime(2026,9,18))
  self.assertEqual([item['name'] for item in items],['Fewer points, high weight','Many points, low weight'])
  self.assertEqual(list(grouped),['Exams','Practice'])

 def test_report_lesson_plan_is_compact_and_linked(self):
  plan=json.loads((ROOT/'data/course_plans/431290.json').read_text())
  html=report_fragment(plan,1,3)
  self.assertIn('class="course-utility-strip"',html)
  self.assertIn('class="report-lesson-plan"',html)
  self.assertIn('Preview explanation, example, and real-life use',html)
  self.assertIn('course_plans/431290/concepts/workflow.html',html)
  self.assertIn('href="#course-431290-assignments"',html)
  self.assertNotIn('data-course-progress',html)

 def test_report_course_heading_shows_concept_count(self):
  plan=json.loads((ROOT/'data/course_plans/431290.json').read_text())
  courses=[{
   'course_id':'431290','course_plan':plan,'course_name':plan['name'],
   'course_code':plan['course_code'],'module_topic':'Setup Week',
   'learning_items':[],'announcements':[],'grouped_assignments':{},
   'all_assignments':[],'student_grade':None,
  }]
  from generate_summary import generate_html_report
  html=generate_html_report(1,datetime(2026,9,12),datetime(2026,9,18),courses)
  header=html.split('<summary class="course-header">',1)[1].split('</summary>',1)[0]
  self.assertIn('<span class="stat-number">2</span>',header)
  self.assertIn('<span class="stat-label">concepts</span>',header)

 def test_canvas_completed_control_is_checked_and_locked(self):
  html=control({'id':'canvas:1:assignment:2','canvas_completed':True})
  self.assertIn('data-canvas-completed="true"',html)
  self.assertIn('checked disabled',html)
  self.assertIn('Completed in Canvas',html)

 def test_reviewed_override_preserves_export(self):
  plan={'requirements':[{'id':'canvas:1:assignment:2','due_at':'2026-04-01T00:00:00Z','notes':[]}], 'weeks':[{'number':1,'due_ids':[]},{'number':2,'due_ids':[]}], 'facts':[]}
  apply_overrides(plan,{'deadlines':[{'id':'canvas:1:assignment:2','due_at':'2026-09-23T05:59:00Z','reason':'Updated instructor schedule','source_url':'https://example.test/schedule'}]})
  self.assertEqual(plan['requirements'][0]['exported_due_at'],'2026-04-01T00:00:00Z')
  self.assertEqual(plan['weeks'][1]['due_ids'],['canvas:1:assignment:2'])
  with self.assertRaises(ValueError): apply_overrides(plan,{'facts':[{'label':'Attendance','status':'confirmed'}]})

 def test_partial_export_dedup_and_unknown_policy(self):
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d)/'course_420634';folder.mkdir()
   values={'course_info':{'id':420634,'name':'Example','course_code':'BA 300'},'assignments':[{'id':9,'name':'Exam','published':True,'due_at':'2026-04-01T00:00:00Z'}],'quizzes':[{'id':7,'assignment_id':9,'title':'Exam','published':True}],'pages':[],'modules':[]}
   for k,v in values.items():(folder/(k+'.json')).write_text(json.dumps(v))
   plan=normalize(Path(d),'420634')
   self.assertEqual(len(plan['requirements']),1)
   self.assertIsNone(plan['requirements'][0]['due_week'])
   self.assertTrue(plan['gaps'])
   self.assertEqual(next(f for f in plan['facts'] if f['label']=='Attendance')['status'],'not_documented')
   self.assertEqual(next(f for f in plan['facts'] if f['label']=='Testing center')['status'],'not_documented')

class WorkedExampleTests(unittest.TestCase):
 def test_sql_group_and_cte_examples(self):
  db=sqlite3.connect(':memory:')
  db.execute('CREATE TABLE purchases(customer TEXT, amount INTEGER)')
  db.executemany('INSERT INTO purchases VALUES (?,?)',[('Ada',10),('Ada',30),('Bo',15)])
  result=db.execute('SELECT customer, SUM(amount) FROM purchases WHERE amount > 0 GROUP BY customer HAVING SUM(amount) >= 25').fetchall()
  self.assertEqual(result,[('Ada',40)])
  result=db.execute('WITH totals AS (SELECT customer, SUM(amount) AS spent FROM purchases GROUP BY customer) SELECT customer, spent FROM totals WHERE spent > (SELECT AVG(spent) FROM totals)').fetchall()
  self.assertEqual(result,[('Ada',40)])
  db.close()

 def test_window_tie_example(self):
  db=sqlite3.connect(':memory:')
  db.execute('CREATE TABLE sales(store TEXT, sale_id INTEGER, amount INTEGER)')
  db.executemany('INSERT INTO sales VALUES (?,?,?)',[('A',1,40),('A',2,40),('A',3,10)])
  result=db.execute('SELECT ROW_NUMBER() OVER (PARTITION BY store ORDER BY amount DESC, sale_id), RANK() OVER (PARTITION BY store ORDER BY amount DESC) FROM sales ORDER BY sale_id').fetchall()
  self.assertEqual(result,[(1,1),(2,1),(3,3)]);db.close()

class Links(HTMLParser):
 def __init__(self):super().__init__();self.links=[];self.ids=set()
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if 'id' in a:self.ids.add(a['id'])
  for name in ('href','src'):
   if name in a:self.links.append(a[name])

def broken_links(root):
 root=Path(root).resolve();cache={};broken=[]
 for file in root.rglob('*.html'):
  parser=Links();parser.feed(file.read_text(errors='replace'));cache[file]=parser
 for file,parser in cache.items():
  for raw in parser.links:
   url=urlsplit(raw)
   if url.scheme or url.netloc or not raw or raw.startswith('data:'):continue
   target=(file.parent/unquote(url.path)).resolve() if url.path else file
   if target.is_dir():target=target/'index.html'
   if not target.exists():broken.append((str(file.relative_to(root)),raw))
   elif url.fragment and target in cache and unquote(url.fragment) not in cache[target].ids:broken.append((str(file.relative_to(root)),raw+' [missing anchor]'))
 return broken

class OutputTests(unittest.TestCase):
 def test_portable_bundles(self):
  for name in ('week_01_summary_2026-09-15.zip','week_02_summary_2026-09-15.zip'):
   with tempfile.TemporaryDirectory() as d:
    with zipfile.ZipFile(ROOT/'output'/name) as bundle:
     self.assertIn('course_plans/431292/index.html',bundle.namelist())
     self.assertNotIn('data/learning_progress.json',bundle.namelist())
     self.assertFalse(any('canvas_state' in n or n.endswith('.env') for n in bundle.namelist()))
     bundle.extractall(d)
    self.assertEqual(broken_links(d),[])

 def test_generated_course_tree(self):
  self.assertEqual(broken_links(ROOT/'output/course_plans'),[])
  missing_export_course=ROOT/'output/course_plans/424334'
  self.assertTrue((missing_export_course/'index.html').exists())
  self.assertEqual(len(list((missing_export_course/'weeks').glob('week-*.html'))),14)
  self.assertIn('Natural Disasters', (missing_export_course/'index.html').read_text())
 def test_manifest_no_double_counts(self):
  m=json.loads((ROOT/'output/assets/learning-manifest.json').read_text())
  self.assertEqual(len(m['courses']),6)
  self.assertIn('424334',m['courses'])
  for p in m['courses'].values():
   self.assertEqual(len(p['assigned']),len(set(p['assigned'])))
   self.assertEqual(len(p['concepts']),len(set(p['concepts'])))

 def test_weekly_report_calendar(self):
  report=(ROOT/'output/week_01_summary_2026-09-16.html').read_text()
  self.assertIn('class="week-calendar"',report)
  positions=[report.index(f'<span class="calendar-day-name">{day}</span>') for day in ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')]
  self.assertEqual(positions,sorted(positions))
  self.assertIn('class="calendar-assignment"',report)
  self.assertEqual(report.count('<details class="calendar-day">'),6)
  self.assertNotIn('<details class="calendar-day" open>',report)
  self.assertIn('class="calendar-day-count">0 assignments',report)

if __name__=='__main__':unittest.main()
