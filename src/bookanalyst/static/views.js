import { escape as e } from "./api.js";
export const stateNames = {
  PENDING: "待开始",
  RUNNING: "运行中",
  PAUSED: "已暂停",
  NEEDS_REVIEW: "需要处理",
  FAILED: "失败",
  PASSED: "已通过",
  COMPLETED: "已完成",
  BOOK_ACCEPTED: "旧版已完成",
};
export const badge = (s) =>
  `<span class="badge ${s}">${e(stateNames[s] || s)}</span>`;
const head = (title, description, actions = "") =>
  `<div class="page-head"><div><h1>${title}</h1><p>${description}</p></div><div class="row">${actions}</div></div>`;
export function runRows(runs) {
  return runs.length
    ? `<div class="table-wrap"><table><thead><tr><th>文献</th><th>页面</th><th>状态</th><th></th></tr></thead><tbody>${runs.map((r) => `<tr><td>${e(r.title)}<small>${r.id.slice(0, 8)} · ${r.legacy ? "历史流程" : "视觉转换"}</small></td><td>${r.pages.join("–")}</td><td>${badge(r.state)}</td><td><button data-run="${r.id}">打开</button></td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty">尚未创建转换任务</div>`;
}
export function library(data) {
  return (
    head(
      "书库",
      "从原书图像恢复数学内容，由 TeX 统一排版。",
      '<button data-upload>导入 PDF</button><button data-new class="primary">新建转换</button>',
    ) +
    `<div class="books">${data.books.map((b) => `<article class="book"><div class="cover"><img loading="lazy" decoding="async" src="/api/books/${b.id}/image?page=1&dpi=55" alt="${e(b.title)}首页"></div><div><span class="eyebrow">PDF · ${b.page_count} 页</span><h2>${e(b.title)}</h2><p>${(b.size_bytes / 1048576).toFixed(1)} MB · 已保存到本地</p><div class="row"><button data-new="${b.id}" class="primary">转换为 TeX</button><a href="/api/books/${b.id}/pdf" target="_blank" rel="noopener">查看原书 ↗</a></div></div></article>`).join("")}</div><h2 class="section-title">最近运行</h2>${runRows(data.runs.slice(0, 5))}`
  );
}
export function runs(data) {
  return (
    head("运行记录", "完成的批次即时保存，恢复时继续处理未完成部分。") +
    runRows(data.runs)
  );
}
export function runView(r, stages) {
  return (
    head(
      e(r.title),
      `${r.legacy ? "历史运行" : "视觉独立转换"} · PDF ${r.pages.join("–")} 页`,
      `<a class="button" href="/api/runs/${r.id}/export">下载 TeX 工程</a><a class="button" id="result-pdf" href="/api/runs/${r.id}/pdf" target="_blank" rel="noopener">查看输出 PDF</a>`,
    ) +
    `<section class="run-status"><div class="row"><div id="status-badge">${badge(r.state)}</div><span id="progress-text"></span><span id="usage" class="muted"></span><div class="spacer"></div><button id="resume" class="primary">开始 / 恢复</button><button id="pause">暂停</button></div>${
      r.legacy
        ? ""
        : `<div class="stages">${Object.entries(stages)
            .map(
              ([id, title]) =>
                `<div id="stage-${id}" class="stage"><span>${e(title)}</span><small></small></div>`,
            )
            .join("")}</div>`
    }<p id="run-message" class="error" hidden></p></section>` +
    (r.legacy
      ? '<section class="empty">旧流程记录保留，可下载已有产物。新版转换请从书库创建。</section>'
      : `<section class="reader"><div class="reader-toolbar"><button id="previous" aria-label="上一页">←</button><label>PDF 页<input id="page-number" type="number" min="${r.pages[0]}" max="${r.pages[1]}" value="${r.pages[0]}"></label><span>/ ${r.pages[1]}</span><button id="next" aria-label="下一页">→</button><span class="spacer"></span><small id="page-state"></small></div><div class="reader-columns"><div class="page-pane"><img id="source-image" alt="当前 PDF 原页" decoding="async"></div><div class="tex-pane"><div class="pane-title">本页 TeX <span>按需加载</span></div><pre id="page-tex">等待选择页面</pre></div></div></section><details id="batch-details"><summary>批次进度</summary><div id="batches" class="batch-grid"></div></details>`) +
    `<details id="requests-details"><summary>请求与用量</summary><div id="requests-content"></div></details>`
  );
}
export function settings(s) {
  const o = s.connections.openai_subscription,
    c = s.connections.custom_api;
  return (
    head("模型连接", "连接配置独立于文档工作流。") +
    `<form id="settings-form"><div class="settings-grid"><section class="card"><h2>ChatGPT 官方订阅</h2><label>默认模型<input name="official_model" value="${e(o.model_id)}" placeholder="使用上游默认模型"></label><div class="fields"><label>连接并发<input type="number" name="official_concurrency" value="${o.max_in_flight}" min="1"></label><label>请求等待（秒）<input type="number" name="official_timeout" value="${o.timeout_seconds}" min="1" max="3600"></label></div><div class="row"><button type="button" id="account-check">检查连接</button><button type="button" id="login">登录</button></div><p id="account-status" class="hint"></p></section><section class="card"><h2>自定义 API / vLLM</h2><label class="check"><input type="checkbox" name="custom_enabled" ${c.enabled ? "checked" : ""}>启用</label><label>API 地址<input name="custom_url" value="${e(c.base_url)}"></label><div class="fields"><label>协议<select name="custom_protocol"><option value="chat_completions">Chat Completions</option><option value="responses" ${c.protocol === "responses" ? "selected" : ""}>Responses</option></select></label><label>模型<input name="custom_model" value="${e(c.model_id)}"></label><label>鉴权<select name="custom_auth"><option value="bearer">Bearer</option><option value="none" ${c.auth_mode === "none" ? "selected" : ""}>本地无鉴权</option></select></label><label>密钥环境变量<input name="custom_env" value="${e(c.api_key_env)}"></label><label>连接并发<input type="number" name="custom_concurrency" min="1" value="${c.max_in_flight}"></label><label>请求等待（秒）<input type="number" name="custom_timeout" min="1" max="3600" value="${c.timeout_seconds}"></label></div><label>图像支持<select name="image_support"><option value="unknown">待确认</option><option value="supported" ${c.image_support === "supported" ? "selected" : ""}>已确认支持</option><option value="unsupported" ${c.image_support === "unsupported" ? "selected" : ""}>不支持</option></select></label></section></div><p id="settings-error" class="error"></p><button type="submit" class="primary">保存连接</button></form>`
  );
}
