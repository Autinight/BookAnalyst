"""Template samples and restyling preserve saved books and durable agent work."""
import io
import json
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

from bookanalyst.library_outputs import retain_run, retained_directory
from bookanalyst.store import WorkflowError, atomic_text, atomic_json
from bookanalyst.templates import TemplateSpec, TemplateApply, create_template_run, save_template, template_agent_instructions
from test_library import headers
from test_library_outputs import completed
from test_rebuild import BOOK


def spec(name="Compact book"):
    return {"name": name, "description": "Use compact margins", "entrypoint": "main.tex",
            "files": [{"path": "main.tex", "content": r"\documentclass{book}\begin{document}Example\end{document}"}]}


def retained(app):
    run, project = completed(app, "% PDF page 1\nThe original book body.")
    atomic_text(project / 'main.tex', r'\documentclass{book}\input{preamble}\begin{document}\input{body}\end{document}')
    atomic_text(project / 'preamble.tex', '% Original style\n')
    app.state.store.change(run['id'], lambda r:r['config'].update(resolved=True))
    result = retain_run(app.state.store, run['id'])
    return result


def job(app):
    result = retained(app)
    template = save_template(app.state.store, TemplateSpec(**spec()))
    command = TemplateApply(template_id=template['id'], result_id=result['id'], operation_id='template-test-operation')
    run = create_template_run(app.state.engine, BOOK, command)
    return run, template, command


def test_template_crud_import_export_and_revision(app):
    with TestClient(app) as client:
        auth = headers(client)
        assert client.post('/api/templates', json=spec()).status_code == 403
        created = client.post('/api/templates', json=spec(), headers=auth).json()
        tid = created['id']
        assert client.get('/api/templates').json()[0]['file_count'] == 1
        assert 'files' not in client.get('/api/templates').json()[0]
        updated = client.put(f'/api/templates/{tid}', json=spec('Updated') | {'revision': 1}, headers=auth)
        assert updated.status_code == 200 and updated.json()['revision'] == 2
        assert client.put(f'/api/templates/{tid}', json=spec() | {'revision': 1}, headers=auth).status_code == 409
        with zipfile.ZipFile(io.BytesIO(client.get(f'/api/templates/{tid}/export').content)) as z:
            assert z.read('main.tex').decode() == spec()['files'][0]['content']
        raw = io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:
            z.writestr('sample/main.tex',spec()['files'][0]['content'])
            z.writestr('sample/style.sty','% style')
            z.writestr('sample/font.otf',b'\xff\x00\xfe')
        imported = client.post('/api/templates/import', files={'file':('example.zip',raw.getvalue())},headers=auth)
        assert imported.status_code == 200
        item = imported.json()
        assert item['entrypoint'] == 'sample/main.tex' and len(item['files']) == 3
        assert item['files'][2]['encoding'] == 'base64'
        tex = client.post('/api/templates/import',files={'file':('simple.tex',b'\xef\xbb\xbf\\documentclass{book}')},headers=auth)
        assert tex.status_code == 200 and tex.json()['files'][0]['content'].startswith('\\documentclass')
        assert client.delete(f'/api/templates/{tid}',headers=auth).status_code == 200
        assert client.get(f'/api/templates/{tid}').status_code == 404


@pytest.mark.parametrize('path', ['../outside.tex','C:/outside.tex','/absolute.tex','folder\\escape.tex','CON.tex','file.tex:stream','./main.tex'])
def test_template_paths_cannot_escape_or_alias_windows_files(app,path):
    with TestClient(app) as client:
        data=spec();data['files'][0]['path']=path;data['entrypoint']=path
        assert client.post('/api/templates',json=data,headers=headers(client)).status_code == 422


def test_zip_traversal_and_duplicate_names_rejected(app):
    with TestClient(app) as client:
        auth=headers(client)
        raw=io.BytesIO()
        with zipfile.ZipFile(raw,'w') as z:z.writestr('../escape.tex','unsafe')
        assert client.post('/api/templates/import',files={'file':('bad.zip',raw.getvalue())},headers=auth).status_code == 422
        data=spec();data['files'].append({'path':'MAIN.tex','content':'duplicate'})
        assert client.post('/api/templates',json=data,headers=auth).status_code == 422


def test_apply_is_isolated_idempotent_and_uses_immutable_template_snapshot(app,monkeypatch):
    run,template,command=job(app);store=app.state.store
    old=store.get('book',BOOK)['retained_result']
    with TestClient(app) as client:
        auth=headers(client);launched=[]
        monkeypatch.setattr(app.state.engine,'launch',lambda rid:launched.append(rid))
        response=client.post(f'/api/books/{BOOK}/apply-template',json=command.model_dump(),headers=auth)
        assert response.status_code == 200 and response.json()['kind']=='template'
        assert client.post(f'/api/books/{BOOK}/apply-template',json=command.model_dump(),headers=auth).json()['id']==run['id']
        assert store.tasks(run['id'],'convert') == []
        changed=spec('Changed');changed['files'][0]['content']='Changed template'
        assert client.put(f"/api/templates/{template['id']}",json=changed|{'revision':1},headers=auth).status_code==200
        assert client.delete(f"/api/templates/{template['id']}",headers=auth).status_code==200
        base=store.directory(run['id'])
        assert (base/'template-example/main.tex').read_text()==spec()['files'][0]['content']
        assert store.get('book',BOOK)['retained_result']==old
        assert client.get(f"/api/runs/{run['id']}/content?page=1").json()['tex'].strip()=='The original book body.'
        assert client.get(f"/api/runs/{run['id']}/content?page=99").status_code==422


@pytest.mark.asyncio
async def test_template_agent_runs_and_waits_for_manual_save(app,monkeypatch):
    run,_,_=job(app);engine=app.state.engine;store=app.state.store;calls=[]
    before=store.get('book',BOOK)['retained_result'];base=store.directory(run['id'])
    body=(base/'tex/body.tex').read_bytes()
    async def agent(providers,current,project,feedback):
        calls.append(feedback)
        assert feedback['status']=='TEMPLATE_REQUESTED'
        assert 'byte-for-byte' in template_agent_instructions(current,project)
        atomic_text(project/'bookanalyst-template.tex',r'\usepackage[margin=22mm]{geometry}')
        main=(project/'main.tex').read_text(encoding='utf-8')
        atomic_text(project/'main.tex',main.replace(r'\begin{document}',r'\input{bookanalyst-template.tex}'+chr(10)+r'\begin{document}'))
    monkeypatch.setattr('bookanalyst.finisher.repair_project',agent)
    await engine.execute(run['id'])
    result=store.get('run',run['id'])
    assert result['state']=='COMPLETED',result.get('error')
    assert len(calls)==1 and (base/'tex/body.tex').read_bytes()==body
    assert store.get('book',BOOK)['retained_result']==before
    assert (base/'tex/main.pdf').exists()
    with TestClient(app) as client:
        auth=headers(client)
        response=client.post(f"/api/runs/{run['id']}/retain",headers=auth)
        assert response.status_code==200,response.text
        assert client.post(f"/api/runs/{run['id']}/retain",headers=auth).json()==response.json()
    after=store.get('book',BOOK)['retained_result']
    assert after['run_id']==run['id'] and after['id']!=before['id']
    assert (base/'template-applied.json').exists()
    assert (retained_directory(store,store.get('book',BOOK))/'main.pdf').exists()


@pytest.mark.asyncio
async def test_resume_keeps_applied_template_and_does_not_repeat_agent(app,monkeypatch):
    run,_,_=job(app);engine=app.state.engine;calls=[]
    async def agent(*args):calls.append(True)
    real_compile=engine.compile
    async def pause(*args):raise WorkflowError('PAUSED','Pause before compilation')
    monkeypatch.setattr('bookanalyst.finisher.repair_project',agent)
    monkeypatch.setattr(engine,'compile',pause)
    await engine.execute(run['id'])
    assert app.state.store.get('run',run['id'])['state']=='PAUSED'
    monkeypatch.setattr(engine,'compile',real_compile)
    await engine.execute(run['id'])
    assert app.state.store.get('run',run['id'])['state']=='COMPLETED'
    assert calls==[True]


@pytest.mark.asyncio
async def test_agent_cannot_replace_book_body_with_sample(app,monkeypatch):
    run,_,_=job(app);store=app.state.store;before=store.get('book',BOOK)['retained_result']
    async def agent(providers,current,project,feedback):atomic_text(project/'body.tex','The template sample body')
    monkeypatch.setattr('bookanalyst.finisher.repair_project',agent)
    await app.state.engine.execute(run['id'])
    result=store.get('run',run['id'])
    assert result['state']=='NEEDS_REVIEW' and result['error']['code']=='TEMPLATE_CONTENT_CHANGED'
    assert store.get('book',BOOK)['retained_result']==before


@pytest.mark.asyncio
async def test_codex_receives_template_scope_on_start_and_resume(app,monkeypatch):
    from test_codex_compiler import wire
    from bookanalyst.finisher import repair_project
    store,engine,run,project,sdk=wire(app,monkeypatch)
    run['kind']='template';store.put('run',run['id'],run)
    atomic_json(project.parent/'template.json',{'entrypoint':'main.tex','description':'Use wide margins'})
    for _ in range(2):
        await repair_project(app.state.engine.providers,run,project,{'status':'TEMPLATE_REQUESTED'})
    assert len(sdk.started)==1 and len(sdk.resumed)==1
    for options in sdk.started+sdk.resumed:
        assert 'template-example' in options['developer_instructions']
        assert 'byte-for-byte' in options['developer_instructions']
    assert all(c['metadata']['purpose']=='template_apply' for c in store.calls(run['id']))


@pytest.mark.parametrize('name,content', [
    ('main.tex', r'\documentclass{report}\begin{document}Changed\end{document}'),
    ('preamble.tex', '% Rewritten original definitions'),
    ('headings.tex', r'\chapter{Changed chapter}'),
])
def test_appearance_migration_rejects_original_structure_changes(app,name,content):
    from bookanalyst.templates import validate_template_content
    run,_,_=job(app);base=app.state.store.directory(run['id'])
    if name == 'headings.tex':
        atomic_text(base/'template-original'/name,r'\chapter{Original chapter}')
    atomic_text(base/'tex'/name,content)
    with pytest.raises(WorkflowError,match='原工程文件'):
        validate_template_content(run,base/'tex')
