let token = "";
export function setToken(value) {
  token = value;
}
export async function api(path, { method = "GET", body, signal, timeoutMs = method === "GET" ? 15000 : 60000 } = {}) {
  const headers = {};
  if (method !== "GET") headers["x-bookanalyst-token"] = token;
  if (body && !(body instanceof FormData))
    headers["Content-Type"] = "application/json";
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), timeoutMs);
  try {
    const response = await fetch(path, {
      method,
      headers,
      signal: signal ? AbortSignal.any([signal, timeout.signal]) : timeout.signal,
      body:
        body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json();
    if (!response.ok)
      throw Error(
        data.message || data.detail?.map?.((x) => x.msg).join("；") || "请求失败",
      );
    return data;
  } catch (error) {
    if (timeout.signal.aborted && !signal?.aborted)
      throw Error(method === "GET" ? "服务响应超时，请稍后刷新。" : "服务响应超时，操作可能仍在后台执行，请先查看任务状态。");
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
export const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
