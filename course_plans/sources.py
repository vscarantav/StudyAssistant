"""Read only student-facing export content; retain evidence and date conflicts."""
import hashlib
import json
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
COURSES = ['431292', '420634', '431290', '424344', '420672', '424334']
TZ = ZoneInfo('America/Denver')
START = datetime(2026, 9, 12, tzinfo=TZ)
END = datetime(2026, 12, 18, tzinfo=TZ)
OBSOLETE = re.compile(r'teaching notes|do not (?:publish|use)|backup|\(old\)|instructor resources|TA resources|answer key|prep solution', re.I)


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0; self.links = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
        if tag in ('p', 'li', 'br', 'tr', 'h1', 'h2', 'h3'): self.parts.append('\n')
        if tag in ('a', 'iframe'):
            url = dict(attrs).get('href' if tag == 'a' else 'src', '')
            if safe_url(url): self.links.append(safe_url(url))
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def plain(html):
    p = Text(); p.feed(html or '')
    value = re.sub(r'[ \t\r\f\v]+', ' ', ' '.join(p.parts))
    return re.sub(r'\n\s*\n', '\n', value).strip()


def safe_url(url):
    if not isinstance(url, str): return ''
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.netloc or p.username: return ''
    # Never put authenticated download query values into portable reports.
    return urlunsplit((p.scheme, p.netloc, p.path, '', p.fragment))


def eligible(record):
    title = record.get('title', record.get('name', ''))
    return record.get('published', True) is not False and not record.get('hide_from_students', False) and not OBSOLETE.search(title)


def date(value):
    if not value: return None
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return d.replace(tzinfo=TZ) if d.tzinfo is None else d.astimezone(TZ)
    except (ValueError, TypeError): return None


def week_of(value):
    d = date(value) if isinstance(value, str) else value
    if d is None or not START <= d < END: return None
    return (d.date() - START.date()).days // 7 + 1


def title_week(title):
    m = re.search(r'\b(?:W|Week\s*)(\d{1,2})\b', title, re.I)
    return int(m[1]) if m and 1 <= int(m[1]) <= 14 else None


class Rows(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []; self.row = None; self.cell = None
    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row = []
        if tag in ('td', 'th'): self.cell = []
    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            if self.row is not None: self.row.append(re.sub(r'\s+', ' ', ' '.join(self.cell)).strip())
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row); self.row = None


def schedule_rows(body):
    p = Rows(); p.feed(body or ''); result = []
    for cells in p.rows:
        text = ' | '.join(cells)
        m = re.search(r'\b(Sep|Oct|Nov|Dec)\w*\s+(\d{1,2})\b', text)
        if not m: continue
        d = datetime(2026, {'Sep':9, 'Oct':10, 'Nov':11, 'Dec':12}[m[1]], int(m[2]), tzinfo=TZ)
        weekday = re.search(r'\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b', text)
        valid = not weekday or weekday[1] == d.strftime('%a')
        result.append({'date': d.isoformat(), 'week': week_of(d) if valid else None,
                       'text': text, 'year_inferred': True, 'weekday_valid': valid})
    return result


def load_course(snapshot, cid):
    folder = Path(snapshot) / f'course_{cid}'
    if not (folder / 'course_info.json').exists():
        candidates = sorted((ROOT / 'snapshots').glob(f'*/course_{cid}/course_info.json'), reverse=True)
        if not candidates:
            kinds = ('course_info', 'modules', 'pages', 'assignments', 'quizzes',
                     'assignment_groups', 'announcements', 'files', 'rubrics',
                     'calendar_events')
            data = {kind: {} if kind == 'course_info' else [] for kind in kinds}
            return data, [], [
                f'No Canvas export is available for configured course {cid}. '
                'Run fetch_course.py, then rebuild to populate its identity, '
                'requirements, schedule, sources, and lessons.'
            ], []
        folder = candidates[0].parent
    data = {}; gaps = []; manifest = []
    for kind in ('course_info', 'modules', 'pages', 'assignments', 'quizzes', 'assignment_groups', 'announcements', 'files', 'rubrics', 'calendar_events'):
        file = folder / f'{kind}.json'
        try:
            raw = file.read_bytes(); value = json.loads(raw)
            if not isinstance(value, dict if kind == 'course_info' else list): raise ValueError('Wrong JSON type')
            data[kind] = value
            manifest.append({'file': f'{folder.name}/{file.name}', 'sha256': hashlib.sha256(raw).hexdigest(), 'records': 1 if kind == 'course_info' else len(value)})
        except (OSError, ValueError):
            data[kind] = {} if kind == 'course_info' else []
            gaps.append(f'{kind}: missing or unreadable in this export')
    if folder.parent != Path(snapshot): gaps.append(f'Using older course export: {folder.parent.name}')
    if not data['course_info'].get('syllabus_body'): gaps.append('Current course-level syllabus text is not present. Official outcomes, grade thresholds and general attendance/late-work rules may be unavailable.')
    if not any(a.get('submission') for a in data['assignments']): gaps.append('No student submission records: progress is manually tracked, not synced from Canvas.')
    sources = []
    if data['course_info'].get('syllabus_body'):
        sources.append({'id':'course_info:syllabus','record_id':cid,'kind':'syllabus',
                        'title':'Current course syllabus','text':plain(data['course_info']['syllabus_body']),
                        'url':f'https://byui.instructure.com/courses/{cid}/assignments/syllabus',
                        'links':[],'snapshot':folder.parent.name,'file':f'course_{cid}/course_info.json',
                        'pointer':'/syllabus_body','updated_at':None})
    for kind in ('pages', 'assignments', 'quizzes', 'modules', 'announcements'):
        for idx, r in enumerate(data[kind]):
            if not eligible(r): continue
            rid = str(r.get('id', r.get('page_id', idx)))
            body = r.get('body') or r.get('description') or r.get('message') or ''
            parser = Text(); parser.feed(body)
            source = {'id': f'{kind}:{rid}', 'record_id':rid, 'kind':kind,
                      'title':r.get('title', r.get('name','')), 'text':plain(body),
                      'url':safe_url(r.get('html_url','')) or f'https://byui.instructure.com/courses/{cid}/{kind}' + (f'/{r.get("url", rid)}' if kind != 'modules' else ''),
                      'links':list(dict.fromkeys(parser.links)), 'snapshot':folder.parent.name,
                      'file': f'course_{cid}/{kind}.json', 'pointer':f'/{idx}', 'updated_at':r.get('updated_at')}
            sources.append(source)
    for m in data['modules']:
        if eligible(m) and m.get('items_count',0) > len(m.get('items',[])):
            gaps.append(f'Incomplete module items: {m["name"]}')
    return data, sources, gaps, manifest
