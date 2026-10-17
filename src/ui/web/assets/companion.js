/* 角色名占位：把界面里写死的「达妮娅」在运行时替换成当前角色名。
 * 用法：
 *   - 任意页面引入本文件即可自动生效（覆盖静态文案 + 后续动态插入的文案）。
 *   - 角色加载/切换时调用 window.setCompanionName('名字') 即可全局联动。
 * 安全：脚本/样式节点内的「达妮娅」不替换（避免误改代码）；
 *       聊天消息容器（#chatMessages / .chat-messages / #chatInput）整体排除，
 *       防止切换角色时把用户自己打出的角色名也改掉。
 */
(function () {
  "use strict";
  var DEFAULT = "达妮娅";
  window.COMPANION_NAME = window.COMPANION_NAME || DEFAULT;
  var last = window.COMPANION_NAME;

  function inSkip(node) {
    var el = node && node.nodeType === 1 ? node : (node ? node.parentNode : null);
    while (el) {
      if (el.nodeType === 1) {
        if (el.id === "chatMessages" || el.id === "chatInput") return true;
        var cn = typeof el.className === "string" ? el.className : (el.className ? el.className.baseVal : "");
        var sp = " " + cn + " ";
        if (sp.indexOf(" chat-messages ") > -1 || sp.indexOf(" chat-input ") > -1) return true;
      }
      el = el.parentNode;
    }
    return false;
  }

  function isCode(node) {
    var p = node.parentNode;
    return !!(p && (p.tagName === "SCRIPT" || p.tagName === "STYLE"));
  }

  function resolveInto(root) {
    if (!root) return;
    var name = window.COMPANION_NAME || DEFAULT;
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var n;
    while ((n = w.nextNode())) {
      if (!n.nodeValue || n.nodeValue.indexOf(DEFAULT) === -1) continue;
      if (inSkip(n) || isCode(n)) continue;
      n.nodeValue = n.nodeValue.split(DEFAULT).join(name);
    }
  }

  function replaceToken(from, to) {
    if (!from || from === to) return;
    var body = document.body || document.documentElement;
    if (!body) return;
    var w = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, null);
    var n;
    while ((n = w.nextNode())) {
      if (!n.nodeValue || n.nodeValue.indexOf(from) === -1) continue;
      if (inSkip(n) || isCode(n)) continue;
      n.nodeValue = n.nodeValue.split(from).join(to);
    }
  }

  window.resolveCompanionName = function (root) { resolveInto(root); };

  window.setCompanionName = function (n) {
    n = (n && n.trim && n.trim()) || DEFAULT;
    if (n === window.COMPANION_NAME) return;
    // 切换角色：先把上一次已替换进 DOM 的名字换回新名字
    replaceToken(last, n);
    window.COMPANION_NAME = n;
    last = n;
    // 再处理可能残留的默认占位
    resolveInto(document.body || document.documentElement);
  };

  function start() {
    resolveInto(document.body || document.documentElement);
    if (window.__cnObs) return;
    window.__cnObs = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var added = muts[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var node = added[j];
          if (node.nodeType === 3) {
            if (node.nodeValue && node.nodeValue.indexOf(DEFAULT) !== -1 && !inSkip(node) && !isCode(node)) {
              node.nodeValue = node.nodeValue.split(DEFAULT).join(window.COMPANION_NAME || DEFAULT);
            }
          } else if (node.nodeType === 1) {
            if (node.tagName !== "SCRIPT" && node.tagName !== "STYLE") resolveInto(node);
          }
        }
      }
    });
    window.__cnObs.observe(document.body || document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
