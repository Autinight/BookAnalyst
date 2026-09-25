// Provider UI adapted from openhanako (Apache-2.0); see THIRD_PARTY_NOTICES.md.
import { api, escape as e } from "./api.js";
import { modelList, capabilityFields, formatTokens } from "./model-registry.js";
import { editModel, editProviderOptions, confirmProviderDelete } from "./provider-dialogs.js";
import { providerAuthMarkup, updateProviderAuth, providerLoggedOut, syncProviderAuthDots } from "./provider-auth.js";
const protocols=[["chat_completions","OpenAI Compatible"],["responses","OpenAI Responses"],["anthropic","Anthropic Messages"],["gemini","Google Gemini"]];
export const connectionName=(id,conn)=>conn.name || (conn.kind==="codex_chatgpt" ? "ChatGPT 官方订阅" : conn.kind==="grok_oauth" ? "Grok 官方订阅" : id==="custom_api" ? "自定义 API / vLLM" : id);
const choices=(values,selected)=>values.map(([value,label])=>`<option value="${value}"${value===selected ? " selected" : ""}>${label}</option>`).join("");
const paths={
  edit:'<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>',
  close:'<path d="m18 6-12 12M6 6l12 12"/>',
  link:'<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  refresh:'<path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m21 15-6-6L3 21"/>',
  reasoning:'<path d="M9 18h6M9 21h6M9 15c0-3-4-3-4-7a7 7 0 0 1 14 0c0 4-4 4-4 7"/>',
  video:'<rect x="2" y="5" width="14" height="14" rx="2"/><path d="m16 10 6-4v12l-6-4"/>',
  audio:'<path d="M12 3v18M7 7v10M17 7v10M2 10v4M22 10v4"/>',
  provider:'<rect x="4.5" y="4.5" width="15" height="15" rx="4"/><circle cx="12" cy="12" r="2.5" fill="currentColor" stroke="none"/>',
};
const icon=(name,size=14)=>`<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
export function modelsMarkup(conn) {
  return modelList(conn).map(m=>`<div class="pv-fav-item" data-model-id="${e(m.id)}"><span class="pv-fav-item-name" title="${e(m.name || m.id)}">${e(m.name || m.id)}</span>${m.name && m.name!==m.id ? `<span class="pv-fav-item-id" title="${e(m.id)}">${e(m.id)}</span>` : ""}${["image","video","audio","reasoning"].filter(k=>m[k]).map(k=>`<span class="pv-capability-icon" title="${capabilityFields.find(([key])=>key===k)[1]}">${icon(k,13)}</span>`).join("")}${m.context ? `<span class="pv-model-ctx">${formatTokens(m.context)}</span>` : ""}<div class="pv-fav-item-actions"><button type="button" class="pv-fav-item-edit" data-model-edit title="编辑模型" aria-label="编辑 ${e(m.id)}">${icon("edit",11)}</button><button type="button" class="pv-fav-item-remove" data-model-remove title="移除模型" aria-label="移除 ${e(m.id)}">${icon("close",10)}</button></div></div>`).join("");
}
const credential=(label,content,top=false)=>`<div class="pv-cred-row${top ? " pv-cred-row-top" : ""}"><span class="pv-cred-label">${label}</span><div class="pv-cred-url-row">${content}</div></div>`;
export function connectionCard(id,conn,unsaved=false,selected=true) {
  const custom=conn.kind==="openai_compatible",count=modelList(conn).length;
  return `<section class="pv-detail-inner" data-connection-id="${e(id)}" id="provider-panel-${e(id)}" aria-labelledby="provider-title-${e(id)}" ${selected ? "" : "hidden"}>
    <div class="pv-detail-header"><div class="pv-title-row"><h2 class="pv-detail-title" id="provider-title-${e(id)}" data-connection-title>${e(connectionName(id,conn))}</h2><button type="button" class="pv-options-button" data-provider-options aria-label="供应商选项" title="名称、默认模型与超时">${icon("edit",12)}</button></div>${custom ? '<button type="button" class="pv-delete-btn" data-provider-delete>删除供应商</button>' : ""}</div>
    <div class="pv-credentials">${custom ? `${credential("API Key",`<div class="pv-cred-key-row"><input class="settings-input" data-connection-field="api_key" aria-label="API Key" type="text" value="${e(conn.api_key || "")}" autocomplete="off" spellcheck="false"><button type="button" class="pv-cred-conn-icon" data-provider-probe title="检查连接" aria-label="检查连接">${icon("link")}</button></div>`)}
      ${credential("Headers",`<textarea class="settings-textarea pv-headers-textarea" data-provider-headers aria-label="Headers" placeholder="Authorization=Bearer token&#10;X-Corp-Auth=secret">${e(Object.entries(conn.headers || {}).map(([k,v])=>k+"="+v).join("\n"))}</textarea>`,true)}
      ${credential("Base URL",`<input class="settings-input" data-connection-field="base_url" aria-label="Base URL" value="${e(conn.base_url || "")}" placeholder="https://api.example.com/v1">`)}
      ${credential("API 类型",`<select class="settings-input" data-connection-field="protocol" aria-label="API 类型">${choices(protocols,conn.protocol)}</select>`)}
    ` : credential("OAuth",`<div class="pv-oauth-status">${providerAuthMarkup()}</div>`)}</div>
    <div class="pv-models"><div class="pv-fav-section" ${count ? "" : "hidden"}><div class="pv-fav-title">已添加的模型 <span class="pv-models-count" data-provider-model-count>${count}</span></div><div class="pv-fav-list" data-model-list>${modelsMarkup(conn)}</div></div>
      <div class="pv-models-action-row"><button type="button" class="pv-model-dropdown-trigger" data-model-menu aria-expanded="false"><span>添加模型</span><span aria-hidden="true">⌄</span></button><button type="button" class="pv-fetch-btn-inline" data-provider-discover>${icon("refresh")}读取模型</button></div>
      <div class="provider-theme pv-model-dropdown-panel" popover data-model-popup><input class="pv-model-dropdown-search" data-model-search aria-label="搜索模型" placeholder="搜索模型"><div class="pv-model-dropdown-list" data-discovered-models></div><div class="pv-model-dropdown-custom"><input class="pv-model-dropdown-custom-input" data-model-input aria-label="自定义模型 ID" placeholder="自定义模型 ID"><button type="button" class="pv-model-add-btn" data-model-add aria-label="添加模型">↵</button></div></div>
    </div><p class="pv-feedback" data-connection-status role="status"></p>
  </section>`;
}
function providerItem(id,conn,selected) {
  return `<button type="button" class="pv-list-item${id===selected ? " selected" : ""}" data-provider-select="${e(id)}" aria-controls="provider-panel-${e(id)}" aria-pressed="${id===selected}"><i class="pv-status-dot${conn.kind==="openai_compatible" && conn.base_url ? " on" : ""}" aria-hidden="true"></i><span class="pv-list-item-icon">${icon("provider",15)}</span><span class="pv-list-item-name" data-provider-label>${e(connectionName(id,conn))}</span><span class="pv-list-item-count" data-provider-state>${modelList(conn).length}</span></button>`;
}
export function providerList(connections,selected) {
  return [["OAuth",false],["API",true]].map(([label,custom])=>{
    const entries=Object.entries(connections).filter(([,c])=>(c.kind==="openai_compatible")===custom);
    return entries.length ? `<div class="pv-list-group-label">${label}</div>${entries.map(([id,c])=>providerItem(id,c,selected)).join("")}` : "";
  }).join("");
}
export function connectionEditor(settings,selected) {
  selected=selected && settings.connections[selected] ? selected : settings.default_connection;
  return `<section class="provider-theme pv-layout" aria-label="模型供应商"><div class="pv-list"><div id="provider-list">${providerList(settings.connections,selected)}</div><div class="pv-add-wrapper"><button type="button" class="pv-add-btn" id="add-provider">+ 添加自定义供应商</button></div></div><div class="pv-detail" id="connection-cards">${Object.entries(settings.connections).map(([id,c])=>connectionCard(id,c,false,id===selected)).join("")}</div></section>`;
}
export function selectProvider(form,id) {
  form.querySelectorAll("[data-model-popup]:popover-open").forEach(p=>p.hidePopover());
  form.querySelectorAll("[data-connection-id]").forEach(c=>{c.hidden=c.dataset.connectionId!==id;});
  form.querySelectorAll("[data-provider-select]").forEach(b=>{b.setAttribute("aria-pressed",String(b.dataset.providerSelect===id));b.classList.toggle("selected",b.dataset.providerSelect===id);});
  form.querySelector("#connection-cards").scrollTop=0;
}
export function updateProviderItem(form,id,conn) {
  const item=[...form.querySelectorAll("[data-provider-select]")].find(b=>b.dataset.providerSelect===id);
  if(!item) return;
  item.querySelector("[data-provider-label]").textContent=connectionName(id,conn);
  item.querySelector("[data-provider-state]").textContent=modelList(conn).length;
  if(conn.kind==="openai_compatible") item.querySelector(".pv-status-dot").classList.toggle("on",Boolean(conn.base_url));
  else syncProviderAuthDots(form);
}
export function validateSettings(form) {
  for(const input of form.elements) if(input.willValidate && !input.checkValidity()) {const card=input.closest("[data-connection-id]");if(card) selectProvider(form,card.dataset.connectionId);input.reportValidity();return false;}
  return true;
}
function readHeaders(value) {
  const headers={};
  for(const line of value.split("\n")) {
    if(!line.trim()) continue;
    const split=line.search(/[:=]/);
    if(split<1) throw Error("请求头格式应为 名称=值");
    headers[line.slice(0,split).trim()]=line.slice(split+1).trim();
  }
  return headers;
}
function readCard(card,previous) {
  const conn=structuredClone(previous);
  card.querySelectorAll("[data-connection-field]").forEach(input=>{conn[input.dataset.connectionField]=input.value.trim();});
  if(conn.kind==="openai_compatible") {conn.auth_mode=conn.api_key ? "bearer" : "none";conn.enabled=Boolean(conn.base_url);conn.headers=readHeaders(card.querySelector("[data-provider-headers]").value);}
  return conn;
}
export function readConnections(form,settings) {return Object.fromEntries([...form.querySelectorAll("[data-connection-id]")].map(card=>[card.dataset.connectionId,readCard(card,settings.connections[card.dataset.connectionId])]));}
export function refreshConnectionChoices(form,settings) {
  syncProviderAuthDots(form);
  form.querySelectorAll("[data-stage-connection]").forEach(select=>{
    const selected=select.value;
    select.innerHTML=Object.entries(settings.connections).map(([id,c])=>`<option value="${e(id)}"${id===selected ? " selected" : ""}>${e(connectionName(id,c))}</option>`).join("");
  });
  form.dispatchEvent(new CustomEvent("provider-registry-change",{bubbles:true}));
}
let saveQueue=Promise.resolve();
export const flushProviderSaves=()=>saveQueue;
export function enqueueSettingsSave(mutate) {
  const task = saveQueue.catch(() => {}).then(async () => {
    const saved = await api("/api/settings");
    return api("/api/settings", { method: "PUT", body: mutate(saved) });
  });
  saveQueue = task;
  return task;
}
function saveConnection(id,conn) {
  const snapshot=structuredClone(conn);
  return enqueueSettingsSave(saved => { saved.connections[id] = snapshot; return saved; });
}
function feedback(card,message,error=false) {
  const status=card.querySelector("[data-connection-status]");status.textContent=message;status.classList.toggle("error",error);
  clearTimeout(card._feedbackTimer);
  if(!error) card._feedbackTimer=setTimeout(()=>{status.textContent="";},2500);
}
export async function autoSaveProvider(input,form,settings) {
  const card=input.closest("[data-connection-id]");
  if(!card || !input.matches("[data-connection-field],[data-provider-headers]") || !input.dataset.providerDirty) return;
  const id=card.dataset.connectionId,conn=readCard(card,settings.connections[id]);
  delete input.dataset.providerDirty;
  if(JSON.stringify(conn)===card._lastSaved) return;
  try {await saveConnection(id,conn);card._lastSaved=JSON.stringify(conn);settings.connections[id]=readCard(card,conn);updateProviderItem(form,id,conn);feedback(card,"已保存");}
  catch(error) {input.dataset.providerDirty="true";feedback(card,error.message,true);throw error;}
}
async function commitCard(form,settings,id,card,conn) {
  await saveConnection(id,conn);settings.connections[id]=conn;
  card.querySelector("[data-connection-title]").textContent=connectionName(id,conn);
  refreshModels(form,settings,id,card);feedback(card,"已保存");
}
export function addProvider(form,settings) {
  const panel=form.querySelector(".pv-layout");
  if(panel.querySelector(".pv-add-overlay")) return;
  const overlay=document.createElement("div");overlay.className="pv-add-overlay";
  overlay.innerHTML=`<div class="pv-add-overlay-header"><button type="button" class="pv-add-overlay-back" data-provider-cancel>‹ 取消</button><span>添加自定义供应商</span></div><div class="pv-add-form">${[["name","供应商名称","my-provider"],["base_url","Base URL","https://api.example.com/v1"],["api_key","API Key",""]].map(([key,label,placeholder])=>`<label class="pv-add-form-field"><span class="pv-add-form-label">${label}</span><input class="settings-input" data-new-provider="${key}" placeholder="${placeholder}" ${key!=="api_key" ? "required" : ""}></label>`).join("")}<label class="pv-add-form-field"><span class="pv-add-form-label">Headers</span><textarea class="settings-textarea pv-headers-textarea" data-new-provider="headers" placeholder="Authorization=Bearer token&#10;X-Corp-Auth=secret"></textarea></label><label class="pv-add-form-field"><span class="pv-add-form-label">API 类型</span><select class="settings-input" data-new-provider="protocol">${choices(protocols,"chat_completions")}</select></label><p class="pv-dialog-error" role="alert" hidden></p><div class="pv-add-form-actions"><button type="button" class="pv-add-form-btn primary" data-provider-create>添加</button></div></div>`;
  panel.append(overlay);panel.querySelector(".pv-list").inert=true;panel.querySelector(".pv-detail").inert=true;
  const close=()=>{overlay.remove();panel.querySelector(".pv-list").inert=false;panel.querySelector(".pv-detail").inert=false;panel.querySelector("#add-provider").focus();};
  overlay.querySelector("[data-provider-cancel]").onclick=close;
  overlay.addEventListener("keydown",event=>{
    if(event.key==="Escape") {event.preventDefault();close();}
    if(event.key==="Enter" && event.target.matches("input")) {event.preventDefault();overlay.querySelector("[data-provider-create]").click();}
  });
  overlay.querySelector("[data-provider-create]").onclick=async event=>{
    const button=event.currentTarget;
    for(const input of overlay.querySelectorAll("input")) if(!input.reportValidity()) return;
    button.disabled=true;
    try {
      const values=Object.fromEntries([...overlay.querySelectorAll("[data-new-provider]")].map(input=>[input.dataset.newProvider,input.value.trim()]));
      const id="api_"+crypto.randomUUID().replaceAll("-","");
      const conn={...values,headers:readHeaders(values.headers),kind:"openai_compatible",enabled:true,model_id:"",models:[],timeout_seconds:600,image_support:"unknown",auth_mode:values.api_key ? "bearer" : "none"};
      await saveConnection(id,conn);settings.connections[id]=conn;
      form.querySelector("#connection-cards").insertAdjacentHTML("beforeend",connectionCard(id,conn));
      form.querySelector("#provider-list").innerHTML=providerList(settings.connections,id);close();selectProvider(form,id);refreshConnectionChoices(form,settings);
    } catch(error) {const status=overlay.querySelector('[role="alert"]');status.hidden=false;status.textContent=error.message;}
    finally {button.disabled=false;}
  };
  overlay.querySelector("input").focus();
}
function refreshModels(form,settings,id,card) {
  const conn=settings.connections[id],count=modelList(conn).length;
  card.querySelector("[data-model-list]").innerHTML=modelsMarkup(conn);
  card.querySelector("[data-provider-model-count]").textContent=count;
  card.querySelector(".pv-fav-section").hidden=!count;
  updateProviderItem(form,id,conn);refreshConnectionChoices(form,settings);
  renderDiscovered(card,conn);
}
function renderDiscovered(card,conn) {
  const models=new Map([...modelList(conn),...(card._discovered || [])].map(m=>[m.id,m]));
  card.querySelector("[data-discovered-models]").innerHTML=[...models.values()].map(m=>{
    const added=modelList(conn).some(registered=>registered.id===m.id);
    return `<button type="button" class="pv-model-dropdown-option${added ? " added" : ""}" data-discovered-add="${e(m.id)}" ${added ? "disabled" : ""}><span class="pv-model-dropdown-option-name">${e(m.name || m.id)}</span>${added ? '<span class="pv-model-dropdown-option-check">✓</span>' : ""}${m.context ? `<span class="pv-model-ctx">${formatTokens(m.context)}</span>` : ""}</button>`;
  }).join("") || '<div class="pv-model-dropdown-empty">暂无模型</div>';
  filterDiscovered(card.querySelector("[data-model-search]"));
}
export function filterDiscovered(input) {input.closest("[data-connection-id]").querySelectorAll("[data-discovered-add]").forEach(b=>{b.hidden=!(b.textContent+" "+b.dataset.discoveredAdd).toLowerCase().includes(input.value.toLowerCase());});}
function openModelMenu(card,conn) {
  const panel=card.querySelector("[data-model-popup]"),trigger=card.querySelector("[data-model-menu]");
  if(panel.matches(":popover-open")) {panel.hidePopover();return;}
  renderDiscovered(card,conn);
  const rect=trigger.getBoundingClientRect(),width=Math.min(rect.width+80,innerWidth-24);
  panel.style.width=width+"px";panel.style.left=Math.min(rect.left,innerWidth-width-12)+"px";
  panel.style.top=Math.max(12,Math.min(rect.bottom+4,innerHeight-300))+"px";
  panel.onbeforetoggle=event=>trigger.setAttribute("aria-expanded",String(event.newState==="open"));
  panel.showPopover();
  panel.querySelector("input").focus();
}
export async function providerAction(button,form,settings) {
  const card=button.closest("[data-connection-id]");
  if(!card || !button.matches("[data-provider-delete],[data-provider-options],[data-provider-probe],[data-provider-discover],[data-provider-logout],[data-model-menu],[data-model-add],[data-discovered-add],[data-model-remove],[data-model-edit]")) return false;
  const id=card.dataset.connectionId,conn=readCard(card,settings.connections[id]);
  if(button.hasAttribute("data-provider-options")) {editProviderOptions(conn,next=>commitCard(form,settings,id,card,next));return true;}
  if(button.hasAttribute("data-model-menu")) {openModelMenu(card,conn);return true;}
  if(button.hasAttribute("data-model-edit")) {const mid=button.closest("[data-model-id]").dataset.modelId;editModel(modelList(conn).find(m=>m.id===mid),next=>commitCard(form,settings,id,card,{...conn,models:modelList(conn).map(m=>m.id===mid ? next : m)}));return true;}
  if(button.hasAttribute("data-provider-delete")) {
    confirmProviderDelete(connectionName(id,conn),async()=>{
      await flushProviderSaves().catch(()=>{});
      const result=await api(`/api/connections/${encodeURIComponent(id)}`,{method:"DELETE"});
      delete settings.connections[id];card.remove();
      const fallback=result.default_connection;settings.default_connection=fallback;
      form.querySelectorAll("[data-stage-connection]").forEach(select=>{if(select.value===id) {select.value=fallback;select.closest("[data-model-stage]").querySelector("[data-stage-model]").value="";}});
      form.querySelector("#provider-list").innerHTML=providerList(settings.connections,fallback);selectProvider(form,fallback);refreshConnectionChoices(form,settings);
    });return true;
  }
  button.disabled=true;
  try {
    if(button.matches("[data-model-add],[data-discovered-add],[data-model-remove]")) {
      const mid=button.dataset.discoveredAdd || button.closest("[data-model-id]")?.dataset.modelId || card.querySelector("[data-model-input]").value.trim();
      if(!mid) throw Error("请输入模型 ID");
      conn.models=[...modelList(conn)];
      if(button.hasAttribute("data-model-remove")) {conn.models=conn.models.filter(m=>m.id!==mid);if(conn.model_id===mid) conn.model_id=conn.models[0]?.id || "";}
      else if(!conn.models.some(m=>m.id===mid)) {conn.models.push(card._discovered?.find(m=>m.id===mid) || {id:mid});if(!conn.model_id) conn.model_id=mid;}
      await commitCard(form,settings,id,card,conn);card.querySelector("[data-model-input]").value="";return true;
    }
    const discover=button.hasAttribute("data-provider-discover");
    const result=button.hasAttribute("data-provider-logout") ? await api(`/api/connections/${encodeURIComponent(id)}/logout`,{method:"POST",body:{}})
      : conn.kind==="openai_compatible" ? await api("/api/providers/probe",{method:"POST",body:{id,connection:conn,discover}})
      : await api(`/api/connections/${encodeURIComponent(id)}/${discover ? "models" : "status"}`,discover ? {method:"POST",body:{}} : {});
    if(button.hasAttribute("data-provider-logout")) providerLoggedOut(card,result);
    else if(conn.kind!=="openai_compatible") updateProviderAuth(card,result);
    feedback(card,result.message || (result.status==="READY" ? "连接正常" : result.status),result.status!=="READY" && !button.hasAttribute("data-provider-logout"));
    if(discover && result.status==="READY") {
      card._discovered=(result.models || []).map(m=>({id:m.model || m.id,...Object.fromEntries(["name","context","max_output",...capabilityFields.map(([k])=>k)].filter(k=>m[k]!==undefined).map(k=>[k,m[k]])),...(m.inputModalities ? {image:m.inputModalities.includes("image"),video:m.inputModalities.includes("video"),audio:m.inputModalities.includes("audio")} : {})}));
      const panel=card.querySelector("[data-model-popup]");if(panel.matches(":popover-open")) panel.hidePopover();openModelMenu(card,conn);
    }
  } finally {button.disabled=false;}
  return true;
}
