import { api, escape as e } from "./api.js";
let callbacks;
let timer;
let generation = 0;
const $ = selector => document.querySelector(selector);

function rows(data) {
  const eligible = data.books.filter(b => b.result);
  const tasks = data.runs.filter(r => r.kind === "tex_structure");
  return `<div class="page-head"><div><h1>TeX 文件结构</h1><p>在已保存工程的副本中，用 Agent 按章、节整理正文。原工程和书库当前版本不变。</p></div></div>
    <section class="run-status"><h2>创建整理任务</h2>
    <p class="hint">Agent 会读取最终 TeX 工程并整理文件；程序核对正文逐字一致并重新编译。完成后可预览 PDF、下载或选择保存到书库；保存前不改变书库当前版本。</p>
    ${eligible.length ? `<form id="structure-form"><label>选择已保存的文献<select name="structure_book_id">${eligible.map(b => `<option value="${e(b.id)}">${e(b.title)}</option>`).join("")}</select></label>
      <p id="structure-model" class="hint"></p><p id="structure-error" class="error" role="alert"></p><button type="submit" class="primary">开始整理</button></form>` : `<p class="hint">请先完成转换并把结果保存到书库。</p>`}</section>
    <section><h2>整理记录</h2>${tasks.length ? `<div class="table-wrap"><table><thead><tr><th>文献</th><th>状态</th><th>操作</th></tr></thead><tbody>${tasks.map(t => `<tr><td>${e(t.title)}<small>${e(t.id.slice(0, 8))}</small></td><td>${e(t.state)}${t.error ? `<small class="error">${e(t.error.message)}</small>` : ""}</td><td class="row">${t.state === "COMPLETED" ? `<a class="button" href="/api/runs/${e(t.id)}/pdf" data-pdf-preview="整理后的 PDF" data-pdf-title="${e(t.title)}">预览 PDF</a><a class="button" href="/api/runs/${e(t.id)}/structured-export">下载分层工程</a><button type="button" data-structure-save="${e(t.id)}" ${data.books.find(b => b.id === t.book_id)?.result?.run_id === t.id ? "disabled" : ""}>${data.books.find(b => b.id === t.book_id)?.result?.run_id === t.id ? "已保存到书库" : "保存到书库"}</button>` : t.state !== "RUNNING" ? `<button data-structure-resume="${e(t.id)}" data-revision="${t.revision}">继续整理</button>` : "正在整理…"}</td></tr>`).join("")}</tbody></table></div>` : `<p class="hint">还没有整理任务。</p>`}</section>`;
}

export function leave() { ++generation; clearTimeout(timer); }
export async function mount(data) {
  leave();
  const current = generation;
  $("#main").innerHTML = rows(data);
  if ($("#structure-form")) {
    try {
      const settings = await api("/api/settings");
      if (generation !== current || !$("#structure-model")) return;
      const model = settings.stage_models?.finish;
      $("#structure-model").textContent = model ? `使用最终编译模型：${model.connection_id} / ${model.model_id || "连接默认模型"} · 思考 ${model.reasoning_effort}` : "使用当前最终编译模型";
    } catch (error) { if ($("#structure-model")) $("#structure-model").textContent = error.message; }
  }
  if (data.runs.some(r => r.kind === "tex_structure" && r.state === "RUNNING")) {
    timer = setTimeout(async () => {
      if (current !== generation) return;
      try { await callbacks.navigate("structure"); } catch (error) { callbacks.toast(error.message); }
    }, 4000);
  }
}

export function init(options) {
  callbacks = options;
  document.addEventListener("submit", async event => {
    if (event.target.id !== "structure-form") return;
    event.preventDefault();
    const form = event.target, button = form.querySelector("button[type=submit]");
    const book = callbacks.getData().books.find(b => b.id === form.elements.structure_book_id.value);
    button.disabled = true;
    $("#structure-error").textContent = "";
    try {
      const result = await api(`/api/books/${book.id}/organize-tex`, {method:"POST", body:{result_id:book.result.id, operation_id:crypto.randomUUID()}});
      await api(`/api/runs/${result.id}/start`, {method:"POST", body:{revision:result.revision, operation_id:crypto.randomUUID()}});
      callbacks.toast("整理任务已启动，原工程保持不变");
      await callbacks.navigate("structure");
    } catch (error) { $("#structure-error").textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.addEventListener("click", async event => {
    const save = event.target.closest("[data-structure-save]");
    if (save) {
      save.disabled = true;
      try {
        await api(`/api/runs/${save.dataset.structureSave}/retain`, {method:"POST", body:{}});
        callbacks.toast("分层工程已保存到书库，旧版文件保留");
        await callbacks.navigate("structure");
      } catch (error) { callbacks.toast(error.message); save.disabled = false; }
      return;
    }
    const b = event.target.closest("[data-structure-resume]");
    if (!b) return;
    b.disabled = true;
    try {
      await api(`/api/runs/${b.dataset.structureResume}/start`, {method:"POST", body:{revision:Number(b.dataset.revision),operation_id:crypto.randomUUID()}});
      await callbacks.navigate("structure");
    } catch (error) { callbacks.toast(error.message); b.disabled = false; }
  });
}
