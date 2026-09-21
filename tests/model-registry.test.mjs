import { test } from 'node:test';
import assert from 'node:assert/strict';
import { registeredModel, stageModels, modelEfforts, modelSummary, formatTokens, contextPresets } from '../src/bookanalyst/static/model-registry.js';

test('model selection uses the provider registry including its default', () => {
  const model={id:'vision',name:'视觉模型',context:200000,max_output:65536,image:true,reasoning:true,xhigh:true,max:false};
  const conn={model_id:'vision',models:[model]};
  assert.equal(registeredModel(conn,''),model);
  assert.match(modelSummary(model),/上下文 200K · 输出 64K · 视觉 · 推理 · 超深推理/);
  assert.deepEqual(modelEfforts(model),['low','medium','high','xhigh']);
  assert.deepEqual(modelEfforts({...model,reasoning:false}),[]);
});

test('unknown legacy capabilities remain distinct from explicitly unsupported ones', () => {
  assert.deepEqual(modelEfforts(undefined),['low','medium','high','xhigh','max']);
  assert.deepEqual(modelEfforts({reasoning:true,xhigh:false,max:false}),['low','medium','high']);
});

test('reference presets retain their exact numeric values', () => {
  assert.deepEqual(contextPresets.map(([,n])=>n),[65536,131072,200000,262144,1048576]);
  assert.equal(formatTokens(200000),'200K');
  assert.equal(formatTokens(262144),'256K');
});

test('only conversion and image repair require registered vision models', () => {
  const conn={model_id:'unregistered',models:[{id:'vision',image:true},{id:'text',image:false},{id:'unknown'}]};
  for(const stage of ['convert','image_repair']) assert.deepEqual(stageModels(conn,stage).map(m=>m.id),['vision']);
  for(const stage of ['setup','seams','headings','references','reference_repair','finish','template_apply'])
    assert.deepEqual(stageModels(conn,stage),conn.models);
  assert.deepEqual(stageModels({model_id:'unregistered'},'setup'),[]);
  assert.deepEqual(stageModels({models:[{id:'text',image:false}]},'image_repair'),[]);
});
