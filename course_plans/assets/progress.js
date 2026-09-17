'use strict';
(() => {
 const manifest = window.LEARNING_MANIFEST || {courses:{}};
 let state = {schema_version:1, revision:0, items:{}}, busy=false, ready=false;
 const local = location.protocol === 'http:' && ['localhost','127.0.0.1'].includes(location.hostname);
 const status = document.querySelector('[data-save-status]');
 const say = text => { if(status) status.textContent=text; };
 function validBackup(value) {
  if(!value || value.schema_version!==1 || !value.items || Array.isArray(value.items) || typeof value.items!=='object') throw Error('This is not a version 1 progress backup.');
  for(const [id,item] of Object.entries(value.items)) {
   if(!/^(canvas:\d+:(assignment|quiz):\d+|(?:concept|task):\d+:[a-z0-9-]+)$/.test(id) || !item || !['not_started','learning','practiced','mastered','completed'].includes(item.status)) throw Error('The backup contains an invalid item.');
   if((id.startsWith('canvas:') || id.startsWith('task:')) && !['not_started','completed'].includes(item.status)) throw Error('Invalid assignment state.');
   if(id.startsWith('concept:') && item.status==='completed') throw Error('Invalid concept state.');
   if(typeof (item.notes || '')!=='string' || (item.notes || '').length>5000) throw Error('Invalid notes.');
   if(item.source && item.source!=='manual') throw Error('Only manual progress backups are supported.');
  }
  return value;
 }
 function refresh() {
  document.querySelectorAll('[data-progress-id]').forEach(el=>{
   const item=state.items[el.dataset.progressId] || {};
   if(!busy){if(el.type==='checkbox') el.checked=item.status==='completed'; else el.value=item.status || 'not_started';}
   el.disabled=!ready || busy;
  });
  document.querySelectorAll('[data-review-id]').forEach(el=>{ if(!busy)el.checked=!!state.items[el.dataset.reviewId]?.needs_review; el.disabled=!ready || busy; });
  document.querySelectorAll('[data-notes-id]').forEach(el=>{ if(document.activeElement!==el) el.value=state.items[el.dataset.notesId]?.notes || ''; el.disabled=!ready || busy; });
  document.querySelectorAll('[data-course-progress]').forEach(el=>{
   const course=manifest.courses[el.dataset.courseProgress]; if(!course)return;
   const assignments=course.assigned, concepts=course.concepts;
   const done=assignments.filter(id=>state.items[id]?.status==='completed').length;
   const tasks=course.tasks || [];
   const taskCount=tasks.filter(id=>state.items[id]?.status==='completed').length;
   const mastered=concepts.filter(id=>state.items[id]?.status==='mastered').length;
   const review=concepts.filter(id=>state.items[id]?.needs_review || (state.items[id]?.content_hash && state.items[id].content_hash!==course.items[id].hash)).length;
   el.textContent=ready ? `Assignments: ${done}/${assignments.length} manually completed · Concepts: ${mastered}/${concepts.length} self-assessed mastered${review ? ` · ${review} to review` : ''}${tasks.length ? ` · Personal tasks: ${taskCount}/${tasks.length}` : ''}` : 'Progress unavailable until storage connects';
  });
  document.querySelectorAll('[data-continue-course]').forEach(el=>{
   const course=manifest.courses[el.dataset.continueCourse]; if(!course)return;
   const id=course.concepts.find(id=>state.items[id]?.needs_review) || course.concepts.find(id=>state.items[id]?.status!=='mastered');
   if(id){el.href=el.dataset.root+course.items[id].path;el.textContent=state.items[id]?.needs_review?'Review: '+course.items[id].title:'Continue: '+course.items[id].title;}
   else el.textContent='All planned concepts marked mastered';
  });
 }
 async function load(){
  if(!local){ready=true; say('Portable mode: changes stay on this page only. Export before leaving; import on another page. Use the local server for shared saved progress.'); refresh(); return;}
  try {const r=await fetch('/api/progress'); const data=await r.json(); if(!r.ok)throw Error(data.error || 'Storage unavailable'); state=validBackup(data);ready=true;say('Connected · saved progress is shared across reports and course plans.');}
  catch(e){ready=false;say('Progress could not load: '+e.message+' Start serve_learning.py to use saved progress.');} refresh();
 }
 async function save(items, importing=false){
  if(busy || !ready)return; busy=true;refresh();
  try{
   if(local){const r=await fetch(importing?'/api/progress/import':'/api/progress',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items,revision:state.revision})}); const value=await r.json(); if(!r.ok){if(r.status===409)await load();throw Error(value.error || 'Save failed');}state=value;say('Saved to this workspace.');}
   else{Object.assign(state.items,items);say('Changed on this page only. Export a backup before navigating away.');}
  }catch(e){say('Not saved: '+e.message);}finally{busy=false;refresh();}
 }
 const detailsFor=id=>Object.values(manifest.courses).map(c=>c.items[id]).find(Boolean);
 document.addEventListener('change',e=>{
  const el=e.target; const id=el.dataset.progressId || el.dataset.reviewId || el.dataset.notesId; if(!id)return;
  const prior=state.items[id] || {status:'not_started',source:'manual',term:'2026-fall'};
  const item={...prior,content_hash:detailsFor(id)?.hash || ''};
  if(el.dataset.progressId)item.status=el.type==='checkbox'?(el.checked?'completed':'not_started'):el.value;
  if(el.dataset.reviewId)item.needs_review=el.checked;
  if(el.dataset.notesId)item.notes=el.value;
  save({[id]:item});
 });
 document.querySelector('[data-export-progress]')?.addEventListener('click',()=>{
  if(!ready){say('Load progress before exporting.');return;}
  const a=document.createElement('a'), url=URL.createObjectURL(new Blob([JSON.stringify(state,null,2)],{type:'application/json'}));a.href=url;a.download='learning-progress.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 });
 document.querySelector('[data-import-progress]')?.addEventListener('change',async e=>{
  try {const file=e.target.files[0];if(!file)return;if(file.size>2000000)throw Error('Backup too large.');const imported=validBackup(JSON.parse(await file.text()));
   const conflicts=Object.keys(imported.items).filter(id=>state.items[id] && JSON.stringify(state.items[id])!==JSON.stringify(imported.items[id]));
   if(conflicts.length && !confirm(`Import will replace ${conflicts.length} existing item states with the backup versions. Continue?`))return;
   await save(imported.items,true);
  }catch(err){say('Import failed: '+err.message);}finally{e.target.value='';}
 });
 window.addEventListener('focus',()=>{if(local && !busy && !document.activeElement?.matches('textarea'))load();});
 load();
})();
