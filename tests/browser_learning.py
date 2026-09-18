"""Run explicitly: python3 tests/browser_learning.py. Uses only temporary progress."""
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from course_plans.sources import ROOT
from serve_learning import create_server
from build_course_plans import build
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory() as td:
 temp=Path(td); output=temp/'output';shutil.copytree(ROOT/'output',output)
 progress=temp/'progress.json';server=create_server(0,output,progress)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 base=f'http://127.0.0.1:{server.server_address[1]}'
 try:
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True)
   context=browser.new_context(viewport={'width':1360,'height':1000},accept_downloads=True)
   context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(base) or route.request.url.startswith('file:') else route.abort())
   page=context.new_page();errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
   page.goto(base+'/course_plans/index.html');page.get_by_text('Connected ·',exact=False).wait_for()
   assert page.locator('[data-course-progress]').count()==6
   page.screenshot(path='/tmp/learning-dashboard.png',full_page=True)
   page.goto(base+'/course_plans/431292/concepts/workflow.html')
   with page.expect_response(lambda r:r.url.endswith('/api/progress') and r.request.method=='POST'):
    page.locator('[data-progress-id]').select_option('mastered')
   assert '1/18' in page.locator('[data-course-progress]').inner_text()
   with page.expect_response(lambda r:r.url.endswith('/api/progress') and r.request.method=='POST'):
    page.locator('[data-review-id]').check()
   page.locator('[data-notes-id]').fill('Recheck standalone chart output')
   with page.expect_response(lambda r:r.url.endswith('/api/progress') and r.request.method=='POST'):
    page.locator('[data-notes-id]').blur()
   with page.expect_download() as download:
    page.locator('[data-export-progress]').click()
   backup=temp/'backup.json';download.value.save_as(backup)
   assert json.loads(backup.read_text())['items']['concept:431292:workflow']['status']=='mastered'
   report=context.new_page();report.goto(base+'/week_01_summary_2026-09-15.html')
   report.locator('details.course-section').first.locator('summary').first.click()
   report.get_by_text('Connected ·',exact=False).wait_for()
   assert '1/18' in report.locator('[data-course-progress="431292"]').inner_text()
   checkbox=report.locator('[data-progress-id]').first
   with report.expect_response(lambda r:r.url.endswith('/api/progress') and r.request.method=='POST'):checkbox.check()
   assert any(v['status']=='completed' for v in json.loads(progress.read_text())['items'].values())
   before=progress.read_bytes();build(output=output,data_dir=temp/'models');assert progress.read_bytes()==before
   page.reload();page.get_by_text('Connected ·',exact=False).wait_for()
   assert page.locator('[data-progress-id]').input_value()=='mastered'
   assert page.locator('[data-notes-id]').input_value()=='Recheck standalone chart output'
   page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/tmp/learning-lesson-mobile.png',full_page=True)
   assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
   assert context.request.post(base+'/api/progress',data={'items':{},'revision':0}).status==403
   assert context.request.get(base+'/../data/ol_account_canvas_state.json').status==404
   portable=browser.new_page();portable.goto((output/'course_plans/431292/concepts/workflow.html').as_uri())
   portable.get_by_text('Portable mode:',exact=False).wait_for()
   portable.locator('[data-import-progress]').set_input_files(backup)
   portable.wait_for_function('document.querySelector("[data-progress-id]").value === "mastered"')
   assert portable.locator('[data-notes-id]').input_value()=='Recheck standalone chart output'
   assert not errors,errors
   browser.close()
  print('PASS: dashboard, course/report shared progress, notes, review, backup/import, regeneration, mobile layout, origin guard and portable mode.')
 finally:
  server.shutdown();server.server_close()
