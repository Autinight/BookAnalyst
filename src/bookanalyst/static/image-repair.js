import { api, escape as e } from "./api.js";
import { badge, stateNames } from "./views.js";

const $ = (s) => document.querySelector(s);
const names = { PENDING: "待检查", RUNNING: "检查中", PAUSED: "等待继续", PASSED: "已确认", FIXED: "已修复", DEFERRED: "待确认", FAILED: "检查失败" };
let callbacks, generation = 0, timer, books = [], jobs = [], book, inventory, job, selectedImage;
const encode = encodeURIComponent;
const imageSize = a => a.width_ratio == null ? "插入宽度：沿用原设置" : `插入宽度：可用正文宽度的 ${Number((a.width_ratio * 100).toFixed(2))}%（等比，高度上限 80%）`;
const imageState = a => `${names[a.state] || a.state}${a.attempt ? ` · 第 ${a.attempt}/${a.max_attempts} 次` : ""}`;

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
  if (job) return `/api/runs/${job.run.id}/images/${encode(a.id)}?version=${version}&v=${encode(version === "before" ? "original" : a.image_version || "original")}`;
  return `/api/books/${book.id}/images/${encode(a.id)}?result_id=${encode(inventory.result_id)}`;
}

function modelDescription(binding) {
  return `${binding?.connection_id || "未设置"} / ${binding?.model_id || "连接默认模型"} · 思考 ${binding?.reasoning_effort || "medium"}`;
}

function cards(images, selecting) {
  return `<div class="image-grid">${images.map(a => `<article class="image-card" data-image-card="${e(a.id)}" data-image-url="${e(a.available ? imageUrl(a) : "")}">
    <div class="row">${selecting ? `<label class="image-select"><input type="checkbox" name="asset_ids" value="${e(a.id)}" ${a.repairable ? "checked" : "disabled"}>${a.page ? `原书第 ${a.page} 页` : "来源待确认"}</label>` : `<span>原书第 ${a.page} 页</span>`}<span data-image-state>${selecting ? "" : e(imageState(a))}</span></div>
    ${selecting ? "" : `<label class="image-select" data-rerun-selection hidden><input type="checkbox" name="rerun_asset_ids" form="image-rerun-form" value="${e(a.id)}" disabled>再次修复这张图片</label>`}
    <button type="button" class="image-preview" data-inspect-image="${e(a.id)}" aria-label="查看图片 ${e(a.id)}">${a.available ? `<img src="${imageUrl(a)}" loading="lazy" decoding="async" alt="${e(a.id)} 裁图">` : '<span>裁图文件缺失</span>'}</button>
    <small class="image-id">${e(a.id)}</small><small data-image-width>${e(imageSize(a))}</small><p data-image-reason>${e(a.reason || "")}</p></article>`).join("")}</div>`;
}

async function loadBook() {
  const version = ++generation;
  clearTimeout(timer); job = null; selectedImage = null;
  $("#image-job").value = ""; $("#image-detail").hidden = true; $("#image-error").textContent = "";
  book = books.find(b => b.id === $("#image-book").value);
  if (!book) { $("#image-workspace").innerHTML = '<div class="empty">请选择已保存书籍。</div>'; return; }
  $("#image-workspace").innerHTML = '<p class="hint">正在读取图片来源记录…</p>';
  const [result, settings] = await Promise.all([api(`/api/books/${book.id}/images`), api("/api/settings")]);
  if (version !== generation) return;
  inventory = result;
  $("#image-workspace").innerHTML = `<form id="image-start-form"><div class="row image-actions"><h2>${e(book.title)}</h2><span class="spacer"></span><button type="button" id="image-select-all">全选可修图片</button><button type="button" id="image-select-none">取消全选</button><button type="submit" class="primary" ${result.images.some(a => a.repairable) ? "" : "disabled"}>检查并修复所选图片</button></div>
    <div class="row image-actions"><label>本次修图并发<input name="image_concurrency" type="number" min="1" step="1" required value="${settings.image_repair_concurrency}"></label><button type="button" id="image-model-settings">设置修图模型</button></div>
    <p class="hint">图片修复模型：${e(modelDescription(settings.stage_models.image_repair))}。检查与裁剪后复核均使用此模型。</p>
    <p class="hint">共 ${result.images.length} 张登记图片，<span id="image-selected-count"></span>。模型和并发独立于正文转换。逐图检查，对比新旧裁图选优，再集中编译；待确认自动再次修复，每张最多尝试 3 次（含首次），保留选中的版本。</p>
    <p class="hint">模型可保留裁图并调整插入宽度，也可从原页重新裁剪。保持长宽比；不调整整页布局，也不扫描未登记的漏图。完成后不自动更新书库，由你预览并手动保存。</p>
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

function updateRerunSelection() {
  const form = $("#image-rerun-form");
  if (!form) return;
  const special = form.elements.repair_mode.value === "special";
  $("#image-special-notes").hidden = !special;
  form.elements.image_concurrency.disabled = special;
  const byId = new Map(job.images.map(a => [a.id, a]));
  for (const checkbox of document.querySelectorAll('[name="rerun_asset_ids"]')) {
    const a = byId.get(checkbox.value);
    const selectable = job.run.state === "COMPLETED" && (special || (a.state === "DEFERRED" && a.repairable));
    checkbox.closest('[data-rerun-selection]').hidden = !selectable;
    checkbox.disabled = !selectable;
    if (!selectable) checkbox.checked = false;
  }
  $("#image-select-deferred").disabled = !job.images.some(a => a.state === "DEFERRED" && (special || a.repairable));
  const count = document.querySelectorAll('[name="rerun_asset_ids"]:checked:not(:disabled)').length;
  $("#image-rerun-count").textContent = `已选 ${count} 张${special ? "" : "待确认"}图片`;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = !count || form.dataset.submitting === "true";
  if (form.dataset.submitting !== "true") submit.textContent = special ? "特殊修复所选图片" : "再次修复所选图片";
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
  $("#image-workspace").innerHTML = `<section class="run-status"><div class="row image-actions"><h2>${e(job.run.title)}</h2><span id="image-run-badge"></span><span class="spacer"></span><button id="image-resume" class="primary">继续修图</button><button id="image-retry" title="只在上次请求结果未确认时使用；上游可能重复计费">重试未返回请求</button><button id="image-pause">暂停</button><button id="image-save" class="primary" hidden>保存到书库</button><a id="image-pdf" class="button" href="/api/runs/${id}/pdf" target="_blank" rel="noopener">修图版 PDF ↗</a><a class="button" href="/api/runs/${id}/export">下载工程</a></div>
    <p id="image-save-note" class="hint" role="status"></p>
    <p id="image-model-summary" class="hint"></p><div id="image-config-controls" class="row image-actions"><label>修图并发<input id="image-concurrency" type="number" min="1" step="1" required value="${job.run.pending_llm_concurrency ?? job.run.concurrency}"></label><button type="button" id="image-model-settings">设置修图模型</button><button type="button" id="image-update-config">更新模型与并发</button><small>后续请求生效，已发出的请求继续执行，已完成的图片不重查。</small></div>
    <p id="image-progress" class="hint"></p><p id="image-agent-activity" class="hint" role="status"></p><p id="image-run-message" class="error" role="alert"></p><p id="image-repair-policy" class="hint"></p></section>
    <form id="image-rerun-form" hidden><div class="row image-actions"><label>修复方式<select name="repair_mode" id="image-repair-mode"><option value="standard">普通再次修复</option><option value="special">特殊修复（Agent）</option></select></label><button type="button" id="image-select-deferred">全选待确认</button><button type="button" id="image-clear-deferred">取消全选</button><span id="image-rerun-count"></span><label>本次修图并发<input name="image_concurrency" type="number" min="1" step="1" required value="${job.run.concurrency}"></label><button type="submit" class="primary" disabled>再次修复所选图片</button></div>
    <div id="image-special-notes" hidden><p class="hint">自动将待确认或失败原因、上一轮裁剪与复核记录交给 Agent，使用图片修复模型和结尾编译员相同的工具权限，读取原书、查看图片、运行命令、修改选中图片及相关 TeX，并编译检查。一个 Agent 处理本次选中目标，可暂停继续；也可勾选检查失败或已处理但仍有问题的图片。</p></div>
    <p class="hint">从本任务当前结果创建独立副本，使用设置页已保存的修图模型，无需先保存到书库。普通修复每张最多再尝试 3 次；特殊修复由 Agent 在同一会话中处理，结果需预览后保存到书库。</p></form>${cards(job.images, false)}`;
  updateJob(); schedule();
}

function updateJob() {
  const r = job.run;
  const binding = r.stage_models?.image_repair || r.stage_models?.convert || r.model;
  const special = r.image_repair?.mode === "special";
  $("#image-model-summary").textContent = `${special ? "特殊修复 Agent" : "本任务修图模型"}：${modelDescription(binding)} · ${special ? "同一会话处理所选图片" : `并发 ${r.concurrency}`}${r.model_refresh_pending ? "；模型/并发待更新，下次派发前生效" : ""}`;
  $("#image-agent-activity").textContent = special ? job.agent?.activity || "" : "";
  $("#image-repair-policy").textContent = special
    ? "特殊修复使用结尾编译员的工具权限处理所选图片及相关 TeX。原任务和书库结果保留；完成只表示会话与编译完成，仍需查看逐图结论及 PDF。"
    : "原结果保留。新旧裁图由模型比较选优，待确认自动再次修复，每张最多尝试 3 次（含首次）；达到上限保留选中的版本和原因。已完成只表示处理与编译完成，不表示所有图片都已确认。";
  $("#image-config-controls").hidden = r.state === "COMPLETED";
  $("#image-rerun-form").hidden = r.state !== "COMPLETED";
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
  const saved = book?.result?.run_id === r.id && !r.retention_error;
  $("#image-save").hidden = r.state !== "COMPLETED";
  $("#image-save").disabled = saved;
  $("#image-save").textContent = saved ? "已保存到书库" : "保存到书库";
  $("#image-save-note").textContent = saved
    ? "这份修图结果已保存为书籍当前版本，旧版文件保留。"
    : "修图结果只保留在任务中，不自动更新书库。编译完成后可预览，再点击“保存到书库”设为书籍当前版本；旧版文件保留。";
  $("#image-run-message").textContent = (r.error || r.retention_error)?.message || "";
  const byId = new Map(job.images.map(a => [a.id, a]));
  for (const card of document.querySelectorAll("[data-image-card]")) {
    const a = byId.get(card.dataset.imageCard);
    card.querySelector("[data-image-state]").textContent = imageState(a);
    card.querySelector("[data-image-width]").textContent = imageSize(a);
    card.querySelector("[data-image-reason]").textContent = a.reason || "";
    const url = a.available ? imageUrl(a) : "";
    if (card.dataset.imageUrl !== url) {
      card.dataset.imageUrl = url;
      card.querySelector(".image-preview").innerHTML = url ? `<img src="${url}" loading="lazy" decoding="async" alt="${e(a.id)} 裁图">` : '<span>裁图文件缺失</span>';
      if (selectedImage === a.id) inspect(a.id);
    } else if (selectedImage === a.id) {
      $("[data-image-detail-reason]").textContent = a.reason || "";
      $("[data-image-detail-width]").textContent = imageSize(a);
    }
  }
  updateRerunSelection();
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
  detail.innerHTML = `<h2>图片对照 · ${e(id)}</h2><p data-image-detail-reason>${e(a.reason || "")}</p><p data-image-detail-width>${e(imageSize(a))}</p><p class="hint">这里展示裁图内容；实际插入大小请查看修图版 PDF。</p><div class="image-comparison">
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
      if (event.target.name === "rerun_asset_ids") updateRerunSelection();
      if (event.target.id === "image-repair-mode") updateRerunSelection();
    } catch (error) { if ($("#image-error")) $("#image-error").textContent = error.message; }
  });
  document.addEventListener("click", async event => {
    const button = event.target.closest("button");
    if (!button) return;
    if (!button.dataset.inspectImage && !button.id.startsWith("image-")) return;
    try {
      if (button.dataset.inspectImage) { inspect(button.dataset.inspectImage); $("#image-detail").scrollIntoView({ behavior: "smooth", block: "start" }); }
      if (button.id === "image-current") await loadBook();
      if (button.id === "image-model-settings") return await callbacks.navigate("settings");
      if (button.id === "image-save") {
        const id = job.run.id, version = generation;
        button.disabled = true;
        $("#image-error").textContent = "";
        const result = await api(`/api/runs/${id}/retain`, { method: "POST", body: {} });
        if (version !== generation) return;
        if (book) book.result = result;
        job.run.retention_error = null;
        updateJob();
        callbacks.toast("已保存到书库，书籍当前版本已更新，旧版文件保留");
        return;
      }
      if (["image-select-all", "image-select-none"].includes(button.id)) {
        document.querySelectorAll('[name="asset_ids"]:not(:disabled)').forEach(input => input.checked = button.id === "image-select-all"); updateSelection();
      }
      if (["image-select-deferred", "image-clear-deferred"].includes(button.id)) {
        const deferred = new Set(job.images.filter(a => a.state === "DEFERRED").map(a => a.id));
        document.querySelectorAll('[name="rerun_asset_ids"]:not(:disabled)').forEach(input => input.checked = button.id === "image-select-deferred" && deferred.has(input.value));
        updateRerunSelection();
      }
      if (["image-pause", "image-resume", "image-retry", "image-update-config"].includes(button.id)) {
        const updating = button.id === "image-update-config";
        if (updating && !$("#image-concurrency").reportValidity()) return;
        button.disabled = true;
        const id = job.run.id, version = generation;
        await api(`/api/runs/${id}/${updating ? "model" : button.id === "image-pause" ? "pause" : "start"}`, { method: "POST", body: {
          revision: job.run.revision, operation_id: crypto.randomUUID(), ...(button.id === "image-retry" ? { retry_unknown: true } : {}),
          ...(updating ? { llm_concurrency: Number($("#image-concurrency").value) } : {}) } });
        if (version !== generation) return;
        job = await api(`/api/runs/${id}/images`);
        if (version === generation) { updateJob(); schedule(); }
      }
    } catch (error) { if ($("#image-error")) $("#image-error").textContent = error.message; }
    finally {
      button.disabled = button.id === "image-save" && book?.result?.run_id === job?.run.id && !job?.run.retention_error;
      updateRerunSelection();
    }
  });
  document.addEventListener("submit", async event => {
    if (!["image-start-form", "image-rerun-form"].includes(event.target.id)) return;
    event.preventDefault();
    const form = event.target, button = form.querySelector('[type="submit"]'), version = generation;
    if (form.dataset.submitting === "true") return;
    const rerun = form.id === "image-rerun-form";
    const asset_ids = Array.from(document.querySelectorAll(`[name="${rerun ? "rerun_asset_ids" : "asset_ids"}"]:checked:not(:disabled)`)).map(el => el.value);
    if (!asset_ids.length) return;
    form.dataset.submitting = "true";
    button.disabled = true; $("#image-error").textContent = "";
    const label = button.textContent;
    button.textContent = "正在创建修图任务…";
    // Retain the operation ID on network errors so a retry cannot duplicate the job.
    const special = rerun && form.elements.repair_mode.value === "special";
    const request = JSON.stringify({ ...(!rerun ? { result_id: inventory.result_id } : {}), asset_ids,
      llm_concurrency: special ? 1 : Number(form.elements.image_concurrency.value),
      ...(special ? { mode: "special" } : {}) });
    if (form.dataset.request !== request) { form.dataset.request = request; form.dataset.operation = crypto.randomUUID(); }
    try {
      const url = rerun ? `/api/runs/${job.run.id}/repair-images` : `/api/books/${book.id}/repair-images`;
      const result = await api(url, { method: "POST", body: { ...JSON.parse(request), operation_id: form.dataset.operation } });
      if (version === generation) await loadJob(result.id);
      callbacks.toast(rerun ? "已从当前修图结果创建新一轮任务" : "独立修图任务已创建，原书结果保留");
    } catch (error) { if (version === generation) $("#image-error").textContent = error.message; }
    finally { form.dataset.submitting = "false"; button.disabled = false; button.textContent = label; updateRerunSelection(); }
  });
}
