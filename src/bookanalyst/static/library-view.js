import { escape as e } from "./api.js";

export function coverPlaceholder(book) {
  return `<span class="cover-placeholder cover-tone-${book.title.length % 5}" aria-hidden="true"><strong>${e(book.title)}</strong></span>`;
}
function cover(book) {
  const kind=book.result ? "保留的 PDF" : "原书 PDF";
  return `<a class="shelf-cover${book.cover ? " has-cover" : ""}" href="/api/books/${e(book.id)}/pdf" data-pdf-preview="${kind}" data-pdf-title="${e(book.title)}" aria-label="打开 ${e(book.title)} 的${kind}">
    ${coverPlaceholder(book)}${book.cover ? `<img class="book-cover-image" loading="lazy" decoding="async" src="${e(book.cover.url)}" alt="${e(book.title)}封面">` : ""}</a>`;
}
function bookMenu(book, trash, supported) {
  const bid=e(book.id), unavailable=supported ? "" : ' disabled title="请重启服务以使用管理功能"';
  const action=(name,label,extra="") => `<button type="button" data-book-action="${name}" data-book-id="${bid}" ${extra}${unavailable}>${label}</button>`;
  return `<button type="button" class="book-menu-trigger" popovertarget="book-menu-${bid}" aria-label="${e(book.title)}的更多操作" title="更多操作">⋯</button>
    <div popover="auto" class="book-menu" id="book-menu-${bid}" data-book-menu aria-label="${e(book.title)}的操作">
      ${trash ? action("restore","恢复到书库") : `<button type="button" data-new="${bid}">开始转换</button>`}
      <a href="/api/books/${bid}/source" data-pdf-preview="原书" data-pdf-title="${e(book.title)}">打开原书</a>
      ${book.result ? `<a href="/api/books/${bid}/pdf" data-pdf-preview="生成 PDF" data-pdf-title="${e(book.title)}">查看生成 PDF</a><a href="/api/books/${bid}/project">下载 TeX 工程</a>${book.result.review_count ? `<a href="/api/books/${bid}/reference-review" target="_blank" rel="noopener">查看待确认引用</a>` : ""}${trash ? "" : action("images","修复图片")+action("template","更换模板")}` : ""}
      ${trash ? "" : `<hr>${action("cover",book.cover ? "更换封面" : "设置封面")}${action("rename","重命名")}`}
      ${action("reveal","在文件夹中显示")}${trash ? "" : `<hr>${action("delete","移入回收站",'class="danger"')}`}
    </div>`;
}
function bookState(book, runs) {
  const job=runs.find(r=>r.book_id===book.id && r.state==="RUNNING");
  return job ? '<span class="shelf-state processing">转换中</span>' : book.result ? '<span class="shelf-state complete">已有成果</span>' : '<span class="shelf-state">未转换</span>';
}
export function libraryResults(data, state={}) {
  const trash=Boolean(state.trash), query=(state.query || "").trim().toLocaleLowerCase();
  let books=[...(trash ? data.deleted_books || [] : data.books)];
  if(query) books=books.filter(b=>b.title.toLocaleLowerCase().includes(query));
  if(state.sort==="title") books.sort((a,b)=>a.title.localeCompare(b.title,"zh-CN",{numeric:true}));
  if(state.sort==="pages") books.sort((a,b)=>b.page_count-a.page_count);
  const count=`<p class="library-count" role="status">${books.length} 本${query ? "匹配文献" : ""}${trash ? " · 原书和已有任务均保留，可随时恢复" : ""}</p>`;
  if(!books.length) return count+`<div class="shelf-empty">${query ? "没有匹配的书籍，试试其他书名。" : trash ? "回收站为空" : "将 PDF 拖到这里，开始收藏你的第一本文献。"}${!query && !trash ? '<button data-upload class="primary">导入 PDF</button>' : ""}</div>`;
  if(state.view==="list") return count+`<div class="shelf-list">${books.map(book=>`<article class="shelf-list-row" data-library-book="${e(book.id)}">${cover(book)}<div class="shelf-list-title"><h2>${e(book.title)}</h2><p>${book.page_count} 页 · ${(book.size_bytes/1048576).toFixed(1)} MB</p></div>${bookState(book,data.runs || [])}${bookMenu(book,trash,data.library_management)}</article>`).join("")}</div>`;
  return count+`<div class="library-shelf">${books.map(book=>`<article class="shelf-book" data-library-book="${e(book.id)}">${cover(book)}<div class="shelf-book-title"><h2 title="${e(book.title)}">${e(book.title)}</h2>${bookMenu(book,trash,data.library_management)}</div><div class="shelf-metadata"><span>${book.page_count} 页</span>${bookState(book,data.runs || [])}</div></article>`).join("")}</div>`;
}
export function library(data,state={}) {
  const active=data.runs.filter(r=>r.state==="RUNNING");
  return `<section class="bookshelf" data-drop-zone>
    <div class="page-head"><h1>我的书库</h1><button data-upload class="primary">＋ 导入 PDF</button></div>
    <div class="library-toolbar"><div class="segmented" aria-label="书籍范围"><button data-library-trash="false" aria-pressed="${!state.trash}">全部文献</button><button data-library-trash="true" aria-pressed="${Boolean(state.trash)}">回收站${data.deleted_books?.length ? " · "+data.deleted_books.length : ""}</button></div><input id="library-search" type="search" placeholder="搜索书名…" aria-label="搜索书名" value="${e(state.query || "")}"><select id="library-sort" aria-label="书籍排序"><option value="recent"${!state.sort || state.sort==="recent" ? " selected" : ""}>最近导入</option><option value="title"${state.sort==="title" ? " selected" : ""}>按书名</option><option value="pages"${state.sort==="pages" ? " selected" : ""}>按页数</option></select><div class="segmented" aria-label="书库布局"><button data-library-view="grid" aria-pressed="${state.view!=="list"}">书架</button><button data-library-view="list" aria-pressed="${state.view==="list"}">列表</button></div></div>
    <div id="library-results">${libraryResults(data,state)}</div>
    ${active.length ? `<div class="shelf-task-strip"><span class="shelf-state processing">正在处理</span><span>${e(active[0].title)}</span><button data-run="${e(active[0].id)}" class="text-button">查看任务 ↗</button></div>` : '<p class="shelf-drop-hint">拖入 PDF 即可添加文献 · 封面与原书独立保存</p>'}
    </section>
    <dialog id="book-dialog" aria-labelledby="book-dialog-title"><form id="book-form"><div class="row"><h2 id="book-dialog-title">管理书籍</h2><button type="button" data-book-close aria-label="关闭">×</button></div><p id="book-dialog-description"></p><label id="book-title-label">书名<input name="title" maxlength="300" required></label><p id="book-error" class="error" role="alert"></p><div class="row end"><button type="button" data-book-close>取消</button><button id="book-submit" type="submit" class="primary">保存</button></div></form></dialog>
    <dialog id="cover-dialog" aria-labelledby="cover-dialog-title"><form id="cover-form"><div class="row"><h2 id="cover-dialog-title">书籍封面</h2><button type="button" data-cover-close aria-label="关闭封面设置">×</button></div><p id="cover-book-title"></p><div class="cover-editor-preview" id="cover-preview"></div><label class="cover-file-label">选择图片或 PDF<input id="cover-file" name="file" type="file" accept="image/jpeg,image/png,image/webp,application/pdf,.pdf"></label><p class="hint">图片支持 JPG、PNG、WebP（最大 12 MB）；PDF 读取第一页（最大 200 MB）。保存后用作独立封面。</p><p id="cover-error" class="error" role="alert"></p><div class="row end"><button type="button" id="remove-cover">移除封面</button><span class="spacer"></span><button type="button" data-cover-close>取消</button><button type="submit" id="save-cover" class="primary" disabled>保存封面</button></div></form></dialog>`;
}
