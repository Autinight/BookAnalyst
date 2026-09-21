import { connectionEditor } from "./connections.js";
import { usageSection } from "./provider-usage.js";
import { stageModelSettings } from "./stage-models.js";
import { escape as e } from "./api.js";
export const stateNames = {
  PENDING: "待开始",
  RUNNING: "运行中",
  WAITING_MERGE: "已修复，待合并",
  PAUSED: "已暂停",
  NEEDS_REVIEW: "需要处理",
  DEFERRED: "引用待确认",
  REPAIR_REQUIRED: "转局部修复",
  FAILED: "请求失败",
  RESERVED: "请求中",
  RETRYING: "重试中",
  RECOVERED: "已恢复",
  RETRY_EXHAUSTED: "重试耗尽",
  RESULT_UNKNOWN: "结果未确认",
  ABANDONED: "已放弃",
  PASSED: "已通过",
  COMPLETED: "已完成",
  RETURNED: "已返回",
  VALIDATING: "结果处理中",
  VALIDATION_FAILED: "校验未通过",
  REPAIRING: "修复中",
  REPAIR_EXHAUSTED: "修复达上限",
  BOOK_ACCEPTED: "旧版已完成",
};
export const badge = (s) =>
  `<span class="badge ${s}">${e(stateNames[s] || s)}</span>`;
const requestPurpose = (r) => ({
  setup: "书籍设置", convert: "分批视觉转换", seam: "衔接修复", seams: "衔接修复",
  headings: "章节标题", references: "标签与引用", reference_repair: "识别局部修复", compile_repair: "编译修复", template_apply: "模板重排",
  image_repair: "图片检查与修复",
}[r.purpose] || r.purpose);
const requestTokens = (r) => {
  const usage = r.usage;
  if (!usage) return "未返回";
  const tokens = usage.tokens?.total;
  const input = tokens?.inputTokens ?? usage.input_tokens ?? usage.prompt_tokens;
  const output = tokens?.outputTokens ?? usage.output_tokens ?? usage.completion_tokens;
  return input != null || output != null ? `${input ?? "未知"} / ${output ?? "未知"}` : "未返回";
};
export function requestHistory(result, offset = 0) {
  const summary = Object.entries(result.states || {}).map(([state, count]) =>
    `${e(stateNames[state] || state)} ${count}`).join(" · ");
  return `<p class="hint">共 ${result.total} 组，${result.request_total} 次请求（含重试） · ${summary}</p>
    <p class="hint">已返回用量的 ${result.tokens.reported_calls} 次：输入 ${result.tokens.input.toLocaleString()} / 输出 ${result.tokens.output.toLocaleString()} token。展开可查看逐次结果；请求成功不代表该批次质量验收通过。</p>
    <div class="table-wrap"><table class="request-table"><thead><tr><th>用途 / 批次</th><th>当前结果</th><th>原因 / 说明</th><th>模型 / 思考</th><th>尝试记录</th></tr></thead><tbody>${result.rows.map((r) => {
      const pages = r.pages?.length ? `PDF ${r.pages.join(", ")} 页` : "";
      const error = r.error_message || r.error || "未记录具体原因";
      const reason = r.reason || r.error || r.error_message
        ? `${e(r.reason || "")}<small>${e(error)}</small>` : "—";
      return `<tr><td>${e(requestPurpose(r))}<small>${e(r.task_id || "")}</small><small>${e(pages)}</small></td>
        <td>${badge(r.state)}<small>共 ${r.attempt_count} 次 · 请求失败 ${r.failed_count} 次 · 校验失败 ${r.validation_failed_count || 0} 次</small>${r.repair_count ? `<small>曾修复 ${r.repair_count} 次</small>` : ""}</td>
        <td class="request-error">${reason}</td>
        <td>${e(r.model || "未知")}<small>思考 ${e(r.effort || "默认")}</small></td>
        <td><details data-request-group="${e(r.group_id)}"><summary>查看 ${r.attempt_count} 次尝试</summary><ol class="request-attempts">${[...r.attempts].reverse().map((a, i) =>
          `<li>第 ${i + 1} 次 · ${e(a.state === "COMPLETED" ? "已返回" : a.error === "SCHEMA_ERROR" ? "已返回" : stateNames[a.state] || a.state)}${a.validation_state === "PASSED" ? " · 校验通过" : a.validation_state === "FAILED" || a.error === "SCHEMA_ERROR" ? " · 校验未通过" : ""} · ${a.seconds} 秒<small>${e(a.model || "未知")} / ${e(a.effort || "默认")} · 输入 / 输出：${e(requestTokens(a))}</small>${a.error || a.error_message ? `<p class="error">${e(a.error_message || a.error)}</p>` : ""}${a.validation_error ? `<p class="error">${a.validation_state === "PASSED" ? "曾校验失败：" : "校验失败："}${e(a.validation_error.message || a.validation_error.code)}</p>` : ""}<small>请求 ${e(a.id)}</small></li>`
        ).join("")}</ol></details></td></tr>`;
    }).join("") || '<tr><td colspan="5">尚未发出请求</td></tr>'}</tbody></table></div>
    <div class="row"><button data-requests-offset="${Math.max(0, offset - 50)}" ${offset === 0 ? "disabled" : ""}>上一页</button><span class="hint">${result.total ? offset + 1 : 0}–${Math.min(offset + result.rows.length, result.total)} / ${result.total} 组</span><button data-requests-offset="${offset + 50}" ${offset + result.rows.length >= result.total ? "disabled" : ""}>下一页</button></div>`;
}
const picked = (value, current) => (value === current ? " selected" : "");
const head = (title, description, actions = "") =>
  `<div class="page-head"><div><h1>${title}</h1><p>${description}</p></div><div class="row">${actions}</div></div>`;
export function runRows(runs) {
  return runs.length
    ? `<div class="table-wrap"><table><thead><tr><th>文献</th><th>页面</th><th>状态</th><th></th></tr></thead><tbody>${runs.map((r) => `<tr><td>${e(r.title)}<small>${r.id.slice(0, 8)} · ${r.kind === "image_repair" ? "图片修复" : r.kind === "manual_layout" ? "公式排版修复" : r.kind === "template" ? "模板重排 · " + e(r.template?.name) : r.legacy ? "历史流程" : "视觉转换"}</small></td><td>${r.pages.join("–")}</td><td>${badge(r.state)}</td><td><button data-run="${r.id}">打开</button></td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty">尚未创建转换任务</div>`;
}
export { library, libraryResults } from "./library-view.js";
export function runs(data) {
  return (
    head("运行记录", "完成的批次即时保存，恢复时继续处理未完成部分。") +
    runRows(data.runs)
  );
}
export function runView(r, stages, liveConcurrency = false) {
  return (
    head(
      e(r.title),
      `${r.kind === "manual_layout" ? "公式排版修复" : r.kind === "template" ? "模板重排 · " + e(r.template?.name) : r.legacy ? "历史运行" : "视觉独立转换"} · PDF ${r.pages.join("–")} 页`,
      `<button id="retain-result"${r.state === "COMPLETED" ? "" : " hidden"}>保存到书库</button><a class="button" href="/api/runs/${r.id}/export">下载 TeX 工程</a><a class="button" id="result-pdf" href="/api/runs/${r.id}/pdf" data-pdf-preview="生成 PDF" data-pdf-title="${e(r.title)}">查看输出 PDF</a>`,
    ) +
    '<p id="result-save-note" class="hint" role="status">结果只保留在任务中，不自动更新书库。编译完成后可预览，再点击“保存到书库”设为书籍当前版本；旧版文件保留。</p>' +
    `<section class="run-status"><div class="row"><div id="status-badge">${badge(r.state)}</div><span id="progress-text"></span><span id="usage" class="muted"></span><div class="spacer"></div><button id="resume" class="primary">开始 / 恢复</button><button id="retry-unknown" title="保留已有成果，重新提交未返回结果的请求；上游可能重复计费。">重试未返回请求</button><button id="pause">暂停</button></div><progress id="run-progress" max="100" value="0" aria-label="转换批次完成进度"></progress>${
      r.legacy
        ? ""
        : `<div class="stages">${Object.entries(stages).filter(([id]) => !["template", "manual_layout"].includes(r.kind) || id === "finish")
            .map(
              ([id, title]) =>
                `<div id="stage-${id}" class="stage"><span>${e(title)}</span><small></small></div>`,
            )
            .join("")}</div>`
    }<p id="run-message" class="error" hidden></p><p id="compiler-activity" class="hint" hidden></p><p id="reference-review-note" class="hint" hidden><a href="/api/runs/${e(r.id)}/reference-review" target="_blank" rel="noopener"></a> · 留给用户核对，不阻塞编译</p>${
      r.legacy
        ? ""
        : `<div class="run-model-summary"><p id="run-model-live" class="hint"></p><div id="run-config-controls" class="row">${liveConcurrency ? `<label class="run-concurrency-label">并行任务数<input id="run-concurrency" type="number" min="1" step="1" required value="${r.pending_llm_concurrency ?? r.concurrency}"></label>` : ""}<button type="button" id="update-model-config">${liveConcurrency ? "更新配置" : "更新模型配置"}</button></div>${liveConcurrency ? '<p class="hint run-config-note">模型读取设置页已保存的配置，并发使用此处数值；仅应用到当前任务，已发出的请求继续执行。</p>' : ""}</div>`
    }</section>` +
    (r.legacy
      ? '<section class="empty">旧流程记录保留，可下载已有产物。新版转换请从书库创建。</section>'
      : `<section class="reader" data-reader-mode="split"><div class="reader-toolbar"><button id="previous" aria-label="上一页">←</button><label>PDF 页<input id="page-number" type="number" min="${r.pages[0]}" max="${r.pages[1]}" value="${r.pages[0]}"></label><span>/ ${r.pages[1]}</span><button id="next" aria-label="下一页">→</button><span class="spacer"></span><small id="page-state"></small><div class="segmented" aria-label="阅读布局"><button data-reader-mode="source" aria-pressed="false">原页</button><button data-reader-mode="split" aria-pressed="true">对照</button><button data-reader-mode="tex" aria-pressed="false">TeX</button></div><button id="reader-focus" aria-pressed="false">专注阅读</button></div><div class="reader-columns"><div class="page-pane"><img id="source-image" alt="当前 PDF 原页" decoding="async"></div><div class="tex-pane"><div class="pane-title">本页 TeX <button id="copy-tex" class="text-button" disabled>复制</button></div><pre id="page-tex">等待选择页面</pre></div></div></section><details id="batch-details"><summary>批次进度</summary><div id="batches" class="batch-grid"></div></details>`) +
    `<details id="requests-details"><summary>请求与用量</summary><div id="requests-content"></div></details>`
  );
}
export function settings(s, stages, selectedProvider) {
  return head("模型设置", "分别设置各阶段的模型、思考强度与连接。") +
    `<form id="settings-form" novalidate>
      <div class="section-heading provider-section-heading"><h2>模型供应商</h2></div>
      ${connectionEditor(s, selectedProvider)}
      ${stageModelSettings(s, stages)}
      <div class="settings-actions"><div><p id="settings-error" class="error" role="alert"></p></div><button type="submit" class="primary">保存设置</button></div>
    </form>${usageSection()}`;
}
