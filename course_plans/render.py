"""Static pages and report fragments; all user/source text is escaped."""
import json
import shutil
from html import escape
from pathlib import Path
from .sources import ROOT, date, safe_url
from .lessons import RESOURCES


def e(value): return escape(str(value if value is not None else 'Not provided'),quote=True)
def link(url,title):
    return f'<a href="{e(url)}">{e(title)}</a>' if url else e(title)
def lis(values): return '<ul class="lp-list">'+''.join(f'<li>{x}</li>' for x in values)+'</ul>'
def when(value):
    d=date(value)
    return d.strftime('%b %d, %Y · %I:%M %p %Z') if d else 'No date provided'
def source_links(plan,ids,prefix=''):
    return ' · '.join(link(prefix+'index.html#source-'+s.replace(':','-'), next((x['title'] for x in plan['sources'] if x['id']==s),s)) for s in list(dict.fromkeys(ids))[:8])

def toolbar():
    return '''<div class="lp-toolbar"><div class="lp-links"><button class="lp-button secondary" data-export-progress>Export progress backup</button><label>Import progress backup <input type="file" accept=".json,application/json" data-import-progress></label></div><p class="lp-status" role="status" aria-live="polite" data-save-status>Loading progress…</p></div>'''

def progress(plan):
    cid=plan['course_id']
    return f'<div class="lp-progress" data-course-progress="{cid}">Progress loads when this page opens.</div>'

def control(item,concept=False):
    iid=e(item['id'])
    if concept:
        opts=''.join(f'<option value="{v}">{t}</option>' for v,t in [('not_started','Not started'),('learning','Learning'),('practiced','Practiced'),('mastered','Mastered (self-assessed)')])
        return f'<div class="lp-controls"><label>My understanding <select data-progress-id="{iid}" disabled>{opts}</select></label><label><input type="checkbox" data-review-id="{iid}" disabled> Needs review</label></div><label>My learning notes<textarea class="lp-notes" maxlength="5000" data-notes-id="{iid}" disabled></textarea></label>'
    return f'<div class="lp-controls"><label><input type="checkbox" data-progress-id="{iid}" disabled> I completed this item</label></div>'

def manifest(plans):
    result={'schema_version':1,'courses':{}}
    for p in plans:
        cid=p['course_id']; items={}
        for r in p['requirements']: items[r['id']]={'title':r['title'],'path':f'course_plans/{cid}/index.html#requirements','hash':''}
        for c in p['concepts']: items[c['id']]={'title':c['title'],'path':f'course_plans/{cid}/concepts/{c["key"]}.html','hash':c['content_hash']}
        result['courses'][cid]={'title':p['course_code'],'assigned':[r['id'] for r in p['requirements'] if r['category']=='assigned'], 'concepts':[c['id'] for c in p['concepts']],'tasks':[r['id'] for r in p['requirements'] if r['category']=='personal'],'items':items}
    return result

def page(title,body,root,nav='',subtitle='',with_toolbar=True):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)} · Learning studio</title><link rel="stylesheet" href="{root}assets/learning.css"><script src="{root}assets/learning-manifest.js" defer></script><script src="{root}assets/progress.js" defer></script></head><body class="learning-site"><main class="lp-shell"><nav class="lp-nav">{link(root+'course_plans/index.html','All courses')}{nav}</nav><header class="lp-hero"><span class="lp-eyebrow">Fall 2026 · Your learning studio</span><h1>{e(title)}</h1><p>{e(subtitle)}</p></header>{toolbar() if with_toolbar else ''}<div class="lp-content">{body}</div><footer class="lp-footer">Course requirements come from exported course content. Explanations and practice examples are study aids. Progress is manual and does not submit work or change Canvas grades.</footer></main></body></html>'''

def requirement(r):
    if r['category'] == 'personal':
        return f'<article class="lp-card"><h3>{e(r["title"])}</h3><p class="lp-muted">Personal checklist · Week {r["learning_weeks"][0]:02d}</p><p>{e(r["description"])}</p>{link(r["url"],"Watch the tutorial")}{control(r)}</article>'
    notes=''.join(f'<p class="lp-warning">{e(n)}</p>' for n in r['notes'])
    q=r['quiz']; fields=[('Due',when(r.get('due_at'))),('Until',when(r.get('lock_at')))]
    if r['kind']=='assessment':
        fields += [('Quiz setting: time limit',str(q['time_limit'])+' minutes' if q.get('time_limit') else 'Not provided'),('Allowed attempts','Unlimited' if q.get('allowed_attempts')==-1 else q.get('allowed_attempts')),('Opens',when(q.get('unlock_at'))),('Locks',when(q.get('lock_at'))),('Proctoring / location',r['proctoring'])]
    fields += [('Points',r['points']),('Submission',', '.join(r['submission_types']) or 'See source instructions'),('Learning week(s)',', '.join(map(str,r['learning_weeks'])) or 'Unscheduled')]
    detail=lis([f'<strong>{e(k)}:</strong> {e(v)}' for k,v in fields])
    # Show manageable excerpts, with a source link for the full authoritative instructions.
    excerpt=r['description'][:1500]
    if len(r['description'])>1500: excerpt+=' … (continued in source)'
    rubric=lis([f'{e(x["criterion"])} — {e(x["points"])} points' for x in r['rubric']]) if r['rubric'] else ''
    return f'<details class="lp-item"><summary>{e(r["title"])} <span class="lp-muted">{e(when(r["due_at"]))} · {e(r["category"])} · {e(r["kind"])}</span></summary>{control(r)}{notes}{detail}<p>{e(excerpt or "Detailed instructions are not included here; open the course item.")}</p>{rubric}{link(r["url"],"Open original course item")}</details>'

def course_page(p):
    cid=p['course_id']; facts=''
    not_doc=[]
    for f in p['facts']:
        external=link(f.get('external_url'),'Linked course instructions') if f.get('external_url') else ''
        card = f'<article class="lp-card"><h3>{e(f["label"])}</h3><span class="lp-chip {f["status"]}">{e(f["status"].replace("_"," "))}</span><p>{e(f["value"])}</p><p class="lp-source">{source_links(p,f["source_ids"])} {external}</p></article>'
        if f['status'] == 'not_documented':
            not_doc.append(card)
        else:
            facts += card
    if not_doc:
        facts += f'<details class="lp-item" style="grid-column: 1 / -1;"><summary>Not documented in exports ({len(not_doc)} items)</summary><div class="lp-grid" style="margin-top: 15px;">{"".join(not_doc)}</div></details>'
    weeks=''
    for w in p['weeks']:
        concepts=[next(c for c in p['concepts'] if c['key']==k) for k in w['concepts']]
        weeks+=f'<tr><td>{link(f"weeks/week-{w["number"]:02d}.html",f"Week {w["number"]:02d}")}<br>{e(date(w["start"]).strftime("%b %d"))}</td><td>{e(w["focus"])}'+lis([link(f'concepts/{c["key"]}.html',c['title']) for c in concepts])+f'</td><td>{len(w["due_ids"])} exported deadlines<br>{len(w["learning_ids"])} linked learning items</td></tr>'
    exams=[r for r in p['requirements'] if r['kind']=='assessment']; projects=[r for r in p['requirements'] if r['kind']=='project']
    source_html=''
    for s in p['sources']:
        source_html+=f'<details class="lp-item lp-source lp-anchor" id="source-{s["id"].replace(":","-")}"><summary>{e(s["title"])} · {e(s["kind"])}</summary><p>{e(s["text"][:500] or "Module/item metadata; see original source.")}</p>{link(s["url"],"Open source")}<p>{e(s["file"])} · record {e(s["record_id"])} · {e(s["snapshot"])}</p>'+lis([link(u,'Linked course resource') for u in s['links'][:8]])+'</details>'
    upcoming=sorted((r for r in p['requirements'] if r['due_week'] and r['kind'] in ('assessment','project')),key=lambda x:x['due_at'])
    return f'''{progress(p)}<p class="lp-muted">Counts cover exported assigned items, including career activities. Optional, conditional/stretch and reference items are listed separately and excluded from the completion denominator. This is not a grade calculation.</p>
+<nav class="lp-links">{link('#roadmap','Semester roadmap')}{link('#policies','Policies & exams')}{link('#projects','Major projects')}{link('#requirements','All coursework')}{link('#sources','Sources')}</nav>
+<section class="lp-card"><h2>What you will learn</h2><p><strong>Suggested study outcomes</strong> — authored study guidance where reviewed; the official course-wide syllabus outcome list may not be available.</p>{lis([e(x) for x in p['suggested_outcomes']])}<p><strong>Tools and materials:</strong> {e(p['tools'])}</p><p><a data-continue-course="{cid}" data-root="../../" href="weeks/week-01.html">Continue learning</a></p></section>
+<section class="lp-card"><h2>What needs confirmation</h2>{lis([e(g) for g in p['gaps']])}<p>{e(p['calendar']['basis'])} Times shown in Mountain time. The fall calendar groups the learning plan; it does not rewrite Canvas deadlines.</p>{link(f'https://byui.instructure.com/courses/{cid}/assignments/syllabus','Open current Canvas syllabus')}</section>
+<h2 id="roadmap">Your semester roadmap</h2><p>Lessons are suggested ways to learn the concepts in the exported modules and schedules. Internship weekly checkpoints are suggested preparation for the published milestones.</p><div class="lp-table-wrap"><table class="lp-table"><thead><tr><th>Week</th><th>Focus & concept lessons</th><th>Workload evidence</th></tr></thead><tbody>{weeks}</tbody></table></div>
+<h2 id="policies">Policies, attendance and important requirements</h2><div class="lp-grid">{facts}</div>
+<section class="lp-card"><h2>Grading structure from the export</h2><p>{'Assignment-group weighting is enabled.' if p['weighted_grading'] else 'Assignment-group weighting is not enabled in exported metadata; do not interpret group weights as the grading formula.'}</p>{lis([e(g['name'])+(f' — {e(g["weight"])}%' if p['weighted_grading'] else '') for g in p['grading_groups']])}</section>
+<h2>Exams, quizzes and coding challenges</h2><p>Expand each item for its deadline, available settings and original instructions. Quiz settings and description requirements may differ; conflicts are flagged.</p>{''.join(requirement(r) for r in exams) or '<p>No assessment records are available.</p>'}
+<h2 id="projects">Major projects and applied work</h2><p>Suggested preparation: understand the deliverable → study the linked weekly concepts → build a small example → complete your analysis → compare it with the rubric → verify the submission format.</p>{''.join(requirement(r) for r in projects) or '<p>No separately labeled project records are available.</p>'}
+<h2 id="requirements">Complete exported coursework inventory</h2><p>Includes optional work and items with missing or stale dates. Completion controls refer to the same item everywhere.</p>{''.join(requirement(r) for r in p['requirements'])}
+<h2>Module and internship phases</h2>{lis([e(m['name'])+': '+e(', '.join(m['items'])) for m in p['phases'] if m['items']])}
+<h2 id="sources">Sources and export freshness</h2><p>Export: {e(p['snapshot'])}. These links may require your Canvas login. Source file paths below are audit references within the workspace.</p>{source_html}'''.replace('\n+','\n')

def week_page(p,w):
    cid=p['course_id']; n=w['number']; body=progress(p)
    body+=f'<p class="lp-muted">{e(date(w["start"]).strftime("%B %d"))} – {e((date(w["end"])).strftime("%B %d"))} (end exclusive) · {e(w["focus"])}</p>'
    if w['suggested']:body+='<p class="lp-warning">Suggested internship study checkpoint. The export uses start/middle/end milestones, not an official weekly syllabus.</p>'
    body+='<div class="lp-grid">'
    for k in w['concepts']:
        c=next(c for c in p['concepts'] if c['key']==k)
        body+=f'<article class="lp-card"><h2>{link(f"../concepts/{k}.html",c["title"])}</h2><p>{e(c["hook"])}</p><p class="lp-muted">{e(c["goal"])}</p></article>'
    body+='</div>'
    if not w['concepts']: body+='<p>No scheduled concept lesson is supported for this week by the current export. Review the full plan’s unscheduled items and source gaps.</p>'
    if w['schedule']:body+='<h2>Course schedule entries</h2><p>Year inferred as 2026 from the configured term; weekday labels checked where present. Schedule entries describe class/preparation dates, not necessarily submission deadlines.</p>'+lis([e(r['text']) for r in w['schedule']])
    body+='<h2>Learning items and exported deadlines</h2>'
    ids=set(w['learning_ids']+w['due_ids']); reqs=[r for r in p['requirements'] if r['id'] in ids]
    body+=''.join(requirement(r) for r in reqs) or '<p>No items are mapped here by module/week label or an in-term deadline.</p>'
    milestones=[r for r in p['requirements'] if r['kind'] in ('assessment','project') and ((r['due_week'] and r['due_week']>=n) or any(x>=n for x in r['learning_weeks'])) and r['category']!='reference']
    milestones.sort(key=lambda r:(r['due_week'] or min(r['learning_weeks'] or [99]),r['title']))
    body+='<h2>Next milestones</h2>'+lis([link(r['url'],r['title'])+' — '+e(when(r['due_at'])) for r in milestones[:5]])
    body+='<h2>Where this week comes from</h2><p class="lp-source">'+source_links(p,w['source_ids'],'../')+'</p><p>'+link('../index.html#sources','Full source inventory')+'</p>'
    return body

def lesson_page(p,c):
    resource=''
    if c['resource']:
        title,url=RESOURCES[c['resource']];resource=f'<p>{link(url,title)} — supplementary explanation; verified September 15, 2026.</p>'
    related=' · '.join(link(f'../weeks/week-{w:02d}.html',f'Week {w:02d}') for w in c['weeks'])
    tasks = [r for r in p['requirements'] if r['category'] != 'reference' and r['kind'] in ('assessment', 'project') and ((r['due_week'] and r['due_week'] in c['weeks']) or any(w in c['weeks'] for w in r['learning_weeks'] or []))]
    task_html = ''
    if tasks:
        task_html = '<h2>Apply it this week</h2><p>You will use this concept in the following assignments:</p>' + lis([link(r['url'], r['title']) + f' — {e(when(r["due_at"]))}' for r in sorted(tasks, key=lambda x: x['due_at'])[:5]])
    return f'''<p>{related}</p><section class="lp-card"><h2>The idea, simply</h2><p>{e(c['explanation'])}</p><p><strong>Your goal:</strong> {e(c['goal'])}</p></section><h2>A worked example</h2><p class="lp-muted">Original practice example with invented data; not a course assessment answer.</p><pre class="lp-example">{e(c['example'])}</pre><p class="lp-result">{e(c['result'])}</p><h2>A common mistake</h2><p class="lp-warning">{e(c['mistake'])}</p><section class="lp-card"><h2>Try it yourself</h2><p>{e(c['question'])}</p><details><summary>Show the explanation</summary><p>{e(c['answer'])}</p></details></section><section class="lp-card"><h2>Track your understanding</h2>{control(c,True)}{progress(p)}</section>{task_html}<h2>Read more and connect it to class</h2>{resource}<p class="lp-source">Course context: {source_links(p,c['source_ids'],'../')}</p><p>{link('../index.html#sources','All course sources')}</p>'''

def report_fragment(p,week):
    cid=p['course_id'];w=p['weeks'][week-1]; base=f'course_plans/{cid}/'
    concepts=[next(c for c in p['concepts'] if c['key']==k) for k in w['concepts']]
    rows=lis([link(base+f'concepts/{c["key"]}.html',c['title'])+' — '+e(c['hook']) for c in concepts]) if concepts else '<p>No scheduled concept lesson is documented for this week. See the full course roadmap.</p>'
    milestones=[r for r in p['requirements'] if r['kind'] in ('assessment','project') and r['due_week'] and r['due_week']>=week and r['category']!='reference']
    milestones.sort(key=lambda r:r['due_at'])
    next_text=('<p><strong>Next exported milestone:</strong> '+link(milestones[0]['url'],milestones[0]['title'])+' — '+e(when(milestones[0]['due_at']))+'</p>') if milestones else ''
    important=[f for f in p['facts'] if f['label'] in ('Proctorio','Testing center','Exam date conflict','Work-hour requirement')]
    flags=''.join(f'<p class="lp-muted"><strong>{e(f["label"])}:</strong> {e(f["value"])}</p>' for f in important)
    tasks = [r for r in p['requirements'] if r['category'] == 'personal' and week in r['learning_weeks']]
    if tasks:
        flags = '<h3>Your checklist this week</h3>' + ''.join(requirement(r) for r in tasks) + flags
    return f'<div class="lp-panel"><h3>Your course plan & learning progress</h3><div class="lp-links">{link(base+"index.html","Full course plan")}{link(base+f"weeks/week-{week:02d}.html","This week’s plan")}</div>{progress(p)}<p class="lp-muted">Manual coursework completion and self-assessed concept mastery; all assigned exported items are counted in the course total.</p><h3>Concept lessons for this week</h3>{rows}{next_text}{flags}</div>'

def render_all(plans,output):
    output=Path(output);assets=output/'assets';assets.mkdir(parents=True,exist_ok=True)
    for name in ('learning.css','progress.js'):shutil.copyfile(ROOT/'course_plans/assets'/name,assets/name)
    m=manifest(plans);raw=json.dumps(m,ensure_ascii=False).replace('<','\\u003c')
    (assets/'learning-manifest.json').write_text(raw)
    (assets/'learning-manifest.js').write_text('window.LEARNING_MANIFEST = '+raw+';\n')
    index=[]
    for p in plans:
        cid=p['course_id'];folder=output/'course_plans'/cid
        (folder/'weeks').mkdir(parents=True,exist_ok=True);(folder/'concepts').mkdir(exist_ok=True)
        (folder/'index.html').write_text(page(p['course_code']+' · '+p['name'],course_page(p),'../../',subtitle=p['overview']))
        for w in p['weeks']:
            (folder/'weeks'/f'week-{w["number"]:02d}.html').write_text(page(f'{p["course_code"]} · Week {w["number"]:02d}',week_page(p,w),'../../../',link('../index.html','Full course plan'),w['focus']))
        for c in p['concepts']:
            (folder/'concepts'/f'{c["key"]}.html').write_text(page(c['title'],lesson_page(p,c),'../../../',link('../index.html',p['course_code']+' plan'),c['hook']))
        index.append(f'<article class="lp-card"><span class="lp-eyebrow">{e(p["course_code"])}</span><h2>{link(cid+"/index.html",p["name"])}</h2><p>{e(p["overview"])}</p>{progress(p)}<p>{len(p["concepts"])} concept lessons · 14 weekly pages</p><a data-continue-course="{cid}" data-root="../" href="{cid}/index.html">Continue learning</a></article>')
    # Prefer the newest report for each week; older reports remain available on disk.
    reports_by_week={}
    for report in sorted(output.glob('week_*_summary_*.html')):
        reports_by_week[report.name.split('_')[1]]=report
    reports=list(reports_by_week.values())
    body='<div class="lp-grid">'+''.join(index)+'</div><h2>Weekly reports</h2>'+lis([link('../'+r.name,'Week '+r.name.split('_')[1]+' report · '+r.stem[-10:]) for r in reports])
    body+='<section class="lp-card"><h2>Keep your progress</h2><p>Run <code>python3 serve_learning.py --open</code> in the project folder for shared progress saved across all pages. To move progress to another computer, export a JSON backup. A standalone file copy uses page-only progress until you import/export or use the server.</p></section>'
    (output/'course_plans/index.html').write_text(page('Learn the course. Build the skill.',body,'../',subtitle=f'{len(plans)} semester plans, weekly concept lessons, and a clear view of what you have learned.'))
    (output/'LEARNING_README.txt').write_text('Open course_plans/index.html after extracting the entire ZIP. Keep folders together.\n\nFor durable shared progress in the workspace: python3 serve_learning.py --open\nProgress is saved in data/learning_progress.json, with a previous-version backup.\nFile-open/portable mode holds changes on the current page only: export before navigating, then import on the next page. Portable copies do not automatically sync.\nAll completion is manual; no Canvas work is submitted. Exported stale deadlines remain visible.\n')
