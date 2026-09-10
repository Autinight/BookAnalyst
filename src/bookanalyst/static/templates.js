import { api, escape as e } from "./api.js";
const $ = s => document.querySelector(s);
let editor = null, selectedFile = 0, application = null, callbacks;
const sample = "\\documentclass{book}\n\\usepackage[margin=25mm]{geometry}\n\\usepackage{amsmath,amssymb,amsthm}\n\\begin{document}\n\\chapter{Sample chapter}\nSample body text.\n\\end{document}\n";

export function view(items) {
  return `<div class="page-head"><div><h1>TeX 模板</h1><p>保存排版样例，在书库中为已保留的工程应用模板。</p></div><div class="row"><button data-template-import>导入 .tex / ZIP</button><button class="primary" data-template-new>新建模板</button></div></div>
    ${items.length ? `<div class="template-grid">${items.map(t => `<article class="template-card"><h2>${e(t.name)}</h2><p>${e(t.description || "暂无应用说明")}</p><small>${t.file_count} 个文件 · ${(t.size_bytes / 1024).toFixed(1)} KB · ${e(t.entrypoint)}</small><div class="row"><button data-template-edit="${e(t.id)}">查看 / 编辑</button><a class="button" href="/api/templates/${e(t.id)}/export">导出 ZIP</a><button data-template-delete="${e(t.id)}" data-template-name="${e(t.name)}">删除</button></div></article>`).join("")}</div>` : `<section class="empty"><h2>还没有模板</h2><p>粘贴 TeX 样例，或导入包含样式、字体和图片的模板 ZIP。</p><button data-template-new>创建第一个模板</button></section>`}`;
}

export const dialogs = `<dialog id="template-editor"><form id="template-form"><div class="row"><h2 id="template-editor-title">TeX 模板</h2><span class="spacer"></span><button type="button" data-template-close="template-editor" aria-label="关闭模板编辑器">×</button></div><div class="fields"><label>模板名称<input name="name" maxlength="150" required></label><label>主 TeX 文件<select name="entrypoint"></select></label></div><label>应用说明<textarea name="description" rows="2" maxlength="4000" placeholder="例如：双面排版、较宽页边距、章节标题使用无衬线字体"></textarea></label><div class="template-editor-files"><div><label>文件<select id="template-file"></select></label><div class="row"><input id="template-new-path" placeholder="如 custom.sty" aria-label="新文件路径"><button type="button" data-template-add-file>添加</button><button type="button" data-template-remove-file>移除</button></div></div><p id="template-binary-note" class="hint" hidden>此文件是图片或字体等资源，保留原文件，可通过 ZIP 导入替换。</p><textarea id="template-code" aria-label="TeX 模板代码" rows="18" spellcheck="false"></textarea></div><p id="template-error" class="error"></p><div class="row end"><button type="button" data-template-close="template-editor">取消</button><button type="submit" class="primary">保存模板</button></div></form></dialog>
<dialog id="template-delete-dialog"><form id="template-delete-form"><h2>删除模板</h2><p id="template-delete-description"></p><p class="hint">已经创建的重排任务保留其模板副本。</p><p id="template-delete-error" class="error"></p><div class="row end"><button type="button" data-template-close="template-delete-dialog">取消</button><button type="submit" class="primary">删除</button></div></form></dialog>
<dialog id="template-apply-dialog"><form id="template-apply-form"><div class="row"><h2>用模板重排</h2><span class="spacer"></span><button type="button" data-template-close="template-apply-dialog" aria-label="关闭模板选择">×</button></div><p id="template-apply-book"></p><label>选择模板<select name="template_id" required></select></label><p id="template-apply-description" class="hint"></p><p id="template-apply-model" class="hint"></p><p class="hint">Agent 将外观样式接入原工程副本，冲突仅在模板样式层内调整。原文档类、正文和结构保留，编译成功后保存新版本。</p><p id="template-apply-error" class="error"></p><div class="row end"><button type="button" data-template-close="template-apply-dialog">取消</button><button type="submit" class="primary">开始重排</button></div></form></dialog>
<input id="template-upload" type="file" accept=".tex,.zip" hidden>`;

function stashFile() {
  const f = editor?.files[selectedFile];
  if (f && f.encoding !== "base64") f.content = $("#template-code").value;
}
function showFiles() {
  $("#template-file").innerHTML = editor.files.map((f,i) => `<option value="${i}">${e(f.path)}</option>`).join("");
  $("#template-file").value = selectedFile;
  const field = $("#template-form").elements.entrypoint;
  const entry = field.value || editor.entrypoint;
  field.innerHTML = editor.files.filter(f => f.path.toLowerCase().endsWith(".tex")).map(f => `<option>${e(f.path)}</option>`).join("");
  if (editor.files.some(f => f.path === entry)) field.value = entry;
  const f = editor.files[selectedFile], binary = f?.encoding === "base64";
  $("#template-code").value = binary ? "" : f?.content || "";
  $("#template-code").disabled = !f || binary;
  $("#template-binary-note").hidden = !binary;
}
async function openEditor(id) {
  editor = id ? await api(`/api/templates/${id}`) : { name: "", description: "", entrypoint: "main.tex", files: [{path:"main.tex", content:sample, encoding:"utf8"}] };
  selectedFile = Math.max(0, editor.files.findIndex(f => f.path === editor.entrypoint));
  const form = $("#template-form"); form.reset();
  form.elements.name.value = editor.name;
  form.elements.description.value = editor.description;
  form.elements.entrypoint.innerHTML = "";
  $("#template-error").textContent = "";
  $("#template-editor-title").textContent = id ? "编辑模板" : "新建模板";
  showFiles(); $("#template-editor").showModal();
}
export async function openApply(book) {
  const [items, settings] = await Promise.all([api("/api/templates"), api("/api/settings")]);
  if (!items.length) { callbacks.toast("请先保存一个 TeX 模板"); return callbacks.navigate("templates"); }
  application = {book, items, operation_id: crypto.randomUUID()};
  const form = $("#template-apply-form");
  form.elements.template_id.innerHTML = items.map(t => `<option value="${e(t.id)}">${e(t.name)}</option>`).join("");
  $("#template-apply-book").textContent = book.title;
  const binding = settings.stage_models.template_apply;
  $("#template-apply-model").textContent = `模板迁移模型：${binding.connection_id} / ${binding.model_id || "连接默认模型"} · 思考 ${binding.reasoning_effort}。在设置页统一调整。`;
  $("#template-apply-description").textContent = items[0].description;
  $("#template-apply-error").textContent = "";
  $("#template-apply-dialog").showModal();
}
export function init(options) {
  callbacks = options;
  document.body.insertAdjacentHTML("beforeend", dialogs);
  document.addEventListener("click", async event => {
    const b = event.target.closest("button"); if (!b) return;
    try {
      if (b.dataset.templateClose) return $("#" + b.dataset.templateClose).close();
      if (b.hasAttribute("data-template-new")) return await openEditor();
      if (b.hasAttribute("data-template-import")) return $("#template-upload").click();
      if (b.dataset.templateEdit) return await openEditor(b.dataset.templateEdit);
      if (b.dataset.templateDelete) {
        $("#template-delete-form").dataset.id = b.dataset.templateDelete;
        $("#template-delete-description").textContent = `删除“${b.dataset.templateName}”？`;
        $("#template-delete-error").textContent = "";
        return $("#template-delete-dialog").showModal();
      }
      if (b.hasAttribute("data-template-add-file")) {
        const name = $("#template-new-path").value.trim();
        if (!name || editor.files.some(f => f.path.toLowerCase() === name.toLowerCase())) throw Error("请输入不重复的文件路径");
        stashFile(); editor.files.push({path:name,content:"",encoding:"utf8"}); selectedFile=editor.files.length-1;
        $("#template-new-path").value=""; showFiles();
      }
      if (b.hasAttribute("data-template-remove-file")) {
        if (editor.files.length < 2) throw Error("模板至少保留一个文件");
        stashFile(); editor.files.splice(selectedFile,1); selectedFile=Math.min(selectedFile,editor.files.length-1); showFiles();
      }
    } catch (error) { callbacks.toast(error.message); }
  });
  document.addEventListener("change", async event => {
    if (event.target.id === "template-file") { stashFile(); selectedFile=Number(event.target.value); showFiles(); }
    if (event.target.closest("#template-apply-form") && event.target.name === "template_id") {
      $("#template-apply-description").textContent = application.items.find(t => t.id === event.target.value)?.description || "";
    }
    if (event.target.id !== "template-upload" || !event.target.files.length) return;
    const body=new FormData();body.append("file",event.target.files[0]); event.target.disabled=true;
    try {
      const saved=await api("/api/templates/import",{method:"POST",body});
      await callbacks.navigate("templates");await openEditor(saved.id);callbacks.toast("模板已导入");
    } catch(error){callbacks.toast(error.message);} finally{event.target.disabled=false;event.target.value="";}
  });
  document.addEventListener("submit", async event => {
    const form=event.target;
    if (!["template-form","template-delete-form","template-apply-form"].includes(form.id)) return;
    event.preventDefault(); const button=form.querySelector('[type="submit"]');button.disabled=true;
    const errorId={"template-form":"template-error","template-delete-form":"template-delete-error","template-apply-form":"template-apply-error"}[form.id];
    $("#"+errorId).textContent="";
    try {
      if (form.id === "template-form") {
        stashFile();const body={name:form.elements.name.value,description:form.elements.description.value,entrypoint:form.elements.entrypoint.value,files:editor.files};
        if(editor.id)body.revision=editor.revision;
        await api(editor.id?`/api/templates/${editor.id}`:"/api/templates",{method:editor.id?"PUT":"POST",body});
        $("#template-editor").close();await callbacks.navigate("templates");callbacks.toast("模板已保存");
      } else if(form.id === "template-delete-form") {
        await api(`/api/templates/${form.dataset.id}`,{method:"DELETE"});$("#template-delete-dialog").close();await callbacks.navigate("templates");callbacks.toast("模板已删除");
      } else {
        const result=await api(`/api/books/${application.book.id}/apply-template`,{method:"POST",body:{template_id:form.elements.template_id.value,result_id:application.book.result.id,operation_id:application.operation_id}});
        $("#template-apply-dialog").close();await callbacks.navigate("run",result.id);callbacks.toast("模板重排任务已创建");
      }
    } catch(error){$("#"+errorId).textContent=error.message;} finally{button.disabled=false;}
  });
}
