"use strict";
/**
 * 极简自包含 Markdown 渲染器 + 轻量代码高亮。
 * 无外部依赖,离线可用。够用于渲染对话内容:
 * 标题、段落、粗体/斜体、行内代码、围栏代码块(带高亮)、
 * 有序/无序列表、引用、水平线、链接。
 * 全程转义 HTML,避免 XSS。
 */
(function (global) {
  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---- 轻量代码高亮:按语言粗粒度着色 ----
  var KEYWORDS = (
    "function return if else for while do switch case break continue " +
    "var let const class new this typeof instanceof import from export default " +
    "def elif except finally lambda pass raise try with yield async await " +
    "public private protected static void int float double bool boolean string " +
    "package func type struct interface map range chan go defer select " +
    "and or not in is None True False null true false nil self print " +
    "SELECT FROM WHERE JOIN GROUP ORDER BY LIMIT INSERT UPDATE DELETE CREATE TABLE"
  ).split(" ");
  var KW_RE = new RegExp("\\b(" + KEYWORDS.join("|") + ")\\b", "g");

  function highlight(code) {
    // code 已转义。用占位法避免相互破坏。
    var tokens = [];
    function stash(html) {
      tokens.push(html);
      return "\u0000" + (tokens.length - 1) + "\u0000";
    }
    var out = code;
    // 字符串(单/双引号、反引号)
    out = out.replace(/(&#39;[^&]*?&#39;|&quot;[^&]*?&quot;|`[^`]*?`)/g, function (m) {
      return stash('<span class="tok-str">' + m + "</span>");
    });
    // 注释(// 与 # 到行尾)
    out = out.replace(/((?:\/\/|#).*)$/gm, function (m) {
      return stash('<span class="tok-com">' + m + "</span>");
    });
    // 数字
    out = out.replace(/\b(\d+(?:\.\d+)?)\b/g, function (m) {
      return stash('<span class="tok-num">' + m + "</span>");
    });
    // 关键字
    out = out.replace(KW_RE, function (m) {
      return stash('<span class="tok-kw">' + m + "</span>");
    });
    // 还原占位
    out = out.replace(/\u0000(\d+)\u0000/g, function (_, i) {
      return tokens[+i];
    });
    return out;
  }

  function renderInline(text) {
    // 行内代码先抽出
    var codes = [];
    text = text.replace(/`([^`]+)`/g, function (_, c) {
      codes.push("<code>" + c + "</code>");
      return "\u0001" + (codes.length - 1) + "\u0001";
    });
    // 链接 [text](url)
    text = text.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    // 粗体
    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    // 斜体
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    // 还原行内代码
    text = text.replace(/\u0001(\d+)\u0001/g, function (_, i) { return codes[+i]; });
    return text;
  }

  function render(src) {
    src = String(src == null ? "" : src);
    // 1. 抽出围栏代码块
    var blocks = [];
    src = src.replace(/```(\w*)\n?([\s\S]*?)```/g, function (_, lang, code) {
      var html =
        '<pre class="code"><div class="code-lang">' + escapeHtml(lang || "code") +
        '</div><code>' + highlight(escapeHtml(code.replace(/\n$/, ""))) + "</code></pre>";
      blocks.push(html);
      return "\u0002" + (blocks.length - 1) + "\u0002";
    });

    // 2. 转义其余部分
    var lines = escapeHtml(src).split("\n");

    // 3. 逐行块级解析
    var htmlParts = [];
    var listBuf = null; // {ordered, items:[]}
    function flushList() {
      if (!listBuf) return;
      var tag = listBuf.ordered ? "ol" : "ul";
      htmlParts.push("<" + tag + ">" + listBuf.items.map(function (t) {
        return "<li>" + renderInline(t) + "</li>";
      }).join("") + "</" + tag + ">");
      listBuf = null;
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      // 代码块占位符独占一行
      var ph = line.match(/^\u0002(\d+)\u0002$/);
      if (ph) { flushList(); htmlParts.push(blocks[+ph[1]]); continue; }

      if (/^\s*$/.test(line)) { flushList(); continue; }

      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { flushList(); htmlParts.push("<h" + h[1].length + ">" + renderInline(h[2]) + "</h" + h[1].length + ">"); continue; }

      if (/^\s*([-*])\s+/.test(line)) {
        if (!listBuf || listBuf.ordered) { flushList(); listBuf = { ordered: false, items: [] }; }
        listBuf.items.push(line.replace(/^\s*[-*]\s+/, ""));
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        if (!listBuf || !listBuf.ordered) { flushList(); listBuf = { ordered: true, items: [] }; }
        listBuf.items.push(line.replace(/^\s*\d+\.\s+/, ""));
        continue;
      }

      if (/^\s*&gt;\s?/.test(line)) {
        flushList();
        htmlParts.push("<blockquote>" + renderInline(line.replace(/^\s*&gt;\s?/, "")) + "</blockquote>");
        continue;
      }
      if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { flushList(); htmlParts.push("<hr/>"); continue; }

      flushList();
      htmlParts.push("<p>" + renderInline(line) + "</p>");
    }
    flushList();
    return htmlParts.join("\n");
  }

  global.renderMarkdown = render;
  global.escapeHtml = escapeHtml;
})(window);
