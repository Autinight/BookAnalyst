import {api, escape as e} from "./api.js";
export function usageSection() {
  return `<section class="card provider-usage"><div class="section-heading"><h2>模型用量</h2><button type="button" data-usage-refresh>刷新用量</button></div>
    <div class="row"><label>时间范围<select id="usage-period"><option value="7">最近 7 天</option><option value="30">最近 30 天</option><option value="0">全部</option></select></label>
    <label>查看方式<select id="usage-view"><option value="provider">按供应商</option><option value="model">按模型</option><option value="purpose">按阶段</option><option value="day">按日期</option><option value="requests">请求明细</option></select></label></div>
    <p class="hint">本地请求记录；未返回的 token 用量显示为 —。订阅费用和上游内部调用不作估算。</p><div id="usage-content" class="table-scroll"></div></section>`;
}
export async function loadUsage() {
  const target=document.querySelector("#usage-content");
  if(!target) return;
  const days=document.querySelector("#usage-period").value, view=document.querySelector("#usage-view").value;
  target.textContent="正在读取用量…";
  try {
    const {entries}=await api("/api/providers/usage?days="+days);
    if(!target.isConnected) return;
    if(!entries.length) {target.textContent="此时间范围内暂无请求。";return;}
    const num=n => n == null ? "—" : n.toLocaleString();
    const rows=view==="requests" ? entries.map(r => ({...r,label:r.model,count:1,failed:r.state==="FAILED" ? 1 : 0}))
      : Object.values(entries.reduce((groups,r) => {
        const key=view==="day" ? new Date(r.started_at*1000).toLocaleDateString() : r[view] || "未知";
        const g=groups[key] ||= {label:key,count:0,failed:0,input:null,output:null,cached:null};
        g.count++; if(r.state==="FAILED") g.failed++;
        for(const k of ["input","output","cached"]) if(r[k]!=null) g[k]=(g[k] || 0)+r[k];
        return groups;
      },Object.create(null)));
    target.innerHTML=`<table><thead><tr><th>名称</th><th>请求</th><th>失败</th><th>输入 token</th><th>输出 token</th><th>缓存 token</th></tr></thead><tbody>${rows.map(r => `<tr><td>${e(r.label)}</td><td>${r.count}</td><td>${r.failed}</td><td>${num(r.input)}</td><td>${num(r.output)}</td><td>${num(r.cached)}</td></tr>`).join("")}</tbody></table>`;
  } catch(error) {target.textContent=error.message;}
}

