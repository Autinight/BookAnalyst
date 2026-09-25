"""Stage selection, explicit refresh, and preservation of requests already started."""
import asyncio
import copy

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from bookanalyst.model_config import MODEL_STAGES, request_run
from bookanalyst.models import RunModelUpdate
from bookanalyst.store import WorkflowError
from test_rebuild import make_run


class Result(BaseModel):
    ok: bool


def bindings():
    return {stage: {"connection_id": "openai_subscription", "model_id": stage + "-model",
                    "reasoning_effort": ["low", "medium", "high", "xhigh", "max"][i % 5]}
            for i, stage in enumerate(MODEL_STAGES)}


def install(store):
    settings = store.get("settings", "main")
    settings.update(stage_models=bindings(), llm_concurrency=4)
    store.put("settings", "main", settings)
    return settings


def client_for(app):
    client = TestClient(app)
    client.headers["x-bookanalyst-token"] = client.get("/api/bootstrap").json()["token"]
    return client


def test_settings_roundtrip_and_new_run_use_stage_models(app):
    with client_for(app) as client:
        settings = client.get('/api/settings').json()
        assert set(settings['stage_models']) == set(MODEL_STAGES)
        settings['stage_models'] = bindings()
        settings['llm_concurrency'] = 7
        settings['connections']['custom_api'].update(enabled=True, base_url='http://localhost:9999', auth_mode='none')
        settings['stage_models']['references']['connection_id'] = 'custom_api'
        assert client.put('/api/settings', json=settings).status_code == 200
        saved = client.get('/api/settings').json()
        assert saved['stage_models'] == settings['stage_models']
        result = client.post('/api/runs', json={'book_id': 'wang-2022-g-invariant-min-max', 'end_page': 3})
        assert result.status_code == 200
        assert result.json()['stage_models'] == settings['stage_models']
        assert result.json()['concurrency'] == 7
        for invalid in ['disabled-connection', 'effort', 'missing-stage']:
            bad = copy.deepcopy(saved)
            if invalid == 'disabled-connection': bad['connections']['custom_api']['base_url'] = ''
            elif invalid == 'effort': bad['stage_models']['finish']['reasoning_effort'] = 'invalid'
            else: bad['stage_models'].pop('convert')
            assert client.put('/api/settings', json=bad).status_code == 422
            assert client.get('/api/settings').json()['stage_models'] == saved['stage_models']


def test_legacy_image_settings_are_compatible_but_saved_independently(app):
    store = app.state.store
    legacy = install(store)
    legacy['stage_models'].pop('image_repair')
    legacy.pop('image_repair_concurrency', None)
    store.put('settings', 'main', legacy)
    with client_for(app) as client:
        settings = client.get('/api/settings').json()
        assert settings['stage_models']['image_repair'] == legacy['stage_models']['convert']
        assert settings['image_repair_concurrency'] == legacy['llm_concurrency']
        assert store.get('settings', 'main') == legacy  # Reading does not migrate the DB.
        settings['stage_models']['image_repair']['model_id'] = 'independent-vision'
        settings['image_repair_concurrency'] = 13
        assert client.put('/api/settings', json=settings).status_code == 200
        # An old open form can still save without erasing the independent fields.
        legacy['stage_models']['convert']['model_id'] = 'new-converter'
        legacy['llm_concurrency'] = 200
        saved = client.put('/api/settings', json=legacy)
        assert saved.status_code == 200
        assert saved.json()['stage_models']['image_repair']['model_id'] == 'independent-vision'
        assert saved.json()['image_repair_concurrency'] == 13
        assert saved.json()['llm_concurrency'] == 200
        for invalid in [0, -1, 1.5, True]:
            assert client.put('/api/settings', json=saved.json() | {'image_repair_concurrency': invalid}).status_code == 422


@pytest.mark.asyncio
async def test_old_image_run_keeps_model_until_explicit_refresh(app, monkeypatch):
    from bookanalyst.model_config import stage_binding
    store, engine = app.state.store, app.state.engine
    settings = install(store)
    settings['image_repair_concurrency'] = 9
    store.put('settings', 'main', settings)
    run = make_run(app)
    run['kind'] = 'image_repair'
    run['config']['stage_models'] = bindings()
    run['config']['stage_models'].pop('image_repair')
    original = copy.deepcopy(run['config']['stage_models']['convert'])
    store.put('run', run['id'], run)
    assert stage_binding(run['config'], 'image_repair') == original
    async def resolve(binding, require_image=False): return dict(binding), {}
    monkeypatch.setattr(engine.providers, 'resolve', resolve)
    snapshot = await request_run(engine.providers, run['id'], 'image_repair', require_image=True)
    assert snapshot['config']['model'] == original
    await engine.rebind(run['id'], RunModelUpdate(revision=run['revision'], operation_id='image-refresh-settings'))
    snapshot = await request_run(engine.providers, run['id'], 'image_repair', require_image=True)
    assert snapshot['config']['model'] == settings['stage_models']['image_repair']
    assert snapshot['config']['llm_concurrency'] == 9
    assert store.get('settings', 'main')['llm_concurrency'] == 4


@pytest.mark.asyncio
async def test_all_requests_use_their_own_stage_even_during_repair(app, monkeypatch):
    engine, store = app.state.engine, app.state.store
    run = make_run(app)
    configured = bindings()
    run['config']['stage_models'] = configured
    store.put('run', run['id'], run)
    seen = []

    async def resolve(binding, require_image=False): return dict(binding), {}
    async def generate(snapshot, role, purpose, *args):
        seen.append((purpose, snapshot['config']['model']))
        return {'ok': True}
    monkeypatch.setattr(engine.providers, 'resolve', resolve)
    monkeypatch.setattr(engine.providers, 'generate', generate)
    for stage in MODEL_STAGES:
        purpose = 'compile_repair' if stage == 'finish' else stage
        await engine.ask(run, stage, purpose, {'repair_feedback': {'code': 'RETRY'}}, Result, [])
        assert seen[-1] == (purpose, configured[stage])
    assert store.get('run', run['id'])['config']['stage_models'] == configured


@pytest.mark.asyncio
async def test_refresh_reads_settings_at_next_request_and_keeps_inflight_request(app, monkeypatch):
    engine, store = app.state.engine, app.state.store
    configured = install(store)
    run = make_run(app)
    run['config'].update(model={'connection_id': 'openai_subscription', 'model_id': 'original', 'reasoning_effort': 'medium'}, resolved=True)
    store.put('run', run['id'], run)
    store.task(run['id'], 'done', 'convert', 'PASSED', [1])
    checkpoint = store.directory(run['id']) / 'batches' / 'done.json'
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text('{"preserved":true}')
    started, release = asyncio.Event(), asyncio.Event()
    seen = []
    async def resolve(binding, require_image=False): return dict(binding), {}
    async def generate(snapshot, role, purpose, *args):
        if snapshot['request_task_id'] == 'active':
            started.set()
            await release.wait()
        seen.append((snapshot['request_task_id'], snapshot['config']['model']['model_id']))
        return {'ok': True}
    monkeypatch.setattr(engine.providers, 'resolve', resolve)
    monkeypatch.setattr(engine.providers, 'generate', generate)
    active = asyncio.create_task(engine.ask(run, 'active', 'convert', {}, Result, []))
    await started.wait()
    command = RunModelUpdate(revision=run['revision'], operation_id='refresh-from-settings')
    status = await engine.rebind(run['id'], command)
    assert status['model_refresh_pending']
    assert status['model']['model_id'] == 'original'
    # The button does not freeze settings too early; the next request reads them.
    configured['stage_models']['convert']['model_id'] = 'saved-after-click'
    store.put('settings', 'main', configured)
    await engine.ask(run, 'next', 'convert', {'n': 2}, Result, [])
    release.set()
    await active
    assert seen == [('next', 'saved-after-click'), ('active', 'original')]
    current = store.get('run', run['id'])
    assert not current['model_refresh_pending']
    assert current['config']['llm_concurrency'] == 4
    assert checkpoint.read_text() == '{"preserved":true}'
    assert next(t for t in store.tasks(run['id']) if t['id'] == 'done')['state'] == 'PASSED'
    # Editing settings alone does not silently change a running book.
    configured['stage_models']['convert']['model_id'] = 'not-applied'
    store.put('settings', 'main', configured)
    await engine.ask(run, 'later', 'convert', {'n': 3}, Result, [])
    assert seen[-1] == ('later', 'saved-after-click')
    # Repeated delivery of the same button operation cannot re-arm the refresh.
    await engine.rebind(run['id'], command)
    assert not store.get('run', run['id'])['model_refresh_pending']


@pytest.mark.asyncio
async def test_native_compiler_and_template_select_distinct_bindings(app, monkeypatch):
    run = make_run(app)
    run['config']['stage_models'] = bindings()
    app.state.store.put('run', run['id'], run)
    calls = []
    async def resolve(binding, require_image=False):
        calls.append((binding['model_id'], require_image))
        return dict(binding), {}
    monkeypatch.setattr(app.state.engine.providers, 'resolve', resolve)
    for purpose, stage in [('compile_repair', 'finish'), ('template_apply', 'template_apply'), ('counter_repair', 'setup')]:
        snapshot = await request_run(app.state.engine.providers, run['id'], purpose)
        assert snapshot['config']['model'] == bindings()[stage]
    await request_run(app.state.engine.providers, run['id'], 'compile_repair')
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_model_switch_invalidates_response_cache_and_checks_vision(app, monkeypatch):
    run = make_run(app)
    run['config']['stage_models'] = bindings()
    store, engine = app.state.store, app.state.engine
    store.put('run', run['id'], run)
    seen = []
    async def resolve(binding, require_image=False):
        seen.append(('resolve', binding['model_id'], require_image))
        return dict(binding), {}
    async def generate(snapshot, *args):
        seen.append(('generate', snapshot['config']['model']['model_id']))
        return {'ok': True}
    monkeypatch.setattr(engine.providers, 'resolve', resolve)
    monkeypatch.setattr(engine.providers, 'generate', generate)
    await engine.ask(run, 'same', 'references', {}, Result, [])
    await engine.ask(run, 'same', 'references', {}, Result, [])
    run['config']['stage_models']['references']['model_id'] = 'replacement'
    store.put('run', run['id'], run)
    await engine.ask(run, 'same', 'references', {}, Result, [])
    await request_run(engine.providers, run['id'], 'references', require_image=True)
    assert [x for x in seen if x[0] == 'generate'] == [('generate', 'references-model'), ('generate', 'replacement')]
    assert ('resolve', 'replacement', True) in seen


@pytest.mark.asyncio
@pytest.mark.parametrize('custom', [False, True])
async def test_final_agent_routes_using_finish_binding_not_conversion_binding(app, monkeypatch, custom):
    from bookanalyst.finisher import repair_project
    from test_codex_compiler import wire
    store, engine, run, project, sdk = wire(app, monkeypatch)
    configured = bindings()
    if custom:
        configured['finish']['connection_id'] = 'custom_api'
        settings = store.get('settings', 'main')
        settings['connections']['custom_api'].update(enabled=True, base_url='http://localhost:9999')
        store.put('settings', 'main', settings)
    run['config']['stage_models'] = configured
    store.put('run', run['id'], run)
    async def resolve(binding, require_image=False): return dict(binding), {}
    monkeypatch.setattr(engine.providers, 'resolve', resolve)
    pi_calls = []
    async def pi(providers, snapshot, project, feedback, instructions, binding=None, purpose=None):
        pi_calls.append(binding)
    monkeypatch.setattr('bookanalyst.pi_compiler.repair_project', pi)
    await repair_project(engine.providers, run, project, {})
    if custom:
        assert pi_calls == [configured['finish']]
        assert not sdk.started
    else:
        assert not pi_calls
        assert sdk.started[0]['model'] == configured['finish']['model_id']
        assert sdk.started[0]['config']['model_reasoning_effort'] == configured['finish']['reasoning_effort']


@pytest.mark.asyncio
async def test_unused_stage_model_does_not_block_conversion(app, monkeypatch):
    run = make_run(app)
    run['config']['stage_models'] = bindings()
    app.state.store.put('run', run['id'], run)
    async def resolve(binding, require_image=False):
        if binding['model_id'] == 'finish-model':
            raise WorkflowError('MODEL_UNAVAILABLE', 'Finish unavailable')
        return dict(binding), {}
    monkeypatch.setattr(app.state.engine.providers, 'resolve', resolve)
    snapshot = await request_run(app.state.engine.providers, run['id'], 'convert', require_image=True)
    assert snapshot['config']['model']['model_id'] == 'convert-model'
    with pytest.raises(WorkflowError, match='Finish unavailable'):
        await request_run(app.state.engine.providers, run['id'], 'compile_repair')


@pytest.mark.asyncio
async def test_connection_default_is_frozen_until_explicit_refresh(app, monkeypatch):
    from bookanalyst.model_config import settings_models
    store, engine = app.state.store, app.state.engine
    settings = install(store)
    settings['stage_models']['convert']['model_id'] = ''
    settings['connections']['openai_subscription']['model_id'] = 'connection-default'
    assert settings_models(settings)['convert']['model_id'] == 'connection-default'
    # If the connection itself also uses the upstream default, freeze it on first use.
    settings['connections']['openai_subscription']['model_id'] = ''
    store.put('settings', 'main', settings)
    run = make_run(app)
    run['config']['stage_models'] = settings_models(settings)
    store.put('run', run['id'], run)
    async def resolve(binding, require_image=False):
        return dict(binding, model_id=binding['model_id'] or 'upstream-default'), {}
    monkeypatch.setattr(engine.providers, 'resolve', resolve)
    await request_run(engine.providers, run['id'], 'convert')
    assert store.get('run', run['id'])['config']['stage_models']['convert']['model_id'] == 'upstream-default'
    settings['connections']['openai_subscription']['model_id'] = 'new-default'
    store.put('settings', 'main', settings)
    snapshot = await request_run(engine.providers, run['id'], 'convert')
    assert snapshot['config']['model']['model_id'] == 'upstream-default'


def test_missing_stage_presets_are_visible_but_not_migrated_on_read(app):
    store = app.state.store
    legacy = store.get('settings', 'main')
    legacy.pop('stage_presets', None)
    store.put('settings', 'main', legacy)
    with client_for(app) as client:
        assert client.get('/api/settings').json()['stage_presets'] == []
        assert 'stage_presets' not in store.get('settings', 'main')


def test_stage_presets_are_saved_apart_from_the_active_configuration(app):
    with client_for(app) as client:
        settings = client.get('/api/settings').json()
        assert settings['stage_presets'] == []
        active = copy.deepcopy(settings['stage_models'])
        settings['connections']['custom_api'].update(enabled=True, base_url='http://localhost:9999', auth_mode='none')
        stored = {
            'id': 'quality-preset',
            'name': '  高质量  ',
            'stage_models': bindings(),
            'llm_concurrency': 3,
            'image_repair_concurrency': 5,
        }
        stored['stage_models']['convert']['connection_id'] = 'custom_api'
        settings['stage_presets'] = [stored]
        saved = client.put('/api/settings', json=settings)
        assert saved.status_code == 200
        body = saved.json()
        assert body['stage_models'] == active
        expected = copy.deepcopy(stored)
        expected['name'] = '高质量'
        assert body['stage_presets'] == [expected]
        omitted = copy.deepcopy(body)
        omitted.pop('stage_presets')
        omitted['stage_models']['setup']['model_id'] = 'separate-setup'
        omitted['llm_concurrency'] = 9
        kept = client.put('/api/settings', json=omitted)
        assert kept.status_code == 200
        assert kept.json()['stage_presets'] == [expected]
        assert kept.json()['stage_models']['setup']['model_id'] == 'separate-setup'
        assert kept.json()['llm_concurrency'] == 9
        current = kept.json()

        def blank(bad):
            bad['stage_presets'][0]['name'] = ' '

        def duplicate_name(bad):
            bad['stage_presets'].append(dict(bad['stage_presets'][0], id='another-id'))

        def duplicate_id(bad):
            bad['stage_presets'].append(dict(bad['stage_presets'][0], name='另一套'))

        def missing_stage(bad):
            bad['stage_presets'][0]['stage_models'].pop('finish')

        def bad_effort(bad):
            bad['stage_presets'][0]['stage_models']['finish']['reasoning_effort'] = 'ultra'

        def unknown_connection(bad):
            bad['stage_presets'][0]['stage_models']['seams']['connection_id'] = 'missing'

        def extra_field(bad):
            bad['stage_presets'][0]['note'] = 'nope'

        def bool_concurrency(bad):
            bad['stage_presets'][0]['llm_concurrency'] = True

        def bad_id(bad):
            bad['stage_presets'][0]['id'] = 'has space'

        for label, mutate in [
            ('blank-name', blank), ('duplicate-name', duplicate_name), ('duplicate-id', duplicate_id),
            ('missing-stage', missing_stage), ('bad-effort', bad_effort), ('unknown-connection', unknown_connection),
            ('extra-field', extra_field), ('bool-concurrency', bool_concurrency), ('bad-id', bad_id),
        ]:
            bad = copy.deepcopy(current)
            mutate(bad)
            assert client.put('/api/settings', json=bad).status_code == 422, label
            assert client.get('/api/settings').json()['stage_presets'] == current['stage_presets']
        too_many = copy.deepcopy(current)
        too_many['stage_presets'] = [dict(expected, id=f'preset-{index}', name=f'预设{index}') for index in range(31)]
        assert client.put('/api/settings', json=too_many).status_code == 422
        deleted = client.delete('/api/connections/custom_api')
        assert deleted.status_code == 200
        convert = deleted.json()['stage_presets'][0]['stage_models']['convert']
        assert convert == {'connection_id': 'openai_subscription', 'model_id': '', 'reasoning_effort': expected['stage_models']['convert']['reasoning_effort']}
        assert deleted.json()['stage_presets'][0]['stage_models']['setup'] == expected['stage_models']['setup']
        assert client.get('/api/settings').json()['stage_presets'] == deleted.json()['stage_presets']
