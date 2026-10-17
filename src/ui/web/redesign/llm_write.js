/* ============================================================================
 * llm_write.js —— 聊天模型「写路径」唯一真值（P1 收敛双份实现）
 * ----------------------------------------------------------------------------
 * 背景（为什么要这个东西）：
 *   在 2026-10-08 之前，改一个厂商配置要走 `saveTextChatProviders({providers: 全量数组})`
 *   —— **整表覆盖**。前端每次只想改一条，却必须把整个 llmProviderCache 重新序列化，
 *   于是出现了三件坏事：
 *     ① 丢更新竞争：两个入口（弹窗 picker / 手动表单 / 能力实测回写）同时改 →
 *        后写的把先写的覆盖掉（因为都基于各自的旧快照）。
 *     ② `__KEEP__` 哨兵：因为后端拿不到"这次没改 Key"这个信息，前端被迫往数组里塞
 *        '__KEEP__' 占位再在发请求前删掉，绕且易漏。
 *     ③ 双份实现：设置页 4 处 + 其它页各自拼 payload，"改一处忘一处"。
 *
 *   现在后端提供了**增量接口**：`upsert_text_chat_provider({provider:{...}})` /
 *   `delete_text_chat_provider(id)`，只动一条、只覆盖显式给出的字段、
 *   Key 不传即保留。本模块把这些接口包成前端唯一的写入口。
 *
 * 契约（调用方只认这四个）：
 *   LlmWrite.ready()                                   → 后端是否可用
 *   LlmWrite.upsert(patch, {mode, manual_provider})    → Promise<result>
 *   LlmWrite.remove(id)                                → Promise<result>
 *   LlmWrite.setManual(id)                             → Promise<result>  设为当前
 *   LlmWrite.setAuto()                                 → Promise<result>  自动选
 *   LlmWrite.upsertMany(patches)                       → Promise<{ok, results}>
 *
 * ★ 兼容策略（这是"不改变现有功能"的关键）：
 *   若运行时**没有**增量接口（老后端 / 少一个桥），自动降级到全量接口，
 *   并且降级路径自己负责把 patch 合并进全量数组 + 处理 Key 语义。
 *   调用方代码完全不用变 —— 有增量就用增量，没有就全量。
 * ==========================================================================*/
(function (root) {
  'use strict';

  var VERSION = '2026-10-08';

  /* 摘掉 patch 里的 undefined，避免把"没传"误变成"传了 null" */
  function _clean(o) {
    var out = {};
    if (!o) return out;
    for (var k in o) {
      if (Object.prototype.hasOwnProperty.call(o, k) && o[k] !== undefined) out[k] = o[k];
    }
    return out;
  }

  function make(B) {
    if (!B) return null;

    var hasIncremental = (typeof B.upsertTextChatProvider === 'function') &&
                         (typeof B.deleteTextChatProvider === 'function');

    /* -------- 全量降级：把 patch 合并进现表，再整表写回 --------
       只在没有增量接口时走到。__KEEP__ 哨兵就在这里被消化掉。 */
    function _fullWrite(mutator) {
      return B.getLlm().then(function (d) {
        if (!d || !d.success) throw new Error('读取模型配置失败');
        var tc = d.text_chat || {};
        var list = (tc.providers || []).map(function (p) {
          // 全量接口要求"未改的 Key 用 __KEEP__ 占位"
          return {
            id: p.id, name: p.name, provider: p.provider,
            base_url: p.base_url || '', model: p.model,
            timeout_s: p.timeout_s || 30,
            api_key: p.has_key ? '__KEEP__' : '',
            caps: p.caps ? {
              inputs: p.caps.inputs || [], outputs: p.caps.outputs || [],
              features: p.caps.features || []
            } : undefined,
            vision: p.vision
          };
        });
        var payload = mutator(list, tc);
        list.forEach(function (p) { if (p.api_key === '__KEEP__') delete p.api_key; });
        var req = { providers: list };
        if (payload && payload.mode !== undefined) req.mode = payload.mode;
        if (payload && payload.manual_provider !== undefined) req.manual_provider = payload.manual_provider;
        return B.saveTextChatProviders(req);
      });
    }

    /* -------- upsert：新增或就地改一条 -------- */
    function upsert(patch, opts) {
      patch = patch || {};
      opts = opts || {};
      if (!patch.id) return Promise.reject(new Error('upsert 缺少 id'));

      // 上游可能只想改 Key：显式 '' ⇒ 清空；不传 ⇒ 保留（增量接口原生支持）
      var body = { provider: _clean(patch) };
      if (hasIncremental) {
        if (opts.mode !== undefined) body.mode = opts.mode;
        if (opts.manual_provider !== undefined) body.manual_provider = opts.manual_provider;
        return Promise.resolve(B.upsertTextChatProvider(body));
      }
      // 降级：合并进全量
      return _fullWrite(function (list) {
        var found = false;
        for (var i = 0; i < list.length; i++) {
          if (list[i].id === patch.id) {
            found = true;
            var merged = _clean(patch);
            if (Object.prototype.hasOwnProperty.call(merged, 'api_key')) {
              merged.api_key = merged.api_key;           // 显式给了就照给
            }
            for (var k in merged) { list[i][k] = merged[k]; }
            break;
          }
        }
        if (!found) list.push(_clean(patch));
        return { mode: opts.mode, manual_provider: opts.manual_provider };
      });
    }

    /* -------- remove：按 id 删一条（幂等） --------
       降级实现单独写：读现表 → 滤掉目标 → 整表写回。删除时的
       manual_provider 回落交给后端（全量接口已实现该逻辑）。 */
    function removeFull(id) {
      return B.getLlm().then(function (d) {
        if (!d || !d.success) throw new Error('读取模型配置失败');
        var tc = d.text_chat || {};
        var kept = (tc.providers || []).filter(function (p) { return p.id !== id; });
        var out = kept.map(function (p) {
          return {
            id: p.id, name: p.name, provider: p.provider,
            base_url: p.base_url || '', model: p.model,
            timeout_s: p.timeout_s || 30,
            api_key: p.has_key ? '__KEEP__' : '',
            caps: p.caps ? {
              inputs: p.caps.inputs || [], outputs: p.caps.outputs || [],
              features: p.caps.features || []
            } : undefined
          };
        });
        out.forEach(function (p) { if (p.api_key === '__KEEP__') delete p.api_key; });
        var req = { providers: out };
        // 删的正好是当前 manual ⇒ 让后端自己回落（全量接口已实现该逻辑）
        return B.saveTextChatProviders(req);
      });
    }

    function remove(id) {
      if (!id) return Promise.reject(new Error('remove 缺少 id'));
      if (hasIncremental) return Promise.resolve(B.deleteTextChatProvider(id));
      return removeFull(id);
    }

    /* -------- 设为当前 / 自动 -------- */
    function setManual(id) {
      if (typeof B.setTextChatMode !== 'function') {
        return Promise.reject(new Error('后端缺少 setTextChatMode'));
      }
      return Promise.resolve(B.setTextChatMode('manual', id || ''));
    }
    function setAuto() {
      if (typeof B.setTextChatMode !== 'function') {
        return Promise.reject(new Error('后端缺少 setTextChatMode'));
      }
      return Promise.resolve(B.setTextChatMode('auto', ''));
    }

    /* -------- 批量：顺序执行，逐条独立成败（★ 一条失败不停下后面的），
       最后交回汇总结果由调用方决定提示。 */
    function upsertMany(patches) {
      var results = [];
      var chain = Promise.resolve();
      (patches || []).forEach(function (p) {
        chain = chain.then(function () {
          var prog = p.provider || p;
          return upsert(prog, p.opts || {}).then(function (r) {
            results.push({ id: prog.id, ok: !!(r && r.success), raw: r });
          }).catch(function (e) {
            results.push({ id: prog.id, ok: false, error: String((e && e.message) || e) });
          });
        });
      });
      return chain.then(function () {
        return { ok: results.every(function (r) { return r.ok; }), results: results };
      });
    }

    return {
      version: VERSION,
      incremental: hasIncremental,
      ready: function () { return !!(B && typeof B.getLlm === 'function'); },
      upsert: upsert,
      remove: remove,
      setManual: setManual,
      setAuto: setAuto,
      upsertMany: upsertMany
    };
  }

  root.LlmWrite = { version: VERSION, make: make };
})(typeof window !== 'undefined' ? window : this);
