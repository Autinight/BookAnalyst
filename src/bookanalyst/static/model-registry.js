// One registry shape for provider editing and every stage's model picker.
export const capabilityFields = [["image", "视觉"], ["video", "视频"], ["audio", "音频"], ["reasoning", "推理"], ["xhigh", "超深推理"], ["max", "极致推理"]];
export const contextPresets = [["64K",65536],["128K",131072],["200K",200000],["256K",262144],["1M",1048576]];
export const outputPresets = [["8K",8192],["16K",16384],["32K",32768],["64K",65536]];
export const modelList = conn => conn?.models || (conn?.model_id ? [{id:conn.model_id}] : []);
export const registeredModel = (conn,id) => modelList(conn).find(m => m.id === (id || conn?.model_id));
export function stageModels(conn,stage) {
  const models=conn?.models || [];
  return ["convert","image_repair"].includes(stage) ? models.filter(model=>model.image===true) : models;
}
export function formatTokens(value) {
  return value % 1048576 === 0 ? `${value/1048576}M` : value % 1024 === 0 ? `${value/1024}K` : value >= 1000 && value % 1000 === 0 ? `${value/1000}K` : value.toLocaleString();
}
export function modelSummary(model) {
  if (!model) return "未注册参数";
  return [model.context && `上下文 ${formatTokens(model.context)}`, model.max_output && `输出 ${formatTokens(model.max_output)}`,
    ...capabilityFields.filter(([key]) => model[key] === true).map(([,label]) => label)].filter(Boolean).join(" · ");
}
export function modelEfforts(model) {
  if (model?.reasoning === false) return [];
  // Missing metadata remains unknown for existing installations.
  return ["low","medium","high",...(model?.xhigh !== false ? ["xhigh"] : []),...(model?.max !== false ? ["max"] : [])];
}
