let token = "";
export function setToken(value) {
  token = value;
}
export async function api(path, { method = "GET", body, signal } = {}) {
  const headers = {};
  if (method !== "GET") headers["x-bookanalyst-token"] = token;
  if (body && !(body instanceof FormData))
    headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    signal,
    body:
      body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok)
    throw Error(
      data.message || data.detail?.map?.((x) => x.msg).join("；") || "请求失败",
    );
  return data;
}
export const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
