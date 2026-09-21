import { api, escape as e } from "./api.js";
import { coverPlaceholder } from "./library-view.js";

let previewURL, refresh, toast, preparedFile, previewSequence=0;
export function leave() {
  previewSequence++;
  preparedFile=null;
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = null;
}
export function openCover(book) {
  leave();
  const dialog=document.querySelector("#cover-dialog"), form=document.querySelector("#cover-form");
  form.reset(); form.dataset.bookId=book.id;
  document.querySelector("#cover-book-title").textContent=book.title;
  document.querySelector("#cover-preview").innerHTML=book.cover
    ? `<img src="${e(book.cover.url)}" alt="当前封面">` : coverPlaceholder(book);
  document.querySelector("#cover-error").textContent="";
  document.querySelector("#save-cover").disabled=true;
  document.querySelector("#remove-cover").hidden=!book.cover;
  dialog.showModal();
}
export function init(callbacks) {
  ({refresh,toast}=callbacks);
  document.addEventListener("toggle", event => {
    if(!event.target.matches("[data-book-menu]") || event.newState!=="open") return;
    const menu=event.target;
    const trigger=document.querySelector(`[popovertarget="${menu.id}"]`);
    if(!trigger) return;
    const rect=trigger.getBoundingClientRect();
    const top=rect.bottom+6+menu.offsetHeight>innerHeight-12 ? rect.top-menu.offsetHeight-6 : rect.bottom+6;
    menu.style.left=Math.max(12,Math.min(rect.right-menu.offsetWidth,innerWidth-menu.offsetWidth-12))+"px";
    menu.style.top=Math.max(12,top)+"px";
  },true);
  document.addEventListener("click", event => {
    if(event.target.closest("[data-cover-close]")) document.querySelector("#cover-dialog")?.close();
    if(event.target.closest("[data-book-menu] a")) event.target.closest("[data-book-menu]").hidePopover();
  });
  document.addEventListener("close", event => {if(event.target.id==="cover-dialog") leave();},true);
  document.addEventListener("error", event => {
    if(event.target.matches?.(".book-cover-image")) {
      event.target.closest(".shelf-cover").classList.remove("has-cover");
      event.target.remove();
    }
  },true);
  window.addEventListener("resize",()=>document.querySelectorAll("[data-book-menu]:popover-open").forEach(menu=>menu.hidePopover()));
  document.addEventListener("change", async event => {
    if(event.target.id!=="cover-file") return;
    leave();
    const input=event.target, file=input.files[0], error=document.querySelector("#cover-error"), save=document.querySelector("#save-cover");
    const sequence=previewSequence;
    save.disabled=true; error.textContent="";
    if(!file) return;
    const isPDF=file.type==="application/pdf" || /\.pdf$/i.test(file.name);
    if(file.size>(isPDF ? 200 : 12)*1024*1024) {error.textContent=isPDF ? "PDF 不能超过 200 MB" : "封面图片不能超过 12 MB";return;}
    if(isPDF) {
      document.querySelector("#cover-preview").textContent="正在读取 PDF 第一页…";
      try {
        const body=new FormData(); body.append("file",file);
        const preview=await api(`/api/books/${encodeURIComponent(input.form.dataset.bookId)}/cover-preview`,{method:"POST",body});
        if(sequence!==previewSequence || !input.isConnected) return;
        const bytes=Uint8Array.from(atob(preview.data_url.split(",")[1]),c=>c.charCodeAt(0));
        preparedFile=new File([bytes],"cover.webp",{type:"image/webp"});
      } catch(err) {
        if(sequence===previewSequence && input.isConnected) {
          error.textContent=err.message;
          document.querySelector("#cover-preview").textContent="无法预览此 PDF";
        }
        return;
      }
    } else preparedFile=file;
    previewURL=URL.createObjectURL(preparedFile);
    const image=document.createElement("img");
    image.alt="新封面预览";
    image.onload=()=>{if(image.isConnected) save.disabled=false;};
    image.onerror=()=>{if(image.isConnected) {preparedFile=null; error.textContent="无法预览封面，请选择有效的图片或 PDF。";}};
    image.src=previewURL;
    document.querySelector("#cover-preview").replaceChildren(image);
  });
  document.addEventListener("submit", async event => {
    if(event.target.id!=="cover-form") return;
    event.preventDefault();
    const form=event.target, file=preparedFile;
    if(!file) return;
    const body=new FormData(); body.append("file",file);
    await updateCover(form,"PUT",body);
  });
  document.addEventListener("click", async event => {
    if(event.target.closest("#remove-cover")) await updateCover(document.querySelector("#cover-form"),"DELETE");
  });
}
async function updateCover(form,method,body) {
  const dialog=form.closest("dialog"), save=form.querySelector("#save-cover"), remove=form.querySelector("#remove-cover");
  save.disabled=true; remove.disabled=true;
  try {
    await api(`/api/books/${encodeURIComponent(form.dataset.bookId)}/cover`,{method,body});
    if(dialog.isConnected) dialog.close();
    await refresh();
    toast(method==="DELETE" ? "已移除封面，恢复书名占位" : "封面已保存");
  } catch(error) {
    if(form.isConnected) form.querySelector("#cover-error").textContent=error.message;
  } finally {
    save.disabled=!preparedFile; remove.disabled=false;
  }
}
