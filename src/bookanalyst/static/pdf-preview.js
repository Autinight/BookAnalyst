import { api } from "./api.js";

let panel, sheet, message, opener, controller, baseURL, page=1, pageCount=0, sequence=0, pageSequence=0;
const preferredWidth=()=>Math.min(620, Math.round(innerWidth*.44));

function setWidth(width) {
  const maximum=innerWidth>1100 ? innerWidth-550 : innerWidth;
  const size=Math.max(Math.min(320,maximum),Math.min(maximum,width));
  document.body.style.setProperty("--pdf-preview-width",`${size}px`);
  const handle=panel.querySelector(".pdf-preview-resize");
  handle.setAttribute("aria-valuenow",Math.round(size));
  handle.setAttribute("aria-valuemin",Math.min(320,maximum));
  handle.setAttribute("aria-valuemax",maximum);
}

export function close(restoreFocus=true) {
  sequence++;
  controller?.abort();
  if(!panel || panel.hidden) return;
  panel.hidden=true;
  pageSequence++;
  sheet.replaceChildren();
  document.body.classList.remove("pdf-preview-open","pdf-preview-resizing");
  if(restoreFocus) (opener?.isConnected ? opener : document.querySelector("#main"))?.focus();
}

async function open(link) {
  const current=++sequence;
  controller?.abort();
  controller=new AbortController();
  const menu=link.closest("[data-book-menu]");
  opener=menu ? document.querySelector(`[popovertarget="${menu.id}"]`) : link;
  menu?.hidePopover();
  const title=link.dataset.pdfTitle || "PDF";
  panel.querySelector("#pdf-preview-title").textContent=title;
  panel.querySelector("#pdf-preview-kind").textContent=link.dataset.pdfPreview;
  pageSequence++;
  sheet.replaceChildren();
  sheet.style.setProperty("--pdf-page-scale","100%");
  panel.querySelector("#pdf-preview-zoom").value="100";
  panel.querySelector(".pdf-preview-toolbar").hidden=true;
  message.textContent="正在打开 PDF…";
  message.hidden=false;
  panel.hidden=false;
  document.body.classList.add("pdf-preview-open");
  setWidth(parseFloat(document.body.style.getPropertyValue("--pdf-preview-width")) || preferredWidth());
  panel.querySelector("[data-pdf-close]").focus({preventScroll:true});
  try {
    const match=new URL(link.href).pathname.match(/^\/api\/(books|runs)\/([^/]+)\/(source|pdf)$/);
    if(!match) throw Error("无法识别 PDF 地址");
    const kind=match[1]==="runs" ? "run" : match[3]==="source" ? "source" : "book";
    baseURL=`/api/pdf-preview/${kind}/${match[2]}`;
    const info=await api(baseURL,{signal:controller.signal});
    if(current!==sequence) return;
    pageCount=info.page_count;
    panel.querySelector("#pdf-preview-title").textContent=info.title;
    panel.querySelector("#pdf-preview-total").textContent=`/ ${pageCount}`;
    panel.querySelector("#pdf-preview-page-number").max=pageCount;
    panel.querySelector(".pdf-preview-toolbar").hidden=false;
    showPage(1);
  } catch(error) {
    if(current!==sequence) return;
    message.textContent=error.name==="TimeoutError" ? "打开 PDF 超时，请重新打开。" : error.message;
  }
}

function showPage(requested) {
  const current=++pageSequence;
  page=Math.max(1,Math.min(pageCount,Math.trunc(Number(requested)) || 1));
  panel.querySelector("#pdf-preview-page-number").value=page;
  panel.querySelector("[data-pdf-previous]").disabled=page===1;
  panel.querySelector("[data-pdf-next]").disabled=page===pageCount;
  message.textContent="正在读取页面…";message.hidden=false;
  sheet.replaceChildren();
  const image=new Image();
  image.alt=`${panel.querySelector("#pdf-preview-title").textContent} · 第 ${page} 页`;
  image.onload=()=>{
    if(current!==pageSequence || panel.hidden) return;
    sheet.replaceChildren(image);message.hidden=true;
    panel.querySelector(".pdf-preview-content").scrollTo(0,0);
  };
  image.onerror=()=>{
    if(current!==pageSequence || panel.hidden) return;
    message.textContent="此页读取失败，请重新选择页码或打开 PDF。";
  };
  image.src=`${baseURL}/pages/${page}?dpi=150`;
}

export function init() {
  document.body.insertAdjacentHTML("beforeend",`<section id="pdf-preview" class="pdf-preview" role="region" aria-label="PDF 预览" hidden>
    <div class="pdf-preview-resize" role="separator" aria-label="调整 PDF 预览宽度" aria-orientation="vertical" tabindex="0"></div>
    <div class="pdf-preview-heading"><div><span id="pdf-preview-kind"></span><h2 id="pdf-preview-title"></h2></div><button type="button" data-pdf-close aria-label="关闭 PDF 预览" title="关闭预览">×</button></div>
    <div class="pdf-preview-toolbar" hidden><button type="button" data-pdf-previous aria-label="PDF 上一页">←</button><label>页码<input id="pdf-preview-page-number" type="number" min="1" value="1" aria-label="PDF 页码"></label><span id="pdf-preview-total"></span><button type="button" data-pdf-next aria-label="PDF 下一页">→</button><select id="pdf-preview-zoom" aria-label="PDF 缩放"><option value="100">适合宽度</option><option value="75">75%</option><option value="125">125%</option><option value="150">150%</option><option value="200">200%</option></select></div>
    <div class="pdf-preview-content"><p id="pdf-preview-message" role="status"></p><div class="pdf-preview-sheet"></div></div>
  </section>`);
  panel=document.querySelector("#pdf-preview");
  sheet=panel.querySelector(".pdf-preview-sheet");
  message=panel.querySelector("#pdf-preview-message");
  document.addEventListener("click",event=>{
    const link=event.target.closest("a[data-pdf-preview]");
    if(link) {event.preventDefault();void open(link);}
    if(event.target.closest("[data-pdf-close]")) close();
    if(event.target.closest("[data-pdf-previous]")) showPage(page-1);
    if(event.target.closest("[data-pdf-next]")) showPage(page+1);
  });
  panel.addEventListener("change",event=>{
    if(event.target.id==="pdf-preview-page-number") showPage(event.target.value);
    if(event.target.id==="pdf-preview-zoom") sheet.style.setProperty("--pdf-page-scale",`${event.target.value}%`);
  });
  panel.addEventListener("keydown",event=>{
    if(event.key==="Enter" && event.target.id==="pdf-preview-page-number") {event.preventDefault();showPage(event.target.value);}
  });
  document.addEventListener("keydown",event=>{
    if(event.key==="Escape" && !panel.hidden && !document.querySelector("dialog[open]")) {
      event.preventDefault();close();
    }
  });
  const handle=panel.querySelector(".pdf-preview-resize");
  let dragging=false;
  handle.addEventListener("pointerdown",event=>{
    if(event.button!==0) return;
    event.preventDefault();dragging=true;
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add("pdf-preview-resizing");
  });
  handle.addEventListener("pointermove",event=>{if(dragging) setWidth(innerWidth-event.clientX);});
  const end=()=>{dragging=false;document.body.classList.remove("pdf-preview-resizing");};
  handle.addEventListener("pointerup",end);
  handle.addEventListener("pointercancel",end);
  handle.addEventListener("lostpointercapture",end);
  handle.addEventListener("keydown",event=>{
    if(!["ArrowLeft","ArrowRight","Home"].includes(event.key)) return;
    event.preventDefault();
    setWidth(event.key==="Home" ? preferredWidth() : panel.getBoundingClientRect().width+(event.key==="ArrowLeft" ? 40 : -40));
  });
  window.addEventListener("resize",()=>{if(!panel.hidden) setWidth(panel.getBoundingClientRect().width);});
}
