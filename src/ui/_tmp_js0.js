
/* ============================================================
   表情包管理页 前端控制器（2026-10-13）
   后端：Backend.sticker*() → src/ui/sticker_bridge.py → 技能包
   设计：全部走 design_tokens 变量；动效统一用 --ease / --ease-out
   ============================================================ */
(function () {
  var S = { packs: [], cur: null, memes: [], vocab: [], filter: "", chars: [] };
  var $ = function (id) { return document.getElementById(id); };

  /* ---------------- Toast（动态创建，理由见 HTML 顶部注释） ---------------- */
  function toast(msg, kind) {
    var t = document.querySelector(".sk-toast");
    if (!t) {
      t = document.createElement("div");
      t.className = "sk-toast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.borderColor = kind === "err" ? "var(--bad)" : (kind === "ok" ? "var(--good)" : "var(--line-2)");
    t.classList.add("show");
    clearTimeout(t._h);
    t._h = setTimeout(function () { t.classList.remove("show"); }, 2800);
  }

  /* ---------------- 弹窗（动态创建） ---------------- */
  function dialog(title, html) {
    var mask = document.querySelector(".sk-modal-mask");
    if (!mask) {
      mask = document.createElement("div");
      mask.className = "sk-modal-mask";
      mask.innerHTML =
        '<div class="sk-modal-panel">' +
          '<div class="sk-modal-head">' +
            '<div class="ct" style="margin:0" data-dt></div>' +
            '<button class="sk-ico" data-close style="margin-left:auto">✕</button>' +
          '</div>' +
          '<div data-dbody></div>' +
        '</div>';
      document.body.appendChild(mask);
      mask.addEventListener("click", function (e) {
        if (e.target === mask || e.target.closest("[data-close]")) closeDialog();
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") closeDialog();
      });
    }
    mask.querySelector("[data-dt]").textContent = title;
    mask.querySelector("[data-dbody]").innerHTML = html;
    mask.classList.add("open");
  }
  function closeDialog() {
    var m = document.querySelector(".sk-modal-mask");
    if (m) m.classList.remove("open");
  }
  /* 取当前弹窗的正文容器（弹窗是动态创建的，没有固定 id） */
  function dbody() {
    var m = document.querySelector(".sk-modal-mask");
    return m ? m.querySelector("[data-dbody]") : null;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  /* 角色头像 URL：avatar 是**方头像**（不是 banner 头图），走 /local_img 取图 */
  function avatarUrl(c) {
    if (c && c.avatar) return "/local_img?path=" + encodeURIComponent(c.avatar);
    return "";
  }
  function avatarHtml(c) {
    var u = avatarUrl(c);
    if (u) return '<span class="sk-cav"><img src="' + esc(u) + '" alt=""></span>';
    // 无头像：中性人形剪影（与角色页占位一致，不用姓名首字）
    return '<span class="sk-cav ph"><svg viewBox="0 0 24 24" fill="currentColor">' +
      '<circle cx="12" cy="8.2" r="3.7"/>' +
      '<path d="M12 13.7c-4.15 0-7.5 2.34-7.5 5.23 0 .63.53 1.14 1.18 1.14h12.64c.65 0 1.18-.51 1.18-1.14 0-2.89-3.35-5.23-7.5-5.23z"/>' +
      '</svg></span>';
  }
  function call(m) {
    var args = Array.prototype.slice.call(arguments, 1);
    if (!window.Backend || !Backend[m]) {
      return Promise.resolve({ ok: false, offline: true, error: "后端未连接（" + m + "）" });
    }
    /* ⚠️【2026-10-13 关键修复】app_backend.js 的 call() 有**两套返回约定**：
         成功 → 原样返回后端 dict（本项目后端用 {ok:true,...}）
         失败 → { success:false, offline:true|error:'xxx' }
       旧版这里只判断 `r.ok === false`，于是：
         r.ok 是 undefined（离线/异常）≠ false → **被当成成功** →
         接着读 r.packs 得到 undefined → 渲染空列表 → 且不报错。
       表现就是用户看到的「永远加载中、点什么都没反应」。
       现在统一收敛成 { ok, offline, error, ... }。 */
    return Promise.resolve(Backend[m].apply(null, args)).then(function (r) {
      if (r && r.success === false) {
        return {
          ok: false,
          offline: !!r.offline,
          error: r.offline ? "后端未连接（页面在预览模式或桥未就绪）" : (r.error || "调用失败")
        };
      }
      if (!r || typeof r !== "object") return { ok: true, value: r };
      // 后端显式 ok:false 也算失败
      if (r.ok === false) return r;
      return r;
    }).catch(function (e) {
      return { ok: false, error: "调用异常：" + ((e && e.message) || e) };
    });
  }
  /* 统一的失败提示：区分「后端没连」与「后端报错」，两者文案不同。 */
  function failMsg(r, fallback) {
    if (!r) return fallback || "失败";
    if (r.offline) return "后端未连接：请从达妮娅界面进入本页（当前是预览模式）";
    return r.error || fallback || "失败";
  }

  /* 兼容别名：旧代码里写的是 modal()/closeModal()，现在统一走动态创建的 dialog()。
     （留着别名可少改调用点，也避免漏改导致 undefined 报错把整页脚本打断。） */
  var modal = dialog;
  var closeModal = closeDialog;

  /* ---------- 渲染：包列表 ---------- */
  function renderPacks() {
    var el = $("skPacks");
    if (!S.packs.length) {
      el.innerHTML = '<div class="sk-hint">还没有表情包。<br/>点右上「＋ 新建表情包」开始。</div>';
      return;
    }
    el.innerHTML = S.packs.map(function (p) {
      return '<div class="sk-pack' + (S.cur === p.id ? " on" : "") + '" data-id="' + p.id + '">' +
        '<span class="pn">' + esc(p.name) + '</span>' +
        '<span class="pc">' + (p.meme_count || 0) + '</span>' +
        '<button class="pd" data-del="' + p.id + '" title="删除">🗑</button></div>';
    }).join("");
  }
  $("skPacks").addEventListener("click", function (e) {
    var d = e.target.closest("[data-del]");
    if (d) {
      e.stopPropagation();
      var pid = d.getAttribute("data-del");
      if (!confirm("删除这个表情包？里面的表情会一起删掉。")) return;
      call("stickerDeletePack", pid).then(function (r) {
        if (r && r.ok === false) return toast(failMsg(r, "删除失败"), "err");
        toast("已删除", "ok"); refreshPacks();
      });
      return;
    }
    var row = e.target.closest(".sk-pack");
    if (row) selectPack(row.getAttribute("data-id"));
  });

  /* ---------- 渲染：表情网格 ---------- */
  function provBadge(p) {
    if (p === "manual") return '<span class="sk-prov manual">手动</span>';
    if (p === "ai") return '<span class="sk-prov ai">AI</span>';
    return '<span class="sk-prov none">未标注</span>';
  }
  /* ---------- 分批渲染（不一次拉完）----------
     表情包可能有几百张，一次性全渲染会卡。
     每批 BATCH 张，渲染完一批等 1 帧再渲染下一批（浏览器有机会画出来）。
     滚动到底自动加载下一批（ Infinite Scroll）。 */
  var BATCH = 24;
  var renderCursor = 0;      // 已渲染到第几条
  var renderList = [];       // 当前过滤后的完整列表
  var loadingMore = false;

  function loadMore() {
    if (loadingMore || renderCursor >= renderList.length) return;
    loadingMore = true;
    var grid = $("skGrid");
    var slice = renderList.slice(renderCursor, renderCursor + BATCH);
    renderCursor += BATCH;
    var base = renderCursor - slice.length;
    var frag = document.createDocumentFragment();
    slice.forEach(function (m, i) {
      var wrap = document.createElement("div");
      wrap.innerHTML = cardHtml(m, base + i);
      var card = wrap.firstElementChild;
      // 瀑布流交错入场
      card.style.animationDelay = Math.min(i * 22, 260) + "ms";
      frag.appendChild(card);
      loadThumb(m, card);
    });
    grid.appendChild(frag);
    loadingMore = false;
    // 更新「还有多少」提示
    var more = $("skMore");
    if (more) {
      var left = renderList.length - renderCursor;
      more.hidden = left <= 0;
      more.textContent = left > 0 ? "下滑加载更多（还有 " + left + " 张）…" : "";
    }
  }

  function cardHtml(m, i) {
    return '<div class="sk-card" data-mid="' + m.id + '" style="animation-delay:' +
      Math.min(i * 22, 260) + 'ms">' +
      '<div class="sk-acts">' +
        '<button class="sk-ico" data-act="edit" title="编辑标签">✎</button>' +
        '<button class="sk-ico" data-act="tag" title="重新 AI 标注">✨</button>' +
        '<button class="sk-ico danger" data-act="del" title="删除">🗑</button>' +
      '</div>' +
      /* 相机对焦：先放主色调模糊占位，原图到了再清晰 + 从 1.04 缩回 1.0 */
      '<div class="sk-thumb" data-thumb="' + m.id + '"><div class="sk-focus"></div></div>' +
      '<div class="sk-meta">' +
        '<div class="sk-badge-row">' + provBadge(m.provenance) +
          (m.category ? '<span class="sk-tag" style="font-size:9px">' + esc(m.category) + '</span>' : '') +
        '</div>' +
        '<div class="sk-desc">' + esc(m.description || "（未描述）") + '</div>' +
        '<div class="sk-tags">' + (m.tags || []).slice(0, 4).map(function (t) {
          return '<span class="sk-tag">' + esc(t) + '</span>';
        }).join("") + '</div>' +
      '</div></div>';
  }

  /* ---------- 相机对焦加载 ----------
     ① 先放一张**同色调模糊小图**（主色 + 20px 高斯模糊）：从卡片 meta 的 tags/desc
        无法取色，故先从后端要一张 8px 迷你图当占位（体积极小、几 KB）。
     ② 原图加载完成 → 淡入清晰 + 从 1.04 缩回 1.0（约 500ms）→ 再替换为动图（GIF）。
     好处：大图/动图加载期间不再"白块"，视觉上有"对焦合焦"的完成感。 */
  function loadThumb(m, card) {
    var box = card.querySelector(".sk-thumb");
    if (!box) return;
    call("stickerImageUrl", S.cur, m.id).then(function (r) {
      if (!box.parentNode) return;
      if (!r || !r.ok || !r.url) {
        box.innerHTML = '<span class="sk-hint">图不可读</span>';
        return;
      }
      var img = document.createElement("img");
      img.decoding = "async";
      img.alt = "";
      // ① 先渲染成**小尺寸模糊占位**（浏览器会自然模糊，因为源被拉伸）
      img.className = "sk-focus-blur";
      img.src = r.url + "&thumb=1";
      box.appendChild(img);
      // ② 原图解码完成后：淡入 + 从 1.04 缩回 1.0
      if (img.decode) {
        img.decode().then(function () { focusIn(box, img, r.url); })
          .catch(function () { setTimeout(function () { focusIn(box, img, r.url); }, 300); });
      } else {
        img.onload = function () { focusIn(box, img, r.url); };
      }
    });
  }

  function focusIn(box, img, url) {
    img.classList.remove("sk-focus-blur");
    img.classList.add("sk-focus-in");           /* 淡入 + 1.04→1.0，约 500ms */
    // ③ 动图：确认它是 GIF（Content-Type 由后端嗅探保证）→ 等对焦动画播完再换
    //    （直接换会打断对焦动画；先静态后动图 = 相机"拍照→显示"的次序感）
    setTimeout(function () {
      var live = box.querySelector("img");
      if (live && live === img) {
        live.src = url;                          /* 换成原始动图 URL（无 thumb 参数） */
        live.classList.remove("sk-focus-in");
      }
    }, 520);
  }

  function renderMemes() {
    var grid = $("skGrid");
    var list = S.memes.filter(function (m) {
      if (!S.filter) return true;
      var hay = ((m.description || "") + " " + (m.trigger || "") + " " + (m.tags || []).join(" ")).toLowerCase();
      return hay.indexOf(S.filter.toLowerCase()) >= 0;
    });
    renderList = list;
    renderCursor = 0;
    if (!S.cur) {
      grid.innerHTML = '<div class="sk-empty" style="grid-column:1/-1"><div class="big">🖼️</div>先从左边选一个表情包</div>';
      return;
    }
    if (!list.length) {
      grid.innerHTML = '<div class="sk-empty" style="grid-column:1/-1"><div class="big">' +
        (S.filter ? "🔍" : "📥") + '</div>' +
        (S.filter ? "没有匹配的表情" : "这个表情包还是空的<br/>把 GIF / PNG 拖到这里即可上传（会自动标注）") +
        '</div>';
      return;
    }
    grid.innerHTML = "";
    // 追加「下滑加载更多」提示条
    if (!document.getElementById("skMore")) {
      var more = document.createElement("div");
      more.id = "skMore";
      more.className = "sk-more";
      more.hidden = true;
      grid.insertAdjacentElement("afterend", more);
    }
    loadMore();          // 先出第一批
  }

  /* 滚动到底 → 加载下一批 */
  function bindInfiniteScroll() {
    var sc = document.querySelector(".content");
    if (!sc || sc.__skInf) return;
    sc.__skInf = true;
    sc.addEventListener("scroll", function () {
      if (renderCursor >= renderList.length || loadingMore) return;
      if (sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 160) loadMore();
    }, { passive: true });
  }
  $("skGrid").addEventListener("click", function (e) {
    var card = e.target.closest("[data-mid]");
    if (!card) return;
    var mid = card.getAttribute("data-mid");
    var m = S.memes.filter(function (x) { return x.id === mid; })[0];
    var btn = e.target.closest("[data-act]");
    if (!btn) return;
    var act = btn.getAttribute("data-act");
    if (act === "edit") openEdit(m);
    else if (act === "tag") {
      toast("AI 标注中…");
      call("stickerAutotagOne", S.cur, mid, true).then(function (r) {
        if (r && r.ok === false) return toast(failMsg(r, "标注失败"), "err");
        toast("已重新标注", "ok"); loadMemes();
      });
    } else if (act === "del") {
      if (!confirm("删除这张表情？")) return;
      call("stickerDeleteMeme", S.cur, mid).then(function () { toast("已删除", "ok"); loadMemes(); });
    }
  });

  /* ---------- 标签编辑（自动标注 + 用户可改） ---------- */
  function openEdit(m) {
    var isManual = m.provenance === "manual";
    var cur = {}; (m.tags || []).forEach(function (t) { cur[t] = 1; });
    var sv = m.style_variants || {}, com = sv.community || {}, auto = m.auto || {};
    var chips = S.vocab.map(function (w) {
      return '<span class="sk-chip' + (cur[w] ? " on" : "") + '" data-w="' + esc(w) + '">' + esc(w) + '</span>';
    }).join("");
    modal("编辑标签", '' +
      '<div class="sk-field"><label>当前来源</label><div class="sk-hint">' +
        (isManual ? "✅ 已手动校对过，自动标注不会覆盖它。"
                  : "由 AI 自动标注。改完会固定为「手动」，之后不再被覆盖。") + '</div></div>' +
      '<div class="sk-field"><label>画面描述</label>' +
        '<input type="text" id="fDesc" value="' + esc(m.description || "") + '" /></div>' +
      '<div class="sk-field"><label>情绪词（点选，可多选）</label>' +
        '<div class="sk-chips" id="fChips">' + (chips || '<span class="sk-hint">词表未加载</span>') + '</div>' +
        '<input type="text" id="fTags" style="margin-top:7px" placeholder="也可直接输入，逗号分隔" value="' +
          esc((m.tags || []).join("、")) + '" /></div>' +
      '<div class="sk-field"><label>适用场景（选贴最关键）</label>' +
        '<input type="text" id="fTrigger" placeholder="用于…时" value="' +
          esc(m.trigger || com.trigger || "") + '" /></div>' +
      '<div class="sk-field"><label>不该用于（防误发）</label>' +
        '<input type="text" id="fAnti" placeholder="不该用于…时" value="' + esc(com.anti_trigger || "") + '" /></div>' +
      '<div class="sk-field"><label>分类</label>' +
        '<input type="text" id="fCat" value="' + esc(m.category || "") + '" /></div>' +
      (auto.description ? '<div class="sk-field"><label>AI 原始结果（对照）</label><div class="sk-hint">' +
        esc(auto.description) + '<br/>' + esc((auto.tags || []).join("、")) + '</div></div>' : "") +
      '<div style="display:flex;gap:9px;margin-top:16px">' +
        '<button class="btn primary" id="fSave">保存</button>' +
        '<button class="btn ghost" id="fRetag">重新 AI 标注</button></div>');

    $("fChips").addEventListener("click", function (e) {
      var c = e.target.closest(".sk-chip");
      if (!c) return;
      c.classList.toggle("on");
      $("fTags").value = Array.prototype.map.call(
        document.querySelectorAll("#fChips .sk-chip.on"), function (x) { return x.dataset.w; }).join("、");
    });
    $("fSave").addEventListener("click", function () {
      var tags = $("fTags").value.split(/[,，、]/).map(function (s) { return s.trim(); }).filter(Boolean);
      call("stickerUpdateLabels", S.cur, m.id, {
        description: $("fDesc").value.trim(),
        tags: tags,
        trigger: $("fTrigger").value.trim(),
        anti_trigger: $("fAnti").value.trim(),
        category: $("fCat").value.trim()
      }).then(function (r) {
        if (r && r.ok === false) return toast(failMsg(r, "保存失败"), "err");
        toast("已保存（固定为手动标签）", "ok"); closeModal(); loadMemes();
      });
    });
    $("fRetag").addEventListener("click", function () {
      if (!confirm("用 AI 重新标注并覆盖当前标签？")) return;
      toast("AI 标注中…");
      call("stickerAutotagOne", S.cur, m.id, true).then(function (r) {
        if (r && r.ok === false) return toast(failMsg(r, "标注失败"), "err");
        toast("已重新标注", "ok"); closeModal(); loadMemes();
      });
    });
  }

  /* ---------- 数据加载 ---------- */
  /* 角色名册（含头像）—— 供绑定角色用。失败不阻塞页面，只是绑定弹窗里提示"读不到"。 */
  function loadCharacters() {
    return call("stickerCharacters").then(function (r) {
      S.chars = (r && r.characters) || [];
      return S.chars;
    }).catch(function () { S.chars = []; return []; });
  }

  function refreshPacks() {
    $("skPacks").innerHTML = '<div class="sk-hint">加载中…</div>';
    return call("stickerPacks").then(function (r) {
      if (!r || r.ok === false) {
        var msg = failMsg(r, "读取失败");
        $("skPacks").innerHTML =
          '<div class="sk-offline"><div class="big">' + (r && r.offline ? "🔌" : "⚠️") + '</div>' +
          esc(msg) + '<div class="hint2">重试可点右上「功能自检」</div></div>';
        $("skBadge").textContent = (r && r.offline) ? "未连接" : "出错";
        $("skBadge").className = "badge off";
        $("skGrid").innerHTML = '<div class="sk-empty" style="grid-column:1/-1">' +
          '<div class="big">🔌</div>' + esc(msg) + '</div>';
        return;
      }
      S.packs = r.packs || [];
      $("skBadge").textContent = "已连接";
      $("skBadge").className = "badge on";
      if (!S.cur && S.packs.length) S.cur = S.packs[0].id;
      renderPacks();
      if (S.cur) return loadMemes();
      renderMemes();
    });
  }
  function selectPack(pid) {
    S.cur = pid;
    S.filter = ""; $("skSearch").value = "";
    renderPacks();
    loadMemes();
  }
  /* 绑定状态文案：统一成「所有角色通用 / N 个角色专属」两种（不再提黑白名单） */
  function bindText(b) {
    b = b || { mode: "none", characters: [] };
    var cs = (b.characters || []).filter(Boolean);
    if (b.mode === "none" || !cs.length) return "所有角色通用";
    if (b.mode === "blacklist") return "除 " + cs.join("、") + " 外通用";
    return (cs.length === 1 ? cs[0] + " 专属" : cs.length + " 个角色专属");
  }
  function loadMemes() {
    if (!S.cur) return Promise.resolve();
    var p = S.packs.filter(function (x) { return x.id === S.cur; })[0];
    $("skCurPack").textContent = p ? p.name : "未选择";
    var b = (p && p.bindings) || { mode: "none", characters: [] };
    var txt = bindText(b);
    $("skBind").textContent = txt;
    $("skBind").className = "badge " + ((b.mode === "none" || !(b.characters || []).length) ? "off" : "on");
    return call("stickerMemes", S.cur).then(function (r) {
      if (!r || r.ok === false) {
        S.memes = [];
        updateStats();
        $("skGrid").innerHTML = '<div class="sk-empty" style="grid-column:1/-1">' +
          '<div class="big">⚠️</div>' + esc(failMsg(r, "读取表情失败")) + '</div>';
        return;
      }
      S.memes = r.memes || [];
      updateStats();
      renderMemes();
    });
  }
  function updateStats() {
    var manual = 0, auto = 0, none = 0;
    S.memes.forEach(function (m) {
      if (m.provenance === "manual") manual++;
      else if (m.provenance === "ai") auto++;
      else none++;
    });
    $("stPacks").textContent = S.packs.length;
    $("stMemes").textContent = S.memes.length;
    $("stManual").textContent = manual;
    $("stAuto").textContent = auto;
    $("stNone").textContent = none;
  }

  /* ---------- 顶部操作 ---------- */
  $("skNewPack").addEventListener("click", function () {
    modal("新建表情包", '<div class="sk-field"><label>名称</label>' +
      '<input type="text" id="npName" placeholder="例如：达妮娅日常" /></div>' +
      '<button class="btn primary" id="npOk">创建</button>');
    $("npName").focus();
    $("npOk").addEventListener("click", function () {
      var n = $("npName").value.trim();
      if (!n) return toast("请输入名称", "err");
      call("stickerCreatePack", n).then(function (r) {
        if (r && r.ok === false) return toast(failMsg(r, "创建失败"), "err");
        toast("已创建", "ok"); closeModal();
        return refreshPacks().then(function () {
          if (r && r.pack) selectPack(r.pack.id);
        });
      });
    });
  });

  $("skSearch").addEventListener("input", function (e) { S.filter = e.target.value.trim(); renderMemes(); });

  /* ---------- 整包自动标注：带进度 + 成功/失败明细 ---------- */
  $("skAutoPack").addEventListener("click", function () {
    if (!S.cur) return toast("先选一个表情包", "err");
    dialog("自动标注", '<div class="sk-hint" id="atHint">正在准备…</div>' +
      '<div class="sk-prog"><i id="atBar"></i></div>' +
      '<div class="sk-hint" id="atNow" style="font-family:var(--font-mono);font-size:10px"></div>' +
      '<div id="atBody" style="margin-top:12px"></div>' +
      '<div class="dfoot"><button class="btn ghost" data-close>关闭</button></div>');

    // 轮询进度（后端是同步方法，无法流式返回，故用进度字段）
    var timer = setInterval(function () {
      call("stickerAutotagProgress").then(function (p) {
        if (!p || !p.total) return;
        var pct = Math.round((p.done / p.total) * 100);
        var bar = $("atBar");
        if (bar) bar.style.width = pct + "%";
        var now = $("atNow");
        if (now) now.textContent = p.current ? ("处理中：" + p.current) : "";
        var hint = $("atHint");
        if (hint) hint.textContent = "进度 " + p.done + "/" + p.total +
          "　成功 " + (p.ok || 0) + "　失败 " + (p.fail || 0);
      });
    }, 400);

    call("stickerAutotagPack", S.cur, 30, true).then(function (r) {
      clearInterval(timer);
      var bar = $("atBar"); if (bar) bar.style.width = "100%";
      if (!r || r.ok === false) {
        var h = $("atHint"); if (h) h.textContent = failMsg(r, "标注失败");
        return toast(failMsg(r, "标注失败"), "err");
      }
      var results = r.results || [];
      var oks = results.filter(function (x) { return x.ok; });
      var bads = results.filter(function (x) { return !x.ok; });
      var body = $("atBody");
      if (body) {
        body.innerHTML =
          '<div class="sk-sum">' +
            '<div><b style="color:var(--good)">' + oks.length + '</b><div class="k">成功</div></div>' +
            '<div><b style="color:var(--err)">' + bads.length + '</b><div class="k">失败</div></div>' +
            '<div><b>' + (r.skipped || 0) + '</b><div class="k">跳过（已标/手改）</div></div>' +
            '<div><b>' + (r.total || 0) + '</b><div class="k">总数</div></div>' +
          '</div>' +
          '<div class="sk-res">' +
            results.map(function (x, i) {
              return '<div class="sk-res-row ' + (x.ok ? "ok" : "bad") +
                '" style="animation-delay:' + Math.min(i * 18, 220) + 'ms">' +
                '<span class="st">' + (x.ok ? "✓ 成功" : "✗ 失败") + '</span>' +
                '<span class="fl">' + esc(String(x.file || "").slice(0, 14)) + '</span>' +
                '<span class="rs">' + (x.ok
                  ? esc((x.tags || []).join("、") || "（已写入标签）")
                  : '<span class="why">' + esc(x.reason || "未知原因") + '</span>') + '</span>' +
                '</div>';
            }).join("") +
          '</div>';
      }
      var h2 = $("atHint");
      if (h2) h2.textContent = "完成：成功 " + oks.length + "，失败 " + bads.length +
        "，跳过 " + (r.skipped || 0);
      toast("标注完成：成功 " + oks.length + "，失败 " + bads.length, bads.length ? "err" : "ok");
      loadMemes();
    });
  });

  /* ---------- 绑定角色：默认全角色 / 绑定后专属（头像卡片选择，不用逗号） ---------- */
  function renderCharPicker(selected) {
    var sel = {};
    (selected || []).forEach(function (c) { sel[c] = 1; });
    if (!S.chars.length) {
      return '<div class="sk-hint">没有读到角色名册（可在「角色」页先创建角色）。' +
        '<br/>不选任何角色 = 所有角色通用。</div>';
    }
    return '<div class="sk-chars" id="bdChars">' + S.chars.map(function (c) {
      var on = sel[c.name] || sel[c.id];
      return '<div class="sk-char' + (on ? " on" : "") + '" data-name="' + esc(c.name) + '">' +
        avatarHtml(c) +
        '<span class="sk-cn">' + esc(c.name) + '</span>' +
        '<span class="sk-tick">✓</span></div>';
    }).join("") + '</div>';
  }

  $("skBindBtn").addEventListener("click", function () {
    if (!S.cur) return toast("先选一个表情包", "err");
    var p = S.packs.filter(function (x) { return x.id === S.cur; })[0] || {};
    var b = p.bindings || { mode: "none", characters: [] };
    var isExclusive = (b.mode === "whitelist" || b.mode === "blacklist") &&
      (b.characters || []).length > 0;

    // 先确保角色名册已加载（首次点开才拉，避免每次进页都请求）
    var ensure = S.chars.length ? Promise.resolve() : loadCharacters();
    ensure.then(function () {
      dialog("绑定角色", '' +
        '<div class="sk-field"><label>使用范围</label>' +
          '<div class="hint" id="bdDesc">' + (isExclusive
            ? "当前：<b>专属</b> —— 只有选中的角色会用它。"
            : "当前：<b>所有角色通用</b>（默认）。勾选角色后即变为该角色专属。") + '</div></div>' +
        '<div class="sk-field"><label>勾选专属角色（不勾 = 所有角色通用）</label>' +
          renderCharPicker(b.characters) + '</div>' +
        '<div class="dfoot">' +
          '<button class="btn primary" id="bdOk">保存</button>' +
          '<button class="btn ghost" id="bdClear">设为所有角色通用</button>' +
          '<button class="btn ghost" data-close>取消</button>' +
        '</div>');

      // 点卡片切换勾选
      var box = $("bdChars");
      if (box) box.addEventListener("click", function (e) {
        var it = e.target.closest(".sk-char");
        if (!it) return;
        it.classList.toggle("on");
        var n = box.querySelectorAll(".sk-char.on").length;
        var d = $("bdDesc");
        if (d) d.innerHTML = n
          ? ("已选 <b>" + n + "</b> 个角色 → 保存后为<b>专属</b>（只有它们会用它）。")
          : "未选角色 → 保存后<b>所有角色通用</b>。";
      });

      function doSave(names) {
        call("stickerBindings", S.cur, names.length ? "whitelist" : "none", names)
          .then(function (r) {
            if (r && r.ok === false) return toast(failMsg(r, "保存失败"), "err");
            toast(names.length ? ("已设为 " + names.length + " 个角色专属") : "已设为所有角色通用", "ok");
            closeDialog(); refreshPacks();
          });
      }
      $("bdOk").addEventListener("click", function () {
        var names = box
          ? Array.prototype.map.call(box.querySelectorAll(".sk-char.on"),
              function (x) { return x.getAttribute("data-name"); })
          : [];
        doSave(names);
      });
      $("bdClear").addEventListener("click", function () { doSave([]); });
    });
  });

  /* ---------- 资源广场（真实索引 + 真实安装） ---------- */
  var MARKET = [];   // 缓存本次拉到的条目，安装时按索引取
  $("skMarket").addEventListener("click", function () {
    dialog("资源广场", '<div class="sk-hint" id="mkHint">正在拉取社区表情包索引…</div>' +
      '<div id="mkBody"></div>' +
      '<div class="dfoot"><button class="btn ghost" data-close>关闭</button></div>');
    call("stickerMarketplace").then(function (r) {
      var entries = (r && r.entries) || [];
      MARKET = entries;
      var hint = $("mkHint"), body = $("mkBody");
      if (!r || r.ok === false) {
        if (body) body.innerHTML = '<div class="sk-offline"><div class="big">🛒</div>' +
          esc(failMsg(r, "索引不可达")) + '<div class="hint2">' +
          '资源广场需要联网；装本地包不受影响。</div></div>';
        if (hint) hint.textContent = "";
        return;
      }
      if (hint) hint.textContent = "共 " + entries.length +
        " 个社区表情包（来自 astrbot meme-pack-index）· 点「安装」直接入库（含分类语义）";
      if (!body) return;
      body.innerHTML = '<div class="sk-mk">' + entries.map(function (e, i) {
        var src = e.source || {};
        var n = e.meme_count || e.count || "";
        return '<article class="sk-mk-card" style="animation-delay:' + Math.min(i * 26, 260) + 'ms">' +
          '<div class="sk-mk-top">' +
            '<div class="sk-mk-ico">' + esc(String(e.name || e.id || "?").slice(0, 1)) + '</div>' +
            '<div class="sk-mk-ttl"><b>' + esc(e.name || e.id) + '</b>' +
              '<span class="sk-mk-n">' + esc(src.repo || "") + (n ? (" · " + n + " 张") : "") + '</span>' +
            '</div>' +
            (e.verified ? '<span class="badge on">已验证</span>' : '<span class="badge off">社区</span>') +
          '</div>' +
          '<p class="sk-mk-desc">' + esc(e.description || "") + '</p>' +
          '<div class="sk-mk-foot">' +
            '<div class="sk-mk-tags">' + (e.tags || []).slice(0, 3).map(function (t) {
              return '<span class="sk-tag">' + esc(t) + '</span>';
            }).join("") + '</div>' +
            '<button class="btn primary" data-mk="' + i + '">安装</button>' +
          '</div>' +
          '<div class="sk-mk-prog" data-prog="' + i + '" style="display:none">' +
            '<div class="sk-prog"><i></i></div>' +
            '<div class="sk-hint" data-progtxt="' + i + '"></div>' +
          '</div>' +
          '</article>';
      }).join("") + '</div>';

      body.addEventListener("click", function (ev) {
        var b = ev.target.closest("[data-mk]");
        if (!b) return;
        var idx = parseInt(b.getAttribute("data-mk"), 10);
        var e = MARKET[idx];
        if (!e) return;
        b.disabled = true;
        b.textContent = "安装中…";
        // 显示进度块（真下载，可能几秒~1 分钟）
        var pb = body.querySelector('[data-prog="' + idx + '"]');
        var pt = body.querySelector('[data-progtxt="' + idx + '"]');
        if (pb) pb.style.display = "block";
        if (pt) pt.textContent = "正在下载并解包（大包可能较久）…";
        var bar = pb ? pb.querySelector("i") : null;
        if (bar) bar.style.width = "30%";
        call("stickerInstall", e, 120).then(function (rr) {
          if (bar) bar.style.width = "100%";
          b.disabled = false;
          b.textContent = "重新安装";
          if (rr && rr.ok) {
            b.textContent = "已安装";
            if (pt) pt.textContent = "已装入 " + (rr.imported || 0) + " 张" +
              (rr.categories ? ("，分类 " + rr.categories + " 个") : "");
            toast("已安装「" + (e.name || e.id) + "」" + (rr.imported || 0) + " 张", "ok");
            refreshPacks();
          } else {
            if (pb) pb.style.display = "none";
            toast(failMsg(rr, "安装失败"), "err");
          }
        });
      });
    });
  });

  $("skCheck").addEventListener("click", function () {
    dialog("功能自检", '<div class="sk-hint">检测中…</div>');
    call("stickerSelfcheck").then(function (r) {
      var items = (r && r.items) || [];
      var body = dbody();
      if (!body) return;
      body.innerHTML = '<div class="sk-check">' + items.map(function (it, i) {
        return '<div class="sk-check-row" style="animation-delay:' + (i * 45) + 'ms">' +
          '<span class="sk-dot ' + (it.ok ? "ok" : "bad") + '"></span>' +
          '<span class="nm">' + esc(it.name) + '</span>' +
          '<span class="dt">' + esc(it.detail || "") + '</span></div>';
      }).join("") + '</div>' +
        '<div class="sk-hint" style="margin-top:12px">技能包目录：' + esc((r && r.skill_dir) || "未找到") + '</div>' +
        '<div class="dfoot"><button class="btn ghost" data-close>关闭</button></div>';
    });
  });

  /* ---------- 模拟选贴（调选贴效果用） ---------- */
  $("skPickBtn").addEventListener("click", function () {
    var chars = window.__SK_CHARS || [];
    modal("模拟选贴", '' +
      '<div class="sk-field"><label>角色</label>' +
        '<input type="text" id="pkChar" value="' + esc(chars[0] || "达妮娅") + '" /></div>' +
      '<div class="sk-field"><label>用户说（每行一句，逐句测）</label>' +
        '<input type="text" id="pkSay" placeholder="例如：没事，我自己能搞定，不用你管" /></div>' +
      '<div class="hint" id="pkOut" style="margin-top:10px"></div>' +
      '<button class="btn primary" style="margin-top:12px" id="pkGo">开始选贴</button>');
    $("pkSay").focus();
    $("pkGo").addEventListener("click", function () {
      var ch = $("pkChar").value.trim() || "达妮娅";
      var lines = $("pkSay").value.split("\n").map(function (s) { return s.trim(); }).filter(Boolean);
      if (!lines.length) { $("pkOut").textContent = "请先输入一句话"; return; }
      $("pkOut").innerHTML = '选贴中…（走真实模型，几秒）';
      var out = [];
      var chain = Promise.resolve();
      lines.forEach(function (say) {
        chain = chain.then(function () {
          return call("stickerPick", ch, [{ role: "user", content: say }]).then(function (r) {
            if (!r || r.ok === false) {
              out.push('<div class="sk-pickrow"><b>' + esc(say) + '</b><br/>' +
                '<span style="color:var(--bad)">' + esc(failMsg(r, "失败")) + '</span></div>');
              return;
            }
            var res = r.result || {};
            var hit = res.found ? (res.description || res.file || "") : "（决定不发）";
            out.push('<div class="sk-pickrow">' +
              '<b>' + esc(say) + '</b><br/>' +
              '<span class="k">选中：</span>' + esc(String(hit).slice(0, 60)) + '<br/>' +
              '<span class="k">理由：</span>' + esc(String(res.reason || "").slice(0, 70)) +
              (r.candidates ? ' <span class="k">（候选 ' + r.candidates + ' 张）</span>' : '') +
              '</div>');
          });
        });
      });
      chain.then(function () {
        $("pkOut").innerHTML = out.join("") || "无结果";
      });
    });
  });

  /* ---------- 拖拽上传 ---------- */
  var dz = $("skDrop");
  ["dragenter", "dragover"].forEach(function (ev) {
    dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add("drag"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove("drag"); });
  });
  dz.addEventListener("drop", function (e) {
    if (!S.cur) return toast("先选一个表情包", "err");
    var fs = Array.prototype.slice.call((e.dataTransfer && e.dataTransfer.files) || [])
      .filter(function (f) { return /^image\//.test(f.type) || /\.(gif|png|jpe?g|webp)$/i.test(f.name); });
    if (!fs.length) return toast("没有可上传的图片", "err");
    toast("上传 " + fs.length + " 张并自动标注…");
    var done = 0;
    fs.forEach(function (f) {
      var rd = new FileReader();
      rd.onload = function () {
        call("stickerUpload", S.cur, f.name, String(rd.result || "").split(",").pop()).then(function () {
          if (++done === fs.length) { toast("已上传 " + done + " 张", "ok"); loadMemes(); }
        });
      };
      rd.readAsDataURL(f);
    });
  });

  /* ---------- 初始化 ---------- */
  call("stickerVocab").then(function (r) { S.vocab = (r && r.vocab) || []; });
  loadCharacters();
  bindInfiniteScroll();      /* 表情包分批加载：滚到底自动补下一批 */
  refreshPacks();
})();
