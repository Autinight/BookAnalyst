// Layout adapted from openhanako (Apache-2.0); see THIRD_PARTY_NOTICES.md.
import { escape as e } from "./api.js";
import { capabilityFields, contextPresets, outputPresets } from "./model-registry.js";

function dialog(markup, className = "pv-model-edit-card") {
  const element = document.createElement("dialog");
  element.className = `provider-theme ${className}`;
  element.innerHTML = markup;
  document.body.append(element);
  element.addEventListener("close", () => element.remove(), {once:true});
  element.addEventListener("click", event => {
    if (event.target === element) {
      const r=element.getBoundingClientRect();
      if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) element.close();
    }
    if(event.target.closest("[data-dialog-cancel]")) element.close();
  });
  element.showModal();
  return element;
}
const actions = label => `<div class="pv-model-edit-actions"><button type="button" class="pv-add-form-btn" data-dialog-cancel>取消</button><button type="submit" class="pv-add-form-btn primary">${label}</button></div><p class="pv-dialog-error" role="alert" hidden></p>`;
function combo(field,label,value,presets) {
  return `<label class="pv-model-edit-field"><span class="pv-model-edit-label">${label}</span><div class="cml-combo"><input class="cml-edit-panel-input" name="${field}" aria-label="${label}" type="number" min="1" step="1" value="${value || ""}"><button type="button" class="cml-combo-toggle" aria-label="${label}预设" aria-expanded="false">▾</button><div class="cml-combo-dropdown" role="group" aria-label="${label}预设选项">${presets.map(([name,n])=>`<button type="button" class="cml-combo-option" data-preset="${n}"><span>${name}</span><span class="cml-combo-value">${n.toLocaleString("en-US")}</span></button>`).join("")}</div></div></label>`;
}
export function editModel(model,onSave) {
  const element=dialog(`<form><div class="pv-model-edit-field"><span class="pv-model-edit-label">ID</span><span class="pv-model-edit-id">${e(model.id)}</span></div>
    <label class="pv-model-edit-field"><span class="pv-model-edit-label">显示名称</span><input class="settings-input" name="name" value="${e(model.name || "")}" placeholder="${e(model.id)}"></label>
    <div class="pv-model-edit-row">${combo("context","上下文长度",model.context,contextPresets)}${combo("max_output","最大输出",model.max_output,outputPresets)}</div>
    <div class="pv-model-edit-capabilities">${capabilityFields.map(([key,label])=>`<label class="pv-model-edit-field"><span class="pv-model-edit-label">${label}</span><input type="checkbox" role="switch" aria-label="${label}" name="${key}" class="pv-toggle" ${model[key] ? "checked" : ""}></label>`).join("")}</div>${actions("保存")}</form>`);
  element.setAttribute("aria-label", `编辑 ${model.id}`);
  element.addEventListener("click",event=>{
    const toggle=event.target.closest(".cml-combo-toggle"), option=event.target.closest("[data-preset]");
    const comboBox=(toggle || option)?.closest(".cml-combo");
    element.querySelectorAll(".cml-combo").forEach(box=>{
      const open=box===comboBox && !!toggle && !box.querySelector(".cml-combo-dropdown").classList.contains("open");
      box.querySelector(".cml-combo-dropdown").classList.toggle("open",open);
      box.querySelector(".cml-combo-toggle").setAttribute("aria-expanded",String(open));
    });
    if(option) {comboBox.querySelector("input").value=option.dataset.preset;comboBox.querySelector("input").focus();}
  });
  element.querySelector("form").addEventListener("submit",async event=>{
    event.preventDefault();
    const form=event.target, next={...model};
    for(const key of ["name","context","max_output"]) {
      const value=form.elements[key].value.trim();
      if(value) next[key]=key==="name" ? value : Number(value); else delete next[key];
    }
    // Saving this editor registers the capabilities shown by all six switches.
    for(const [key] of capabilityFields) next[key]=form.elements[key].checked;
    if(next.reasoning===false) {next.xhigh=false;next.max=false;}
    if(next.xhigh || next.max) next.reasoning=true;
    await submitDialog(element,()=>onSave(next));
  });
  element.addEventListener("change",event=>{
    const form=element.querySelector("form");
    if(event.target.name==="reasoning" && !event.target.checked) for(const key of ["xhigh","max"]) form.elements[key].checked=false;
    if(["xhigh","max"].includes(event.target.name) && event.target.checked) form.elements.reasoning.checked=true;
  });
}
export function confirmProviderDelete(name,onDelete) {
  const element=dialog(`<form><p class="pv-confirm-text">删除供应商「${e(name)}」？</p>${actions("删除")}</form>`,"pv-confirm-dialog");
  element.setAttribute("aria-label","删除供应商");
  element.querySelector('[type="submit"]').classList.replace("primary","danger");
  element.querySelector("form").addEventListener("submit",async event=>{event.preventDefault();await submitDialog(element,onDelete);});
}
async function submitDialog(element,save) {
  const button=element.querySelector('[type="submit"]');button.disabled=true;
  try {await save();element.close();}
  catch(error) {const status=element.querySelector('[role="alert"]');status.textContent=error.message;status.hidden=false;}
  finally {button.disabled=false;}
}
export function editProviderOptions(conn,onSave) {
  const element=dialog(`<form><label class="pv-model-edit-field"><span class="pv-model-edit-label">名称</span><input class="settings-input" name="name" required maxlength="100" value="${e(conn.name)}"></label><label class="pv-model-edit-field"><span class="pv-model-edit-label">默认模型</span><input class="settings-input" name="model_id" value="${e(conn.model_id)}"></label><label class="pv-model-edit-field"><span class="pv-model-edit-label">请求超时（秒）</span><input class="settings-input" name="timeout_seconds" type="number" min="1" max="3600" required value="${conn.timeout_seconds}"></label>${actions("保存")}</form>`);
  element.setAttribute("aria-label","供应商选项");
  element.querySelector("form").addEventListener("submit",async event=>{
    event.preventDefault();const values=new FormData(event.target);
    await submitDialog(element,()=>onSave({...conn,name:values.get("name").trim(),model_id:values.get("model_id").trim(),timeout_seconds:Number(values.get("timeout_seconds"))}));
  });
}
