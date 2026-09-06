"""Workflow mechanics with real MinerU source; fake handoffs are not fidelity evidence."""
import asyncio
import copy
import json
from pathlib import Path
import shutil
import pytest
from bookanalyst.models import RunCreate
from bookanalyst.pageflow import plan_pages, neighbor_blocks, convert_pages, check_fragment, render_pages, MergeError
from bookanalyst.store import WorkflowError, digest
from bookanalyst.tex import compile_tex

DATA = json.loads((Path(__file__).parent / 'fixtures/wang-mineru-blocks.json').read_text(encoding='utf-8'))
PROFILE = {'documentclass':'article', 'heading_notes':'', 'numbering_notes':'explicit', 'toc':[], 'profile_hash':'profile-test'}

def node(aid, **kwargs):
    return dict(atom_id=aid, kind='source_ref', value='', source_prefix='', number_target=None, opens=[], closes=[], join_previous=False) | kwargs

def fragment(task, nodes):
    return {k:task[k] for k in ('task_id','input_hash','profile_hash')} | dict(request_neighbors=[],nodes=nodes,changes=[],findings=[])

def setup(app, review='on_demand'):
    config=RunCreate(book_id='elliptic-pde-second-order',profile='book',end_page=532,
        max_submitted_pages=532,visual_mode='disabled',review_mode=review,pages_per_task=1).model_dump()
    store=app.state.store
    run=store.create_run(config,store.get('book',config['book_id']),store.get('settings','main')['mineru'])
    atoms=[dict(atom_id=str(p), page_idx=p, text='Source '+str(p),type='text',bbox=[0,0,1,1],resources=[],discarded_candidate=False) for p in range(3)]
    tasks=plan_pages(atoms,[0,1,2],PROFILE,config)['tasks']
    return app.state.engine.pipeline,run,atoms,tasks

@pytest.mark.parametrize('size',[1,2,4,45])
def test_real_45_page_mineru_ownership_has_no_token_based_splitting(size):
    atoms=[a for a in DATA['atoms'] if a['type'] not in ('header','footer','page_number')]
    config=RunCreate(book_id='wang',pages_per_task=size).model_dump()
    plan=plan_pages(atoms,list(range(45)),PROFILE,config)
    assert len(plan['tasks'])==(45+size-1)//size
    assert [i for t in plan['tasks'] for i in t['owned_atom_ids']]==[a['atom_id'] for a in atoms]
    for t in plan['tasks']:
        assert t['context_atom_ids']==[]
        assert t['owned_atom_ids']==[a['atom_id'] for a in atoms if a['page_idx'] in t['pages']]
        for direction in ('previous','next'):
            assert neighbor_blocks(t,direction,atoms)==[a for a in atoms if a['page_idx']==t['neighbor_pages'][direction]]
    assert plan['tasks'][0]['neighbor_pages']['previous'] is None
    assert plan['tasks'][-1]['neighbor_pages']['next'] is None
    longer=copy.deepcopy(atoms); longer[0]['text'] *= 100
    assert [t['owned_atom_ids'] for t in plan_pages(longer,list(range(45)),PROFILE,config)['tasks']]==[t['owned_atom_ids'] for t in plan['tasks']]

@pytest.mark.asyncio
async def test_neighbor_query_is_read_only_and_resume_uses_saved_conversion(app,monkeypatch):
    pipeline,run,atoms,tasks=setup(app); task=tasks[1]; calls=[]
    async def call(run,role,purpose,payload,*args):
        calls.append((role,purpose,copy.deepcopy(payload)))
        assert [a['atom_id'] for a in payload['source']]==['1']
        if len(calls)==1:
            assert payload['read_only_neighbors']=={}
            return fragment(task,[])|{'request_neighbors':['previous']}
        assert list(payload['read_only_neighbors'])==['previous']
        assert [a['atom_id'] for a in payload['read_only_neighbors']['previous']]==['0']
        return fragment(task,[node('1')])
    monkeypatch.setattr(pipeline,'call',call)
    result=await convert_pages(pipeline,run,task,atoms,PROFILE,asyncio.Semaphore(1))
    again=await convert_pages(pipeline,run,task,atoms,PROFILE,asyncio.Semaphore(1))
    assert result==again and len(calls)==2
    assert all(purpose=='convert_pages' for _,purpose,_ in calls)
    assert result['review']['decision']=='NOT_REQUESTED'
    leaked=fragment(task,[node('0'),node('1')])
    with pytest.raises(WorkflowError,match='正文块'):
        check_fragment(leaked,task,{a['atom_id']:a for a in atoms},[],PROFILE)

@pytest.mark.asyncio
async def test_review_failure_resume_keeps_candidate_and_requests_only_review(app,monkeypatch):
    pipeline,run,atoms,tasks=setup(app,'all'); task=tasks[0]; calls=[]
    async def call(run,role,purpose,payload,*args):
        calls.append(purpose)
        if purpose=='convert_pages': return fragment(task,[node('0')])
        if calls.count('review_pages')==1: raise WorkflowError('REQUEST_UNKNOWN','unknown result')
        return dict(decision='PASS',source_ids=['0'],evidence='Compared all owned source',findings=[])
    monkeypatch.setattr(pipeline,'call',call)
    with pytest.raises(WorkflowError,match='unknown'):
        await convert_pages(pipeline,run,task,atoms,PROFILE,asyncio.Semaphore(1))
    result=await convert_pages(pipeline,run,task,atoms,PROFILE,asyncio.Semaphore(1))
    assert calls==['convert_pages','review_pages','review_pages']
    assert result['review']['decision']=='PASS'

@pytest.mark.asyncio
async def test_repairs_stop_after_two_and_do_not_accept_dropped_source(app,monkeypatch):
    pipeline,run,atoms,tasks=setup(app); calls=[]
    async def call(*args):
        calls.append(1)
        return fragment(tasks[0],[])
    monkeypatch.setattr(pipeline,'call',call)
    with pytest.raises(WorkflowError) as exc:
        await convert_pages(pipeline,run,tasks[0],atoms,PROFILE,asyncio.Semaphore(1))
    assert exc.value.code=='REPAIR_LIMIT' and len(calls)==3
    assert pipeline.store.get('run',run['id'])['repair_counts'][tasks[0]['task_id']]==2

@pytest.mark.asyncio
async def test_s2_only_filters_types_and_preserves_ocr_for_converter(app,monkeypatch):
    pipeline,run,atoms,tasks=setup(app); atoms[0]['text']='Unresolved \x03'
    atoms[2]['type']='header'; calls=[]
    monkeypatch.setattr(pipeline,'normalized',lambda run:(atoms,{}))
    async def call(run,role,purpose,payload,*args):
        calls.append(purpose)
        assert all('example' not in t for t in payload['inventory'])
        return {'types':[{'type':t['type'],'action':'EXCLUDE' if t['type']=='header' else 'KEEP','reason':'Type policy'} for t in payload['inventory']]}
    monkeypatch.setattr(pipeline,'call',call)
    result=await pipeline.S2(run,asyncio.Semaphore(1))
    assert calls==['box_type_policy']
    assert [a['atom_id'] for a in result['content.json']['atoms']]==['0','1']
    assert result['content.json']['atoms'][0]['text']=='Unresolved \x03'
    assert result['visual_review/index.json']['status']=='DEFERRED_TO_CONVERSION'

@pytest.mark.asyncio
async def test_conversion_repairs_controls_from_image_in_same_request(app,monkeypatch):
    pipeline,run,atoms,tasks=setup(app); atoms[0]['text']='Source \x03'; calls=[]
    evidence=[{'evidence_id':'image-0','source_ids':['0']}]
    async def images(*args): return [],evidence
    monkeypatch.setattr('bookanalyst.pageflow.source_images',images)
    async def call(run,role,purpose,payload,*args):
        calls.append(purpose)
        assert payload['source'][0]['text']=='Source \x03'
        return fragment(tasks[0],[node('0',kind='text',value='Source □')]) | {'changes':[dict(atom_id='0',before='Source \x03',after='Source □',reason='Visible square in supplied original image',evidence_ids=['image-0'])]}
    monkeypatch.setattr(pipeline,'call',call)
    result=await convert_pages(pipeline,run,tasks[0],atoms,PROFILE,asyncio.Semaphore(1))
    assert calls==['convert_pages'] and result['fragment']['nodes'][0]['value']=='Source □'

@pytest.mark.asyncio
async def test_cross_batch_proof_and_partial_equation_compile_with_author_label(tmp_path):
    if not shutil.which('xelatex'): pytest.skip('XeLaTeX required')
    atoms=[dict(atom_id=str(i),page_idx=i,type='text' if i==0 else 'equation',text=t,bbox=[0,0,1,1],resources=[]) for i,t in enumerate(['Proof. This spans pages.',r'\frac{1',r'}{2}'])]
    results=[{'task_id':str(i),'fragment':{'nodes':[n]}} for i,n in enumerate([
        node('0',source_prefix='Proof. ',opens=[dict(kind='proof',title='',number=None)]),
        node('1',opens=[dict(kind='equation',title='',number='7.2')]),
        node('2',join_previous=True,closes=['equation','proof'])])]
    outputs,ledger,structure=render_pages(atoms,PROFILE,results)
    body=outputs['tex/body.tex']; assert body.count(r'\begin{proof}')==body.count(r'\end{proof}')==1
    for name,content in outputs.items():
        path=tmp_path/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(content) if isinstance(content,dict) else content,encoding='utf-8')
    report=await compile_tex(tmp_path/'tex',ledger,structure)
    assert report['status']=='PASSED',report
    assert '7.2' in report['labels'].values()
    results[-1]['fragment']['nodes'][0]['closes']=['proof']
    with pytest.raises(MergeError): render_pages(atoms,PROFILE,results)
@pytest.mark.asyncio
async def test_merge_repairs_more_than_two_distinct_tasks_without_reconverting_others(app,monkeypatch):
    import bookanalyst.pipeline as module
    pipeline,run,atoms,tasks=setup(app)
    results=[{'task_id':t['task_id'],'fragment':fragment(t,[node(t['owned_atom_ids'][0])])} for t in tasks]
    fixed=set(); repaired=[]
    def load(run,stage,name):
        return {('S2','content.json'):{'atoms':atoms},('S4','book_profile.json'):PROFILE,
                ('S5','tasks.json'):{'tasks':tasks},('S6','results.json'):results}[(stage,name)]
    monkeypatch.setattr(pipeline,'load',load)
    def render(atoms,profile,results):
        for t in tasks:
            if t['task_id'] not in fixed: raise MergeError('Localized boundary defect',[t['task_id']])
        return {'tex/main.tex':'Test compiler harness','tex/body.tex':'Test harness','tex/source_map.json':{}},[],{'nodes':[]}
    async def repair(pipeline,run,task,*args):
        fixed.add(task['task_id']); repaired.append(task['task_id'])
        return next(r for r in results if r['task_id']==task['task_id'])
    async def compile(path,*args):
        (path/'main.pdf').write_bytes(b'test-only compiler output')
        return {'status':'PASSED'}
    monkeypatch.setattr(module,'render_pages',render); monkeypatch.setattr(module,'convert_pages',repair); monkeypatch.setattr(module,'compile_tex',compile)
    output=await pipeline.S7(run,asyncio.Semaphore(1))
    assert repaired==[t['task_id'] for t in tasks]
    assert len(output['repairs.json'])==3

@pytest.mark.asyncio
async def test_reconcile_reads_existing_turn_without_new_inference_or_usage(app,monkeypatch):
    from types import SimpleNamespace
    from bookanalyst.store import atomic_json
    pipeline,run,atoms,tasks=setup(app); provider=app.state.engine.providers; store=pipeline.store
    key=store.reserve(run['id'],1,'llm',1,{'role':'converter','model_id':'gpt-6-astra'})
    store.finish_call(key,'RESULT_UNKNOWN')
    directory=store.root/'runs'/run['id']/'requests'/key
    atomic_json(directory/'upstream.json',{'thread_id':'thread-known','turn_id':'turn-known'})
    atomic_json(directory/'request.json',{'schema':{'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}})
    async def read(**kwargs):
        assert kwargs=={'include_turns':True}
        return SimpleNamespace(model_dump=lambda **kw:{'thread':{'turns':[{'id':'turn-known','status':'completed','items':[{'type':'agentMessage','phase':'final_answer','text':'{"ok":true}'}]}]}})
    async def resume(thread_id):
        assert thread_id=='thread-known'
        return SimpleNamespace(read=read)
    async def codex(): return SimpleNamespace(thread_resume=resume)
    monkeypatch.setattr(provider,'codex',codex)
    result=await provider.reconcile(run)
    assert result==[{'call_id':key,'status':'RECOVERED'}]
    assert store.calls(run['id'])[0]['state']=='COMPLETED'
    assert store.get('run',run['id'])['usage']['llm']==1
    assert json.loads((directory/'response.json').read_text(encoding='utf-8'))['text']=='{"ok":true}'

@pytest.mark.asyncio
async def test_unreceipted_unknown_request_remains_unknown(app):
    pipeline,run,atoms,tasks=setup(app); store=pipeline.store
    key=store.reserve(run['id'],1,'llm',1,{})
    store.finish_call(key,'RESULT_UNKNOWN')
    result=await app.state.engine.providers.reconcile(run)
    assert result==[{'call_id':key,'status':'UNKNOWN','reason':'NO_UPSTREAM_RECEIPT'}]
    assert store.calls(run['id'])[0]['state']=='RESULT_UNKNOWN'

def test_structure_prefix_cannot_discard_an_unknown_parser_character(app):
    pipeline,run,atoms,tasks=setup(app)
    atoms[0]['text']='Proof.\x03 Source'
    f=fragment(tasks[0],[node('0',source_prefix='Proof.\x03 ',opens=[dict(kind='proof',title='',number=None)])])
    with pytest.raises(WorkflowError) as exc:
        check_fragment(f,tasks[0],{a['atom_id']:a for a in atoms},[],PROFILE)
    assert exc.value.code=='UNRESOLVED_CONTROL_CHARACTER'

@pytest.mark.asyncio
async def test_subscription_persists_handle_before_waiting_for_model(app,monkeypatch,tmp_path):
    from types import SimpleNamespace
    provider=app.state.engine.providers
    receipt=tmp_path/'upstream.json'
    async def run():
        assert json.loads(receipt.read_text(encoding='utf-8'))=={'thread_id':'th','turn_id':'tu'}
        return SimpleNamespace(final_response='{}')
    async def turn(*args,**kwargs):
        assert json.loads(receipt.read_text(encoding='utf-8'))['thread_id']=='th'
        return SimpleNamespace(id='tu',run=run)
    async def start(**kwargs):
        assert kwargs['ephemeral'] is False
        return SimpleNamespace(id='th',turn=turn)
    async def sdk(): return SimpleNamespace(thread_start=start)
    monkeypatch.setattr(provider,'codex',sdk)
    response,_=await provider._subscription({'model_id':'gpt-6-astra'},'test',{'type':'object'},[],receipt)
    assert response=='{}'

@pytest.mark.parametrize('authorized',[False,True])
def test_unknown_retry_requires_explicit_authorization_and_keeps_ledger(app,monkeypatch,authorized):
    from bookanalyst.models import Rerun
    pipeline,run,atoms,tasks=setup(app);store=pipeline.store;engine=app.state.engine
    key=store.reserve(run['id'],1,'llm',1,{})
    store.finish_call(key,'RESULT_UNKNOWN')
    store.change(run['id'],lambda r:r.update(state='NEEDS_REVIEW'))
    monkeypatch.setattr(engine,'launch',lambda rid:store.get('run',rid))
    command=Rerun(revision=1,operation_id='authorized-retry-test',stage='S0',reason='Explicit operator authorization',retry_unrecoverable=authorized)
    if not authorized:
        with pytest.raises(WorkflowError) as exc:engine.rerun(run['id'],command)
        assert exc.value.code=='RESULT_UNKNOWN'
    else:
        r=engine.rerun(run['id'],command)
        assert r['authorized_unknown_retries']==[key] and r['revision']==2
        assert r['usage']['llm']==1 and store.calls(run['id'])[0]['state']=='RESULT_UNKNOWN'
@pytest.mark.asyncio
async def test_targeted_rerun_generates_new_candidate_and_receives_reason(app,monkeypatch):
    pipeline,run,atoms,tasks=setup(app);task=tasks[0];calls=[]
    async def call(run,role,purpose,payload,*args):
        calls.append(payload)
        return fragment(task,[node('0')])
    monkeypatch.setattr(pipeline,'call',call)
    await convert_pages(pipeline,run,task,atoms,PROFILE,asyncio.Semaphore(1))
    run['rerun_task_id']=task['task_id'];run['rerun_reason']='Fix the reported source word boundary'
    await convert_pages(pipeline,run,task,atoms,PROFILE,asyncio.Semaphore(1))
    assert len(calls)==2
    assert calls[1]['error']['problem']==run['rerun_reason']

def test_common_template_owns_heading_punctuation():
    atom=dict(atom_id='a',page_idx=0,type='text',text='Theorem 1.1. Statement.',bbox=[0,0,1,1],resources=[])
    n=node('a',source_prefix='Theorem 1.1. ',opens=[dict(kind='theorem',title='',number='1.1.')],closes=['theorem'])
    output,ledger,_=render_pages([atom],PROFILE,[{'task_id':'t','fragment':{'nodes':[n]}}])
    assert ledger[0]['number']=='1.1'
    assert r'\renewcommand{\baauthornumber}{ 1.1}' in output['tex/body.tex']

def test_number_metadata_can_have_an_exact_conversion_record(app):
    pipeline,run,atoms,tasks=setup(app);atoms[0]['text']='(4.3)'
    f=fragment(tasks[0],[node('0',kind='number',value='4.3',number_target='formula')])
    f['changes']=[dict(atom_id='0',before='(4.3)',after='4.3',reason='Represent the source equation label in metadata',evidence_ids=[])]
    check_fragment(f,tasks[0],{a['atom_id']:a for a in atoms},[],PROFILE)
    f['changes'][0]['after']='4.4'
    with pytest.raises(WorkflowError):check_fragment(f,tasks[0],{a['atom_id']:a for a in atoms},[],PROFILE)
@pytest.mark.asyncio
async def test_pause_stops_next_request_without_cancelling_inflight(app,monkeypatch):
    pipeline,run,atoms,tasks=setup(app);store=pipeline.store
    started=asyncio.Event();finish=asyncio.Event();calls=[]
    async def generate(*args):
        calls.append(1);started.set();await finish.wait();return {'completed':True}
    monkeypatch.setattr(app.state.engine.providers,'generate',generate)
    pending=asyncio.create_task(pipeline.call(run,'converter','convert_pages',{}, {},asyncio.Semaphore(1)))
    await started.wait()
    store.change(run['id'],lambda r:r.update(pause_requested=True))
    finish.set();assert await pending=={'completed':True}
    with pytest.raises(WorkflowError) as exc:
        await pipeline.call(run,'converter','convert_pages',{}, {},asyncio.Semaphore(1))
    assert exc.value.code=='WORKFLOW_PAUSED' and len(calls)==1


def test_exact_author_tag_relocation_is_structural_but_changed_label_is_not(app):
    pipeline,run,atoms,tasks=setup(app)
    atoms[0]['type']='equation';atoms[0]['text']=r'x=1 \tag{4.3}'
    n=node('0',kind='math',value='x=1',opens=[dict(kind='equation',title='',number='4.3')],closes=['equation'])
    f=fragment(tasks[0],[n])
    amap={a['atom_id']:a for a in atoms}
    check_fragment(f,tasks[0],amap,[],PROFILE)
    n['opens'][0]['number']='4.4'
    with pytest.raises(WorkflowError) as exc:
        check_fragment(f,tasks[0],amap,[],PROFILE)
    assert exc.value.code=='UNDOCUMENTED_CHANGE'


def test_math_row_break_does_not_turn_following_letters_into_macros(app):
    from bookanalyst.semantics import check_math,math_commands
    text=r'\begin{aligned}p&=1\\M_p&=2\\H(t,T)&=3\end{aligned}'
    assert math_commands(text)==['begin','end']
    check_math(text)
    pipeline,run,atoms,tasks=setup(app)
    atoms[0]['type']='equation';atoms[0]['text']=text
    f=fragment(tasks[0],[node('0',opens=[dict(kind='equation',title='',number=None)],closes=['equation'])])
    check_fragment(f,tasks[0],{a['atom_id']:a for a in atoms},[],PROFILE)
    with pytest.raises(WorkflowError):check_math(r'\begin{aligned}x&=1\\\write{bad}\end{aligned}')


@pytest.mark.asyncio
async def test_compiler_rejects_missing_source_glyph_instead_of_silent_success(tmp_path):
    if not shutil.which('xelatex'):pytest.skip('XeLaTeX required')
    (tmp_path/'main.tex').write_text(r'\documentclass{article}\begin{document}\input{body}\end{document}',encoding='utf-8')
    body=tmp_path/'body.tex'
    body.write_text('normal bundle vanishing on ∂Σ.',encoding='utf-8')
    report=await compile_tex(tmp_path,[],{'nodes':[]})
    assert report['status']=='FAILED' and report['code']=='TEX_ERROR'
    assert 'body.tex:1:' in report['log'] and 'Missing character' in report['log']
    body.write_text(r'normal bundle vanishing on \(\partial\Sigma\).',encoding='utf-8')
    report=await compile_tex(tmp_path,[],{'nodes':[]})
    assert report['status']=='PASSED'


def test_confirmed_interruption_releases_repair_round_once_but_keeps_usage(app,monkeypatch):
    from bookanalyst.models import Rerun
    from bookanalyst.store import atomic_json
    pipeline,run,atoms,tasks=setup(app);store=pipeline.store;engine=app.state.engine
    key=store.reserve(run['id'],1,'llm',1,{'purpose':'convert_pages'})
    store.finish_call(key,'FAILED',error_code='UPSTREAM_INTERRUPTED')
    atomic_json(store.root/'runs'/run['id']/'requests'/key/'request.json',{'prompt':json.dumps({'task':{'task_id':tasks[0]['task_id']},'error':{'code':'TEX_STYLE'}})})
    store.change(run['id'],lambda r:r.update(state='NEEDS_REVIEW',repair_counts={tasks[0]['task_id']:2}))
    monkeypatch.setattr(engine,'launch',lambda rid:store.get('run',rid))
    for revision in [1,2]:
        store.change(run['id'],lambda r:r.update(state='NEEDS_REVIEW'))
        r=engine.rerun(run['id'],Rerun(revision=revision,operation_id='interrupted-retry-'+str(revision),stage='S0',reason='Resume confirmed interruption'))
        assert r['repair_counts'][tasks[0]['task_id']]==1
        assert r['usage']['llm']==1 and store.calls(run['id'])[0]['state']=='FAILED'
    assert r['released_interrupted_repairs']==[key]


def test_resource_preserves_original_diagram_without_duplicate_ocr(app,tmp_path):
    pipeline,run,atoms,tasks=setup(app)
    resource=tmp_path/'diagram.png';resource.write_bytes(b'fixture-image-content')
    atoms[0].update(type='interline_equation',text='OCR of a commutative diagram',resources=[str(resource)])
    f=fragment(tasks[0],[node('0',kind='resource')]);amap={a['atom_id']:a for a in atoms}
    check_fragment(f,tasks[0],amap,[],PROFILE)
    outputs,_,_=render_pages([atoms[0]],PROFILE,[{'task_id':tasks[0]['task_id'],'fragment':f}])
    assert r'\includegraphics[width=\linewidth,keepaspectratio]{' in outputs['tex/body.tex']
    assert 'OCR of a commutative diagram' not in outputs['tex/body.tex']
    assert b'fixture-image-content' in outputs.values()
    atoms[0]['resources']=[]
    with pytest.raises(WorkflowError) as exc:check_fragment(f,tasks[0],amap,[],PROFILE)
    assert exc.value.code=='MISSING_RESOURCE'


@pytest.mark.asyncio
async def test_merge_resume_reuses_previous_revision_repairs(app,monkeypatch):
    import bookanalyst.pipeline as module
    from bookanalyst.store import atomic_json,digest
    pipeline,run,atoms,tasks=setup(app)
    initial=[{'task_id':'t','fragment':{'nodes':[]}}]
    repaired=[{'task_id':'t','fragment':{'nodes':[]},'saved_real_response_reference':'already-validated'}]
    history=[{'task_ids':['t'],'problem':{'code':'TEX_ERROR'}}]
    atomic_json(pipeline.store.directory(run['id'],1,'S7')/'candidate/repairs.json',{'input_hash':digest(initial),'results':repaired,'hash':digest(repaired),'repairs':history})
    run=run|{'revision':2,'retry_from_revision':1}
    monkeypatch.setattr(pipeline,'load',lambda r,s,n:{('S2','content.json'):{'atoms':atoms},('S4','book_profile.json'):PROFILE,('S5','tasks.json'):{'tasks':tasks},('S6','results.json'):initial}[(s,n)])
    def render(a,p,results):
        assert results==repaired
        return {'tex/main.tex':'compiler harness','tex/body.tex':'compiler harness','tex/source_map.json':{}},[],{'nodes':[]}
    async def compile(path,*args):
        (path/'main.pdf').write_bytes(b'compiler harness');return {'status':'PASSED'}
    async def unexpected(*args):raise AssertionError('Do not regenerate saved repairs')
    monkeypatch.setattr(module,'render_pages',render);monkeypatch.setattr(module,'compile_tex',compile)
    monkeypatch.setattr(module,'convert_pages',unexpected)
    result=await pipeline.S7(run,asyncio.Semaphore(1))
    assert result['results.json']==repaired and result['repairs.json']==history


def test_source_map_counts_embedded_newlines_before_next_batch():
    atoms=[dict(atom_id='a',page_idx=0,type='text',text='First line\nSecond line\nThird line',bbox=[0,0,1,1],resources=[]),
           dict(atom_id='b',page_idx=1,type='text',text='Following batch',bbox=[0,0,1,1],resources=[])]
    results=[{'task_id':'first','fragment':{'nodes':[node('a')]}},{'task_id':'second','fragment':{'nodes':[node('b')]}}]
    outputs,_,_=render_pages(atoms,PROFILE,results)
    lines=outputs['tex/body.tex'].splitlines();mapping=outputs['tex/source_map.json']
    reported_line=lines.index('Following batch')+1
    owner=max((m for m in mapping.values() if m['line']<=reported_line),key=lambda m:m['line'])
    assert mapping['b']['line']==4 and owner['task_id']=='second'


@pytest.mark.asyncio
async def test_missing_glyph_repairs_are_parallel_and_receive_only_owned_errors(app,monkeypatch):
    import bookanalyst.pipeline as module
    pipeline,run,atoms,tasks=setup(app);started=[];both=asyncio.Event();fixed=set()
    initial=[{'task_id':t['task_id'],'fragment':fragment(t,[node(t['owned_atom_ids'][0])])} for t in tasks]
    monkeypatch.setattr(pipeline,'load',lambda r,s,n:{('S2','content.json'):{'atoms':atoms},('S4','book_profile.json'):PROFILE,('S5','tasks.json'):{'tasks':tasks},('S6','results.json'):initial}[(s,n)])
    mappings={a['atom_id']:{'task_id':t['task_id'],'line':i+1} for i,(a,t) in enumerate(zip(atoms,tasks))}
    monkeypatch.setattr(module,'render_pages',lambda *args:({'tex/main.tex':'compiler harness','tex/body.tex':'compiler harness','tex/source_map.json':mappings},[],{'nodes':[]}))
    async def compile(path,*args):
        if len(fixed)==3:
            (path/'main.pdf').write_bytes(b'compiler harness');return {'status':'PASSED'}
        return {'status':'FAILED','code':'TEX_ERROR','log':'body.tex:1: Missing character',
                'errors':[{'file':'body.tex','line':i+1,'message':'Missing character: symbol'} for i in range(3)]}
    async def repair(pipeline,run,task,atoms,profile,semaphore,error):
        started.append(task['task_id'])
        if len(started)>=2:both.set()
        await asyncio.wait_for(both.wait(),2)
        assert error['related_fragments']==[]
        assert {e['task_id'] for e in error['problem']['errors']}=={task['task_id']}
        fixed.add(task['task_id'])
        return next(r for r in initial if r['task_id']==task['task_id'])
    monkeypatch.setattr(module,'compile_tex',compile);monkeypatch.setattr(module,'convert_pages',repair)
    output=await pipeline.S7(run,asyncio.Semaphore(2))
    assert len(output['repairs.json'])==3 and len(started)==3
