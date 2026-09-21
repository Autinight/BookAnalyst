import { connectionName } from "./connections.js";
import { escape as e } from "./api.js";
import { stageModels, registeredModel, modelEfforts } from "./model-registry.js";

const efforts = ["low", "medium", "high", "xhigh", "max"];
function modelOptions(conn,stage,selected) {
  const models=stageModels(conn,stage),valid=models.some(model=>model.id===selected);
  const placeholder=models.length ? "请选择模型" : ["convert","image_repair"].includes(stage) ? "暂无已注册的视觉模型" : "暂无已注册模型";
  return `${valid ? "" : `<option value="" disabled selected>${placeholder}</option>`}${models.map(model=>`<option value="${e(model.id)}"${model.id===selected ? " selected" : ""}>${e(model.name && model.name!==model.id ? `${model.name}（${model.id}）` : model.id)}</option>`).join("")}`;
}
export function stageModelSettings(settings, stages) {
  const rows = Object.entries(stages).map(([stage, title]) => {
    const binding = settings.stage_models[stage];
    const connections = Object.entries(settings.connections)
      .map(([id, conn]) => `<option value="${e(id)}"${id === binding.connection_id ? " selected" : ""}>${e(connectionName(id, conn))}</option>`).join("");
    return `<div class="stage-model-row" data-model-stage="${stage}">
      <div class="stage-model-title">${e(title)}</div>
      <label><span>模型连接</span><select name="stage_${stage}_connection" data-stage-connection>${connections}</select></label>
      <label><span>模型</span><select name="stage_${stage}_model" data-stage-model required>${modelOptions(settings.connections[binding.connection_id],stage,binding.model_id || settings.connections[binding.connection_id]?.model_id)}</select></label>
      <label><span>思考强度</span><select name="stage_${stage}_effort">${efforts.map(effort => `<option value="${effort}"${effort === binding.reasoning_effort ? " selected" : ""}>${effort}</option>`).join("")}</select></label>
      ${stage === "image_repair" ? `<label><span>修图默认并发</span><input name="image_repair_concurrency" type="number" min="1" step="1" required value="${settings.image_repair_concurrency}"></label><p class="hint">独立用于图片检查和裁剪后复核，不跟随正文转换模型或并发。建议选择不同的视觉模型复查。</p>` : ""}
    </div>`;
  }).join("");
  return `<section class="card stage-model-settings"><h2>各阶段模型</h2>
    <p class="hint">新任务使用这里保存的配置。已有任务点击“更新模型配置”，在下一次请求前读取；已开始的请求保持原配置。</p>
    <div class="stage-model-grid">${rows}</div>
    <label class="workflow-concurrency">并行任务数<input name="workflow_concurrency" type="number" min="1" required value="${settings.llm_concurrency}"></label>
    <p class="hint">正文转换等任务的默认并行批次数；图片修复使用上方独立并发。已有任务可在运行页修改，并点击“更新配置”。</p>
  </section>`;
}

export async function loadStageModelChoices(container, settings) {
  refreshRegisteredModels(container,settings);
}

export function readStageModels(form, stages) {
  const values = new FormData(form);
  return Object.fromEntries(Object.keys(stages).map(stage => [stage, {
    connection_id: values.get(`stage_${stage}_connection`),
    model_id: String(values.get(`stage_${stage}_model`) || "").trim(),
    reasoning_effort: form.elements[`stage_${stage}_effort`].value,
  }]));
}

export function stageConnectionChanged(select, settings) {
  const row = select.closest("[data-model-stage]"), model = row.querySelector("[data-stage-model]");
  const conn=settings.connections[select.value],choices=stageModels(conn,row.dataset.modelStage);
  const selected=choices.find(m=>m.id===conn?.model_id)?.id || choices[0]?.id || "";
  model.innerHTML=modelOptions(conn,row.dataset.modelStage,selected);
  updateModelRow(row,settings);
}

function updateModelRow(row,settings) {
  const conn=settings.connections[row.querySelector("[data-stage-connection]").value];
  const input=row.querySelector("[data-stage-model]");
  input.innerHTML=modelOptions(conn,row.dataset.modelStage,input.value);
  const model=input.value ? registeredModel(conn,input.value) : undefined;
  const effort=row.querySelector('select[name$="_effort"]');
  const selected=effort.value || "medium", allowed=modelEfforts(model);
  effort.disabled=!allowed.length;
  if(!allowed.length) effort.innerHTML='<option value="medium">不使用推理</option>';
  else {
    effort.innerHTML=allowed.map(value=>`<option value="${value}"${value===selected ? " selected" : ""}>${value}</option>`).join("");
    if(!allowed.includes(selected)) effort.insertAdjacentHTML("beforeend",`<option value="${e(selected)}" selected disabled>${e(selected)}（未注册支持）</option>`);
  }
  effort.setCustomValidity(allowed.length && !allowed.includes(selected) ? "所选思考强度未注册支持，请重新选择" : "");
  effort.onchange=()=>effort.setCustomValidity("");
}
export function refreshRegisteredModels(container,settings) {container.querySelectorAll("[data-model-stage]").forEach(row=>updateModelRow(row,settings));}
export function stageModelChanged(select,settings) {updateModelRow(select.closest("[data-model-stage]"),settings);}
