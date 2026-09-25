import { connectionName, enqueueSettingsSave } from "./connections.js";
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
  const presets = settings.stage_presets || [];
  return `<section class="card stage-model-settings"><h2>各阶段模型</h2>
    <div class="stage-preset-bar">
      <label><span>预设</span><select data-stage-preset>${presetOptions(presets, "")}</select></label>
      <button type="button" data-stage-preset-action="apply" disabled>应用</button>
      <button type="button" data-stage-preset-action="save">另存为</button>
      <button type="button" data-stage-preset-action="update" disabled>更新</button>
      <button type="button" data-stage-preset-action="rename" disabled>重命名</button>
      <button type="button" data-stage-preset-action="delete" disabled>删除</button>
    </div>
    <p class="hint">新任务使用这里保存的配置。预设记住这一板块的连接、模型、思考强度和两处并发；应用只填入表单，保存设置后才生效。已有任务点击“更新模型配置”，在下一次请求前读取；已开始的请求保持原配置。</p>
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

const PRESET_LIMIT = 30;
function presetOptions(presets, selected) {
  return `<option value="">选择预设</option>${presets.map(preset => `<option value="${e(preset.id)}"${preset.id === selected ? " selected" : ""}>${e(preset.name)}</option>`).join("")}`;
}
export function syncStagePresetButtons(form) {
  const selected = form.querySelector("[data-stage-preset]")?.value;
  form.querySelectorAll("[data-stage-preset-action]").forEach(button => {
    button.disabled = button.dataset.stagePresetAction !== "save" && !selected;
  });
}
function showPresets(form, presets, selected) {
  const select = form.querySelector("[data-stage-preset]");
  select.innerHTML = presetOptions(presets, selected);
  select.value = selected && presets.some(preset => preset.id === selected) ? selected : "";
  syncStagePresetButtons(form);
}
function selectedPreset(form, settings) {
  const id = form.querySelector("[data-stage-preset]").value;
  return (settings.stage_presets || []).find(preset => preset.id === id);
}
function readConcurrency(form, name, label) {
  const value = Number(form.elements[name].value);
  if (!Number.isInteger(value) || value < 1) throw new Error(`${label}必须为正整数`);
  return value;
}
function snapshotStagePreset(form, stages) {
  return {
    stage_models: readStageModels(form, stages),
    llm_concurrency: readConcurrency(form, "workflow_concurrency", "并行任务数"),
    image_repair_concurrency: readConcurrency(form, "image_repair_concurrency", "修图默认并发"),
  };
}
function applyBinding(row, settings, binding) {
  const connection = row.querySelector("[data-stage-connection]");
  if (![...connection.options].some(option => option.value === binding.connection_id)) return "connection";
  connection.value = binding.connection_id;
  const model = row.querySelector("[data-stage-model]");
  const conn = settings.connections[binding.connection_id];
  model.innerHTML = modelOptions(conn, row.dataset.modelStage, binding.model_id);
  if ([...model.options].some(option => option.value === binding.model_id)) model.value = binding.model_id;
  const effort = row.querySelector('select[name$="_effort"]');
  effort.innerHTML = `<option value="${e(binding.reasoning_effort)}">${e(binding.reasoning_effort)}</option>`;
  updateModelRow(row, settings);
  if (model.value !== binding.model_id) return "model";
  if (effort.validationMessage) return "effort";
  return "";
}
function applyStagePreset(form, settings, stages, preset) {
  const issues = [];
  for (const [stage, title] of Object.entries(stages)) {
    const binding = preset.stage_models?.[stage];
    const row = form.querySelector(`[data-model-stage="${stage}"]`);
    if (!binding || !row || applyBinding(row, settings, binding)) issues.push(title);
  }
  form.elements.workflow_concurrency.value = preset.llm_concurrency;
  form.elements.image_repair_concurrency.value = preset.image_repair_concurrency;
  return issues;
}
function openPresetDialog({ title, confirmLabel, danger, body, read, setup }) {
  return new Promise(resolve => {
    const element = document.createElement("dialog");
    element.className = "stage-preset-dialog";
    element.setAttribute("aria-label", title);
    element.innerHTML = `<form novalidate>
      <h3>${e(title)}</h3>
      ${body}
      <div class="stage-preset-actions">
        <button type="button" data-preset-cancel>取消</button>
        <button type="submit" class="${danger ? "danger" : "primary"}">${e(confirmLabel)}</button>
      </div>
      <p class="error" role="alert" hidden></p>
    </form>`;
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
      if (element.open) element.close();
    };
    element.addEventListener("close", () => { element.remove(); finish(null); }, { once: true });
    element.addEventListener("click", event => {
      event.stopPropagation();
      if (event.target === element || event.target.closest("[data-preset-cancel]")) finish(null);
    });
    element.querySelector("form").addEventListener("submit", event => {
      event.preventDefault();
      const result = read ? read(element) : true;
      if (result?.error) {
        const alert = element.querySelector('[role="alert"]');
        alert.hidden = false;
        alert.textContent = result.error;
        return;
      }
      finish(result);
    });
    setup?.(element);
    document.body.append(element);
    element.showModal();
    (element.querySelector("input") || element.querySelector('[type="submit"]')).focus();
  });
}
function askPresetName(title, initial, presets, { overwrite = true } = {}) {
  return openPresetDialog({
    title,
    confirmLabel: "保存",
    body: `<label><span>名称</span><input name="name" maxlength="60" value="${e(initial)}" autocomplete="off"></label><p class="hint" data-preset-hint></p>`,
    setup(element) {
      const input = element.querySelector("input[name=name]"), hint = element.querySelector("[data-preset-hint]"), submit = element.querySelector('[type="submit"]');
      const render = () => {
        const name = input.value.trim();
        const exists = overwrite && presets.some(preset => preset.name === name);
        hint.textContent = overwrite
          ? (exists ? `将覆盖「${name}」里的阶段配置。已经保存的当前配置不变。` : "只写入预设列表。已经保存的当前配置不变，应用后仍需保存设置。")
          : "只修改名称，阶段配置保持不变。";
        submit.textContent = exists ? "覆盖" : "保存";
      };
      input.addEventListener("input", render);
      render();
    },
    read(element) {
      const name = element.querySelector("input[name=name]").value.trim();
      if (!name || name.length > 60) return { error: "名称须为 1–60 个字符" };
      if (!overwrite && presets.some(preset => preset.name === name)) return { error: "已有同名预设" };
      return name;
    },
  }).then(value => typeof value === "string" ? value : null);
}
let presetBusy = false;
export async function stagePresetAction(button, form, settings, stages) {
  if (presetBusy) return;
  const action = button.dataset.stagePresetAction;
  const presets = settings.stage_presets || [];
  const current = selectedPreset(form, settings);
  if (action !== "save" && !current) throw new Error("请先选择一个预设");
  presetBusy = true;
  const picker = form.querySelector("[data-stage-preset]");
  picker.disabled = true;
  form.querySelectorAll("[data-stage-preset-action]").forEach(item => { item.disabled = true; });
  try {
    if (action === "apply") {
      const issues = applyStagePreset(form, settings, stages, current);
      return { message: issues.length
        ? `已填入「${current.name}」，这些阶段需要重新选择：${issues.join("、")}。保存设置后才会用于新任务。`
        : `已填入「${current.name}」。保存设置后，新任务才会使用这套配置。` };
    }
    let next = presets, selected = current?.id || "", message = "";
    if (action === "save") {
      const name = await askPresetName("另存为预设", "", presets);
      if (!name) return;
      const shot = snapshotStagePreset(form, stages);
      const existing = presets.find(preset => preset.name === name);
      if (!existing && presets.length >= PRESET_LIMIT) throw new Error("预设最多保存 30 个");
      selected = existing?.id || crypto.randomUUID();
      next = existing
        ? presets.map(preset => preset.id === existing.id ? { id: preset.id, name: preset.name, ...shot } : preset)
        : [...presets, { id: selected, name, ...shot }];
      message = existing ? `已覆盖预设「${name}」` : `已保存预设「${name}」`;
    } else if (action === "update") {
      const confirmed = await openPresetDialog({
        title: "更新预设",
        confirmLabel: "覆盖",
        body: `<p>用当前表单覆盖预设「${e(current.name)}」。已经保存的当前配置不会变。</p>`,
      });
      if (!confirmed) return;
      const shot = snapshotStagePreset(form, stages);
      next = presets.map(preset => preset.id === current.id ? { id: preset.id, name: preset.name, ...shot } : preset);
      selected = current.id;
      message = `已更新预设「${current.name}」`;
    } else if (action === "rename") {
      const name = await askPresetName("重命名预设", current.name, presets.filter(preset => preset.id !== current.id), { overwrite: false });
      if (!name || name === current.name) return;
      next = presets.map(preset => preset.id === current.id ? { ...preset, name } : preset);
      selected = current.id;
      message = `预设已改名为「${name}」`;
    } else if (action === "delete") {
      const confirmed = await openPresetDialog({
        title: "删除预设",
        confirmLabel: "删除",
        danger: true,
        body: `<p>删除预设「${e(current.name)}」？各阶段当前配置不会变化。</p>`,
      });
      if (!confirmed) return;
      next = presets.filter(preset => preset.id !== current.id);
      selected = "";
      message = `已删除预设「${current.name}」`;
    }
    const saved = await enqueueSettingsSave(latest => { latest.stage_presets = next; return latest; });
    showPresets(form, saved.stage_presets, selected);
    return { settings: saved, message };
  } finally {
    presetBusy = false;
    picker.disabled = false;
    syncStagePresetButtons(form);
  }
}
