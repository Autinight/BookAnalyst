(function bookanalystApp(){
"use strict";
const $=s=>document.querySelector(s), esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let data, page="library", runId=null, pageIdx=13, selected=null, viewData=null, loading=false;
const labels={library:"书籍",runs:"运行记录",compare:"内容对照",settings:"设置"};
const states={PENDING:"待开始",PARSE_IMPORTED:"解析缓存已导入",PAUSED:"已暂停",RUNNING:"运行中",PASSED:"已通过",NEEDS_REVIEW:"需要处理",FAILED:"未通过",STALE:"已失效",TEST_COMPLETED:"解析测试完成",SAMPLE_ACCEPTED:"样本已验收",BOOK_ACCEPTED:"全书已验收",FIXTURE_PASSED:"离线夹具通过"};
const profiles={cloud_smoke:"线上解析测试",local_smoke:"本地解析测试",offline_fixture:"离线程序夹具",llm_smoke:"样本转换",book:"书籍转换"};
const roles={analyst:"全书设置",converter:"TeX 转换",reviewer:"独立审查",visual_reviewer:"多模态核对"};
const accepted=s=>["SAMPLE_ACCEPTED","BOOK_ACCEPTED","FIXTURE_PASSED"].includes(s);
function badge(state){return `<span class="badge ${accepted(state)||state==="PASSED"||state==="TEST_COMPLETED"?"green":state==="NEEDS_REVIEW"?"amber":state==="FAILED"?"red":""}">${esc(states[state]||state)}</span>`;}
function toast(message){$("#toast").textContent=message;$("#toast").hidden=false;setTimeout(()=>$("#toast").hidden=true,5000);}
async function api(url,method="GET",body){const headers={};if(method!=="GET")headers["x-bookanalyst-token"]=data?.token||"";if(body&&!(body instanceof FormData))headers["Content-Type"]="application/json";
 const response=await fetch(url,{method,headers,body:body?(body instanceof FormData?body:JSON.stringify(body)):undefined});const result=await response.json();
 if(!response.ok)throw Error(result.message||result.detail?.map?.(e=>e.msg).join("；")||"操作失败，请检查输入");return result;}
async function refresh(){data=await api("/api/bootstrap");$("#run-count").textContent=data.runs.length;}
function header(title,subtitle,actions=""){return `<div class="page-head"><div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div><div class="actions">${actions}</div></div>`;}
const startButton=`<button class="button primary" data-action="new-run">＋ 创建运行</button>`;
function empty(title,detail){return `<div class="empty"><div class="empty-icon">⌁</div><strong>${title}</strong><p>${detail}</p></div>`;}
function runTable(runs){if(!runs.length)return empty("还没有运行记录","选择一本书，创建你的第一次解析或转换运行。");
return `<div class="table-wrap"><table><thead><tr><th>书籍 / 运行</th><th>处理范围</th><th>模式</th><th>状态</th><th></th></tr></thead><tbody>${runs.map(r=>`<tr><td class="table-title">${esc(r.source.title)}<small>${r.id.slice(0,8)} · 修订 ${r.revision}</small></td><td>PDF ${r.config.start_page}–${r.config.end_page} 页<small>${r.scope==="fixture"?"夹具证据":r.scope==="full"?"整本书籍":"有限样本"}</small></td><td>${profiles[r.config.profile]}</td><td>${badge(r.state)}</td><td><button class="button small" data-run="${r.id}">打开 →</button></td></tr>`).join("")}</tbody></table></div>`;}
function library(){return header("书籍","从原始书页出发，恢复数学内容与作者的表达。",`<button class="button" data-action="upload">↑ 导入 PDF</button>${startButton}`)+
`<div class="stats"><div class="stat"><span>书库</span><strong>${data.books.length}<small>本书籍</small></strong></div><div class="stat"><span>进行中的运行</span><strong>${data.runs.filter(r=>r.state==="RUNNING").length}<small>个运行</small></strong></div><div class="stat"><span>已验收结果</span><strong>${data.runs.filter(r=>accepted(r.state)&&r.scope!=="fixture").length}<small>份结果</small></strong></div></div>
<div class="section-line"><h2>我的书籍</h2><span>全部 ${data.books.length} 本</span></div>`+
data.books.map(b=>`<article class="book-card"><div class="book-art"><img src="/api/books/${b.id}/image?page_idx=${Math.min(13,b.page_count-1)}&dpi=72" alt="书籍原始页面缩略图"><small>原始 PDF · 第 ${Math.min(14,b.page_count)} 页</small></div><div class="book-info"><span class="eyebrow">MATHEMATICAL LIBRARY</span><h2 class="book-title">${esc(b.title)}</h2><div class="book-meta"><span>PDF 文档</span><span>${b.page_count} 页</span><span>${(b.size_bytes/1024/1024).toFixed(1)} MB</span></div><div class="book-bottom"><span class="badge green">${b.id==="elliptic-pde-second-order"?"默认测试用书":"已保存到本地"}</span><div class="actions"><a class="button small" href="/api/books/${b.id}/pdf" target="_blank" rel="noopener">查看原书 ↗</a><button class="button small primary" data-book="${b.id}">创建运行 →</button></div></div></div></article>`).join("")+
`<div class="section-line"><h2>最近运行</h2><button class="button small" data-page="runs">查看全部 →</button></div>`+runTable(data.runs.slice(0,4))+
`<div class="intro-note"><b>↳</b><span>保留数学表达、标题结构与作者编号。原书的页面布局用于定位和核对，最终由 TeX 统一排版。</span></div>`;}
function settings(){const s=data.settings,o=s.connections.openai_subscription,c=s.connections.custom_api,m=s.mineru;
return header("设置","管理解析服务与模型连接。运行中的任务通过独立工作流执行。")+`<form id="settings-form"><div class="settings-grid">
<section class="panel"><div class="card-heading"><h2>ChatGPT 官方订阅</h2><span class="badge green">优先接入</span></div><p class="card-intro">通过官方登录完成授权，选择账户可用模型。</p>
<label>默认模型<input name="official_model" list="models-openai_subscription" value="${esc(o.model_id)}" placeholder="留空使用账户默认模型"></label><label>连接并发上限<input type="number" min="1" name="official_concurrency" value="${o.max_in_flight}"></label>
<label>请求等待时间（秒）<input name="official_timeout" type="number" min="1" max="3600" value="${o.timeout_seconds}"></label><div class="actions"><button class="button small" type="button" data-action="account-status">检查登录状态</button><button class="button small primary" type="button" data-action="login">登录 ChatGPT ↗</button></div><p id="account-status" class="status-detail"></p></section>
<section class="panel"><div class="card-heading"><h2>自定义 API</h2><label style="margin:0"><input type="checkbox" name="custom_enabled" ${c.enabled?"checked":""}>启用</label></div><p class="card-intro">连接兼容接口，也可填写本地 vLLM 服务地址。</p>
<label>API 根地址<input name="custom_url" value="${esc(c.base_url)}" placeholder="http://127.0.0.1:8001/v1"></label><div class="form-grid">
<label>协议<select name="custom_protocol"><option value="chat_completions" ${c.protocol==="chat_completions"?"selected":""}>Chat Completions</option><option value="responses" ${c.protocol==="responses"?"selected":""}>Responses</option></select></label>
<label>模型 ID<input name="custom_model" value="${esc(c.model_id)}"></label>
<label>鉴权方式<select name="custom_auth"><option value="bearer">Bearer</option><option value="none" ${c.auth_mode==="none"?"selected":""}>无鉴权（本地服务）</option></select></label>
<label>密钥环境变量<input name="custom_env" value="${esc(c.api_key_env)}"></label>
<label>图像能力<select name="image_support"><option value="unknown">尚未确认</option><option value="supported" ${c.image_support==="supported"?"selected":""}>已确认支持</option><option value="unsupported" ${c.image_support==="unsupported"?"selected":""}>不支持</option></select></label>
<label>请求等待时间（秒）<input name="custom_timeout" type="number" min="1" max="3600" value="${c.timeout_seconds}"></label><label>连接并发上限<input name="custom_concurrency" type="number" min="1" value="${c.max_in_flight}"></label></div></section>
<section class="panel full"><div class="card-heading"><h2>MinerU 解析服务</h2><span class="badge ${data.credentials.mineru?"green":""}">${data.credentials.mineru?"已有本地凭据":"待配置凭据"}</span></div><p class="card-intro">线上 API 与本地部署分别配置；每次运行可选择解析策略。</p><div class="form-grid">
<label>线上 API 根地址<input name="cloud_url" value="${esc(m.cloud_url)}"></label><label>本地服务地址<input name="local_url" value="${esc(m.local_url)}"></label>
<label>密钥环境变量<input name="mineru_env" value="${esc(m.api_key_env)}"></label><label>本地解析后端<input name="backend" value="${esc(m.backend)}"></label>
<label>每块最大页数<input name="max_pages" type="number" min="1" value="${m.max_pages}"></label><label>每块最大文件大小（MiB）<input name="max_mib" type="number" min="1" value="${m.max_bytes/1024/1024}"></label></div></section></div>
<div class="save-row"><span class="subtitle">实际密钥保存在本机环境变量中。修改设置不自动发起测试。</span><button class="button primary" type="submit">保存设置</button></div><div id="settings-error" class="form-error"></div></form>`;}
function mathHTML(text,display=false){if(!window.katex)return `<pre class="code">${esc(text)}</pre>`;return katex.renderToString(text,{displayMode:display,throwOnError:false,trust:false,maxExpand:1000,strict:"ignore"});}
function sourceHTML(atom){if(["interline_equation","equation"].includes(atom.type))return `<div class="math-preview">${mathHTML(atom.text,true)}</div>`;
return atom.text.split(/(\\\([\s\S]*?\\\)|(?<!\\)\$(?!\$)[^\n$]+(?<!\\)\$)/g).map(p=>p.startsWith("\\(")&&p.endsWith("\\)")?mathHTML(p.slice(2,-2)):p.startsWith("$")&&p.endsWith("$")?mathHTML(p.slice(1,-1)):esc(p).replace(/\n/g,"<br>")).join("");}
async function compare(){if(!runId)runId=data.runs[0]?.id;if(!runId)return header("内容对照","并排查看原书、解析结果与 TeX。",startButton)+empty("选择一个运行开始对照","运行产生解析内容后，可以按原页和数学对象定位。");
const d=await api(`/api/runs/${runId}/view?page_idx=${pageIdx}`);viewData=d;const r=d.run,b=r.source;
const firstError=Object.entries(r.stages).find(([,s])=>s.error);
const runChoices=data.runs.map(x=>`<option value="${x.id}" ${x.id===runId?"selected":""}>${esc(x.source.title.slice(0,38))} · ${x.id.slice(0,6)}</option>`).join("");
const fm=new Map(d.fragments.flatMap(f=>f.nodes).map(n=>[n.atom_id,n]));
const original=`<div class="original-page"><img src="/api/books/${b.id}/image?page_idx=${pageIdx}" alt="原始 PDF 第 ${pageIdx+1} 页">${d.atoms.map(a=>{const [x0,y0,x1,y1]=a.bbox;const [w,h]=a.page_size;return w&&h?`<button aria-label="定位内容 ${a.order+1}" class="region ${a.atom_id===selected?"selected":""}" data-atom="${a.atom_id}" style="left:${x0/w*100}%;top:${y0/h*100}%;width:${(x1-x0)/w*100}%;height:${(y1-y0)/h*100}%"></button>`:"";}).join("")}</div>`;
const parsed=d.atoms.length?d.atoms.map(a=>`<article class="atom ${a.atom_id===selected?"selected":""}" data-atom="${a.atom_id}"><div class="atom-meta"><span>${esc(a.type)} · ${a.order+1}</span><button class="button small" data-correct="${a.atom_id}" ${r.state==="RUNNING"?"disabled":""}>修正</button></div>${sourceHTML(a)}<details><summary>原始 JSON</summary><pre class="raw-json">${esc(JSON.stringify(a.source_block,null,2))}</pre></details></article>`).join(""):empty("等待解析结果","原始页面可以先行查看。");
const texNodes=d.atoms.filter(a=>fm.has(a.atom_id)).map(a=>{const n=fm.get(a.atom_id),text=n.kind==="source_ref"?a.text.slice((n.source_prefix||"").length):n.value??n.latex??n.text??a.text;return `<article class="atom ${a.atom_id===selected?"selected":""}" data-atom="${a.atom_id}"><div class="atom-meta"><span>${n.kind==="math"?"数学表达":"引用原文"} · ${a.order+1}</span></div>${["equation","interline_equation"].includes(a.type)?`<div class="math-preview">${mathHTML(text,true)}</div>`:""}<pre class="code">${esc(text)}</pre></article>`;}).join("");
const stages=Object.entries(r.stages).map(([s,v],i)=>`<div class="stage ${v.state}" title="${esc(states[v.state]||v.state)}"><strong>${s}</strong>${esc(data.stages[i])}</div>`).join("");
const visual=d.visual;
return header("内容对照",`${esc(b.title)} · ${r.scope==="fixture"?"离线夹具":r.scope==="full"?"全书":"样本"} · 修订 ${r.revision}`,`<button class="button small" data-action="refresh-view">刷新</button>${d.tex?`<a class="button small ${accepted(r.state)?"primary":""}" href="/api/runs/${r.id}/export${accepted(r.state)?"":"?candidate=true"}">${accepted(r.state)?"导出已验收工程":"下载候选 TeX"} ↓</a>`:""}`)+
`<div class="compare-controls"><select id="compare-run" aria-label="选择运行">${runChoices}</select><button class="button small" data-action="prev-page" ${pageIdx===0?"disabled":""}>←</button><span>PDF 页</span><input id="compare-page" type="number" value="${pageIdx+1}" min="1" max="${b.page_count}" aria-label="PDF 页"><span>/ ${b.page_count}</span><button class="button small" data-action="next-page" ${pageIdx+1>=b.page_count?"disabled":""}>→</button>${badge(r.state)}${r.state==="RUNNING"?`<button class="button small" data-action="pause-run" ${r.pause_requested?"disabled":""}>${r.pause_requested?"正在等待请求结束":"暂停派发"}</button>`:""}<span>LLM ${r.usage.llm}/${r.config.max_llm_requests??"不限"} · 解析 ${r.usage.pages}/${r.config.max_submitted_pages} 页 · 每批 ${r.config.pages_per_task??"旧版"} 页 · 并发 ${r.config.llm_concurrency}</span></div>
<div class="stage-strip">${stages}</div>
<details class="panel"><summary>修正与局部重跑</summary><div class="compare-controls">
<select id="rerun-stage" aria-label="重跑起始阶段">${Object.keys(r.stages).map((s,i)=>`<option value="${s}" ${s==="S6"?"selected":""}>${s} · ${esc(data.stages[i])}</option>`).join("")}</select>
<select id="rerun-task" aria-label="仅重跑此转换任务"><option value="">全部相关任务</option>${d.tasks.map(t=>`<option value="${t.task_id}">${t.task_id} · ${t.owned_atom_ids.length} 个内容块</option>`).join("")}</select>
<button class="button small" data-action="rerun-preview" ${r.state==="RUNNING"?"disabled":""}>查看影响范围</button></div>
<div class="form-grid"><label>恢复后的 LLM 并发<input id="rerun-concurrency" type="number" min="1" value="${r.config.llm_concurrency}"></label><label>LLM 累计请求预算<input id="rerun-llm-budget" type="number" min="${r.config.max_llm_requests??0}" value="${r.config.max_llm_requests??""}" placeholder="不限"></label>
<label>解析提交预算<input id="rerun-parse-budget" type="number" min="${r.config.max_parse_submissions}" value="${r.config.max_parse_submissions}"></label>
<label>累计解析页数预算<input id="rerun-page-budget" type="number" min="${r.config.max_submitted_pages}" value="${r.config.max_submitted_pages}"></label></div>
<label><input id="rerun-recompute" type="checkbox">重新生成已通过的相关任务；恢复时默认复用</label>
<label><input id="rerun-unrecoverable" type="checkbox">允许重新请求缺少恢复标识的旧调用；原未知记录与用量保留</label>
<div id="rerun-plan" class="audit-row"></div>
${r.corrections.length?`<p class="note">已记录 ${r.corrections.length} 条修正依据。内容修正需要从 S2 重新核对。</p>`:""}</details>
${d.parser_progress?.some(p=>p.remote_completed&&!p.downloaded)?`<div class="run-message"><strong>远端解析已完成 · 结果尚未取回</strong>　下载连接未完成。恢复此阶段会核对并取回已有任务，不重复提交解析。</div>`:""}
${firstError?`<div class="run-message"><strong>${firstError[0]} · ${esc(firstError[1].error.code)}</strong>　${esc(firstError[1].error.message)}
<button class="button small" data-rerun="${firstError[0]}">恢复此阶段</button></div>`:""}
<div class="node-nav">${(d.structure?.nodes||[]).filter(n=>["chapter","section","subsection"].includes(n.kind)).map(n=>`<button data-node="${n.id}">${esc(n.number||"")} ${esc(n.title)}</button>`).join("")}</div>
<div class="comparison"><section class="column"><div class="column-header">原始 PDF<small><a href="/api/books/${b.id}/image?page_idx=${pageIdx}&dpi=300" target="_blank" rel="noopener">放大原页 ↗</a></small></div><div class="column-content">${original}</div></section>
<section class="column"><div class="column-header">解析内容<small>${d.atoms.length} 个内容块</small></div><div class="column-content">${parsed}</div></section>
<section class="column"><div class="column-header">TeX 与数学表达<small>${d.tex?"已生成候选工程":"逐块转换"}</small></div><div class="column-content">${texNodes||empty("等待转换","全书设置完成后，按所属页并行转换。")}</div></section></div>
${d.tex?`<details class="panel" style="margin-top:18px"><summary>查看统一生成的 TeX 正文 · 编译${d.compile_report?.status==="PASSED"?"通过":"未通过"}</summary><pre class="code">${esc(d.tex)}</pre></details>`:""}
<section class="panel audit-panel"><h3>原图核对与差异证据</h3><div class="audit-row">${visual?`${visual.evidence_origin==="fixture"?"预录夹具证据 · ":""}原图输入 ${visual.crop_regions?.length||0} 个区域 · ${visual.review_mode==="all"?"逐批独立审查":visual.status==="DEFERRED_TO_CONVERSION"?"待转换时核对":"转换时核对，未追加独立审查"}`:"尚未核对原图。解析成功不代表已完成视觉核对。"}</div>
${(visual?.reports||[]).filter(e=>e.page_idx===pageIdx).map(e=>`<details class="evidence-row"><summary>PDF ${e.page_idx+1} 页 · ${esc(e.comparison.result)}</summary><p class="audit-row">${esc(e.comparison.evidence)}</p><h3>独立视觉读数</h3><pre>${esc(e.observation.reading)}</pre><h3>差异记录</h3><pre>${esc(JSON.stringify(e.comparison.findings,null,2))}</pre></details>`).join("")}</section>`;
}
async function navigate(next){page=next;document.querySelectorAll(".nav").forEach(n=>n.classList.toggle("active",n.dataset.page===page));$("#page-label").textContent=labels[page];await renderPage();}
async function renderPage(){const main=$("#main");const positions=[...document.querySelectorAll(".column-content")].map(e=>e.scrollTop);
main.innerHTML=page==="library"?library():page==="runs"?header("运行记录","每次运行保留处理范围、修订、预算与审查记录。",startButton)+runTable(data.runs):page==="settings"?settings():await compare();
if(page==="compare")document.querySelectorAll(".column-content").forEach((e,i)=>e.scrollTop=positions[i]||0);}
function openRun(bookId) {
 const form=$("#run-form"); form.reset(); $("#run-error").textContent="";
 $("#run-book").innerHTML=data.books.map(b=>`<option value="${b.id}">${esc(b.title)}</option>`).join("");
 if(bookId)$("#run-book").value=bookId;
 $("#role-fields").innerHTML=Object.entries(roles).map(([role,title])=>`<div class="role-row"><h3>${title}</h3><div class="form-grid"><label>连接<select name="${role}_connection">${Object.entries(data.settings.connections).map(([id,c])=>`<option value="${id}" ${id===data.settings.default_connection?"selected":""} ${c.enabled?"":"disabled"}>${c.kind==="codex_chatgpt"?"ChatGPT 官方订阅":esc(id)}</option>`).join("")}</select></label><label>模型<input name="${role}_model" list="models-${data.settings.default_connection}" placeholder="使用上游默认模型"></label><label>上下文上限<input name="${role}_context" type="number" value="32768" min="4096"></label><label>预留输出 token<input name="${role}_output" type="number" value="4096" min="256"></label></div></div>`).join("");
 bookChanged(); $("#run-dialog").showModal();
 loadModels(data.settings.default_connection).catch(error=>toast(error.message));
}
function bookChanged(){
 const form=$("#run-form"),book=data.books.find(b=>b.id===form.elements.book_id.value);
 if(!book)return;
 form.elements.profile.value="book";
 form.elements.start_page.value=1; form.elements.end_page.value=book.page_count;
 form.elements.max_parse_submissions.value=Math.ceil(book.page_count/data.settings.mineru.max_pages);
 form.elements.max_submitted_pages.value=book.page_count;
 $("#profile-note").textContent=`处理完整书籍（${book.page_count} 页）。解析结果聚合后，按标题、数学环境及 block 集群规划转换；PDF 页序号只用于来源定位和图像核对。`;
 $("#cached-run").innerHTML='<option value="">重新解析书籍</option>'+data.runs.filter(r=>r.stages.S1.state==="PASSED"&&r.scope!=="fixture"&&r.source.id===book.id&&r.config.start_page===1&&r.config.end_page===book.page_count).map(r=>`<option value="${r.id}">复用整本解析 · ${r.id.slice(0,8)}</option>`).join("");
}
async function loadModels(connectionId){
 const state=await api(`/api/connections/${connectionId}/status`);
 let list=document.getElementById("models-"+connectionId);
 if(!list){list=document.createElement("datalist");list.id="models-"+connectionId;document.body.append(list);}
 list.innerHTML=(state.models||[]).filter(m=>m.model||m.id).map(m=>`<option value="${esc(m.model||m.id)}">${esc(m.displayName||m.model||m.id)}${m.isDefault?" · 上游默认":""}</option>`).join("");
 return state;
}

document.addEventListener("click",async event=>{const button=event.target.closest("button,a[data-page],[data-atom]");if(!button||button.disabled)return;
try{
if(button.dataset.page){await refresh();await navigate(button.dataset.page);return;}
if(button.dataset.book){openRun(button.dataset.book);return;}
if(button.dataset.run){runId=button.dataset.run;pageIdx=data.runs.find(r=>r.id===runId).config.start_page-1;await navigate("compare");return;}
if(button.dataset.correct){selected=button.dataset.correct;const a=viewData.atoms.find(a=>a.atom_id===selected);$("#correction-before").value=a.text;$("#correction-after").value=a.text;$("#correction-evidence").value="";$("#correction-error").textContent="";$("#correction-dialog").showModal();return;}
if(button.dataset.atom){selectAtom(button.dataset.atom);return;}
if(button.dataset.rerun){button.disabled=true;const preview=await api(`/api/runs/${runId}/rerun-preview?stage=${button.dataset.rerun}`);const target=$("#main .run-message");target.innerHTML+=`<p>将重跑 ${preview.stages.join(" → ")}，累计用量保持不变。</p><button class="button small primary" data-confirm-rerun="${button.dataset.rerun}">执行重跑</button>`;return;}
if(button.dataset.action==="pause-run"){button.disabled=true;await api(`/api/runs/${runId}/pause`,"POST",{revision:viewData.run.revision,operation_id:crypto.randomUUID()});await refresh();await renderPage();return;}
if(button.dataset.confirmRerun){button.disabled=true;await api(`/api/runs/${runId}/rerun`,"POST",{revision:viewData.run.revision,operation_id:crypto.randomUUID(),stage:button.dataset.confirmRerun,task_id:button.dataset.task||null,force_recompute:$("#rerun-recompute").checked,retry_unrecoverable:$("#rerun-unrecoverable").checked,llm_concurrency:Number($("#rerun-concurrency").value),
max_llm_requests:$("#rerun-llm-budget").value.trim()?Number($("#rerun-llm-budget").value):null,max_parse_submissions:Number($("#rerun-parse-budget").value),
max_submitted_pages:Number($("#rerun-page-budget").value),reason:"从界面恢复该阶段"});await refresh();await renderPage();return;}
if(button.dataset.node){const node=viewData.structure.nodes.find(n=>n.id===button.dataset.node);const atom=viewData.atoms.find(a=>node.atom_ids.includes(a.atom_id));if(atom)selectAtom(atom.atom_id);else {const location=viewData.locations[node.atom_ids[0]];if(location){pageIdx=location.page_idx;selected=node.atom_ids[0];await renderPage();selectAtom(selected);}}return;}
switch(button.dataset.action){
case "new-run":openRun();break;case "upload":$("#pdf-upload").click();break;
case "prev-page":pageIdx--;await renderPage();break;case "next-page":pageIdx++;await renderPage();break;
case "refresh-view":await refresh();await renderPage();break;
case "rerun-preview":{const stage=$("#rerun-stage").value;const task=stage==="S6"?$("#rerun-task").value:"";const preview=await api(`/api/runs/${runId}/rerun-preview?stage=${stage}${task?"&task_id="+encodeURIComponent(task):""}`);
$("#rerun-plan").innerHTML=`将重跑 ${preview.stages.join(" → ")}；涉及 ${preview.task_ids.length} 个转换任务。累计请求用量保持不变。 <button class="button small primary" data-confirm-rerun="${stage}" data-task="${esc(task)}">执行重跑</button>`;break;}
case "account-status":button.disabled=true;$("#account-status").textContent="正在检查…";{const status=await loadModels("openai_subscription");$("#account-status").textContent=status.status==="READY"?"已登录 · 可用模型："+status.models.map(m=>m.model||m.id).join("、")+(status.runtime?.version?" · "+status.runtime.version:""):status.status==="AUTH_REQUIRED"?"尚未登录，请通过官方登录完成授权。":status.message||status.status;}break;
case "login":button.disabled=true;{const login=await api("/api/connections/openai_subscription/login","POST",{});$("#account-status").innerHTML=`<a href="${esc(login.auth_url)}" target="_blank" rel="noopener">打开官方授权页面 ↗</a><p>授权完成后，点击“检查登录状态”。</p>`;}break;
}
}catch(error){toast(error.message);}finally{if(button.dataset.action==="account-status"||button.dataset.action==="login")button.disabled=false;}});
function selectAtom(id){selected=id;document.querySelectorAll("[data-atom]").forEach(e=>e.classList.toggle("selected",e.dataset.atom===id));document.querySelectorAll(`.atom[data-atom="${id}"]`).forEach(e=>{const container=e.closest(".column-content"),rect=e.getBoundingClientRect(),bounds=container.getBoundingClientRect();if(rect.top<bounds.top||rect.height>bounds.height)container.scrollTop+=rect.top-bounds.top;else if(rect.bottom>bounds.bottom)container.scrollTop+=rect.bottom-bounds.bottom;});}
$("#close-dialog").onclick=()=>$("#run-dialog").close();$("#close-correction").onclick=()=>$("#correction-dialog").close();$("#run-book").onchange=bookChanged;
document.addEventListener("change",async e=>{try{if(e.target.name?.endsWith("_connection")){const role=e.target.name.slice(0,-11);$("#run-form").elements[role+"_model"].setAttribute("list","models-"+e.target.value);await loadModels(e.target.value);}if(e.target.id==="compare-run"){runId=e.target.value;pageIdx=data.runs.find(r=>r.id===runId).config.start_page-1;await renderPage();}
if(e.target.id==="compare-page"){pageIdx=Math.max(0,Math.min(viewData.run.source.page_count-1,Number(e.target.value)-1));await renderPage();}
if(e.target.id==="pdf-upload"&&e.target.files[0]){const body=new FormData();body.append("file",e.target.files[0]);toast("正在保存并检查 PDF…");await api("/api/books","POST",body);await refresh();await navigate("library");e.target.value="";toast("书籍已保存");}}catch(error){toast(error.message);}});
$("#run-form").onsubmit=async e=>{e.preventDefault();const form=e.target,button=form.querySelector("[type=submit]");button.disabled=true;try{
const fields=new FormData(form),payload={};for(const key of ["book_id","profile","parser","visual_mode","review_mode"])payload[key]=fields.get(key);
for(const key of ["start_page","end_page","llm_concurrency","pages_per_task","max_parse_submissions","max_submitted_pages"])payload[key]=Number(fields.get(key));
payload.max_llm_requests=fields.get("max_llm_requests")?Number(fields.get("max_llm_requests")):null;
payload.visual_pages=String(fields.get("visual_pages")||"").split(/[,，\s]+/).filter(Boolean).map(Number);payload.cached_run_id=fields.get("cached_run_id")||null;
for(const role of Object.keys(roles))payload[role]={connection_id:fields.get(role+"_connection"),model_id:fields.get(role+"_model"),context_limit:Number(fields.get(role+"_context")),output_tokens:Number(fields.get(role+"_output"))};
const run=await api("/api/runs","POST",payload);runId=run.id;pageIdx=payload.start_page-1;await api(`/api/runs/${runId}/start`,"POST",{revision:run.revision,operation_id:crypto.randomUUID()});
$("#run-dialog").close();await refresh();await navigate("compare");
}catch(error){$("#run-error").textContent=error.message;}finally{button.disabled=false;}};
$("#correction-form").onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector("[type=submit]");button.disabled=true;try{await api(`/api/runs/${runId}/corrections`,"POST",{revision:viewData.run.revision,operation_id:crypto.randomUUID(),atom_id:selected,before:$("#correction-before").value,after:$("#correction-after").value,evidence:$("#correction-evidence").value,page_idx:pageIdx});$("#correction-dialog").close();toast("已保存修正依据，请从 S2 重新核对并生成新修订");await refresh();await renderPage();}catch(error){$("#correction-error").textContent=error.message;}finally{button.disabled=false;}};
document.addEventListener("submit",async e=>{if(e.target.id!=="settings-form")return;e.preventDefault();const button=e.target.querySelector("[type=submit]");button.disabled=true;
try{const f=new FormData(e.target),s=structuredClone(data.settings),o=s.connections.openai_subscription,c=s.connections.custom_api,m=s.mineru;
o.model_id=f.get("official_model");o.max_in_flight=Number(f.get("official_concurrency"));o.timeout_seconds=Number(f.get("official_timeout"));
Object.assign(c,{enabled:f.has("custom_enabled"),base_url:f.get("custom_url"),protocol:f.get("custom_protocol"),model_id:f.get("custom_model"),auth_mode:f.get("custom_auth"),api_key_env:f.get("custom_env"),image_support:f.get("image_support"),max_in_flight:Number(f.get("custom_concurrency")),timeout_seconds:Number(f.get("custom_timeout"))});
Object.assign(m,{cloud_url:f.get("cloud_url"),local_url:f.get("local_url"),api_key_env:f.get("mineru_env"),backend:f.get("backend"),max_pages:Number(f.get("max_pages")),max_bytes:Number(f.get("max_mib"))*1024*1024});
await api("/api/settings","PUT",s);await refresh();toast("设置已保存");}catch(error){$("#settings-error").textContent=error.message;}finally{button.disabled=false;}});
async function poll(){if(loading||!data||page==="settings"||$("dialog[open]"))return;loading=true;try{const active=data.runs.some(r=>r.state==="RUNNING");if(active){await refresh();await renderPage();}}catch(error){toast(error.message);}finally{loading=false;}}
refresh().then(()=>{const requested=new URLSearchParams(location.search).get("run"),run=data.runs.find(r=>r.id===requested);if(run){runId=run.id;pageIdx=run.config.start_page-1;return navigate("compare");}return navigate("library");}).catch(error=>{$("#main").innerHTML=empty("工作台连接失败",esc(error.message));});setInterval(poll,2500);
})();
