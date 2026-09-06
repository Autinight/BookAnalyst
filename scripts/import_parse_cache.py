"""Import only verified S0/S1 parse artifacts from another local workspace (read-only)."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
from bookanalyst.app import create_app
from bookanalyst.models import RunCreate
from bookanalyst.store import WorkflowError, digest, file_hash


def import_parse(source_workspace, run_id, target_workspace):
    source_root=Path(source_workspace).resolve()/'.bookanalyst'
    target_workspace=Path(target_workspace).resolve()
    if source_root==target_workspace/'.bookanalyst':
        raise ValueError('Source and destination must be different workspaces')
    with sqlite3.connect((source_root/'bookanalyst.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT payload FROM objects WHERE kind=? AND id=?',('run',run_id)).fetchone()
    if row is None: raise ValueError('Source run not found')
    source=json.loads(row[0]); artifacts={}
    if source['scope']=='fixture': raise ValueError('A fixture cannot become a real parse cache')
    for stage in ('S0','S1'):
        state=source['stages'][stage]
        if state['state']!='PASSED': raise ValueError('Source parse has not passed')
        folder=source_root/'runs'/run_id/('r'+str(state['artifact_revision']))/stage
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        if digest(manifest)!=state['manifest_hash']: raise ValueError('Manifest hash mismatch')
        outputs={}
        for name,sha in manifest['files'].items():
            path=(folder/name).resolve()
            if not path.is_relative_to(folder.resolve()) or file_hash(path)!=sha:
                raise ValueError('Artifact path or hash mismatch')
            outputs[name]=path.read_bytes()
        artifacts[stage]=outputs
    app=create_app(target_workspace); store=app.state.store
    try:
        existing=next((r for r in store.list('run') if r.get('imported_parse_manifest')==source['stages']['S1']['manifest_hash']),None)
        if existing: return existing
        book=next((b for b in store.list('book') if b['sha256']==source['source']['sha256']),None)
        if book is None: raise ValueError('Register the same PDF in the destination first')
        fields=RunCreate.model_fields
        config=RunCreate(**({k:v for k,v in source['config'].items() if k in fields}|{'book_id':book['id'],'cached_run_id':None,'max_llm_requests':None})).model_dump()
        run=store.create_run(config,book,source['parser_settings'])
        store.change(run['id'],lambda r:r.update(state='RUNNING'),1)
        for stage in ('S0','S1'):
            store.transition(run['id'],1,stage,'RUNNING')
            artifacts[stage]['cache_provenance.json']={'source_run_id':run_id,'source_manifest_hash':source['stages'][stage]['manifest_hash'],'imported_parse_only':True}
            store.commit(run['id'],1,stage,artifacts[stage],['CACHE_HASH_VERIFIED'],{'source_sha256':book['sha256']})
        store.change(run['id'],lambda r:r.update(state='PARSE_IMPORTED',imported_parse_manifest=source['stages']['S1']['manifest_hash']),1)
        settings=store.get('settings','main'); settings['mineru']=source['parser_settings']; store.put('settings','main',settings)
        return store.get('run',run['id'])
    finally:
        asyncio.run(app.state.engine.close())

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-workspace',type=Path,required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--workspace',type=Path,default=Path.cwd())
    args=parser.parse_args()
    run=import_parse(args.source_workspace,args.run_id,args.workspace)
    print(json.dumps({'cached_run_id':run['id'],'book_id':run['config']['book_id'],'pages':run['source']['page_count'],'state':run['state']}))
