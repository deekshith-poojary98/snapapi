/**
 * SnapAPI docs highlighter — zero-build, no CDN.
 * Browser: auto-highlights <pre><code> on load.
 * Node: `const hl = require("./highlight.js")`.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.SnapAPIHighlight = factory();
    var api = root.SnapAPIHighlight;
    if (typeof document !== "undefined") {
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () { api.highlightPage(); });
      } else {
        api.highlightPage();
      }
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var LINE_KEYWORDS = [
    "FOLLOW-REDIRECTS",
    "SUITE-TEARDOWN",
    "SUITE-SETUP",
    "QUARANTINE",
    "EXAMPLES",
    "TEARDOWN",
    "HELPER",
    "DEPENDS",
    "HEADERS",
    "REQUEST",
    "TIMEOUT",
    "GRAPHQL",
    "OPTIONS",
    "IMPORT",
    "HEADER",
    "EXPECT",
    "SETUP",
    "QUERY",
    "PARAM",
    "PATCH",
    "DELETE",
    "SUITE",
    "DESC",
    "TEST",
    "TAG",
    "BODY",
    "DATA",
    "AUTH",
    "SAVE",
    "WAIT",
    "FILE",
    "SKIP",
    "ONLY",
    "POST",
    "PUT",
    "HEAD",
    "GET",
    "SET",
    "CALL",
    "URL",
  ];

  var SNAPTEST_START_RE = new RegExp(
    "^\\s*(?://|(?:" + LINE_KEYWORDS.map(function (k) {
      return k.replace(/[-\s]/g, "\\$&");
    }).join("|") + ")\\b)"
  );

  var NAME_KWS = { SUITE: 1, TEST: 1, HELPER: 1, SETUP: 1, TEARDOWN: 1, DEPENDS: 1, "SUITE-SETUP": 1, "SUITE-TEARDOWN": 1 };
  var AUX_RE = /\b(FROM|STATUS|contains-all|contains-only|contains-any|contains-sequence|contains-keys|subset-of|starts-with|ends-with|equals-ignoring-case|contains-ignoring-case|close-to|CONTAINS|JSON|HEADER|BODY|SCHEMA|DURATION|RETRY|INLINE|OPENAPI|XPATH|TIMEOUT|BACKOFF|OPTIONS|AND|OR|BECAUSE|bearer|basic|digest|token|oauth2|true|false|form|raw|matches|length|each|between|empty|unique|absent|exists|present|sorted|zero|positive|negative|strict|not)\b/gi;
  var METHOD_RE = /\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b/g;
  var VALUE_RE = /("(?:\\.|[^"\\])*")|(\$\{[^}]+\})|(\$\.[^\s]+)|(==|!=)|(\b\d+(?:\.\d+)?(?:ms|s)?\b)/g;
  var PYTHON_KW_RE = /\b(def|return|import|from|as|class|if|elif|else|for|in|assert|True|False|None|with|pass|not|and|or|lambda|yield|try|except)\b/g;
  var BASH_CMD_RE = /\b(snapapi|python3|python|pip|git|source|export|pytest|npx|code|cp|cd|mkdir|curl|cat)\b/g;
  var BASH_FLAG_RE = /--[A-Za-z0-9][A-Za-z0-9-]*/g;

  function esc(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function span(cls, text) {
    return '<span class="' + cls + '">' + esc(text) + "</span>";
  }

  function highlightString(raw) {
    var inner = raw.slice(1, -1);
    var out = ['<span class="tok-str">"'];
    var re = /\$\{[^}]+\}/g;
    var last = 0;
    var m;
    while ((m = re.exec(inner))) {
      if (m.index > last) out.push(esc(inner.slice(last, m.index)));
      out.push('</span>' + span("tok-var", m[0]) + '<span class="tok-str">');
      last = m.index + m[0].length;
    }
    if (last < inner.length) out.push(esc(inner.slice(last)));
    out.push('"</span>');
    return out.join("");
  }

  function highlightPlain(text, lineKw) {
    var re = lineKw === "REQUEST" ? METHOD_RE : AUX_RE;
    var cls = lineKw === "REQUEST" ? "tok-kw" : "tok-aux";
    var out = [];
    var last = 0;
    re.lastIndex = 0;
    var m;
    while ((m = re.exec(text))) {
      if (m.index > last) out.push(esc(text.slice(last, m.index)));
      out.push(span(cls, m[0]));
      last = m.index + m[0].length;
    }
    if (last < text.length) out.push(esc(text.slice(last)));
    return out.join("");
  }

  function highlightValue(text, lineKw) {
    if (!text) return "";
    var out = [];
    var last = 0;
    VALUE_RE.lastIndex = 0;
    var m;
    while ((m = VALUE_RE.exec(text))) {
      if (m.index > last) out.push(highlightPlain(text.slice(last, m.index), lineKw));
      if (m[1]) out.push(highlightString(m[1]));
      else if (m[2]) out.push(span("tok-var", m[2]));
      else if (m[3]) out.push(span("tok-path", m[3]));
      else if (m[4]) out.push(span("tok-op", m[4]));
      else if (m[5]) out.push(span("tok-num", m[5]));
      last = m.index + m[0].length;
    }
    if (last < text.length) out.push(highlightPlain(text.slice(last), lineKw));
    return out.join("");
  }

  function matchLineKeyword(rest) {
    for (var i = 0; i < LINE_KEYWORDS.length; i++) {
      var kw = LINE_KEYWORDS[i];
      if (rest.length < kw.length) continue;
      if (rest.slice(0, kw.length) !== kw) continue;
      var next = rest.charAt(kw.length);
      if (next === ":") return { kw: kw, kind: "plain" };
      if (kw === "HEADER" && /\s/.test(next)) {
        var hm = rest.match(/^(HEADER)(\s+)(\S+)(\s*)(:)/);
        if (hm) return { kw: "HEADER", kind: "header", match: hm };
      }
    }
    return null;
  }

  function highlightLine(line) {
    var indent = (line.match(/^\s*/) || [""])[0];
    var rest = line.slice(indent.length);
    var out = indent ? esc(indent) : "";
    if (!rest) return out;
    if (rest.slice(0, 2) === "//") return out + span("tok-cmt", rest);

    var hit = matchLineKeyword(rest);
    if (hit && hit.kind === "header") {
      var hm = hit.match;
      var after = rest.slice(hm[0].length);
      return out + span("tok-kw", hm[1]) + esc(hm[2]) + span("tok-aux", hm[3]) + esc(hm[4]) + span("tok-p", ":") + highlightValue(after, "HEADER");
    }
    if (hit) {
      var kw = hit.kw;
      var value = rest.slice(kw.length + 1);
      out += span("tok-kw", kw) + span("tok-p", ":");
      if (NAME_KWS[kw]) return out + span("tok-name", value);
      return out + highlightValue(value, kw);
    }
    return out + highlightValue(rest);
  }

  function highlightSnaptest(source) {
    return String(source).split("\n").map(highlightLine).join("\n");
  }

  function highlightPython(source) {
    return String(source).split("\n").map(function (line) {
      var hash = line.indexOf("#");
      var code = hash === -1 ? line : line.slice(0, hash);
      var cmt = hash === -1 ? "" : line.slice(hash);
      var out = [];
      var re = /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')/g;
      var last = 0;
      var m;
      while ((m = re.exec(code))) {
        if (m.index > last) {
          out.push(esc(code.slice(last, m.index)).replace(PYTHON_KW_RE, '<span class="tok-kw">$1</span>'));
        }
        out.push(span("tok-str", m[0]));
        last = m.index + m[0].length;
      }
      if (last < code.length) {
        out.push(esc(code.slice(last)).replace(PYTHON_KW_RE, '<span class="tok-kw">$1</span>'));
      }
      if (cmt) out.push(span("tok-cmt", cmt));
      return out.join("");
    }).join("\n");
  }

  function highlightBash(source) {
    return String(source).split("\n").map(function (line) {
      var inStr = "";
      var codeEnd = line.length;
      for (var i = 0; i < line.length; i++) {
        var ch = line.charAt(i);
        if (inStr) {
          if (ch === inStr && line.charAt(i - 1) !== "\\") inStr = "";
          continue;
        }
        if (ch === '"' || ch === "'") { inStr = ch; continue; }
        if (ch === "#") { codeEnd = i; break; }
      }
      var code = line.slice(0, codeEnd);
      var cmt = line.slice(codeEnd);
      var out = [];
      var re = /("(?:\\.|[^"\\])*"|'(?:\\.|[^']*)')/g;
      var last = 0;
      var m;
      while ((m = re.exec(code))) {
        if (m.index > last) out.push(paintBashPlain(code.slice(last, m.index)));
        out.push(span("tok-str", m[0]));
        last = m.index + m[0].length;
      }
      if (last < code.length) out.push(paintBashPlain(code.slice(last)));
      if (cmt) out.push(span("tok-cmt", cmt));
      return out.join("");
    }).join("\n");
  }

  function paintBashPlain(text) {
    return esc(text)
      .replace(BASH_FLAG_RE, '<span class="tok-aux">$&</span>')
      .replace(BASH_CMD_RE, '<span class="tok-kw">$1</span>');
  }

  function highlightJson(source) {
    return highlightValue(String(source));
  }

  function looksLikeSnaptest(text) {
    var lines = String(text).split("\n");
    for (var i = 0; i < lines.length; i++) {
      if (!lines[i].trim()) continue;
      return SNAPTEST_START_RE.test(lines[i]);
    }
    return false;
  }

  function looksLikePython(text) {
    return /^\s*(?:def |import |from |@pytest|assert )/m.test(text);
  }

  function looksLikeJson(text) {
    return /^\s*[{\[]/.test(text);
  }

  function looksLikeBash(text) {
    return /^\s*(?:git |pip |python3? |source |export |npx |code |cp |cd |snapapi |pytest |TOKEN=)/m.test(text);
  }

  function detectLanguage(elOrText, className) {
    var cls = className || "";
    var text = elOrText;
    if (elOrText && elOrText.textContent != null) {
      cls = elOrText.className || cls;
      text = elOrText.textContent;
    }
    if (/language-snaptest|\bsnaptest\b/.test(cls)) return "snaptest";
    if (/language-python|\bpython\b/.test(cls)) return "python";
    if (/language-bash|\bbash\b|\bshell\b/.test(cls)) return "bash";
    if (/language-json|\bjson\b/.test(cls)) return "json";
    text = String(text || "");
    if (looksLikeSnaptest(text)) return "snaptest";
    if (looksLikePython(text)) return "python";
    if (looksLikeJson(text)) return "json";
    if (looksLikeBash(text)) return "bash";
    return "";
  }

  function highlightByLang(text, lang) {
    if (lang === "snaptest") return highlightSnaptest(text);
    if (lang === "python") return highlightPython(text);
    if (lang === "bash") return highlightBash(text);
    if (lang === "json") return highlightJson(text);
    return esc(text);
  }

  function highlightPage(root) {
    if (typeof document === "undefined") return;
    var scope = root || document;
    var blocks = scope.querySelectorAll("pre > code");
    for (var i = 0; i < blocks.length; i++) {
      var el = blocks[i];
      if (el.getAttribute("data-highlighted")) continue;
      var lang = detectLanguage(el);
      if (!lang) continue;
      el.innerHTML = highlightByLang(el.textContent, lang);
      if (el.className.indexOf("language-" + lang) === -1) {
        el.className = (el.className + " language-" + lang).trim();
      }
      el.setAttribute("data-highlighted", "1");
      if (el.parentNode && el.parentNode.classList) el.parentNode.classList.add("hl-block");
    }
  }

  return {
    highlightSnaptest: highlightSnaptest,
    highlightPython: highlightPython,
    highlightBash: highlightBash,
    highlightJson: highlightJson,
    highlightByLang: highlightByLang,
    detectLanguage: detectLanguage,
    highlightPage: highlightPage,
    looksLikeSnaptest: looksLikeSnaptest,
  };
});
