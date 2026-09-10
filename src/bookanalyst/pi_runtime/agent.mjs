// pi.dev owns the tool loop, retries, compaction and persistent conversation.
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";
import { InMemoryCredentialStore } from "@earendil-works/pi-ai";
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from "@earendil-works/pi-coding-agent";

const input = readline.createInterface({ input: process.stdin });
const emit = (event) => process.stdout.write(JSON.stringify(event) + "\n");
let session, aborted = false;
const config = await new Promise((resolve, reject) => {
  input.once("line", line => { try { resolve(JSON.parse(line)); } catch (error) { reject(error); } });
  input.once("close", () => reject(new Error("Missing configuration")));
});
input.on("line", line => {
  try {
    if (JSON.parse(line).type === "abort") {
      aborted = true;
      void session?.abort();
    }
  } catch { /* Ignore malformed control messages; configuration is already fixed. */ }
});
input.on("close", () => { aborted = true; void session?.abort(); });

try {
  const cwd = path.resolve(config.project);
  const agentDir = path.resolve(config.agent_dir);
  fs.mkdirSync(agentDir, { recursive: true });
  const modelRuntime = await ModelRuntime.create({
    credentials: new InMemoryCredentialStore(), modelsPath: null,
    modelsStorePath: path.join(agentDir, "models-cache.json"), refreshOnCreate: false,
  });
  const api = config.protocol === "responses" ? "openai-responses" : "openai-completions";
  const known = modelRuntime.getModels().find(m => m.id === config.model_id);
  const level = { none: "off", minimal: "minimal", low: "low", medium: "medium", high: "high", xhigh: "xhigh", max: "xhigh", ultra: "xhigh" }[config.effort] || "medium";
  modelRuntime.registerProvider("bookanalyst", {
    name: "BookAnalyst API", api, baseUrl: config.base_url,
    authHeader: config.auth_mode !== "none",
    models: [{
      id: config.model_id, name: config.model_id, reasoning: level !== "off",
      input: config.image_support === "supported" ? ["text", "image"] : ["text"],
      contextWindow: known?.contextWindow || 128000,
      maxTokens: known?.maxTokens || 16384,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      thinkingLevelMap: { [level]: config.effort },
      ...(api === "openai-completions" ? { compat: { supportsDeveloperRole: false, maxTokensField: "max_tokens" } } : {}),
    }],
  });
  // Kept in memory; never place API keys in prompts, event logs or argv.
  await modelRuntime.setRuntimeApiKey("bookanalyst", config.api_key || "bookanalyst-no-auth");
  delete config.api_key;
  const model = modelRuntime.getModel("bookanalyst", config.model_id);
  if (!model) throw new Error("Configured model could not be registered");
  const settingsManager = SettingsManager.inMemory({
    compaction: { enabled: true },
    retry: { enabled: true, provider: { timeoutMs: config.timeout_seconds * 1000 } },
  });
  const resourceLoader = new DefaultResourceLoader({
    cwd, agentDir, settingsManager, noExtensions: true, noSkills: true,
    noPromptTemplates: true, noThemes: true, noContextFiles: true,
    appendSystemPrompt: [config.instructions],
  });
  await resourceLoader.reload();
  if (config.session_file && !fs.existsSync(config.session_file)) {
    throw new Error("Saved Pi session is missing: " + config.session_file);
  }
  const manager = config.session_file
    ? SessionManager.open(config.session_file, config.session_dir, cwd)
    : SessionManager.create(cwd, config.session_dir);
  ({ session } = await createAgentSession({
    cwd, agentDir, modelRuntime, model, thinkingLevel: level, settingsManager,
    resourceLoader, sessionManager: manager,
    tools: ["read", "edit", "write", "grep", "find", "ls", process.platform === "win32" ? "powershell" : "bash"],
  }));
  emit({ type: "ready", session_id: session.sessionId, session_file: session.sessionFile,
    context_window: model.contextWindow, max_output_tokens: model.maxTokens,
    thinking_level: session.thinkingLevel });
  let lastAssistant;
  session.subscribe(event => {
    if (event.type === "message_end" && event.message.role === "assistant") lastAssistant = event.message;
    if (["tool_execution_start", "tool_execution_end", "message_end", "auto_retry_start", "auto_retry_end", "compaction_start", "compaction_end"].includes(event.type)) emit(event);
  });
  if (aborted) throw new Error("Pi interrupted");
  await session.prompt(config.prompt, { expandPromptTemplates: false });
  if (aborted || lastAssistant?.stopReason === "aborted") {
    emit({ type: "done", status: "INTERRUPTED" });
  } else if (!lastAssistant || lastAssistant.stopReason === "error") {
    throw new Error(lastAssistant?.errorMessage || "Pi returned no assistant result");
  } else {
    emit({ type: "done", status: "COMPLETED",
      text: lastAssistant.content.filter(c => c.type === "text").map(c => c.text).join("\n") });
  }
} catch (error) {
  emit({ type: "done", status: aborted ? "INTERRUPTED" : "FAILED", error: String(error.message || error) });
  process.exitCode = aborted ? 0 : 1;
} finally {
  session?.dispose();
  input.close();
  process.stdin.destroy();
}
