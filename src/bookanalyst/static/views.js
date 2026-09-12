import { connectionCard } from "./connections.js";
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
  BOOK_ACCEPTED: "旧版已完成",
};
export const badge = (s) =>
  `<span class="badge ${s}">${e(stateNames[s] || s)}</span>`;
const requestPurpose = (r) => ({
  setup: "书籍设置", convert: "分批视觉转换", seam: "衔接修复", seams: "衔接修复",
  headings: "章节标题", references: "标签与引用", reference_repair: "识别局部修复", compile_repair: "编译修复", template_apply: "模板重排",
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
      const recovered = r.state === "RECOVERED";
      const reason = recovered
        ? `此前失败 ${r.failed_count} 次，已恢复`
        : `${e(r.reason)}<small>${e(error)}</small>`;
      return `<tr><td>${e(requestPurpose(r))}<small>${e(r.task_id || "")}</small><small>${e(pages)}</small></td>
        <td>${badge(r.state)}<small>共 ${r.attempt_count} 次 · 失败 ${r.failed_count} 次</small></td>
        <td class="request-error">${r.failed_count || r.state === "DEFERRED" ? reason : "—"}</td>
        <td>${e(r.model || "未知")}<small>思考 ${e(r.effort || "默认")}</small></td>
        <td><details data-request-group="${e(r.group_id)}"><summary>查看 ${r.attempt_count} 次尝试</summary><ol class="request-attempts">${[...r.attempts].reverse().map((a, i) =>
          `<li>第 ${i + 1} 次 · ${e(stateNames[a.state] || a.state)} · ${a.seconds} 秒<small>${e(a.model || "未知")} / ${e(a.effort || "默认")} · 输入 / 输出：${e(requestTokens(a))}</small>${a.error || a.error_message ? `<p class="error">${e(a.error_message || a.error)}</p>` : ""}<small>请求 ${e(a.id)}</small></li>`
        ).join("")}</ol></details></td></tr>`;
    }).join("") || '<tr><td colspan="5">尚未发出请求</td></tr>'}</tbody></table></div>
    <div class="row"><button data-requests-offset="${Math.max(0, offset - 50)}" ${offset === 0 ? "disabled" : ""}>上一页</button><span class="hint">${result.total ? offset + 1 : 0}–${Math.min(offset + result.rows.length, result.total)} / ${result.total} 组</span><button data-requests-offset="${offset + 50}" ${offset + result.rows.length >= result.total ? "disabled" : ""}>下一页</button></div>`;
}
const picked = (value, current) => (value === current ? " selected" : "");
const head = (title, description, actions = "") =>
  `<div class="page-head"><div><h1>${title}</h1><p>${description}</p></div><div class="row">${actions}</div></div>`;
export function runRows(runs) {
  return runs.length
    ? `<div class="table-wrap"><table><thead><tr><th>文献</th><th>页面</th><th>状态</th><th></th></tr></thead><tbody>${runs.map((r) => `<tr><td>${e(r.title)}<small>${r.id.slice(0, 8)} · ${r.kind === "manual_layout" ? "公式排版修复" : r.kind === "template" ? "模板重排 · " + e(r.template?.name) : r.legacy ? "历史流程" : "视觉转换"}</small></td><td>${r.pages.join("–")}</td><td>${badge(r.state)}</td><td><button data-run="${r.id}">打开</button></td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty">尚未创建转换任务</div>`;
}
const libraryIcons = {
  folder: '<path d="M3 7V5h6l2 2h10v12H3z"/><path d="M9 13h7m-3-3 3 3-3 3"/>',
  edit: '<path d="m14 4 6 6M4 20l5-1L20 8a2 2 0 0 0-5-5L4 14z"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
};
const libraryIcon = (name) => `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${libraryIcons[name]}</svg>`;
function outputNote(book) {
  return book.result ? `<small class="book-output-note">已保留 · 原书 ${book.result.pages.join("–")} 页 → PDF ${book.result.pdf_pages} 页${book.result.template_name ? ` · ${e(book.result.template_name)}` : ""}${book.result.review_count ? ` · <a href="/api/books/${e(book.id)}/reference-review" target="_blank" rel="noopener">引用待确认 ${book.result.review_count} 条</a>` : ""}</small>` : "";
}
function bookActions(book, trash, supported) {
  const unavailable = supported ? "" : ' disabled title="当前服务更新后可用"';
  return `<div class="book-actions"><span class="book-open-actions">
    ${trash ? `<button data-book-action="restore" data-book-id="${e(book.id)}"${unavailable}>恢复</button>` : `<button data-new="${e(book.id)}" class="primary">转换</button>`}
    <a class="button" href="/api/books/${e(book.id)}/pdf" target="_blank" rel="noopener" aria-label="查看 ${e(book.title)} 的${book.result ? "生成" : "原书"} PDF" title="${book.result ? "打开已保留的生成 PDF" : "打开原书 PDF"}">PDF ↗</a>
    ${book.result ? `<a class="button" href="/api/books/${e(book.id)}/source" target="_blank" rel="noopener">原书 ↗</a><a class="button" href="/api/books/${e(book.id)}/project" title="下载已保留的 TeX 工程">TeX ↓</a>${trash ? "" : `<button data-book-action="template" data-book-id="${e(book.id)}">换模板</button>`}` : ""}
    </span><span class="book-manage-actions"><button class="book-icon" data-book-action="reveal" data-book-id="${e(book.id)}" aria-label="在资源管理器中显示 ${e(book.title)}"${supported ? ' title="在资源管理器中显示"' : unavailable}>${libraryIcon("folder")}</button>
    ${trash ? "" : `<button class="book-icon" data-book-action="rename" data-book-id="${e(book.id)}" aria-label="重命名 ${e(book.title)}"${supported ? ' title="重命名"' : unavailable}>${libraryIcon("edit")}</button><button class="book-icon danger" data-book-action="delete" data-book-id="${e(book.id)}" aria-label="删除 ${e(book.title)}"${supported ? ' title="移入回收站"' : unavailable}>${libraryIcon("trash")}</button>`}
  </span></div>`;
}
export function libraryResults(data, state = {}) {
  const trash = state.trash || false, query = (state.query || "").trim().toLocaleLowerCase();
  let books = [...(trash ? data.deleted_books || [] : data.books)];
  if (query) books = books.filter(b => b.title.toLocaleLowerCase().includes(query));
  if (state.sort === "title") books.sort((a, b) => a.title.localeCompare(b.title, "zh-CN", { numeric: true }));
  if (state.sort === "pages") books.sort((a, b) => b.page_count - a.page_count);
  const count = `<p class="library-count" role="status">${books.length} 本${query ? "匹配书籍" : ""}${trash ? " · 回收站中的原 PDF 和已有任务保留" : ""}</p>`;
  if (!books.length) return count + `<div class="empty">${query ? "没有匹配的书籍，试试其他书名。" : trash ? "回收站为空" : '书库中还没有书籍。<button data-upload>导入 PDF</button>'}</div>`;
  if (state.view === "grid") return count + `<div class="library-cards">${books.map(b => `<article class="library-card"><div class="library-card-heading"><a class="library-cover" href="/api/books/${e(b.id)}/pdf" target="_blank" rel="noopener"><img loading="lazy" decoding="async" src="/api/books/${e(b.id)}/image?page=1&dpi=55" alt="${e(b.title)}首页"></a><div><h2 title="${e(b.title)}">${e(b.title)}</h2><p>${b.page_count} 页 · ${(b.size_bytes / 1048576).toFixed(1)} MB</p>${outputNote(b)}</div></div>${bookActions(b, trash, data.library_management)}</article>`).join("")}</div>`;
  return count + `<div class="table-wrap library-list"><table><thead><tr><th>书名</th><th>页数</th><th>大小</th><th>操作</th></tr></thead><tbody>${books.map(b => `<tr><td><a class="library-title" title="${e(b.title)}" href="/api/books/${e(b.id)}/pdf" target="_blank" rel="noopener">${e(b.title)}</a>${outputNote(b)}</td><td>${b.page_count}</td><td>${(b.size_bytes / 1048576).toFixed(1)} MB</td><td>${bookActions(b, trash, data.library_management)}</td></tr>`).join("")}</tbody></table></div>`;
}
export function library(data, state = {}) {
  return head("书库", "原书、生成 PDF 和 TeX 工程统一保存在书库。", '<button data-upload class="primary">导入 PDF</button>') +
    `<div class="library-toolbar"><div class="segmented" aria-label="书籍范围"><button data-library-trash="false" aria-pressed="${!state.trash}">全部书籍</button><button data-library-trash="true" aria-pressed="${Boolean(state.trash)}">回收站${data.deleted_books?.length ? ` · ${data.deleted_books.length}` : ""}</button></div><input id="library-search" type="search" placeholder="搜索书名…" aria-label="搜索书名" value="${e(state.query || "")}"><select id="library-sort" aria-label="书籍排序"><option value="recent"${picked("recent", state.sort || "recent")}>最近导入</option><option value="title"${picked("title", state.sort)}>书名</option><option value="pages"${picked("pages", state.sort)}>页数从多到少</option></select><div class="segmented" aria-label="书库布局"><button data-library-view="list" aria-pressed="${state.view !== "grid"}">列表</button><button data-library-view="grid" aria-pressed="${state.view === "grid"}">卡片</button></div></div>
    ${data.library_management ? "" : '<p class="hint">紧凑布局已可用。管理功能将在后台任务结束、服务更新后启用。</p>'}
    <div id="library-results">${libraryResults(data, state)}</div>
    <dialog id="book-dialog" aria-labelledby="book-dialog-title"><form id="book-form"><div class="row"><h2 id="book-dialog-title">管理书籍</h2><button type="button" data-book-close aria-label="关闭">×</button></div><p id="book-dialog-description"></p><label id="book-title-label">书名<input name="title" maxlength="300" required></label><p id="book-error" class="error" role="alert"></p><div class="row end"><button type="button" data-book-close>取消</button><button id="book-submit" type="submit" class="primary">保存</button></div></form></dialog>`;
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
      `${r.kind === "manual_layout" ? "公式排版修复" : r.kind === "template" ? "模板重排 · " + e(r.template?.name) : r.legacy ? "历史运行" : "视觉独立转换"} · PDF ${r.pages.join("–")} 页`,
      `<button id="retain-result"${r.state === "COMPLETED" ? "" : " hidden"}>保留到书库</button><a class="button" href="/api/runs/${r.id}/export">下载 TeX 工程</a><a class="button" id="result-pdf" href="/api/runs/${r.id}/pdf" target="_blank" rel="noopener">查看输出 PDF</a>`,
    ) +
    `<section class="run-status"><div class="row"><div id="status-badge">${badge(r.state)}</div><span id="progress-text"></span><span id="usage" class="muted"></span><div class="spacer"></div><button id="resume" class="primary">开始 / 恢复</button><button id="retry-unknown" title="保留已有成果，重新提交未返回结果的请求；上游可能重复计费。">重试未返回请求</button><button id="pause">暂停</button></div>${
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
        : `<div class="run-model-summary"><p id="run-model-live" class="hint"></p><button type="button" id="update-model-config">更新模型配置</button></div>`
    }</section>` +
    (r.legacy
      ? '<section class="empty">旧流程记录保留，可下载已有产物。新版转换请从书库创建。</section>'
      : `<section class="reader"><div class="reader-toolbar"><button id="previous" aria-label="上一页">←</button><label>PDF 页<input id="page-number" type="number" min="${r.pages[0]}" max="${r.pages[1]}" value="${r.pages[0]}"></label><span>/ ${r.pages[1]}</span><button id="next" aria-label="下一页">→</button><span class="spacer"></span><small id="page-state"></small></div><div class="reader-columns"><div class="page-pane"><img id="source-image" alt="当前 PDF 原页" decoding="async"></div><div class="tex-pane"><div class="pane-title">本页 TeX <span>按需加载</span></div><pre id="page-tex">等待选择页面</pre></div></div></section><details id="batch-details"><summary>批次进度</summary><div id="batches" class="batch-grid"></div></details>`) +
    `<details id="requests-details"><summary>请求与用量</summary><div id="requests-content"></div></details>`
  );
}
export function settings(s, stages) {
  return head("模型设置", "分别设置各阶段的模型、思考强度与连接。") +
    `<form id="settings-form">${stageModelSettings(s, stages)}
      <div class="row"><h2>模型连接</h2><button type="button" id="add-provider">添加 API 供应商</button></div>
      <p class="hint">可添加多个供应商，直接修改供应商名称即可重命名。填写后点击“保存设置”。</p>
      <div class="settings-grid" id="connection-cards">${Object.entries(s.connections).map(([id, conn]) => connectionCard(id, conn)).join("")}</div>
      <p id="settings-error" class="error" role="alert"></p><button type="submit" class="primary">保存设置</button>
    </form>`;
}
