import { api, escape as e } from "./api.js";
import { badge, stateNames } from "./views.js";

const $ = (s) => document.querySelector(s);
const names = { PENDING: "待检查", RUNNING: "检查中", PAUSED: "等待继续", PASSED: "已确认", FIXED: "已修复", DEFERRED: "待确认", FAILED: "检查失败" };
let callbacks, generation = 0, timer, books = [], jobs = [], book, inventory, job, selectedImage;
const encode = encodeURIComponent;

export function leave() { generation++; clearTimeout(timer); }

export async function mount(data, id) {
  leave();
  books = data.books.filter(b => b.result);
  jobs = data.runs.filter(r => r.kind === "image_repair");
  book = null; inventory = null; job = null; selectedImage = null;
  $("#main").innerHTML = `<div class="page-head"><div><h1>图片修复</h1><p>检查已保存书籍的图片，在独立副本中修复并生成新版，不重跑正文转换。</p></div></div>
    <section class="image-controls"><label>已保存书籍<select id="image-book"><option value="">选择书籍…</option>${books.map(b => `<option value="${e(b.id)}">${e(b.title)}</option>`).join("")}</select></label>
    <button id="image-current">读取书库当前结果</button><label>修图任务<select id="image-job"><option value="">新建修图任务</option>${jobs.map(r => jobOption(r)).join("")}</select></label></section>
    <p id="image-error" class="error" role="alert"></p><div id="image-workspace"><div class="empty">${books.length ? "选择一本已保存书籍，查看可修复的图片。" : "还没有已保存的转换结果。请先完成转换并保留到书库。"}</div></div><section id="image-detail" hidden></section>`;
  if (id && !books.some(b => b.id === id)) await loadJob(id);
  else if (id || books.length) { $("#image-book").value = id || books[0].id; await loadBook(); }
}

function jobOption(r) {
  return `<option value="${e(r.id)}">${e(r.title)} · ${e(stateNames[r.state] || r.state)} · ${e(new Date(r.created_at * 1000).toLocaleString())}</option>`;
}

function imageUrl(a, version = "after") {
  if (job) return `/api/runs/${job.run.id}/images/${encode(a.id)}?version=${version}&state=${encode(a.state || "PENDING")}`;
  return `/api/books/${book.id}/images/${encode(a.id)}?result_id=${encode(inventory.result_id)}`;
}

function cards(images, selecting) {
  return `<div class="image-grid">${images.map(a => `<article class="image-card" data-image-card="${e(a.id)}">
    <div class="row">${selecting ? `<label class="image-select"><input type="checkbox" name="asset_ids" value="${e(a.id)}" ${a.repairable ? "checked" : "disabled"}>${a.page ? `原书第 ${a.page} 页` : "来源待确认"}</label>` : `<span>原书第 ${a.page} 页</span>`}<span data-image-state>${selecting ? "" : e(names[a.state] || a.state)}</span></div>
    <button type="button" class="image-preview" data-inspect-image="${e(a.id)}" aria-label="查看图片 ${e(a.id)}">${a.available ? `<img src="${imageUrl(a)}" loading="lazy" decoding="async" alt="${e(a.id)} 裁图">` : '<span>裁图文件缺失</span>'}</button>
    <small class="image-id">${e(a.id)}</small><p data-image-reason>${e(a.reason || "")}</p></article>`).join("")}</div>`;
}

async function loadBook() {
  const version = ++generation;
  clearTimeout(timer); job = null; selectedImage = null;
  $("#image-job").value = ""; $("#image-detail").hidden = true; $("#image-error").textContent = "";
  book = books.find(b => b.id === $("#image-book").value);
  if (!book) { $("#image-workspace").innerHTML = '<div class="empty">请选择已保存书籍。</div>'; return; }
  $("#image-workspace").innerHTML = '<p class="hint">正在读取图片来源记录…</p>';
  const result = await api(`/api/books/${book.id}/images`);
  if (version !== generation) return;
  inventory = result;
  $("#image-workspace").innerHTML = `<form id="image-start-form"><div class="row image-actions"><h2>${e(book.title)}</h2><span class="spacer"></span><button type="button" id="image-select-all">全选可修图片</button><button type="button" id="image-select-none">取消全选</button><button type="submit" class="primary" ${result.images.some(a => a.repairable) ? "" : "disabled"}>检查并修复所选图片</button></div>
    <p class="hint">共 ${result.images.length} 张登记图片，<span id="image-selected-count"></span>。使用“分批视觉转换”的模型配置。逐图检查，修复后复核，再集中编译；不能确认的保留原图。</p>
    <p class="hint">旧版统一放大的图片尺寸会一并校正；不调整布局，也不扫描未登记的漏图。</p>
    ${result.images.length ? cards(result.images, true) : '<div class="empty">此结果没有可定位的图片标记。</div>'}</form>`;
  updateSelection();
}

function updateSelection() {
  const form = $("#image-start-form");
  if (!form) return;
  const count = form.querySelectorAll('[name="asset_ids"]:checked').length;
  $("#image-selected-count").textContent = `已选 ${count} 张`;
  form.querySelector('[type="submit"]').disabled = !count;
}

async function loadJob(id) {
  const version = ++generation;
  clearTimeout(timer); selectedImage = null; $("#image-detail").hidden = true;
  $("#image-error").textContent = "";
  const result = await api(`/api/runs/${id}/images`);
  if (version !== generation) return;
  job = result;
  book = books.find(b => b.id === job.run.book_id);
  $("#image-book").value = job.run.book_id;
  if (!Array.from($("#image-job").options).some(o => o.value === id)) $("#image-job").insertAdjacentHTML("beforeend", jobOption(job.run));
  $("#image-job").value = id;
  $("#image-workspace").innerHTML = `<section class="run-status"><div class="row image-actions"><h2>${e(job.run.title)}</h2><span id="image-run-badge"></span><span class="spacer"></span><button id="image-resume" class="primary">继续修图</button><button id="image-retry" title="只在上次请求结果未确认时使用；上游可能重复计费">重试未返回请求</button><button id="image-pause">暂停</button><a id="image-pdf" class="button" href="/api/runs/${id}/pdf" target="_blank" rel="noopener">修图版 PDF ↗</a><a class="button" href="/api/runs/${id}/export">下载工程</a></div><p id="image-progress" class="hint"></p><p id="image-run-message" class="error" role="alert"></p><p class="hint">原结果保留。单张待确认不阻塞其他图片；已完成只表示本轮处理与编译完成，不表示所有图片都已确认。</p></section>${cards(job.images, false)}`;
  updateJob(); schedule();
}

function updateJob() {
  const r = job.run;
  const option = Array.from($("#image-job").options).find(o => o.value === r.id);
  if (option) option.textContent = `${r.title} · ${stateNames[r.state] || r.state} · ${new Date(r.created_at * 1000).toLocaleString()}`;
  $("#image-run-badge").innerHTML = badge(r.state);
  const counts = {};
  job.images.forEach(a => { counts[a.state] = (counts[a.state] || 0) + 1; });
  $("#image-progress").textContent = `${Object.entries(counts).map(([s, n]) => `${names[s] || s} ${n}`).join(" · ")} · LLM ${r.usage?.llm || 0} 次${job.compile ? ` · 编译${job.compile.status === "PASSED" ? "通过" : "未通过"}` : ""}`;
  $("#image-resume").hidden = ["RUNNING", "COMPLETED"].includes(r.state);
  $("#image-retry").hidden = r.state !== "PAUSED" || r.error?.code !== "RESULT_UNKNOWN";
  $("#image-pause").hidden = r.state !== "RUNNING";
  $("#image-pdf").hidden = r.state !== "COMPLETED";
  $("#image-run-message").textContent = (r.error || r.retention_error)?.message || "";
  for (const card of document.querySelectorAll("[data-image-card]")) {
    const a = job.images.find(row => row.id === card.dataset.imageCard);
    card.querySelector("[data-image-state]").textContent = names[a.state] || a.state;
    card.querySelector("[data-image-reason]").textContent = a.reason || "";
    if (card.dataset.state !== a.state) {
      card.dataset.state = a.state;
      if (a.available) card.querySelector(".image-preview").innerHTML = `<img src="${imageUrl(a)}" loading="lazy" decoding="async" alt="${e(a.id)} 裁图">`;
      if (selectedImage === a.id) inspect(a.id);
    }
  }
}

function schedule() {
  clearTimeout(timer);
  if (!job || job.run.state !== "RUNNING") return;
  const version = generation, id = job.run.id;
  timer = setTimeout(async () => {
    try {
      if (document.hidden) { schedule(); return; }
      const result = await api(`/api/runs/${id}/images`);
      if (version !== generation) return;
      job = result; updateJob();
    } catch (error) { if (version === generation) $("#image-error").textContent = error.message; }
    finally { if (version === generation) schedule(); }
  }, 2000);
}

function inspect(id) {
  const a = (job?.images || inventory?.images || []).find(row => row.id === id);
  if (!a) return;
  selectedImage = id;
  const detail = $("#image-detail"); detail.hidden = false;
  detail.innerHTML = `<h2>图片对照 · ${e(id)}</h2><p>${e(a.reason || "")}</p><div class="image-comparison">
    ${a.page ? `<figure><figcaption>原书第 ${a.page} 页</figcaption><a href="/api/books/${book?.id || job.run.book_id}/image?page=${a.page}&dpi=150" target="_blank" rel="noopener"><img src="/api/books/${book?.id || job.run.book_id}/image?page=${a.page}&dpi=110" alt="原书第 ${a.page} 页" decoding="async"></a></figure>` : ""}
    ${job && a.before_available ? `<figure><figcaption>修复前</figcaption><img src="${imageUrl(a, "before")}" alt="修复前裁图"></figure>` : ""}
    <figure><figcaption>${job ? "当前裁图" : "已保存裁图"}</figcaption>${a.available ? `<img src="${imageUrl(a)}" alt="当前裁图">` : "图片缺失"}</figure></div><details><summary>定位信息与附近 TeX</summary><p>裁剪框：${e(JSON.stringify(a.bbox))}</p><pre>${e(a.context || "")}</pre></details>`;
}

export function init(handlers) {
  callbacks = handlers;
  document.addEventListener("change", async event => {
    try {
      if (event.target.id === "image-book") await loadBook();
      if (event.target.id === "image-job") { if (event.target.value) await loadJob(event.target.value); else await loadBook(); }
      if (event.target.name === "asset_ids") updateSelection();
    } catch (error) { if ($("#image-error")) $("#image-error").textContent = error.message; }
  });
  document.addEventListener("click", async event => {
    const button = event.target.closest("button");
    if (!button) return;
    if (!button.dataset.inspectImage && !button.id.startsWith("image-")) return;
    try {
      if (button.dataset.inspectImage) { inspect(button.dataset.inspectImage); $("#image-detail").scrollIntoView({ behavior: "smooth", block: "start" }); }
      if (button.id === "image-current") await loadBook();
      if (["image-select-all", "image-select-none"].includes(button.id)) {
        document.querySelectorAll('[name="asset_ids"]:not(:disabled)').forEach(input => input.checked = button.id === "image-select-all"); updateSelection();
      }
      if (["image-pause", "image-resume", "image-retry"].includes(button.id)) {
        button.disabled = true;
        const id = job.run.id, version = generation;
        await api(`/api/runs/${id}/${button.id === "image-pause" ? "pause" : "start"}`, { method: "POST", body: {
          revision: job.run.revision, operation_id: crypto.randomUUID(), ...(button.id === "image-retry" ? { retry_unknown: true } : {}) } });
        if (version !== generation) return;
        job = await api(`/api/runs/${id}/images`);
        if (version === generation) { updateJob(); schedule(); }
      }
    } catch (error) { if ($("#image-error")) $("#image-error").textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.addEventListener("submit", async event => {
    if (event.target.id !== "image-start-form") return;
    event.preventDefault();
    const form = event.target, button = form.querySelector('[type="submit"]'), version = generation;
    const asset_ids = Array.from(form.querySelectorAll('[name="asset_ids"]:checked')).map(el => el.value);
    if (!asset_ids.length) return;
    button.disabled = true; $("#image-error").textContent = "";
    // Retain the operation ID on network errors so a retry cannot duplicate the job.
    const request = JSON.stringify({ result_id: inventory.result_id, asset_ids });
    if (form.dataset.request !== request) { form.dataset.request = request; form.dataset.operation = crypto.randomUUID(); }
    try {
      const result = await api(`/api/books/${book.id}/repair-images`, { method: "POST", body: { ...JSON.parse(request), operation_id: form.dataset.operation } });
      if (version === generation) await loadJob(result.id);
      callbacks.toast("独立修图任务已创建，原书结果保留");
    } catch (error) { if (version === generation) $("#image-error").textContent = error.message; }
    finally { button.disabled = false; }
  });
}
