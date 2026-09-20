import { connectionName } from "./connections.js";
import { api, escape as e } from "./api.js";

const efforts = ["low", "medium", "high", "xhigh", "max"];
export function stageModelSettings(settings, stages) {
  const rows = Object.entries(stages).map(([stage, title]) => {
    const binding = settings.stage_models[stage];
    const connections = Object.entries(settings.connections)
      .map(([id, conn]) => `<option value="${e(id)}"${id === binding.connection_id ? " selected" : ""}>${e(connectionName(id, conn))}</option>`).join("");
    return `<div class="stage-model-row" data-model-stage="${stage}">
      <div class="stage-model-title">${e(title)}</div>
      <label><span>模型连接</span><select name="stage_${stage}_connection" data-stage-connection>${connections}</select></label>
      <label><span>模型</span><input name="stage_${stage}_model" data-stage-model value="${e(binding.model_id)}" list="stage-models-${e(binding.connection_id)}" placeholder="使用连接默认模型" autocomplete="off"></label>
      <label><span>思考强度</span><select name="stage_${stage}_effort">${efforts.map(effort => `<option value="${effort}"${effort === binding.reasoning_effort ? " selected" : ""}>${effort}</option>`).join("")}</select></label>
      ${stage === "image_repair" ? `<label><span>修图默认并发</span><input name="image_repair_concurrency" type="number" min="1" step="1" required value="${settings.image_repair_concurrency}"></label><p class="hint">独立用于图片检查和裁剪后复核，不跟随正文转换模型或并发。建议选择不同的视觉模型复查。</p>` : ""}
    </div>`;
  }).join("");
  return `<section class="card stage-model-settings"><h2>各阶段模型</h2>
    <p class="hint">新任务使用这里保存的配置。已有任务点击“更新模型配置”，在下一次请求前读取；已开始的请求保持原配置。</p>
    <div class="stage-model-grid">${rows}</div>
    ${Object.keys(settings.connections).map(id => `<datalist id="stage-models-${e(id)}"></datalist>`).join("")}
    <p class="hint">模型可从建议列表选择，也可填写模型 ID；留空使用该连接的默认模型。公共 TeX 由程序按全书规则生成，无独立模型请求。</p>
    <label class="workflow-concurrency">并行任务数<input name="workflow_concurrency" type="number" min="1" required value="${settings.llm_concurrency}"></label>
    <p class="hint">正文转换等任务的默认并行批次数；图片修复使用上方独立并发。已有任务可在运行页修改，并点击“更新配置”。</p>
  </section>`;
}

export async function loadStageModelChoices(container, settings) {
  for (const [id, conn] of Object.entries(settings.connections)) {
    const list = container.querySelector(`[id="stage-models-${id}"]`);
    if (list) list.innerHTML = (conn.models || []).map(m => `<option value="${e(m.id)}">${e(m.name || m.id)}</option>`).join("");
  }
  await Promise.allSettled(Object.entries(settings.connections).filter(([, c]) => c.kind !== "openai_compatible" && !c.models?.length && c.enabled).map(async ([id]) => {
    const status = await api(`/api/connections/${encodeURIComponent(id)}/status`);
    if (!container.isConnected) return;
    const list = container.querySelector(`[id="stage-models-${id}"]`);
    if (list) list.innerHTML = (status.models || []).map(m => `<option value="${e(m.model || m.id)}"></option>`).join("");
  }));
}

export function readStageModels(form, stages) {
  const values = new FormData(form);
  return Object.fromEntries(Object.keys(stages).map(stage => [stage, {
    connection_id: values.get(`stage_${stage}_connection`),
    model_id: String(values.get(`stage_${stage}_model`) || "").trim(),
    reasoning_effort: values.get(`stage_${stage}_effort`),
  }]));
}

export function stageConnectionChanged(select, settings) {
  const row = select.closest("[data-model-stage]"), model = row.querySelector("[data-stage-model]");
  model.setAttribute("list", `stage-models-${select.value}`);
  model.value = settings.connections[select.value]?.model_id || "";
}
