import { api, setToken, escape as e } from "./api.js";
import * as views from "./views.js";
const $ = (s) => document.querySelector(s);
let data,
  settings,
  route = "library",
  run = null,
  page = 1,
  routeVersion = 0,
  pollTimer,
  contentController;
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
  const version = ++routeVersion;
  clearTimeout(pollTimer);
  contentController?.abort();
  route = target;
  run = null;
  document
    .querySelectorAll("[data-nav]")
    .forEach((x) => x.classList.toggle("active", x.dataset.nav === target));
  $("#breadcrumb").textContent = {
    library: "书库",
    runs: "运行记录",
    settings: "模型连接",
    run: "文献转换",
  }[target];
  if (target === "settings") {
    const result = await api("/api/settings");
    if (version !== routeVersion) return;
    settings = result;
    $("#main").innerHTML = views.settings(settings);
  } else if (target === "run") {
    const result = await api(`/api/runs/${id}/status`);
    if (version !== routeVersion) return;
    run = result;
    page = run.pages[0];
    tasks = [];
    pageTaskState = "";
    $("#main").innerHTML = views.runView(run, data.stages);
    updateStatus();
    if (!run.legacy) {
      await updateTasks();
    }
    schedulePoll();
  } else {
    await bootstrap();
    if (version !== routeVersion) return;
    $("#main").innerHTML =
      target === "library" ? views.library(data) : views.runs(data);
  }
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
  $("#pause").hidden = run.legacy || run.state !== "RUNNING";
  $("#result-pdf").hidden = !run.legacy && run.state !== "COMPLETED";
  $("#run-message").hidden = !run.error;
  $("#run-message").textContent = run.error
    ? `${run.error.code} · ${run.error.message}`
    : "";
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
    const changed = next.updated_at !== run.updated_at;
    run = next;
    if (changed) {
      updateStatus();
      await updateTasks();
      if (completed) await loadPage(true);
    }
  } catch (error) {
    toast(error.message);
  } finally {
    if (version === routeVersion) schedulePoll();
  }
}
async function models(cid) {
  const status = await api(`/api/connections/${cid}/status`);
  const select = $("#run-form").elements.model_id;
  select.innerHTML = status.models
    .map(
      (m) =>
        `<option value="${e(m.model || m.id)}" ${m.isDefault ? "selected" : ""}>${e(m.model || m.id)}</option>`,
    )
    .join("");
  const preferred = settings.connections[cid]?.model_id;
  if ([...select.options].some((o) => o.value === preferred))
    select.value = preferred;

  if (!status.models.length)
    select.innerHTML = '<option value="">请先配置有效模型连接</option>';
  return status;
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
  form.elements.connection_id.innerHTML = Object.entries(settings.connections)
    .filter(([, c]) => c.enabled)
    .map(
      ([id, c]) =>
        `<option value="${id}">${c.kind === "codex_chatgpt" ? "ChatGPT 官方订阅" : e(id)}</option>`,
    )
    .join("");
  form.elements.connection_id.value = settings.default_connection;
  $("#new-run").showModal();
  await models(form.elements.connection_id.value);
}
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button,a[data-nav]");
  if (!button) return;
  try {
    if (button.dataset.nav) return await navigate(button.dataset.nav);
    if (button.hasAttribute("data-new"))
      return await newRun(button.dataset.new || null);
    if (button.dataset.run) return await navigate("run", button.dataset.run);
    if (button.hasAttribute("data-upload")) return $("#upload").click();
    if (button.hasAttribute("data-close")) return $("#new-run").close();
    if (button.dataset.page) {
      page = Number(button.dataset.page);
      return await loadPage();
    }
    if (button.id === "previous" || button.id === "next") {
      page += button.id === "next" ? 1 : -1;
      return await loadPage();
    }
    if (button.id === "resume" || button.id === "pause") {
      button.disabled = true;
      await api(
        `/api/runs/${run.id}/${button.id === "pause" ? "pause" : "start"}`,
        {
          method: "POST",
          body: { revision: run.revision, operation_id: crypto.randomUUID() },
        },
      );
      run = await api(`/api/runs/${run.id}/status`);
      updateStatus();
      schedulePoll();
      button.disabled = false;
      return;
    }
    if (button.id === "account-check") {
      button.disabled = true;
      const s = await api("/api/connections/openai_subscription/status");
      $("#account-status").textContent =
        s.status === "READY"
          ? "连接正常 · " + s.models.map((m) => m.model || m.id).join("、")
          : s.message || s.status;
      button.disabled = false;
    }
    if (button.id === "login") {
      const s = await api("/api/connections/openai_subscription/login", {
        method: "POST",
        body: {},
      });
      $("#account-status").innerHTML =
        `<a href="${e(s.auth_url)}" target="_blank" rel="noopener">打开官方登录页面 ↗</a>`;
    }
  } catch (error) {
    button.disabled = false;
    toast(error.message);
  }
});
document.addEventListener("change", async (event) => {
  try {
    if (event.target.name === "book_id") bookChanged();
    if (event.target.name === "connection_id") await models(event.target.value);
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
document.addEventListener(
  "toggle",
  async (event) => {
    if (event.target.id !== "requests-details" || !event.target.open || !run)
      return;
    try {
      const result = await api(`/api/runs/${run.id}/requests`);
      $("#requests-content").innerHTML =
        `<p class="hint">共 ${result.total} 次请求；已返回用量的 ${result.tokens.reported_calls} 次合计：输入 ${result.tokens.input.toLocaleString()} / 输出 ${result.tokens.output.toLocaleString()} token（输出含思考 ${result.tokens.reasoning.toLocaleString()}）。下方显示最新 50 次，缺失用量为未知。</p><div class="table-wrap"><table><thead><tr><th>用途</th><th>状态</th><th>模型 / 思考</th><th>秒</th><th>输入 / 输出 token</th></tr></thead><tbody>${result.rows
          .map((r) => {
            const u = r.usage?.tokens?.total;
            return `<tr><td>${e(r.purpose)}</td><td>${e(r.state)}</td><td>${e(r.model || "未知")} / ${e(r.effort || "默认")}</td><td>${r.seconds}</td><td>${u ? `${u.inputTokens} / ${u.outputTokens}` : "未知"}</td></tr>`;
          })
          .join("")}</tbody></table></div>`;
    } catch (error) {
      toast(error.message);
    }
  },
  true,
);
$("#run-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target,
    button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const f = new FormData(form),
      body = {
        book_id: f.get("book_id"),
        model: {
          connection_id: f.get("connection_id"),
          model_id: f.get("model_id"),
          reasoning_effort: f.get("reasoning_effort") || "medium",
        },
        structure_effort: f.get("structure_effort") || "xhigh",
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
    const f = new FormData(event.target),
      s = structuredClone(settings),
      o = s.connections.openai_subscription,
      c = s.connections.custom_api;
    Object.assign(o, {
      model_id: f.get("official_model"),
      max_in_flight: Number(f.get("official_concurrency")),
      timeout_seconds: Number(f.get("official_timeout")),
    });
    Object.assign(c, {
      enabled: f.has("custom_enabled"),
      base_url: f.get("custom_url"),
      protocol: f.get("custom_protocol"),
      model_id: f.get("custom_model"),
      auth_mode: f.get("custom_auth"),
      api_key: f.get("custom_key"),
      clear_api_key: f.has("custom_clear"),
      image_support: f.get("image_support"),
      max_in_flight: Number(f.get("custom_concurrency")),
      timeout_seconds: Number(f.get("custom_timeout")),
    });
    settings = await api("/api/settings", { method: "PUT", body: s });
    if (route === "settings") $("#main").innerHTML = views.settings(settings);
    toast("连接设置已保存");
  } catch (error) {
    $("#settings-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
window.addEventListener("hashchange", () =>
  navigate(location.hash === "#runs" ? "runs" : "library"),
);
bootstrap()
  .then(() => {
    const id = new URLSearchParams(location.search).get("run");
    return navigate(id ? "run" : "library", id);
  })
  .catch((error) => {
    $("#main").textContent = error.message;
  });
