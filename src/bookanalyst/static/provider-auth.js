import { api } from "./api.js";

const sessions=new WeakMap();
function session(card) {
  if(!sessions.has(card)) sessions.set(card,{revision:0,timer:null,waitingUntil:0});
  return sessions.get(card);
}
export function providerAuthMarkup(state="CHECKING") {
  if(state==="READY") return '<span class="pv-auth-badge" role="status">已登录</span><button type="button" class="pv-add-form-btn" data-provider-logout>登出</button>';
  if(state==="AUTH_REQUIRED") return '<button type="button" class="pv-add-form-btn primary" data-connection-login>登录</button>';
  if(state==="CHECKING") return '<span class="pv-auth-pending" role="status">正在检查登录状态…</span>';
  return `<span class="pv-auth-pending" role="status">${state==="WAITING" ? "等待登录完成" : "登录状态未确认"}</span><button type="button" class="pv-add-form-btn" data-connection-check>重新检查</button>`;
}
export function syncProviderAuthDots(form) {
  for(const card of form.querySelectorAll("[data-auth-state]")) {
    const button=[...form.querySelectorAll("[data-provider-select]")].find(b=>b.dataset.providerSelect===card.dataset.connectionId);
    button?.querySelector(".pv-status-dot")?.classList.toggle("on",card.dataset.authState==="READY");
  }
}
function renderAuth(card,state) {
  const row=card.querySelector(".pv-oauth-status");
  if(!row) return;
  card.dataset.authState=state;
  row.innerHTML=providerAuthMarkup(state);
  const form=card.closest("form");
  if(form) syncProviderAuthDots(form);
}
export function updateProviderAuth(card,result) {
  if(!card.querySelector(".pv-oauth-status")) return;
  const current=session(card);
  current.revision++;
  clearTimeout(current.timer);
  if(result.status==="READY") {
    current.waitingUntil=0;
    card.querySelector("[data-connection-status]").textContent="";
  }
  renderAuth(card,current.waitingUntil>Date.now() && result.status!=="READY" ? "WAITING" : result.status);
}
export async function refreshProviderAuth(card) {
  if(!card.isConnected || !card.querySelector(".pv-oauth-status")) return;
  const current=session(card),revision=++current.revision;
  clearTimeout(current.timer);
  try {
    const result=await api(`/api/connections/${encodeURIComponent(card.dataset.connectionId)}/status`,{timeoutMs:45000});
    if(!card.isConnected || revision!==current.revision) return;
    updateProviderAuth(card,result);
  } catch {
    if(!card.isConnected || revision!==current.revision) return;
    updateProviderAuth(card,{status:"REQUEST_FAILED"});
  }
  if(current.waitingUntil>Date.now()) current.timer=setTimeout(()=>{void refreshProviderAuth(card);},3000);
}
export function watchProviderLogin(card) {
  const current=session(card);
  current.waitingUntil=Date.now()+10*60*1000;
  renderAuth(card,"WAITING");
  void refreshProviderAuth(card);
}
export function providerLoggedOut(card,result) {
  session(card).waitingUntil=0;
  updateProviderAuth(card,result);
}
export async function loadProviderAuth(form) {
  await Promise.allSettled([...form.querySelectorAll("[data-connection-id]")].filter(card=>card.querySelector(".pv-oauth-status")).map(refreshProviderAuth));
}
