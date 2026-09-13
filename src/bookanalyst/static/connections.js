import { escape as e } from "./api.js";

export const connectionName = (id, conn) => conn.name || (conn.kind === "codex_chatgpt" ? "ChatGPT 官方订阅" : id === "custom_api" ? "自定义 API / vLLM" : id);

export function connectionCard(id, conn, unsaved = false) {
  const custom = conn.kind === "openai_compatible";
  const input = (field, label, type = "text", attrs = "") => `<label>${label}<input name="${e(id)}_${field}" data-connection-field="${field}" type="${type}" value="${e(field === "name" ? connectionName(id, conn) : conn[field] ?? "")}" ${attrs}></label>`;
  const select = (field, label, choices) => `<label>${label}<select name="${e(id)}_${field}" data-connection-field="${field}">${choices.map(([value, title]) => `<option value="${value}"${conn[field] === value ? " selected" : ""}>${title}</option>`).join("")}</select></label>`;
  return `<section class="card" data-connection-id="${e(id)}">
    <h2 data-connection-title>${e(connectionName(id, conn))}</h2>
    ${input("name", "供应商名称", "text", 'required maxlength="100" data-provider-name')}
    ${custom ? `<label class="check"><input type="checkbox" data-connection-field="enabled" ${conn.enabled ? "checked" : ""}>启用</label>
      ${input("base_url", "API 地址")}
      <div class="fields">${select("protocol", "协议", [["chat_completions", "Chat Completions"], ["responses", "Responses"]])}
      ${input("model_id", "模型")}
      ${select("auth_mode", "鉴权", [["bearer", "Bearer"], ["none", "本地无鉴权"]])}
      <label>API 密钥<input name="${e(id)}_api_key" data-connection-field="api_key" type="password" autocomplete="new-password" spellcheck="false" autocapitalize="off" placeholder="${conn.api_key_configured ? "已配置，留空保留现有密钥" : "粘贴 API 密钥"}"><small>密钥保存在本机，保存后不回显；留空保留现有密钥。</small></label></div>
      ${conn.api_key_configured ? '<label class="check"><input type="checkbox" data-connection-field="clear_api_key">清除已保存密钥</label>' : ""}
      ${select("image_support", "图像支持", [["unknown", "待确认"], ["supported", "已确认支持"], ["unsupported", "不支持"]])}`
      : input("model_id", "默认模型", "text", 'placeholder="使用上游默认模型"')}
    <div class="fields">${conn.max_in_flight !== undefined ? input("max_in_flight", "连接并发", "number", 'min="1" required') : ""}${input("timeout_seconds", "请求等待（秒）", "number", 'min="1" max="3600" required')}</div>
    <div class="row"><button type="button" data-connection-check ${unsaved ? "disabled" : ""}>检查连接</button>${custom ? "" : '<button type="button" data-connection-login>登录</button>'}</div>
    <p class="hint" data-connection-status>${unsaved ? "保存设置后可检查连接。" : "检查使用已保存的设置。"}</p>
  </section>`;
}

export function readConnections(form, settings) {
  const connections = structuredClone(settings.connections);
  form.querySelectorAll("[data-connection-id]").forEach(card => {
    const conn = connections[card.dataset.connectionId];
    card.querySelectorAll("[data-connection-field]").forEach(input => {
      conn[input.dataset.connectionField] = input.type === "checkbox" ? input.checked
        : input.type === "number" ? Number(input.value) : input.value.trim();
    });
  });
  return connections;
}

export function refreshConnectionChoices(form, settings) {
  form.querySelectorAll("[data-stage-connection]").forEach(select => {
    const selected = select.value;
    select.innerHTML = Object.entries(settings.connections)
      .filter(([id, conn]) => conn.enabled || id === selected)
      .map(([id, conn]) => `<option value="${e(id)}"${id === selected ? " selected" : ""}>${e(connectionName(id, conn))}</option>`).join("");
  });
}

export function addProvider(form, settings) {
  const id = `api_${crypto.randomUUID().replaceAll("-", "")}`;
  const conn = {
    name: "新 API 供应商", kind: "openai_compatible", enabled: false,
    base_url: "", protocol: "chat_completions", model_id: "", auth_mode: "bearer",
    image_support: "unknown", timeout_seconds: 600,
  };
  // Static assets can refresh while an older server is still running a book.
  const legacy = Object.values(settings.connections).find(c => c.max_in_flight !== undefined);
  if (legacy) conn.max_in_flight = legacy.max_in_flight;
  settings.connections[id] = conn;
  form.querySelector("#connection-cards").insertAdjacentHTML("beforeend", connectionCard(id, conn, true));
  const list = document.createElement("datalist");
  list.id = `stage-models-${id}`;
  form.append(list);
  const name = form.querySelector(`[data-connection-id="${id}"] [data-provider-name]`);
  name.focus();
  name.select();
}
