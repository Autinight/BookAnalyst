import assert from "node:assert/strict";
import { requestHistory, badge } from "../src/bookanalyst/static/views.js";

const rejected = {
  id: "first", state: "COMPLETED", seconds: 1,
  validation_state: "FAILED",
  validation_error: { code: "STALE_PATCH", message: "<wrong boundary>" },
};
const row = {
  group_id: "seams:seam-0004", task_id: "seam-0004", purpose: "seams",
  state: "REPAIR_EXHAUSTED", pages: [25, 26], attempt_count: 9,
  failed_count: 0, validation_failed_count: 9, repair_count: 8,
  reason: "本轮自动修复已达上限", error: "REPAIR_LIMIT", error_message: "<8 repairs exhausted>",
  attempts: [rejected],
};
const render = (r) => requestHistory({
  total: 1, request_total: 9, states: { [r.state]: 1 }, rows: [r],
  tokens: { reported_calls: 0, input: 0, output: 0 },
});
const exhausted = render(row);
assert.ok(exhausted.includes("修复达上限"));
assert.ok(exhausted.includes("请求失败 0 次 · 校验失败 9 次"));
assert.ok(exhausted.includes("曾修复 8 次"));
assert.ok(exhausted.includes("&lt;8 repairs exhausted&gt;"));
assert.ok(exhausted.includes("已返回 · 校验未通过"));
assert.ok(!exhausted.includes("已完成"));
assert.ok(!exhausted.includes("<wrong boundary>"));

const passed = render({
  ...row, state: "PASSED", reason: "最近一次校验失败",
  error: "STALE_PATCH", error_message: "<wrong boundary>",
  attempts: [{ ...rejected, validation_state: "PASSED" }],
});
assert.ok(passed.includes("已通过"));
assert.ok(passed.includes("已返回 · 校验通过"));
assert.ok(passed.includes("曾校验失败：&lt;wrong boundary&gt;"));
assert.ok(badge("COMPLETED").includes("已完成")); // The run's completion label is unchanged.
console.log("Request history rendering passed.");
