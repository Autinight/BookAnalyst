import assert from "node:assert/strict";
import test from "node:test";
import { api, setToken } from "../src/bookanalyst/static/api.js";

test("API preserves JSON payload and CSRF header", async (t) => {
  setToken("test-token");
  t.mock.method(global, "fetch", async (path, options) => {
    assert.equal(path, "/test");
    assert.equal(options.headers["x-bookanalyst-token"], "test-token");
    assert.deepEqual(JSON.parse(options.body), { name: "图片" });
    return new Response('{"ok":true}');
  });
  assert.deepEqual(await api("/test", { method: "POST", body: { name: "图片" } }), { ok: true });
});

function stalledFetch(path, { signal }) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) reject(signal.reason);
    else signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}

test("stalled GET has a bounded wait", async (t) => {
  t.mock.method(global, "fetch", stalledFetch);
  await assert.rejects(api("/test", { timeoutMs: 10 }), /服务响应超时，请稍后刷新/);
});

test("POST timeout does not claim the backend operation was cancelled", async (t) => {
  t.mock.method(global, "fetch", stalledFetch);
  await assert.rejects(api("/test", { method: "POST", body: {}, timeoutMs: 10 }), /操作可能仍在后台执行/);
});

test("caller cancellation is preserved instead of reported as a timeout", async (t) => {
  t.mock.method(global, "fetch", stalledFetch);
  const controller = new AbortController();
  const pending = api("/test", { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
});
