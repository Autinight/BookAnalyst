import { api, escape as e } from "./api.js";
const protocols = [["chat_completions", "OpenAI Chat Completions"], ["responses", "OpenAI Responses"], ["anthropic", "Anthropic Messages"], ["gemini", "Google Gemini"]];

export const connectionName = (id, conn) => conn.name || (conn.kind === "codex_chatgpt" ? "ChatGPT 官方订阅" : conn.kind === "grok_oauth" ? "Grok 官方订阅" : id === "custom_api" ? "自定义 API / vLLM" : id);
const modelList = conn => conn.models || (conn.model_id ? [{id:conn.model_id}] : []);
const choices = (values, selected) => values.map(([v,label]) => `<option value="${e(v)}"${String(v) === String(selected) ? " selected" : ""}>${e(label)}</option>`).join("");
const capability = (field,label,m) => `<label>${label}<select data-model-field="${field}">${choices([["","自动 / 未知"],["true","支持"],["false","不支持"]],m[field] ?? "")}</select></label>`;

export function modelsMarkup(conn) {
  return modelList(conn).map(m => `<div class="provider-model" data-model-id="${e(m.id)}">
    <div class="provider-model-head"><div><strong>${e(m.name || m.id)}</strong>${m.name ? `<small>${e(m.id)}</small>` : ""}</div>
    <span class="model-badges">${m.image === true ? "图像 " : ""}${m.reasoning === true ? "思考 " : ""}${m.context ? Math.round(m.context/1024)+"K" : ""}</span>
    <button type="button" data-model-default>${conn.model_id === m.id ? "默认" : "设为默认"}</button><button type="button" data-model-remove aria-label="移除 ${e(m.id)}">×</button></div>
    <details class="model-edit"><summary>编辑模型</summary><div class="fields">
      <label>显示名称<input data-model-field="name" value="${e(m.name || "")}" placeholder="${e(m.id)}"></label>
      <label>上下文长度<input data-model-field="context" type="number" min="1" step="1" value="${m.context || ""}" placeholder="由服务决定"></label>
      <label>最大输出<input data-model-field="max_output" type="number" min="1" step="1" value="${m.max_output || ""}" placeholder="由服务决定"></label>
      ${capability("image","图像输入",m)}${capability("reasoning","思考能力",m)}
    </div></details></div>`).join("") || '<p class="hint">尚未添加模型。可从服务获取，也可直接输入模型 ID。</p>';
}

export function connectionCard(id, conn, unsaved=false, selected=true) {
  const custom = conn.kind === "openai_compatible";
  const input = (field,label,attrs="") => `<label>${label}<input name="${e(id)}_${field}" data-connection-field="${field}" value="${e(field === "name" ? connectionName(id,conn) : conn[field] ?? "")}" ${attrs}></label>`;
  return `<section class="provider-detail" data-connection-id="${e(id)}" id="provider-panel-${e(id)}" aria-labelledby="provider-title-${e(id)}" ${selected ? "" : "hidden"}>
    <div class="provider-detail-head"><div><h3 id="provider-title-${e(id)}" data-connection-title>${e(connectionName(id,conn))}</h3><p>${custom ? "API" : "官方订阅 · 账户登录"}</p></div>${custom ? '<button type="button" class="danger text-button" data-provider-delete>删除供应商</button>' : ""}</div>
    ${input("name","供应商名称",'required maxlength="100" data-provider-name')}
    ${custom ? `<label>API 密钥<div class="provider-key-row"><input name="${e(id)}_api_key" data-connection-field="api_key" aria-label="API 密钥" type="text" value="${e(conn.api_key || "")}" autocomplete="off" spellcheck="false" autocapitalize="off" placeholder="无需密钥时留空"><button type="button" data-provider-probe title="检查当前填写的配置">检查连接</button></div></label>
      ${input("base_url","API 地址",'placeholder="https://api.example.com/v1"')}
      <label>API 协议<select data-connection-field="protocol">${choices(protocols,conn.protocol)}</select></label>` :
      `<div class="provider-auth-note">${conn.kind === "grok_oauth" ? "使用 SuperGrok 或 X Premium+ 账户登录。" : "使用 ChatGPT 官方账户登录。"}</div><div class="row"><button type="button" data-connection-login>登录</button><button type="button" data-connection-check>检查连接</button><button type="button" data-provider-logout>退出登录</button></div>`}
    <p class="hint" data-connection-status>${unsaved ? "填写后保存，或直接检查连接。" : ""}</p>
    <details class="provider-advanced"><summary>高级设置</summary><div>
      ${custom ? `<label>自定义请求头<textarea data-provider-headers rows="3" placeholder="每行一个，例如 X-Project: my-project">${e(Object.entries(conn.headers || {}).map(([k,v]) => k+": "+v).join("\n"))}</textarea></label>` : ""}
      ${input("timeout_seconds","请求等待（秒）",'type="number" min="1" max="3600" required')}
    </div></details>
    <div class="provider-models"><div class="section-heading"><h4>已添加模型</h4><button type="button" data-provider-discover>获取模型</button></div>
      <div data-model-list>${modelsMarkup(conn)}</div>
      <div class="provider-model-add"><input data-model-input placeholder="模型 ID，或搜索获取的模型" aria-label="添加模型 ID" autocomplete="off"><button type="button" data-model-add>添加模型</button></div>
      <div data-discovered-models class="discovered-models" hidden></div>
      ${input("model_id","默认模型",'placeholder="使用上游默认模型"')}
    </div><button type="button" class="primary" data-provider-save>保存供应商</button>
  </section>`;
}

function providerItem(id,conn,selected) {
  const ready = conn.kind === "openai_compatible" ? Boolean(conn.base_url) : conn.enabled;
  return `<button type="button" class="provider-item" data-provider-select="${e(id)}" aria-controls="provider-panel-${e(id)}" aria-pressed="${id === selected}"><i class="provider-dot ${ready ? "enabled" : ""}" aria-hidden="true"></i><span class="provider-item-name" data-provider-label>${e(connectionName(id,conn))}</span><small data-provider-state>${modelList(conn).length}</small></button>`;
}
export function providerList(connections,selected) {
  return [["官方订阅", false], ["API", true]].map(([label, custom]) => {
    const group = Object.entries(connections).filter(([, c]) => (c.kind === "openai_compatible") === custom);
    return group.length ? `<div class="provider-group-label">${label}</div>${group.map(([id,c]) => providerItem(id,c,selected)).join("")}` : "";
  }).join("");
}
export function connectionEditor(settings,selected) {
  selected = selected && settings.connections[selected] ? selected : settings.default_connection || Object.keys(settings.connections)[0];
  return `<section class="provider-editor" aria-label="模型供应商"><div class="provider-sidebar"><div id="provider-list" class="provider-list" role="group" aria-label="供应商列表">${providerList(settings.connections,selected)}</div><button type="button" id="add-provider">＋ 添加自定义供应商</button></div><div id="connection-cards">${Object.entries(settings.connections).map(([id,c]) => connectionCard(id,c,false,id===selected)).join("")}</div></section>`;
}
export function selectProvider(form,id) {
  form.querySelectorAll("[data-connection-id]").forEach(c => {c.hidden=c.dataset.connectionId!==id;});
  form.querySelectorAll("[data-provider-select]").forEach(b => b.setAttribute("aria-pressed",String(b.dataset.providerSelect===id)));
}
export function updateProviderItem(form,id,conn) {
  const item = [...form.querySelectorAll("[data-provider-select]")].find(b => b.dataset.providerSelect===id);
  if (!item) return;
  item.querySelector("[data-provider-label]").textContent=connectionName(id,conn);
  item.querySelector("[data-provider-state]").textContent=modelList(conn).length;
  item.querySelector(".provider-dot").classList.toggle("enabled",Boolean(conn.base_url || conn.kind!=="openai_compatible"));
}
export function validateSettings(form) {
  for (const input of form.elements) {
    if (!input.willValidate || input.checkValidity()) continue;
    const card=input.closest("[data-connection-id]");
    if (card) selectProvider(form,card.dataset.connectionId);
    input.closest("details")?.setAttribute("open","");
    input.reportValidity(); return false;
  }
  return true;
}
function readCard(card,previous) {
  const conn=structuredClone(previous);
  card.querySelectorAll("[data-connection-field]").forEach(input => {conn[input.dataset.connectionField]=input.type==="number" ? Number(input.value) : input.value.trim();});
  conn.models=[...card.querySelectorAll("[data-model-id]")].map(row => {
    const m={id:row.dataset.modelId};
    row.querySelectorAll("[data-model-field]").forEach(input => {
      if(input.value!=="") m[input.dataset.modelField]=input.type==="number" ? Number(input.value) : ["image","reasoning"].includes(input.dataset.modelField) ? input.value==="true" : input.value.trim();
    }); return m;
  });
  if(conn.kind==="openai_compatible") {
    conn.auth_mode=conn.api_key ? "bearer" : "none"; conn.enabled=Boolean(conn.base_url); conn.headers={};
    for(const line of card.querySelector("[data-provider-headers]").value.split("\n")) {
      if(!line.trim()) continue;
      const colon=line.indexOf(":");
      if(colon<1) throw Error("请求头格式应为 名称: 值");
      conn.headers[line.slice(0,colon).trim()]=line.slice(colon+1).trim();
    }
  }
  return conn;
}
export function readConnections(form,settings) {
  return Object.fromEntries([...form.querySelectorAll("[data-connection-id]")].map(card => [card.dataset.connectionId,readCard(card,settings.connections[card.dataset.connectionId])]));
}
export function refreshConnectionChoices(form,settings) {
  form.querySelectorAll("[data-stage-connection]").forEach(select => {
    const selected=select.value;
    select.innerHTML=Object.entries(settings.connections).map(([id,c]) => `<option value="${e(id)}"${id===selected ? " selected" : ""}>${e(connectionName(id,c))}</option>`).join("");
  });
  for(const [id,c] of Object.entries(settings.connections)) {
    const list=form.querySelector(`[id="stage-models-${id}"]`);
    if(list) list.innerHTML=modelList(c).map(m => `<option value="${e(m.id)}">${e(m.name || m.id)}</option>`).join("");
  }
}
export function addProvider(form,settings) {
  const id="api_"+crypto.randomUUID().replaceAll("-","");
  const conn={name:"新 API 供应商",kind:"openai_compatible",enabled:true,base_url:"",protocol:"chat_completions",model_id:"",api_key:"",auth_mode:"none",image_support:"unknown",timeout_seconds:600,models:[],headers:{}};
  settings.connections[id]=conn;
  form.querySelector("#connection-cards").insertAdjacentHTML("beforeend",connectionCard(id,conn,true));
  form.querySelector("#provider-list").innerHTML=providerList(settings.connections,id);
  selectProvider(form,id);
  const list=document.createElement("datalist"); list.id="stage-models-"+id; form.append(list);
  refreshConnectionChoices(form,settings);
  const name=form.querySelector(`[data-connection-id="${id}"] [data-provider-name]`); name.focus(); name.select();
}
function refreshModels(form,settings,id,card) {
  card.querySelector("[data-model-list]").innerHTML=modelsMarkup(settings.connections[id]);
  card.querySelector('[data-connection-field="model_id"]').value=settings.connections[id].model_id;
  updateProviderItem(form,id,settings.connections[id]); refreshConnectionChoices(form,settings);
}
export function filterDiscovered(input) {
  input.closest("[data-connection-id]").querySelectorAll("[data-discovered-add]").forEach(b => {b.hidden=!b.textContent.toLowerCase().includes(input.value.toLowerCase());});
}
export async function providerAction(button,form,settings) {
  const card=button.closest("[data-connection-id]");
  if(!card || !button.matches("[data-provider-delete], [data-provider-save], [data-provider-probe], [data-provider-discover], [data-provider-logout], [data-model-add], [data-discovered-add], [data-model-remove], [data-model-default]")) return false;
  const id=card.dataset.connectionId;
  const status=card.querySelector("[data-connection-status]");
  if(button.hasAttribute("data-provider-delete")) {
    button.disabled=true;
    try {
      const saved=await api("/api/settings");
      if(saved.connections[id]) await api(`/api/connections/${encodeURIComponent(id)}`,{method:"DELETE"});
    } finally {button.disabled=false;}
    delete settings.connections[id];card.remove();
    const fallback=settings.default_connection !== id ? settings.default_connection : "openai_subscription";
    if(settings.default_connection===id) settings.default_connection=fallback;
    form.querySelectorAll("[data-stage-connection]").forEach(select => {
      if(select.value===id) {select.value=fallback;const input=select.closest("[data-model-stage]").querySelector("[data-stage-model]");input.value="";input.setAttribute("list","stage-models-"+fallback);}
    });
    form.querySelector("#provider-list").innerHTML=providerList(settings.connections,fallback);
    selectProvider(form,fallback);refreshConnectionChoices(form,settings);
    form.querySelector("#settings-error").textContent="供应商已删除。引用它的阶段已改为默认供应商。";
    return true;
  }
  const conn=readCard(card,settings.connections[id]); settings.connections[id]=conn;
  if(button.matches("[data-model-add], [data-discovered-add]")) {
    const input=card.querySelector("[data-model-input]"),mid=button.dataset.discoveredAdd || input.value.trim();
    if(!mid) throw Error("请输入模型 ID");
    if(!conn.models.some(m => m.id===mid)) conn.models.push(card._discovered?.find(m => m.id===mid) || {id:mid});
    if(!conn.model_id) conn.model_id=mid;
    input.value="";refreshModels(form,settings,id,card);return true;
  }
  if(button.matches("[data-model-remove], [data-model-default]")) {
    const mid=button.closest("[data-model-id]").dataset.modelId;
    if(button.hasAttribute("data-model-remove")) {conn.models=conn.models.filter(m => m.id!==mid);if(conn.model_id===mid) conn.model_id=conn.models[0]?.id || "";}
    else conn.model_id=mid;
    refreshModels(form,settings,id,card);return true;
  }
  for(const input of card.querySelectorAll("input,select,textarea")) if(!input.checkValidity()) {input.closest("details")?.setAttribute("open","");input.reportValidity();return true;}
  button.disabled=true;
  try {
    if(button.hasAttribute("data-provider-save")) {
      const saved=await api("/api/settings");saved.connections[id]=conn;
      const result=await api("/api/settings",{method:"PUT",body:saved});
      settings.connections[id]=result.connections[id];status.textContent="供应商已保存";updateProviderItem(form,id,conn);return true;
    }
    const discover=button.hasAttribute("data-provider-discover");
    const result=button.hasAttribute("data-provider-logout") ? await api(`/api/connections/${encodeURIComponent(id)}/logout`,{method:"POST",body:{}})
      : conn.kind==="openai_compatible" ? await api("/api/providers/probe",{method:"POST",body:{id,connection:conn,discover}})
      : await api(`/api/connections/${encodeURIComponent(id)}/${discover ? "models" : "status"}`,discover ? {method:"POST",body:{}} : {});
    status.textContent=result.message || (result.status==="READY" ? "连接正常" : result.status);
    if(discover && result.status==="READY") {
      card._discovered=(result.models || []).map(m => ({id:m.model || m.id,...(m.name ? {name:m.name} : {}),...Object.fromEntries(["context","max_output","image","reasoning"].filter(k => m[k]!==undefined).map(k => [k,m[k]])),...(m.inputModalities ? {image:m.inputModalities.includes("image")} : {})}));
      const panel=card.querySelector("[data-discovered-models]");panel.hidden=false;
      panel.innerHTML=card._discovered.map(m => `<button type="button" data-discovered-add="${e(m.id)}"><span>${e(m.name || m.id)}</span><small>${e(m.id)}</small><b>＋</b></button>`).join("") || '<p class="hint">服务未返回模型，可手动添加。</p>';
    }
  } finally {button.disabled=false;}
  return true;
}
