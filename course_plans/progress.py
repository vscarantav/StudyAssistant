"""Validated atomic progress persistence. Building reports never writes this file."""
import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

ITEM = re.compile(r'^(?:canvas:\d+:(?:assignment|quiz):\d+|(?:concept|task):\d+:[a-z0-9-]+)$')
STATUSES = {'not_started','learning','practiced','mastered','completed'}
LOCK = threading.RLock()


def validate(payload):
    if not isinstance(payload,dict) or payload.get('schema_version')!=1 or not isinstance(payload.get('items'),dict):
        raise ValueError('Expected a version 1 progress backup with an items object.')
    if len(payload['items'])>10000: raise ValueError('Too many items.')
    for key,value in payload['items'].items():
        if not ITEM.fullmatch(key) or not isinstance(value,dict): raise ValueError('Invalid item ID or value.')
        if value.get('status') not in STATUSES: raise ValueError('Invalid progress status.')
        if key.startswith(('canvas:', 'task:')) and value['status'] not in ('not_started','completed'): raise ValueError('Invalid assignment/task status.')
        if key.startswith('concept:') and value['status']=='completed': raise ValueError('Use mastery states for concepts.')
        if not isinstance(value.get('notes',''),str) or len(value.get('notes',''))>5000: raise ValueError('Notes must be at most 5000 characters.')
        if not isinstance(value.get('needs_review',False),bool): raise ValueError('Invalid review flag.')
        if value.get('source','manual')!='manual': raise ValueError('This app supports manual progress only.')
        if value.get('term','2026-fall')!='2026-fall': raise ValueError('Wrong term.')
        for field in ('content_hash','updated_at'):
            if not isinstance(value.get(field,''),str) or len(value.get(field,''))>100: raise ValueError('Invalid metadata.')
    return payload


class ProgressStore:
    def __init__(self,path): self.path=Path(path)
    def read(self):
        with LOCK:
            if not self.path.exists(): return {'schema_version':1,'revision':0,'items':{}}
            return validate(json.loads(self.path.read_text()))
    def update(self,items,revision,allowed=None):
        validate({'schema_version':1,'items':items})
        if allowed is not None and not set(items)<=set(allowed): raise ValueError('Unknown course item; import a backup to retain archived items.')
        with LOCK:
            state=self.read()
            if revision!=state.get('revision',0): raise RuntimeError('Progress changed in another page. Reloaded current progress; please retry your change.')
            for key,value in items.items():
                state['items'][key]={k:value[k] for k in ('status','notes','needs_review','content_hash') if k in value}
                state['items'][key].update(source='manual',term='2026-fall',updated_at=datetime.now(timezone.utc).isoformat())
            state['revision']=state.get('revision',0)+1
            self.path.parent.mkdir(parents=True,exist_ok=True)
            fd,tmp=tempfile.mkstemp(dir=self.path.parent,prefix='.progress-',suffix='.tmp')
            try:
                with os.fdopen(fd,'w') as f:
                    json.dump(state,f,indent=2); f.flush(); os.fsync(f.fileno())
                if self.path.exists():
                    backup=self.path.with_suffix('.previous.json')
                    backup.write_bytes(self.path.read_bytes())
                os.replace(tmp,self.path)
            finally:
                if os.path.exists(tmp): os.unlink(tmp)
            return state
