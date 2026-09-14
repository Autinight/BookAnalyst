import { addProvider, readConnections, refreshConnectionChoices, connectionName } from "./connections.js";
import { api, setToken, escape as e } from "./api.js";
import * as views from "./views.js";
import * as templateUI from "./templates.js";
import * as imageUI from "./image-repair.js";
import { loadStageModelChoices, readStageModels, stageConnectionChanged } from "./stage-models.js";
const $ = (s) => document.querySelector(s);
let data,
  settings,
  route = "library",
  run = null,
  page = 1,
  routeVersion = 0,
  pollTimer,
  contentController;
let requestsOffset = 0, requestsSequence = 0;
const libraryState = { view: "list", query: "", sort: "recent", trash: false };
try { if (localStorage.getItem("bookanalyst.library.view") === "grid") libraryState.view = "grid"; } catch {}
let tasks = [],
  pageTaskState = "",
  refreshTimer;
function toast(text) {
  $("#toast").textContent = text;
  $("#toast").hidden = false;
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => ($("#toast").hidden = true), 5000);
}
async function bootstrap() {
  data = await api("/api/bootstrap");
  setToken(data.token);
}
async function navigate(target, id) {
  imageUI.leave();
  const version = ++routeVersion;
  clearTimeout(pollTimer);
  contentController?.abort();
  route = target;
  run = null;
  requestsOffset = 0;
  document
    .querySelectorAll("[data-nav]")
    .forEach((x) => x.classList.toggle("active", x.dataset.nav === target));
  $("#breadcrumb").textContent = {
    library: "书库",
    templates: "TeX 模板",
    images: "图片修复",
    runs: "运行记录",
    settings: "模型设置",
    run: "文献转换",
  }[target];
  if (target === "templates") {
    const templates = await api("/api/templates");
    if (version !== routeVersion) return;
    $("#main").innerHTML = templateUI.view(templates);
  } else if (target === "settings") {
    const result = await api("/api/settings");
    if (version !== routeVersion) return;
    settings = result;
    $("#main").innerHTML = views.settings(settings, data.model_stages);
    void loadStageModelChoices($("#settings-form"), settings);
  } else if (target === "run") {
    const result = await api(`/api/runs/${id}/status`);
    if (version !== routeVersion) return;
    if (result.kind === "image_repair") return await navigate("images", id);
    run = result;
    page = run.pages[0];
    tasks = [];
    pageTaskState = "";
    $("#main").innerHTML = views.runView(run, data.stages, data.live_concurrency_updates);
    updateStatus();
    if (!run.legacy) {
      await updateTasks();
    }
    schedulePoll();
  } else {
    await bootstrap();
    if (version !== routeVersion) return;
    if (target === "images") return await imageUI.mount(data, id);
    $("#main").innerHTML =
      target === "library" ? views.library(data, libraryState) : views.runs(data);
  }
}
function renderLibrary() {
  if (route === "library") $("#main").innerHTML = views.library(data, libraryState);
}
function filterLibrary() {
  if (route === "library") $("#library-results").innerHTML = views.libraryResults(data, libraryState);
}
async function manageBook(button) {
  const action = button.dataset.bookAction, id = button.dataset.bookId;
  const book = [...data.books, ...(data.deleted_books || [])].find(b => b.id === id);
  if (!book) return;
  if (action === "images") return await navigate("images", id);
  if (action === "template") return await templateUI.openApply(book);
  if (action === "reveal" || action === "restore") {
    const version = routeVersion;
    button.disabled = true;
    try {
      await api(`/api/books/${id}/${action}`, { method: "POST", body: {} });
      if (action === "restore") {
        await bootstrap();
        if (version === routeVersion) renderLibrary();
      }
      toast(action === "restore" ? "书籍已恢复" : "已打开书籍文件位置");
    } finally { button.disabled = false; }
    return;
  }
  const form = $("#book-form"), renaming = action === "rename";
  form.dataset.bookId = id;
  form.dataset.action = action;
  $("#book-dialog-title").textContent = renaming ? "重命名书籍" : "移入回收站";
  $("#book-dialog-description").textContent = renaming ? "设置书库中显示的书名。" : `将“${book.title}”移入回收站？原 PDF 和已有转换任务会保留，可随时恢复。`;
  $("#book-title-label").hidden = !renaming;
  form.elements.title.disabled = !renaming;
  form.elements.title.value = book.title;
  $("#book-submit").textContent = renaming ? "保存" : "移入回收站";
  $("#book-error").textContent = "";
  $("#book-dialog").showModal();
  if (renaming) { form.elements.title.focus(); form.elements.title.select(); }
}
function updateStatus() {
  if (!run) return;
  $("#status-badge").innerHTML = views.badge(run.state);
  const counts = run.tasks || {},
    done = counts.PASSED || 0,
    total = Object.values(counts).reduce((a, b) => a + b, 0);
  $("#progress-text").textContent = total ? `${done} / ${total} 批完成` : "";
  $("#usage").textContent =
    `LLM ${run.usage?.llm || 0} 次${run.pages_per_task ? ` · 每批 ${run.pages_per_task} 页 · 并发 ${run.concurrency}` : ""}`;
  for (const [id, state] of Object.entries(run.stages || {})) {
    const element = $("#stage-" + id);
    if (element) {
      element.className = "stage " + state;
      element.querySelector("small").textContent =
        views.stateNames[state] || state;
    }
  }
  $("#resume").hidden =
    run.legacy || run.state === "RUNNING" || run.state === "COMPLETED";
  $("#retry-unknown").hidden = run.legacy || run.state === "RUNNING" || run.state === "COMPLETED";
  $("#pause").hidden = run.legacy || run.state !== "RUNNING";
  $("#result-pdf").hidden = !run.legacy && run.state !== "COMPLETED";
  $("#retain-result").hidden = run.state !== "COMPLETED";
  const saved = data.books.find(b => b.id === run.book_id)?.result?.run_id === run.id && !run.retention_error;
  $("#retain-result").textContent = saved ? "已保存到书库" : "保存到书库";
  $("#retain-result").disabled = saved;
  $("#result-save-note").textContent = saved
    ? "这份结果已保存为书籍当前版本，旧版文件保留。"
    : "结果只保留在任务中，不自动更新书库。编译完成后可预览，再点击“保存到书库”设为书籍当前版本；旧版文件保留。";
  const displayError = run.error || run.retention_error;
  $("#run-message").hidden = !displayError;
  $("#run-message").textContent = displayError
    ? `${displayError.code} · ${displayError.message}`
    : "";
  const reviewNote = $("#reference-review-note");
  reviewNote.hidden = !run.reference_review_count;
  reviewNote.querySelector("a").textContent = `引用待确认 ${run.reference_review_count || 0} 条`;
  updateRunModelLive();
  const compilerActivity = $("#compiler-activity");
  if (compilerActivity) {
    compilerActivity.hidden = !run.compiler?.activity || run.stage !== "finish";
    compilerActivity.textContent = `${run.compiler?.agent === "pi" ? "Pi" : "Codex"} · ${run.compiler?.activity || ""}`;
  }
}
async function updateTasks() {
  const id = run.id,
    version = routeVersion,
    result = await api(`/api/runs/${id}/tasks`);
  if (version !== routeVersion) return;
  tasks = result.filter((t) => t.stage === "convert");
  const container = $("#batches");
  for (const task of tasks) {
    let button = document.getElementById(task.id);
    if (!button) {
      button = document.createElement("button");
      button.id = task.id;
      button.dataset.page = task.pages[0];
      container.append(button);
    }
    button.className = task.state;
    button.textContent = `${task.pages[0]}–${task.pages.at(-1)} 页 · ${views.stateNames[task.state] || task.state}`;
  }
  const state = tasks.find((t) => t.pages.includes(page))?.state || "PENDING";
  if (state !== pageTaskState) {
    pageTaskState = state;
    await loadPage(true);
  }
}
async function loadPage(preserveInput = false) {
  if (!run || run.legacy) return;
  contentController?.abort();
  contentController = new AbortController();
  const version = routeVersion,
    id = run.id,
    wanted = page;
  if (!preserveInput) $("#page-number").value = page;
  $("#previous").disabled = page === run.pages[0];
  $("#next").disabled = page === run.pages[1];
  const image = $("#source-image"),
    url = `/api/books/${run.book_id}/image?page=${page}&dpi=110`;
  if (image.getAttribute("src") !== url) {
    image.hidden = true;
    image.alt = `原 PDF 第 ${wanted} 页`;
    image.src = url;
    image.decode().then(() => {
      if (version === routeVersion && page === wanted) image.hidden = false;
    }).catch(() => {
      if (version === routeVersion && page === wanted) toast("原页图像加载失败，请重新翻页。");
    });
  }
  $("#page-tex").textContent = "正在读取本页…";
  try {
    const result = await api(`/api/runs/${id}/content?page=${wanted}`, {
      signal: contentController.signal,
    });
    if (version !== routeVersion || page !== wanted) return;
    $("#page-tex").textContent = result.tex || "本页尚未转换完成。";
    $("#page-state").textContent = views.stateNames[result.task?.state] || "";
  } catch (error) {
    if (error.name !== "AbortError") toast(error.message);
  }
}
function schedulePoll() {
  clearTimeout(pollTimer);
  if (run && !run.legacy && run.state === "RUNNING")
    pollTimer = setTimeout(poll, 2000);
}
async function poll() {
  if (!run || document.hidden) {
    schedulePoll();
    return;
  }
  const id = run.id,
    version = routeVersion;
  try {
    const next = await api(`/api/runs/${id}/status`);
    if (version !== routeVersion) return;
    const completed = next.state === "COMPLETED" && run.state !== "COMPLETED";
    const changed = next.updated_at !== run.updated_at ||
      JSON.stringify(next.compiler) !== JSON.stringify(run.compiler);
    run = next;
    if (changed) {
      updateStatus();
      await updateTasks();
      if (completed) await loadPage(true);
    }
    await loadRequests();
  } catch (error) {
    toast(error.message);
  } finally {
    if (version === routeVersion) schedulePoll();
  }
}
function updateRunModelLive() {
  const el = $("#run-model-live"), button = $("#update-model-config");
  if (!el || !run) return;
  const stage = run.kind === "template" ? "template_apply" : run.stage;
  const m = run.stage_models?.[stage] || run.model || {};
  const effort = run.stage_models ? m.reasoning_effort : (["convert", "seams"].includes(stage) ? m.reasoning_effort : run.structure_effort);
  el.textContent = run.model_refresh_pending
    ? `配置待更新：下一次派发前读取已保存的模型配置${run.pending_llm_concurrency != null ? `，并行任务数调整为 ${run.pending_llm_concurrency}` : ""}；已开始的请求保持原配置。`
    : stage === "style" ? "按全书规则生成公共 TeX（程序执行）"
    : `${data.model_stages?.[stage] || "当前阶段"}：${m.connection_id || "未设置"} / ${m.model_id || "连接默认模型"} · 思考 ${effort || "medium"}`;
  if (button) button.hidden = run.legacy || run.state === "COMPLETED";
  const controls = $("#run-config-controls"), concurrency = $("#run-concurrency");
  if (controls) controls.hidden = run.legacy || run.state === "COMPLETED";
  if (concurrency && !concurrency.dataset.dirty && document.activeElement !== concurrency)
    concurrency.value = run.pending_llm_concurrency ?? run.concurrency;
}
function bookChanged() {
  const form = $("#run-form"),
    book = data.books.find((b) => b.id === form.elements.book_id.value);
  form.elements.end_page.value = book.page_count;
}
async function newRun(bid) {
  settings = await api("/api/settings");
  const form = $("#run-form");
  form.reset();
  $("#run-error").textContent = "";
  form.elements.book_id.innerHTML = data.books
    .map((b) => `<option value="${b.id}">${e(b.title)}</option>`)
    .join("");
  if (bid) form.elements.book_id.value = bid;
  bookChanged();
  form.elements.llm_concurrency.value = settings.llm_concurrency || 2;
  $("#new-run").showModal();
}
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button,a[data-nav]");
  if (!button) return;
  try {
    if (button.dataset.nav) return await navigate(button.dataset.nav);
    if (button.dataset.libraryView) {
      libraryState.view = button.dataset.libraryView;
      try { localStorage.setItem("bookanalyst.library.view", libraryState.view); } catch {}
      return renderLibrary();
    }
    if (button.dataset.libraryTrash !== undefined) {
      libraryState.trash = button.dataset.libraryTrash === "true";
      libraryState.query = "";
      return renderLibrary();
    }
    if (button.hasAttribute("data-book-close")) return $("#book-dialog").close();
    if (button.dataset.bookAction) return await manageBook(button);
    if (button.hasAttribute("data-new"))
      return await newRun(button.dataset.new || null);
    if (button.id === "update-model-config") {
      const concurrency = $("#run-concurrency");
      if (concurrency && !concurrency.reportValidity()) return;
      const requested = concurrency ? Number(concurrency.value) : null;
      const id = run.id, version = routeVersion;
      button.disabled = true;
      try {
        const result = await api(`/api/runs/${id}/model`, {
          method: "POST", body: { revision: run.revision, operation_id: crypto.randomUUID(), ...(requested != null ? { llm_concurrency: requested } : {}) },
        });
        if (version === routeVersion && run?.id === id) {
          if (concurrency && Number(concurrency.value) === requested) delete concurrency.dataset.dirty;
          run = result; updateStatus();
        }
        toast("已提交配置更新，后续派发使用新配置；已发出的请求继续执行");
      } finally { button.disabled = false; }
      return;
    }
    if (button.id === "retain-result") {
      const id = run.id, version = routeVersion;
      button.disabled = true;
      try {
        await api(`/api/runs/${id}/retain`, { method: "POST", body: {} });
        await bootstrap();
        if (version !== routeVersion) return;
        const current = await api(`/api/runs/${id}/status`);
        if (version !== routeVersion) return;
        run = current;
        updateStatus();
        toast("已保存到书库，书籍当前版本已更新，旧版文件保留");
      } finally {
        button.disabled = false;
        if (version === routeVersion && run?.id === id) updateStatus();
      }
      return;
    }
    if (button.dataset.run) return await navigate("run", button.dataset.run);
    if (button.hasAttribute("data-upload")) return $("#upload").click();
    if (button.hasAttribute("data-close")) return $("#new-run").close();
    if (button.dataset.requestsOffset !== undefined) {
      requestsOffset = Number(button.dataset.requestsOffset);
      return await loadRequests();
    }
    if (button.dataset.page) {
      page = Number(button.dataset.page);
      return await loadPage();
    }
    if (button.id === "previous" || button.id === "next") {
      page += button.id === "next" ? 1 : -1;
      return await loadPage();
    }
    if (button.id === "resume" || button.id === "pause" || button.id === "retry-unknown") {
      button.disabled = true;
      await api(
        `/api/runs/${run.id}/${button.id === "pause" ? "pause" : "start"}`,
        {
          method: "POST",
          body: { revision: run.revision, operation_id: crypto.randomUUID(), ...(button.id === "retry-unknown" ? { retry_unknown: true } : {}) },
        },
      );
      run = await api(`/api/runs/${run.id}/status`);
      updateStatus();
      schedulePoll();
      button.disabled = false;
      return;
    }
    if (button.id === "add-provider") return addProvider($("#settings-form"), settings);
    if (button.hasAttribute("data-connection-check") || button.hasAttribute("data-connection-login")) {
      const card = button.closest("[data-connection-id]"), id = card.dataset.connectionId;
      const status = card.querySelector("[data-connection-status]");
      const login = button.hasAttribute("data-connection-login");
      const action = login ? "login" : settings.connections[id].kind === "openai_compatible" ? "test" : "status";
      button.disabled = true;
      try {
        const result = await api(`/api/connections/${encodeURIComponent(id)}/${action}`,
          action === "status" ? {} : { method: "POST", body: {} });
        if (login) status.innerHTML = `<a href="${e(result.auth_url)}" target="_blank" rel="noopener">打开官方登录页面 ↗</a>`;
        else {
          const names = (result.models || []).map(m => m.model || m.id).filter(Boolean);
          status.textContent = result.status === "READY"
            ? `${result.message || "连接正常"}${names.length ? " · " + names.slice(0, 6).join("、") + (names.length > 6 ? "…" : "") : ""}`
            : result.message || result.status;
        }
      } finally { button.disabled = false; }
    }
  } catch (error) {
    button.disabled = false;
    toast(error.message);
  }
});
document.addEventListener("change", async (event) => {
  try {
    if (event.target.id === "library-sort") {
      libraryState.sort = event.target.value;
      filterLibrary();
    }
    if (event.target.name === "book_id") bookChanged();
    if (event.target.hasAttribute("data-stage-connection"))
      stageConnectionChanged(event.target, settings);
    if (event.target.id === "page-number") {
      page = Math.max(
        run.pages[0],
        Math.min(run.pages[1], Number(event.target.value) || run.pages[0]),
      );
      await loadPage();
    }
    if (event.target.id === "upload" && event.target.files[0]) {
      const body = new FormData();
      body.append("file", event.target.files[0]);
      toast("正在保存 PDF…");
      await api("/api/books", { method: "POST", body });
      await navigate("library");
      toast("PDF 已保存");
      event.target.value = "";
    }
  } catch (error) {
    toast(error.message);
  }
});
document.addEventListener("input", event => {
  if (event.target.id === "run-concurrency") event.target.dataset.dirty = "true";
  if (event.target.hasAttribute("data-connection-field")) {
    const card = event.target.closest("[data-connection-id]"), id = card.dataset.connectionId;
    const field = event.target.dataset.connectionField;
    if (["name", "enabled", "model_id"].includes(field)) {
      settings.connections[id][field] = event.target.type === "checkbox" ? event.target.checked : event.target.value.trim();
      card.querySelector("[data-connection-title]").textContent = connectionName(id, settings.connections[id]);
      refreshConnectionChoices($("#settings-form"), settings);
    }
  }
  if (event.target.id === "library-search") {
    libraryState.query = event.target.value;
    filterLibrary();
  }
});
document.addEventListener("submit", async event => {
  if (event.target.id !== "book-form") return;
  event.preventDefault();
  const form = event.target, button = $("#book-submit"), version = routeVersion;
  const renaming = form.dataset.action === "rename";
  button.disabled = true;
  try {
    await api(`/api/books/${form.dataset.bookId}`, {
      method: renaming ? "PATCH" : "DELETE",
      ...(renaming ? { body: { title: form.elements.title.value.trim() } } : {}),
    });
    $("#book-dialog")?.close();
    await bootstrap();
    if (version === routeVersion) renderLibrary();
    toast(renaming ? "书名已更新" : "已移入回收站，可在回收站恢复");
  } catch (error) {
    if (version === routeVersion) $("#book-error").textContent = error.message;
  } finally { button.disabled = false; }
});
async function loadRequests() {
  if (!run || !$("#requests-details")?.open) return;
  const id = run.id, version = routeVersion, sequence = ++requestsSequence;
  const result = await api(`/api/runs/${id}/requests?grouped=true&offset=${requestsOffset}`);
  if (version !== routeVersion || sequence !== requestsSequence || !$("#requests-details")?.open) return;
  const panel = $("#requests-content");
  const expanded = new Set([...panel.querySelectorAll("details[open]")].map(d => d.dataset.requestGroup));
  panel.innerHTML = views.requestHistory(result, requestsOffset);
  panel.querySelectorAll("details[data-request-group]").forEach(d => {
    d.open = expanded.has(d.dataset.requestGroup);
  });
}
document.addEventListener("toggle", async (event) => {
  if (event.target.id !== "requests-details" || !event.target.open) return;
  try { await loadRequests(); } catch (error) { toast(error.message); }
}, true);
$("#run-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target,
    button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const f = new FormData(form),
      body = {
        book_id: f.get("book_id"),
      setup_pages: String(f.get("setup_pages"))
          .split(/[,，\s]+/)
          .filter(Boolean)
          .map(Number),
      };
    for (const key of [
      "start_page",
      "end_page",
      "pages_per_task",
      "llm_concurrency",
    ])
      body[key] = Number(f.get(key));
    const r = await api("/api/runs", { method: "POST", body });
    await api(`/api/runs/${r.id}/start`, {
      method: "POST",
      body: { revision: r.revision, operation_id: crypto.randomUUID() },
    });
    $("#new-run").close();
    await navigate("run", r.id);
  } catch (error) {
    $("#run-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
document.addEventListener("submit", async (event) => {
  if (event.target.id !== "settings-form") return;
  event.preventDefault();
  const button = event.target.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const f = new FormData(event.target), s = structuredClone(settings);
    s.connections = readConnections(event.target, settings);
    s.stage_models = readStageModels(event.target, data.model_stages);
    s.llm_concurrency = Number(f.get("workflow_concurrency"));
    s.image_repair_concurrency = Number(f.get("image_repair_concurrency"));
    settings = await api("/api/settings", { method: "PUT", body: s });
    if (route === "settings") {
      $("#main").innerHTML = views.settings(settings, data.model_stages);
      void loadStageModelChoices($("#settings-form"), settings);
    }
    toast("模型设置已保存；已有任务点击更新模型配置后生效");
  } catch (error) {
    $("#settings-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
const hashRoute = () => ["library", "images", "templates", "runs", "settings"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "library";
window.addEventListener("hashchange", () => navigate(hashRoute()));
templateUI.init({ navigate, toast });
imageUI.init({ navigate, toast });
bootstrap()
  .then(() => {
    const id = new URLSearchParams(location.search).get("run");
    return navigate(id ? "run" : hashRoute(), id);
  })
  .catch((error) => {
    $("#main").textContent = error.message;
  });
