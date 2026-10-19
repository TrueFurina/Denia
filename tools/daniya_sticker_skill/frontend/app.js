"use strict";

const state = {
  packs: [],
  currentPackId: null,
  characters: [],
  llmAvailable: false,
  ctxPackId: null,
};

// ---------------- 工具 ----------------
async function api(method, path, body, isForm = false) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) {
      opt.body = body;
    } else {
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
  }
  const r = await fetch(path, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  if (r.status === 204) return null;
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : null;
}

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.add("hidden"), 2200);
}

function bindLabel(b) {
  if (!b || b.mode === "none") return "无绑定（全部角色可用）";
  const names = (b.characters || []).join("、") || "（空）";
  return b.mode === "whitelist" ? `白名单：${names}` : `黑名单：${names}`;
}

// ---------------- 包列表 ----------------
async function loadPacks() {
  state.packs = await api("GET", "/api/packs");
  renderPacks();
}

function renderPacks() {
  const ul = document.getElementById("packList");
  ul.innerHTML = "";
  if (!state.packs.length) {
    ul.innerHTML = '<li style="color:var(--muted);font-size:13px;padding:8px">还没有文件夹，点「新建文件夹」</li>';
    return;
  }
  for (const p of state.packs) {
    const li = document.createElement("li");
    li.className = "pack-item" + (p.id === state.currentPackId ? " active" : "");
    li.dataset.id = p.id;
    li.innerHTML = `<span class="emoji">📁</span><span class="name">${escapeHtml(p.name)}</span><span class="cnt">${p.meme_count || 0}</span>`;
    li.addEventListener("click", () => openPack(p.id));
    li.addEventListener("contextmenu", (e) => { e.preventDefault(); showCtx(e.clientX, e.clientY, p.id); });
    ul.appendChild(li);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function openPack(pid) {
  state.currentPackId = pid;
  const p = state.packs.find((x) => x.id === pid);
  document.getElementById("curPack").textContent = p ? p.name : "未选择文件夹";
  const chip = document.getElementById("bindChip");
  chip.textContent = bindLabel(p && p.bindings);
  chip.classList.remove("hidden");
  renderPacks();
  loadMemes();
}

// ---------------- 表情网格 ----------------
async function loadMemes() {
  const grid = document.getElementById("grid");
  const empty = document.getElementById("emptyHint");
  if (!state.currentPackId) { grid.innerHTML = ""; empty.classList.remove("hidden"); return; }
  empty.classList.add("hidden");
  const memes = await api("GET", `/api/packs/${state.currentPackId}/memes`);
  grid.innerHTML = "";
  for (const m of memes) {
    const card = document.createElement("div");
    card.className = "card";
    const desc = m.description ? escapeHtml(m.description) : '<span style="color:var(--muted)">未描述</span>';
    const tags = (m.tags || []).map((t) => "#" + escapeHtml(t)).join(" ");
    const prov = m.provenance === "manual" ? '<span class="prov manual">手动</span>'
      : (m.provenance === "ai" ? '<span class="prov ai">AI</span>' : '<span class="prov none">未标注</span>');
    card.innerHTML = `
      <div class="acts">
        <button class="icon-btn ai" title="AI 标注">✨</button>
        <button class="icon-btn edit" title="编辑标签">✎</button>
        <button class="icon-btn del" title="删除">🗑</button>
      </div>
      <img src="/api/packs/${state.currentPackId}/images/${m.id}" loading="lazy" />
      <div class="meta"><div class="desc">${prov} ${desc}</div><div class="tags">${tags}</div></div>`;
    card.querySelector(".del").addEventListener("click", () => delMeme(m.id));
    card.querySelector(".edit").addEventListener("click", () => openMemeEdit(m));
    card.querySelector(".ai").addEventListener("click", () => retagMeme(m.id));
    grid.appendChild(card);
  }
  if (!memes.length) {
    grid.innerHTML = '<div style="color:var(--muted);grid-column:1/-1;text-align:center;padding:30px">这文件夹还是空的，拖图片进来或点上方上传</div>';
  }
}

async function uploadFiles(files) {
  if (!state.currentPackId) { toast("先选一个文件夹"); return; }
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  try {
    await api("POST", `/api/packs/${state.currentPackId}/upload`, fd, true);
    toast(`已上传 ${files.length} 张`);
    await loadPacks();
    await loadMemes();
  } catch (e) { toast("上传失败：" + e.message); }
}

async function delMeme(mid) {
  if (!confirm("删除这张表情？")) return;
  await api("DELETE", `/api/packs/${state.currentPackId}/memes/${mid}`);
  await loadPacks();
  await loadMemes();
}

async function describeMeme(mid) {
  if (!state.llmAvailable) { toast("未配置智谱 key，无法自动描述"); return; }
  toast("AI 描述中…");
  try {
    const r = await api("POST", "/api/llm/describe", { pack_id: state.currentPackId, meme_id: mid });
    toast(r.description ? "已生成描述" : "未生成描述");
    await loadMemes();
  } catch (e) { toast("描述失败：" + e.message); }
}

// 单张重新自动标注（走完整标签链路：情绪+场景+反触发）
async function retagMeme(mid) {
  if (!state.llmAvailable) { toast("未配置视觉模型，无法自动标注"); return; }
  toast("AI 标注中…");
  try {
    const r = await api("POST", `/api/packs/${state.currentPackId}/memes/${mid}/autotag?force=true`);
    if (r.skipped) { toast(r.reason || "已跳过"); }
    else { toast(r.ok ? "已重新标注" : ("标注失败：" + (r.result && r.result.error || "未知"))); }
    await loadMemes();
  } catch (e) { toast("标注失败：" + e.message); }
}

// ---------------- 右键菜单 ----------------
function showCtx(x, y, pid) {
  state.ctxPackId = pid;
  const m = document.getElementById("ctxMenu");
  m.style.left = x + "px";
  m.style.top = y + "px";
  m.classList.remove("hidden");
}
document.addEventListener("click", () => document.getElementById("ctxMenu").classList.add("hidden"));
document.getElementById("ctxMenu").addEventListener("click", (e) => {
  const act = e.target.dataset.act;
  if (!act || !state.ctxPackId) return;
  const pid = state.ctxPackId;
  if (act === "open") openPack(pid);
  else if (act === "rename") openRename(pid);
  else if (act === "bind") openBind(pid);
  else if (act === "delete") delPack(pid);
});

async function delPack(pid) {
  const p = state.packs.find((x) => x.id === pid);
  if (!confirm(`删除文件夹「${p ? p.name : ""}」及其所有表情？`)) return;
  await api("DELETE", `/api/packs/${pid}`);
  if (state.currentPackId === pid) state.currentPackId = null;
  await loadPacks();
  await loadMemes();
}

// ---------------- 模态框 ----------------
function openModal(title, html) {
  document.getElementById("modalTitle").textContent = title;
  document.getElementById("modalBody").innerHTML = html;
  document.getElementById("modal").classList.remove("hidden");
}
function closeModal() { document.getElementById("modal").classList.add("hidden"); }
document.getElementById("modalClose").addEventListener("click", closeModal);
document.getElementById("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

function openRename(pid) {
  const p = state.packs.find((x) => x.id === pid);
  openModal("重命名文件夹", `
    <div class="field"><label>名称</label><input id="rnInput" type="text" value="${escapeHtml(p.name)}" /></div>
    <div class="row"><button class="btn" id="rnSave">保存</button></div>`);
  document.getElementById("rnSave").addEventListener("click", async () => {
    const name = document.getElementById("rnInput").value.trim();
    if (!name) return;
    await api("PATCH", `/api/packs/${pid}/rename`, { name });
    closeModal(); await loadPacks();
    if (state.currentPackId === pid) document.getElementById("curPack").textContent = name;
  });
}

function openBind(pid) {
  const p = state.packs.find((x) => x.id === pid);
  const b = p.bindings || { mode: "none", characters: [] };
  const modeOpts = [["none", "无绑定（全部角色可用）"], ["whitelist", "白名单（仅这些角色可用）"], ["blacklist", "黑名单（这些角色不可用）"]];
  const modeHtml = modeOpts.map(([v, t]) => `<option value="${v}" ${b.mode === v ? "selected" : ""}>${t}</option>`).join("");
  const charsHtml = state.characters.map((c) => {
    const chk = (b.characters || []).includes(c) ? "checked" : "";
    return `<label class="chip-chk"><input type="checkbox" value="${escapeHtml(c)}" ${chk}/>${escapeHtml(c)}</label>`;
  }).join("");
  openModal("绑定角色", `
    <div class="field"><label>绑定模式</label><select id="bdMode">${modeHtml}</select></div>
    <div class="field"><label>角色（可多选；也可在下方手动添加）</label><div class="chars-box" id="bdChars">${charsHtml}</div>
      <input id="bdAdd" type="text" placeholder="手动添加角色名，回车" /></div>
    <div class="row"><button class="btn" id="bdSave">保存绑定</button></div>`);
  document.getElementById("bdAdd").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const v = e.target.value.trim();
      if (!v) return;
      const box = document.getElementById("bdChars");
      if (![...box.querySelectorAll("input")].some((i) => i.value === v))
        box.insertAdjacentHTML("beforeend", `<label class="chip-chk"><input type="checkbox" value="${escapeHtml(v)}" checked/>${escapeHtml(v)}</label>`);
      e.target.value = "";
    }
  });
  document.getElementById("bdSave").addEventListener("click", async () => {
    const mode = document.getElementById("bdMode").value;
    const chars = [...document.querySelectorAll("#bdChars input:checked")].map((i) => i.value);
    await api("POST", `/api/packs/${pid}/bindings`, { mode, characters: chars });
    closeModal(); await loadPacks();
    if (state.currentPackId === pid) { const np = state.packs.find((x) => x.id === pid); document.getElementById("bindChip").textContent = bindLabel(np.bindings); }
    toast("已保存绑定");
  });
}

// ---------------- 标签编辑（自动标注 + 用户手改）----------------
// 设计：分类权限交给用户。自动标注只是"给个初稿"，用户随时能改；
// 一旦手改（provenance=manual），后续自动标注不会再覆盖它。
function openMemeEdit(m) {
  const isManual = m.provenance === "manual";
  const src = isManual
    ? '<span class="prov manual">手动编辑</span>'
    : (m.provenance === "ai" ? '<span class="prov ai">AI 自动标注</span>' : '<span class="prov none">未标注</span>');
  const cur = new Set(m.tags || []);
  const vocab = (state.emotionVocab || []);
  const chips = vocab.map((w) =>
    `<span class="chip${cur.has(w) ? " on" : ""}" data-w="${escapeHtml(w)}">${escapeHtml(w)}</span>`).join("");
  const sv = m.style_variants || {};
  const com = sv.community || {};
  const auto = m.auto || {};

  openModal("编辑标签" + (src ? "" : ""), `
    <div class="field"><label>当前来源 ${src}</label>
      <div class="hint">${isManual
        ? "已手动编辑过，自动标注不会覆盖。"
        : "自动标注结果，可自行修改；改完会记为「手动编辑」并固定下来。"}</div></div>
    <div class="field"><label>画面描述</label>
      <input id="edDesc" type="text" value="${escapeHtml(m.description || "")}" /></div>
    <div class="field"><label>情绪词（点选，可多选）</label>
      <div id="edChips" class="chips">${chips || '<span class="hint">（情绪词表未加载）</span>'}</div>
      <input id="edTags" type="text" placeholder="也可直接输入，逗号分隔"
             value="${escapeHtml((m.tags || []).join("、"))}" style="margin-top:6px" /></div>
    <div class="field"><label>适用场景（选贴时最重要）</label>
      <input id="edTrigger" type="text" placeholder="用于…时"
             value="${escapeHtml(m.trigger || com.trigger || "")}" /></div>
    <div class="field"><label>不该用于（防误发）</label>
      <input id="edAnti" type="text" placeholder="不该用于…时"
             value="${escapeHtml(com.anti_trigger || "")}" /></div>
    <div class="field"><label>分类</label>
      <input id="edCat" type="text" value="${escapeHtml(m.category || "")}" /></div>
    ${auto.description ? `<div class="field"><label>AI 原始结果（对照用）</label>
      <div class="hint">${escapeHtml(auto.description)}<br/>${escapeHtml((auto.tags || []).join("、"))}</div></div>` : ""}
    <div class="row">
      <button class="btn" id="edSave">保存</button>
      <button class="btn ghost" id="edRetag">重新 AI 标注</button>
    </div>`);

  document.getElementById("edChips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    chip.classList.toggle("on");
    const on = [...document.querySelectorAll("#edChips .chip.on")].map((c) => c.dataset.w);
    document.getElementById("edTags").value = on.join("、");
  });

  document.getElementById("edSave").addEventListener("click", async () => {
    const tags = document.getElementById("edTags").value.split(/[,，、]/).map((s) => s.trim()).filter(Boolean);
    const body = {
      description: document.getElementById("edDesc").value.trim(),
      tags,
      trigger: document.getElementById("edTrigger").value.trim(),
      anti_trigger: document.getElementById("edAnti").value.trim(),
      category: document.getElementById("edCat").value.trim(),
    };
    await api("PATCH", `/api/packs/${state.currentPackId}/memes/${m.id}/labels`, body);
    toast("已保存（固定为手动标签）");
    closeModal(); await loadMemes();
  });

  document.getElementById("edRetag").addEventListener("click", async () => {
    if (!confirm("用 AI 重新标注并覆盖当前标签？")) return;
    toast("AI 标注中…");
    const r = await api("POST", `/api/packs/${state.currentPackId}/memes/${m.id}/autotag?force=true`);
    toast(r.ok ? "已重新标注" : ("标注失败：" + (r.result && r.result.error || "未知")));
    closeModal(); await loadMemes();
  });
}

// 整包补标：把"没标过且没手改过"的图跑一遍
async function autotagPack() {
  if (!state.currentPackId) { toast("先选一个文件夹"); return; }
  if (!state.llmAvailable) { toast("未配置视觉模型，无法自动标注"); return; }
  toast("整包标注中，请稍候…");
  try {
    const r = await api("POST", `/api/packs/${state.currentPackId}/autotag?only_missing=true`);
    toast(`标注完成：成功 ${r.tagged}，失败 ${r.failed}，跳过 ${r.skipped}`);
    await loadMemes();
  } catch (e) { toast("标注失败：" + e.message); }
}

// ---------------- 资源广场 ----------------
async function openMarketplace() {
  openModal("资源广场（云端免费表情包）", `<div id="mkList">加载中…</div>`);
  const data = await api("GET", "/api/marketplace");
  const box = document.getElementById("mkList");
  if (!data.ok) { box.innerHTML = `<div style="color:#d23b3b">索引拉取失败：${escapeHtml(data.error || "")}<br/>（需联网；可在 config 里换 MARKETPLACE_INDEX_URL）</div>`; return; }
  if (!data.entries.length) { box.innerHTML = "<div>索引为空</div>"; return; }
  box.innerHTML = "";
  for (const e of data.entries) {
    const name = e.name || e.title || "未命名包";
    const desc = e.description || e.desc || "";
    const hasZip = [e.url, e.download, e.zip, e.repo].some((u) => u && String(u).toLowerCase().endsWith(".zip"));
    const item = document.createElement("div");
    item.className = "market-item";
    item.innerHTML = `<div><div class="mi-name">${escapeHtml(name)}</div><div class="mi-desc">${escapeHtml(desc)}</div></div>
      <button class="btn small" ${hasZip ? "" : "disabled"}>安装</button>`;
    item.querySelector("button").addEventListener("click", async () => {
      item.querySelector("button").textContent = "安装中…";
      const r = await api("POST", "/api/marketplace/install", { entry: e });
      if (r.ok) { toast(`已安装「${r.name}」(${r.imported} 张)`); await loadPacks(); }
      else toast("安装失败：" + (r.error || ""));
      item.querySelector("button").textContent = "安装";
    });
    box.appendChild(item);
  }
}

// ---------------- 模拟选贴 ----------------
function openPick() {
  const charOpts = state.characters.map((c) => `<option>${escapeHtml(c)}</option>`).join("");
  openModal("模拟选贴（真人感）", `
    <div class="field"><label>角色</label><select id="pkChar">${charOpts}</select></div>
    <div class="field"><label>对话上下文（每行一条，前缀「用户:」或「达妮娅:」，默认用户）</label>
      <textarea id="pkConv" rows="6" placeholder="用户: 今天好累啊&#10;达妮娅: 辛苦啦～"></textarea></div>
    <div class="row"><button class="btn" id="pkRun">让角色选一张</button></div>
    <div id="pkResult" class="pick-result" style="display:none"></div>`);
  document.getElementById("pkRun").addEventListener("click", async () => {
    const character = document.getElementById("pkChar").value;
    const conv = document.getElementById("pkConv").value.split("\n").filter(Boolean).map((l) => {
      if (l.startsWith("达妮娅:")) return { role: "assistant", content: l.slice(4).trim() };
      if (l.startsWith("用户:")) return { role: "user", content: l.slice(3).trim() };
      return { role: "user", content: l.trim() };
    });
    const r = await api("POST", "/api/pick", { character, conversation: conv });
    const box = document.getElementById("pkResult");
    box.style.display = "block";
    if (!state.llmAvailable) {
      box.innerHTML = `<b>本地模式：</b>${escapeHtml(r.reason || "")}，可用候选 ${r.candidates || 0} 张。配置智谱 key 后启用真人感选贴。`;
      return;
    }
    if (r.use && r.found) {
      box.innerHTML = `<b>${escapeHtml(character)}</b> 选择发送：<br/><img src="/api/packs/${r.pack_id}/images/${r.meme_id}" /><br/>理由：${escapeHtml(r.reason || "")}`;
    } else {
      box.innerHTML = `<b>${escapeHtml(character)}</b> 决定不发表情。理由：${escapeHtml(r.reason || "")}`;
    }
  });
}

// ---------------- 搜索 ----------------
async function doSearch() {
  const q = document.getElementById("searchBox").value.trim();
  const character = document.getElementById("searchChar").value;
  const grid = document.getElementById("grid");
  const empty = document.getElementById("emptyHint");
  empty.classList.add("hidden");
  const payload = { query: q };
  if (character) payload.character = character;
  else if (state.currentPackId) payload.pack_id = state.currentPackId;
  const res = await api("POST", "/api/search", payload);
  grid.innerHTML = "";
  if (!res.length) { grid.innerHTML = '<div style="color:var(--muted);grid-column:1/-1;text-align:center;padding:30px">没找到匹配</div>'; return; }
  for (const m of res) {
    const card = document.createElement("div");
    card.className = "card";
    const desc = m.description ? escapeHtml(m.description) : '<span style="color:var(--muted)">未描述</span>';
    const tags = (m.tags || []).map((t) => "#" + escapeHtml(t)).join(" ");
    card.innerHTML = `<img src="/api/packs/${m.pack_id}/images/${m.id}" loading="lazy" />
      <div class="meta"><div class="desc">${desc}</div><div class="tags">${tags}</div></div>`;
    grid.appendChild(card);
  }
}

// ---------------- 自动收集（用户发来的表情·待审队列）----------------
// 后端闸门（采样/去重/过期）已在 collect.py 就绪；这里做人类审核界面。
async function openCollect() {
  const packsOpts = state.packs.map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
  openModal("自动收集（用户发来的表情）", `
    <div class="hint">达妮娅聊天时，用户发来的图会经「重复采样闸门 → 去重 → 待审」进到这里，你确认分类后接收入包。
      下方也可把一张图拖入/选中，模拟「用户发来一张图」（仅本地测试）。</div>
    <div class="field">
      <label>模拟用户发送一张图（测试用）</label>
      <div id="colDrop" class="col-drop">把图片拖到这里，或
        <label class="link">选择文件<input id="colFile" type="file" accept="image/*" hidden /></label>
      </div>
    </div>
    <div id="colStats" class="hint">统计：加载中…</div>
    <div id="colGrid" class="col-grid"></div>`);

  const grid = document.getElementById("colGrid");
  const stats = document.getElementById("colStats");

  async function refresh() {
    const data = await api("GET", "/api/collect/pending");
    const s = data.stats || {};
    stats.textContent = `统计：已记录 ${s.records || 0} · 已接收 ${s.accepted || 0} · 已拒绝 ${s.rejected || 0} · 待审 ${s.pending || 0}`;
    grid.innerHTML = "";
    if (!data.items || !data.items.length) {
      grid.innerHTML = '<div class="hint" style="grid-column:1/-1;text-align:center;padding:18px">待审队列为空。拖一张图进来模拟用户发送试试。</div>';
      return;
    }
    for (const it of data.items) {
      const card = document.createElement("div");
      card.className = "col-item";
      card.innerHTML = `
        <img src="/api/collect/pending/${it.hash}/image" loading="lazy" />
        <div class="col-meta">
          <div class="col-sug">建议分类：${escapeHtml(it.suggested_category || "（未建议）")}
            <span class="col-conf">置信 ${Math.round((it.category_confidence || 0) * 100)}%</span></div>
          <input class="col-cat" type="text" placeholder="确认分类，如 开心/撒娇" value="${escapeHtml(it.suggested_category || "")}" />
          <div class="row" style="margin-top:6px">
            <button class="btn small col-accept">接收</button>
            <button class="btn small ghost col-reject">拒绝</button>
          </div>
        </div>`;
      card.querySelector(".col-accept").addEventListener("click", async () => {
        const cat = card.querySelector(".col-cat").value.trim();
        const r = await api("POST", "/api/collect/accept", { hash: it.hash, category: cat });
        if (r.ok) { toast(`已接收入「${r.pack_id ? "" : ""}」包`); refresh(); await loadPacks(); }
        else toast("接收失败");
      });
      card.querySelector(".col-reject").addEventListener("click", async () => {
        if (!confirm("拒绝这张？相同内容以后不再自动收集。")) return;
        const r = await api("POST", "/api/collect/reject", { hash: it.hash, never_again: true });
        if (r.ok) { toast("已拒绝"); refresh(); }
      });
      grid.appendChild(card);
    }
  }
  await refresh();

  // 本地模拟「用户发来一张图」：走 observe 闸门，platform_marked=true 必进抽样
  const fileInput = document.getElementById("colFile");
  const drop = document.getElementById("colDrop");
  async function feed(file) {
    if (!file || !file.type.startsWith("image/")) return;
    const b64 = await new Promise((res) => {
      const fr = new FileReader();
      fr.onload = () => res(fr.result.split(",")[1]);
      fr.readAsDataURL(file);
    });
    const ext = (file.name.split(".").pop() || "png").toLowerCase();
    try {
      const r = await api("POST", "/api/collect/observe", {
        data_b64: b64, ext, sender: "本地测试", pack_id: state.currentPackId || "",
        platform_marked: true, suggested_category: "",
      });
      toast(r.collected ? "已进待审队列" : ("未收集：" + (r.reason || "")));
      if (r.collected) refresh();
    } catch (e) { toast("提交失败：" + e.message); }
  }
  fileInput.addEventListener("change", () => { if (fileInput.files[0]) feed(fileInput.files[0]); });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => {
    const f = [...(e.dataTransfer?.files || [])].find((x) => x.type.startsWith("image/"));
    if (f) feed(f);
  });
}

// ---------------- 事件绑定 ----------------
document.getElementById("btnNew").addEventListener("click", async () => {
  const name = prompt("文件夹名称（例如：达妮娅）：", "达妮娅");
  if (!name) return;
  await api("POST", "/api/packs", { name });
  await loadPacks();
  const created = state.packs[state.packs.length - 1];
  if (created) openPack(created.id);
});
document.getElementById("btnMarket").addEventListener("click", openMarketplace);
document.getElementById("btnPick").addEventListener("click", openPick);
document.getElementById("btnCollect").addEventListener("click", openCollect);
document.getElementById("btnSearch").addEventListener("click", doSearch);
document.getElementById("searchBox").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

// 拖拽上传
const dz = document.getElementById("dropzone");
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (e) => {
  const files = [...(e.dataTransfer?.files || [])].filter((f) => f.type.startsWith("image/"));
  if (files.length) uploadFiles(files);
});

// ---------------- 初始化 ----------------
(async function init() {
  try {
    state.characters = await api("GET", "/api/characters");
    const sel = document.getElementById("searchChar");
    state.characters.forEach((c) => { const o = document.createElement("option"); o.value = c; o.textContent = c; sel.appendChild(o); });
    const st = await api("GET", "/api/llm/status");
    state.llmAvailable = st.available;
    const badge = document.getElementById("llmBadge");
    badge.textContent = st.available ? "LLM：智谱已连接" : "LLM：未配置（仅本地）";
    badge.className = "badge " + (st.available ? "on" : "off");
    // 情绪词表（供手改时点选）
    try { state.emotionVocab = await api("GET", "/api/emotion_vocab"); } catch (e) { state.emotionVocab = []; }
    const btnTag = document.getElementById("btnAutotag");
    if (btnTag) btnTag.addEventListener("click", autotagPack);
  } catch (e) { toast("初始化失败：" + e.message); }
  await loadPacks();
})();
