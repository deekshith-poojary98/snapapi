/**
 * SnapAPI playground interpreter — a useful subset of the .sapi DSL.
 * Runs in the browser against an in-memory mock (default) or live fetch().
 * Also loadable from Node: `const pg = require("./playground.js")`.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.SnapAPIPlayground = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var HTTP_METHODS = { GET: 1, POST: 1, PUT: 1, PATCH: 1, DELETE: 1, HEAD: 1, OPTIONS: 1 };
  var KNOWN = {
    SUITE: 1, DESC: 1, URL: 1, OPTIONS: 1, TIMEOUT: 1,
    "FOLLOW-REDIRECTS": 1, "SUITE-SETUP": 1, "SUITE-TEARDOWN": 1, TEST: 1, TAG: 1,
    SETUP: 1, TEARDOWN: 1, DEPENDS: 1, HELPER: 1, REQUEST: 1, DATA: 1, BODY: 1, HEADERS: 1, HEADER: 1,
    QUERY: 1, PARAM: 1, AUTH: 1, EXPECT: 1, SAVE: 1, WAIT: 1, SET: 1, EXAMPLES: 1,
    GET: 1, POST: 1, PUT: 1, PATCH: 1, DELETE: 1, HEAD: 1,
  };
  var UNSUPPORTED = {
    FILE: "FILE uploads are not available in the playground. Use the SnapAPI CLI.",
    GRAPHQL: "GRAPHQL is not available in the playground. POST a JSON body instead, or use the CLI.",
    IMPORT: "IMPORT is not available in the playground. Paste the tests into this editor, or use the CLI.",
    SKIP: "SKIP is not available in the playground. Comment the test out, or use the CLI.",
    ONLY: "ONLY is not available in the playground. Delete the other tests, or use the CLI.",
    CALL: "CALL is not available in the playground. Use the SnapAPI CLI with extensions/.",
    QUARANTINE: "QUARANTINE is not available in the playground. Use the SnapAPI CLI.",
  };
  var LINE_KEYWORD_RE = /^([A-Z][A-Z0-9_-]*):(.*)$/;
  var HEADER_LINE_RE = /^HEADER\s+(\S+)\s*:\s*(.*)$/;
  var SPACED_KEYWORD_RE = /^([A-Z][A-Z0-9_-]*(?:\s+[A-Z][A-Z0-9_-]*)+)\s*:(.*)$/;
  var VAR_RE = /\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g;
  var HELPER_RE = /\$\{([A-Za-z_][A-Za-z0-9_.]*)(?:\(([^)]*)\))?\}/g;
  var WAIT_CAP_S = 8;
  var REQUEST_COL = 32;

  function ParseError(message, lineno) {
    this.name = "ParseError";
    this.message = lineno ? "line " + lineno + ": " + message : message;
    this.lineno = lineno || null;
  }
  ParseError.prototype = Object.create(Error.prototype);

  function RunError(message) {
    this.name = "RunError";
    this.message = message;
  }
  RunError.prototype = Object.create(Error.prototype);

  function compactKeyword(raw) {
    return String(raw).trim().split(/[\s_]+/).filter(Boolean).join("-");
  }

  function levenshtein(a, b) {
    var matrix = [];
    var i;
    var j;
    for (i = 0; i <= a.length; i++) matrix[i] = [i];
    for (j = 1; j <= b.length; j++) matrix[0][j] = j;
    for (i = 1; i <= a.length; i++) {
      for (j = 1; j <= b.length; j++) {
        var cost = a.charAt(i - 1) === b.charAt(j - 1) ? 0 : 1;
        matrix[i][j] = Math.min(matrix[i - 1][j] + 1, matrix[i][j - 1] + 1, matrix[i - 1][j - 1] + cost);
      }
    }
    return matrix[a.length][b.length];
  }

  function suggestKeyword(raw) {
    var compacted = compactKeyword(raw);
    if (KNOWN[compacted]) return compacted;
    if (raw.indexOf(" ") < 0 && raw.indexOf("-") < 0) return null;
    var names = Object.keys(KNOWN);
    var best = null;
    var bestScore = 0;
    for (var n = 0; n < names.length; n++) {
      var keyword = names[n];
      var maxLen = Math.max(compacted.length, keyword.length);
      var score = maxLen ? 1 - levenshtein(compacted, keyword) / maxLen : 1;
      if (score > bestScore) {
        bestScore = score;
        best = keyword;
      }
    }
    return bestScore >= 0.7 ? best : null;
  }

  function unknownKeywordMessage(raw) {
    var hint = suggestKeyword(raw);
    if (/\s/.test(raw)) {
      if (hint) {
        if (hint === compactKeyword(raw)) {
          return "Unknown keyword '" + raw + "'. Keywords cannot contain spaces; use " + hint;
        }
        return "Unknown keyword '" + raw + "'. Keywords cannot contain spaces; did you mean " + hint + "?";
      }
      return "Unknown keyword '" + raw + "'. Keywords cannot contain spaces";
    }
    if (hint) return "Unknown keyword '" + raw + "'. Did you mean " + hint + "?";
    return "Unknown keyword '" + raw + "'";
  }

  function stripQuotes(value) {
    if (value && value.length >= 2 && value[0] === value[value.length - 1] && (value[0] === '"' || value[0] === "'")) {
      return value.slice(1, -1);
    }
    return value;
  }

  function parseExpectValue(raw) {
    try {
      return JSON.parse(raw);
    } catch (err) {
      return stripQuotes(raw);
    }
  }

  function isUnquotedIndex(text, index) {
    var quote = null;
    var escape = false;
    for (var i = 0; i < text.length && i < index; i++) {
      var ch = text.charAt(i);
      if (quote) {
        if (escape) escape = false;
        else if (ch === "\\") escape = true;
        else if (ch === quote) quote = null;
        continue;
      }
      if (ch === '"' || ch === "'") quote = ch;
    }
    return quote == null;
  }

  function lastUnquotedMatch(text, re) {
    var last = null;
    var flags = re.flags.indexOf("g") >= 0 ? re.flags : re.flags + "g";
    var globalRe = new RegExp(re.source, flags);
    var match;
    while ((match = globalRe.exec(text))) {
      var ws = match[0].length - match[0].replace(/^\s+/, "").length;
      if (isUnquotedIndex(text, match.index + ws)) last = match;
      if (match.index === globalRe.lastIndex) globalRe.lastIndex += 1;
    }
    return last;
  }

  function takeBecause(rest) {
    var match = lastUnquotedMatch(rest, /\sBECAUSE\s+("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\S+)\s*$/i);
    if (!match) return { rest: rest, because: null };
    return { rest: rest.slice(0, match.index).trim(), because: stripQuotes(match[1].trim()) };
  }

  function takeRetry(rest) {
    var match = rest.match(/\sRETRY\s+(\d+)(?:\s+ON\s+(\S+))?(?:\s+BACKOFF\s+(\S+))?\s*$/i);
    if (!match) return { rest: rest, match: null };
    var ws = match[0].length - match[0].replace(/^\s+/, "").length;
    if (!isUnquotedIndex(rest, match.index + ws)) return { rest: rest, match: null };
    return { rest: rest.slice(0, match.index).trim(), match: match };
  }

  function stripExpectSuffixes(rest) {
    var retryMatch = null;
    var because = null;
    while (rest) {
      var retryTaken = takeRetry(rest);
      if (retryTaken.match) {
        rest = retryTaken.rest;
        retryMatch = retryTaken.match;
        continue;
      }
      var becauseTaken = takeBecause(rest);
      if (becauseTaken.because != null) {
        rest = becauseTaken.rest;
        because = becauseTaken.because;
        continue;
      }
      break;
    }
    return { rest: rest, retryMatch: retryMatch, because: because };
  }

  function parseDurationSeconds(raw) {
    var text = String(raw).trim().toLowerCase();
    if (text.slice(-2) === "ms") return parseFloat(text.slice(0, -2)) / 1000;
    if (text.slice(-1) === "s") return parseFloat(text.slice(0, -1));
    return parseFloat(text);
  }

  function jsonComplete(text) {
    var inString = false;
    var escape = false;
    var depth = 0;
    var started = false;
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (inString) {
        if (escape) escape = false;
        else if (ch === "\\") escape = true;
        else if (ch === '"') inString = false;
        continue;
      }
      if (/\s/.test(ch)) continue;
      if (ch === '"') {
        inString = true;
        started = true;
        continue;
      }
      if (ch === "{" || ch === "[") {
        depth += 1;
        started = true;
      } else if (ch === "}" || ch === "]") {
        depth -= 1;
        if (depth < 0) return false;
        started = true;
      } else {
        started = true;
      }
    }
    return started && depth === 0 && !inString;
  }

  function readJson(rest, lines, index, lineno) {
    var buf = rest;
    var last = index;
    while (!jsonComplete(buf)) {
      last += 1;
      if (last >= lines.length) throw new ParseError("Unterminated JSON value", lineno);
      var nxt = lines[last];
      var stripped = nxt.trim();
      if (!stripped || stripped.indexOf("//") === 0) continue;
      buf = buf + "\n" + nxt;
    }
    try {
      return { value: JSON.parse(buf), last: last };
    } catch (err) {
      throw new ParseError("Invalid JSON: " + (err.message || err), lineno);
    }
  }

  function parseBool(rest, keyword, lineno) {
    if (!rest) throw new ParseError(keyword + " requires a boolean", lineno);
    var lowered = rest.trim().toLowerCase();
    if (lowered === "true" || lowered === "yes" || lowered === "on" || lowered === "1") return true;
    if (lowered === "false" || lowered === "no" || lowered === "off" || lowered === "0") return false;
    throw new ParseError(keyword + " must be a boolean", lineno);
  }

  function parseQueryString(rest, lineno) {
    if (!rest) throw new ParseError("QUERY requires parameters", lineno);
    var params = {};
    var pairs = rest.split("&");
    var found = false;
    for (var i = 0; i < pairs.length; i++) {
      if (!pairs[i]) continue;
      var parts = pairs[i].split("=");
      if (parts.length < 2 && rest.indexOf("=") === -1) {
        throw new ParseError("QUERY must look like: QUERY: page=2&limit=10", lineno);
      }
      params[decodeURIComponent(parts[0])] = decodeURIComponent(parts.slice(1).join("="));
      found = true;
    }
    if (!found) throw new ParseError("QUERY must look like: QUERY: page=2&limit=10", lineno);
    return params;
  }

  function newStep(method, endpoint, lineno) {
    return {
      action: method,
      endpoint: endpoint,
      data: null,
      rawBody: null,
      contentType: null,
      bodyType: "json",
      headers: {},
      query: {},
      checks: [],
      saves: [],
      wait: null,
      sets: [],
      lineno: lineno,
    };
  }

  function parseAuth(rest, lineno) {
    if (!rest) throw new ParseError("AUTH requires a scheme and value", lineno);
    var parts = rest.split(/\s+/);
    var scheme = parts[0].toLowerCase();
    if (scheme === "oauth2") {
      throw new ParseError("AUTH oauth2 is not available in the playground. Use AUTH: bearer <token>, or the CLI.", lineno);
    }
    if (scheme === "digest") {
      throw new ParseError("AUTH digest is not available in the playground. Use AUTH: bearer <token>, or the CLI.", lineno);
    }
    if (parts.length < 2) throw new ParseError("AUTH must look like: AUTH: bearer <token>", lineno);
    var value = rest.slice(parts[0].length).trim();
    if (scheme === "basic" && value.indexOf(":") !== -1) {
      var token = typeof btoa === "function" ? btoa(value) : Buffer.from(value, "utf8").toString("base64");
      return { Authorization: "Basic " + token };
    }
    var headerScheme = { bearer: "Bearer", token: "Bearer", basic: "Basic" }[scheme] || parts[0];
    return { Authorization: headerScheme + " " + value };
  }

  function parseExpect(rest, lineno) {
    if (!rest) throw new ParseError("EXPECT requires a check", lineno);
    var stripped = stripExpectSuffixes(rest);
    rest = stripped.rest;
    var retry = null;
    var retryBackoff = null;
    if (stripped.retryMatch) {
      retry = parseInt(stripped.retryMatch[1], 10);
      if (stripped.retryMatch[3]) retryBackoff = parseDurationSeconds(stripped.retryMatch[3]);
    }
    var node = parseExpectExpr(rest, lineno);
    node.retry = retry;
    node.retryBackoff = retryBackoff;
    if (stripped.because) node.because = stripped.because;
    return node;
  }

  function parseExpectExpr(text, lineno) {
    var parser = { text: text, i: 0, lineno: lineno };
    var node = parseExpectOr(parser);
    skipExpectWs(parser);
    if (parser.i < parser.text.length) {
      throw new ParseError("Unexpected extra EXPECT text '" + parser.text.slice(parser.i).trim() + "'", lineno);
    }
    return node;
  }

  function skipExpectWs(parser) {
    while (parser.i < parser.text.length && /\s/.test(parser.text.charAt(parser.i))) parser.i += 1;
  }

  function consumeExpectOp(parser, wanted) {
    var m = parser.text.slice(parser.i).match(/^\s*(AND|OR)\b/i);
    if (!m || m[1].toUpperCase() !== wanted) return false;
    parser.i += m[0].length;
    return true;
  }

  function parseExpectOr(parser) {
    var terms = [parseExpectAnd(parser)];
    while (consumeExpectOp(parser, "OR")) terms.push(parseExpectAnd(parser));
    return terms.length === 1 ? terms[0] : { type: "OR", terms: terms };
  }

  function parseExpectAnd(parser) {
    var terms = [parseExpectPrimary(parser)];
    while (consumeExpectOp(parser, "AND")) terms.push(parseExpectPrimary(parser));
    return terms.length === 1 ? terms[0] : { type: "AND", terms: terms };
  }

  function parseExpectPrimary(parser) {
    skipExpectWs(parser);
    if (parser.i >= parser.text.length) throw new ParseError("EXPECT requires a check", parser.lineno);
    if (parser.text.charAt(parser.i) === "(") {
      parser.i += 1;
      var node = parseExpectOr(parser);
      skipExpectWs(parser);
      if (parser.i >= parser.text.length || parser.text.charAt(parser.i) !== ")") {
        throw new ParseError("Unbalanced parentheses in EXPECT", parser.lineno);
      }
      parser.i += 1;
      return node;
    }
    var end = atomicExpectEnd(parser);
    var slice = parser.text.slice(parser.i, end).trim();
    if (!slice) throw new ParseError("EXPECT requires a check", parser.lineno);
    parser.i = end;
    return parseExpectAtomic(slice, parser.lineno);
  }

  function atomicExpectEnd(parser) {
    var i = parser.i;
    var text = parser.text;
    var quote = null;
    var escape = false;
    var brackets = 0;
    var parens = 0;
    while (i < text.length) {
      var ch = text.charAt(i);
      if (quote) {
        if (escape) escape = false;
        else if (ch === "\\") escape = true;
        else if (ch === quote) quote = null;
        i += 1;
        continue;
      }
      if (ch === '"' || ch === "'") {
        quote = ch;
        i += 1;
        continue;
      }
      if (ch === "[") {
        brackets += 1;
        i += 1;
        continue;
      }
      if (ch === "]" && brackets) {
        brackets -= 1;
        i += 1;
        continue;
      }
      if (ch === "(") {
        parens += 1;
        i += 1;
        continue;
      }
      if (ch === ")") {
        if (parens) {
          parens -= 1;
          i += 1;
          continue;
        }
        return i;
      }
      if (brackets === 0 && parens === 0 && /^\s+(AND|OR)\b/i.test(text.slice(i))) return i;
      i += 1;
    }
    return i;
  }

  function parseExpectAtomic(rest, lineno) {
    var becauseTaken = takeBecause(rest);
    rest = becauseTaken.rest;
    var kindMatch = rest.match(/^(STATUS|CONTAINS|JSON|HEADER|BODY|SCHEMA|DURATION|OPENAPI|XPATH)(?:\s+|(?==)|$)(.*)$/i);
    if (!kindMatch) {
      var kind = rest.split(/\s+/)[0];
      throw new ParseError("Unknown EXPECT check '" + String(kind).toUpperCase() + "'", lineno);
    }
    var expectKind = kindMatch[1].toUpperCase();
    var remainder = (kindMatch[2] || "").trim();
    if (expectKind === "SCHEMA") {
      throw new ParseError("EXPECT schema is not available in the playground. Use the SnapAPI CLI.", lineno);
    }
    if (expectKind === "XPATH") {
      throw new ParseError("EXPECT xpath is not available in the playground. Use the SnapAPI CLI.", lineno);
    }
    if (expectKind === "DURATION") {
      throw new ParseError("EXPECT duration is not available in the playground. Use the SnapAPI CLI.", lineno);
    }
    if (expectKind === "OPENAPI") {
      throw new ParseError("EXPECT openapi is not available in the playground. Use the SnapAPI CLI.", lineno);
    }
    var check = {};
    if (expectKind === "STATUS") {
      if (!remainder) throw new ParseError("EXPECT STATUS requires a status code", lineno);
      var op = "==";
      var value = remainder;
      var sm = remainder.match(/^(==|!=)?\s*(.+)$/);
      if (sm) {
        op = sm[1] || "==";
        value = sm[2].trim();
      }
      check.type = "STATUS";
      check.operator = op;
      check.value = stripQuotes(value);
    } else if (expectKind === "CONTAINS" || expectKind === "BODY") {
      var negated = false;
      var containsValue = remainder;
      var bodyOp = "CONTAINS";
      if (expectKind === "BODY") {
        var unary = remainder.match(/^(not\s+)?empty\s*$/i);
        var bm = remainder.match(/^(not\s+)?(contains|matches|starts-with|ends-with)\s+(.+)$/i);
        if (unary) {
          negated = !!unary[1];
          bodyOp = negated ? "NOT EMPTY" : "EMPTY";
          containsValue = "";
        } else if (bm) {
          negated = !!bm[1];
          bodyOp = bm[2].toUpperCase();
          if ((bodyOp === "CONTAINS" || bodyOp === "MATCHES") && negated) bodyOp = "NOT " + bodyOp;
          else if (negated) throw new ParseError("EXPECT body must look like: body contains <text>", lineno);
          containsValue = bm[3].trim();
        } else {
          throw new ParseError("EXPECT body must look like: body contains <text>", lineno);
        }
      } else if (/^not\s+contains/i.test(remainder)) {
        negated = true;
        containsValue = remainder.replace(/^not\s+contains\s*/i, "");
      }
      if ((bodyOp === "CONTAINS" || bodyOp === "MATCHES" || bodyOp === "NOT CONTAINS" || bodyOp === "NOT MATCHES" || bodyOp === "STARTS-WITH" || bodyOp === "ENDS-WITH") && !containsValue) {
        throw new ParseError("EXPECT CONTAINS requires a value", lineno);
      }
      check.type = "CONTAINS";
      check.operator = bodyOp;
      check.value = containsValue ? stripQuotes(containsValue) : "";
      check.negated = negated;
    } else if (expectKind === "JSON") {
      var lengthMatch = remainder.match(/^(\S+)\s+length\s+(==|!=|>=|<=|>|<)\s+(.+)$/i);
      var containsKeysMatch = remainder.match(/^(.+?)\s+(not\s+)?contains-keys\s+(.+)$/i);
      var containsSequenceMatch = remainder.match(/^(.+?)\s+contains-sequence\s+(.+)$/i);
      var containsSetMatch = remainder.match(/^(.+?)\s+contains(?:-|\s+)(all|only|any)\s+(.+)$/i);
      var subsetMatch = remainder.match(/^(.+?)\s+subset-of\s+(.+)$/i);
      var notMatchesMatch = remainder.match(/^(.+?)\s+not\s+matches\s+(.+)$/i);
      var notContainsMatch = remainder.match(/^(.+?)\s+not\s+contains\s+(.+)$/i);
      var notInMatch = remainder.match(/^(.+?)\s+not\s+in\s+(.+)$/i);
      var affixMatch = remainder.match(/^(.+?)\s+(starts-with|ends-with|equals-ignoring-case|contains-ignoring-case)\s+(.+)$/i);
      var closeToMatch = remainder.match(/^(.+?)\s+close-to\s+(\S+)\s+(?:delta|±|\+\/-)\s+(\S+)\s*$/i);
      var betweenMatch = remainder.match(/^(.+?)\s+between\s+(\S+)\s+(\S+)\s*$/i);
      var typeMatch = remainder.match(/^(.+?)\s+type\s+(\S+)\s*$/i);
      var unaryMatch = remainder.match(/^(.+?)\s+(not\s+empty|empty|unique|not\s+exists|absent|exists|present|sorted(?:\s+desc|\s+asc)?|zero|positive|negative)\s*$/i);
      var inMatch = remainder.match(/^(.+?)\s+in\s+(.+)$/i);
      var jsonMatch = remainder.match(/^(\S+)\s+(==|!=|CONTAINS|MATCHES|>=|<=|>|<)\s+(.+)$/i);
      if (lengthMatch) {
        check.type = "JSON";
        check.path = lengthMatch[1];
        check.operator = "length " + lengthMatch[2];
        check.value = parseExpectValue(lengthMatch[3].trim());
      } else if (containsKeysMatch) {
        check.type = "JSON";
        check.path = containsKeysMatch[1].trim();
        check.operator = containsKeysMatch[2] ? "NOT CONTAINS-KEYS" : "CONTAINS-KEYS";
        check.value = parseExpectValue(containsKeysMatch[3].trim());
      } else if (containsSequenceMatch) {
        check.type = "JSON";
        check.path = containsSequenceMatch[1].trim();
        check.operator = "CONTAINS-SEQUENCE";
        check.value = parseExpectValue(containsSequenceMatch[2].trim());
      } else if (containsSetMatch) {
        check.type = "JSON";
        check.path = containsSetMatch[1].trim();
        check.operator = "CONTAINS-" + containsSetMatch[2].toUpperCase();
        check.value = parseExpectValue(containsSetMatch[3].trim());
      } else if (subsetMatch) {
        check.type = "JSON";
        check.path = subsetMatch[1].trim();
        check.operator = "SUBSET-OF";
        check.value = parseExpectValue(subsetMatch[2].trim());
      } else if (notMatchesMatch) {
        check.type = "JSON";
        check.path = notMatchesMatch[1].trim();
        check.operator = "NOT MATCHES";
        check.value = parseExpectValue(notMatchesMatch[2].trim());
      } else if (notContainsMatch) {
        check.type = "JSON";
        check.path = notContainsMatch[1].trim();
        check.operator = "NOT CONTAINS";
        check.value = parseExpectValue(notContainsMatch[2].trim());
      } else if (notInMatch) {
        check.type = "JSON";
        check.path = notInMatch[1].trim();
        check.operator = "NOT IN";
        check.value = parseExpectValue(notInMatch[2].trim());
      } else if (affixMatch) {
        check.type = "JSON";
        check.path = affixMatch[1].trim();
        check.operator = affixMatch[2].toUpperCase();
        check.value = parseExpectValue(affixMatch[3].trim());
      } else if (closeToMatch) {
        check.type = "JSON";
        check.path = closeToMatch[1].trim();
        check.operator = "CLOSE-TO";
        check.value = parseExpectValue(closeToMatch[2].trim());
        check.delta = parseExpectValue(closeToMatch[3].trim());
      } else if (betweenMatch) {
        check.type = "JSON";
        check.path = betweenMatch[1].trim();
        check.operator = "BETWEEN";
        check.value = [parseExpectValue(betweenMatch[2].trim()), parseExpectValue(betweenMatch[3].trim())];
      } else if (typeMatch) {
        var typeName = typeMatch[2].trim().toLowerCase();
        var allowedTypes = { string: 1, number: 1, array: 1, object: 1, boolean: 1, "null": 1 };
        if (!allowedTypes[typeName]) {
          throw new ParseError("EXPECT JSON type must be string, number, array, object, boolean, or null", lineno);
        }
        check.type = "JSON";
        check.path = typeMatch[1].trim();
        check.operator = "TYPE";
        check.value = typeName;
      } else if (unaryMatch) {
        check.type = "JSON";
        check.path = unaryMatch[1].trim();
        var uop = unaryMatch[2].replace(/\s+/g, " ").toUpperCase();
        if (uop === "ABSENT" || uop === "NOT EXISTS") uop = "ABSENT";
        else if (uop === "EXISTS" || uop === "PRESENT") uop = "EXISTS";
        else if (uop === "SORTED ASC") uop = "SORTED";
        check.operator = uop;
        check.value = null;
      } else if (inMatch) {
        check.type = "JSON";
        check.path = inMatch[1].trim();
        check.operator = "IN";
        check.value = parseExpectValue(inMatch[2].trim());
      } else if (jsonMatch) {
        var jop = jsonMatch[2];
        check.type = "JSON";
        check.path = jsonMatch[1];
        check.operator = /^(contains|matches)$/i.test(jop) ? jop.toUpperCase() : jop;
        check.value = parseExpectValue(jsonMatch[3].trim());
      } else {
        throw new ParseError('EXPECT JSON must look like: JSON $.path == "value"', lineno);
      }
    } else if (expectKind === "HEADER") {
      var hunary = remainder.match(/^(\S+)\s+(not\s+empty|empty|not\s+exists|absent|exists|present)\s*$/i);
      var hm = remainder.match(/^(\S+)\s+(==|!=|CONTAINS|NOT\s+CONTAINS|NOT\s+MATCHES|MATCHES|STARTS-WITH|ENDS-WITH|IN|NOT\s+IN|EQUALS-IGNORING-CASE|CONTAINS-IGNORING-CASE)\s+(.+)$/i);
      if (hunary) {
        check.type = "HEADER";
        check.name = hunary[1];
        var hopU = hunary[2].replace(/\s+/g, " ").toUpperCase();
        if (hopU === "ABSENT" || hopU === "NOT EXISTS") hopU = "ABSENT";
        else if (hopU === "EXISTS" || hopU === "PRESENT") hopU = "EXISTS";
        check.operator = hopU;
        check.value = "";
      } else {
        if (!hm) throw new ParseError("EXPECT HEADER must look like: HEADER Content-Type CONTAINS json", lineno);
        var hop = hm[2].replace(/\s+/g, " ").toUpperCase();
        check.type = "HEADER";
        check.name = hm[1];
        var headerOps = {
          CONTAINS: 1, MATCHES: 1, "NOT CONTAINS": 1, "NOT MATCHES": 1,
          "STARTS-WITH": 1, "ENDS-WITH": 1, IN: 1, "NOT IN": 1,
          "EQUALS-IGNORING-CASE": 1, "CONTAINS-IGNORING-CASE": 1
        };
        check.operator = headerOps[hop] ? hop : hm[2];
        check.value = hop === "IN" || hop === "NOT IN" ? parseExpectValue(hm[3].trim()) : stripQuotes(hm[3].trim());
      }
    }
    if (becauseTaken.because) check.because = becauseTaken.because;
    return check;
  }

  function parseSave(rest, lineno) {
    var match = rest.match(/^([A-Za-z_][A-Za-z0-9_]*)\s+FROM\s+(header|cookie|json)?\s*(.+)$/i);
    if (!match) throw new ParseError("SAVE must look like: SAVE: name FROM $.path", lineno);
    var source = (match[2] || "json").toLowerCase();
    var selector = match[3].trim();
    if (source === "json" && /^header\s+/i.test(selector)) {
      source = "header";
      selector = selector.replace(/^header\s+/i, "");
    }
    return { name: match[1], source: source, path: selector };
  }

  function parseWait(rest, lineno) {
    if (!rest) throw new ParseError("WAIT requires a check", lineno);
    var timeout = 10;
    var backoff = 0.5;
    var match = rest.match(/\sTIMEOUT\s+(\S+)(?:\s+BACKOFF\s+(\S+))?\s*$/i);
    if (match) {
      timeout = parseDurationSeconds(match[1]);
      if (match[2]) backoff = parseDurationSeconds(match[2]);
      rest = rest.slice(0, match.index).trim();
    }
    return { check: parseExpect(rest, lineno), timeout: timeout, backoff: backoff };
  }

  function parseCsv(text) {
    var lines = text.trim().split(/\r?\n/);
    if (!lines.length) throw new ParseError("EXAMPLES CSV is missing a header row");
    function splitRow(line) {
      var out = [];
      var cur = "";
      var q = false;
      for (var i = 0; i < line.length; i++) {
        var ch = line[i];
        if (q) {
          if (ch === '"' && line[i + 1] === '"') {
            cur += '"';
            i += 1;
          } else if (ch === '"') q = false;
          else cur += ch;
        } else if (ch === '"') q = true;
        else if (ch === ",") {
          out.push(cur.trim());
          cur = "";
        } else cur += ch;
      }
      out.push(cur.trim());
      return out;
    }
    var headers = splitRow(lines[0]);
    if (!headers.length || !headers[0]) throw new ParseError("EXAMPLES CSV is missing a header row");
    var rows = [];
    for (var r = 1; r < lines.length; r++) {
      if (!lines[r].trim()) continue;
      var cells = splitRow(lines[r]);
      var row = {};
      for (var c = 0; c < headers.length; c++) {
        if (headers[c]) row[headers[c]] = cells[c] || "";
      }
      rows.push(row);
    }
    if (!rows.length) throw new ParseError("EXAMPLES CSV has no data rows");
    return rows;
  }

  function applyHeaders(payload, suite, currentTest, currentStep) {
    var keys = Object.keys(payload);
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      if (currentStep) currentStep.headers[key] = payload[key];
      else if (currentTest) currentTest.headers[key] = payload[key];
      else suite.headers[key] = payload[key];
    }
  }

  function parse(text) {
    var lines = String(text || "").split(/\r?\n/);
    var suite = {
      name: null,
      description: null,
      baseUrl: null,
      headers: {},
      options: {},
      setup: null,
      teardown: null,
      sets: [],
      tests: [],
      testMap: {},
    };
    var tests = [];
    var testMap = {};
    var currentTest = null;
    var currentStep = null;
    var i = 0;

    function requireTest(keyword, lineno) {
      if (!currentTest) throw new ParseError(keyword + " must appear inside a TEST or HELPER", lineno);
    }
    function requirePrimary(keyword, lineno) {
      requireTest(keyword, lineno);
      if (currentTest.kind === "helper") throw new ParseError(keyword + " cannot appear on a HELPER", lineno);
    }
    function requireStep(keyword, lineno) {
      if (!currentStep) throw new ParseError(keyword + " must follow a REQUEST", lineno);
    }
    function closeOpenHelper() {
      if (currentTest && currentTest.kind === "helper") {
        tests.push(currentTest);
        currentTest = null;
        currentStep = null;
      }
    }
    function requireNoTest(keyword, lineno) {
      closeOpenHelper();
      if (currentTest) throw new ParseError(keyword + " must appear at suite level (before TEST)", lineno);
    }

    while (i < lines.length) {
      var lineno = i + 1;
      var stripped = lines[i].trim();
      if (!stripped || stripped.indexOf("//") === 0) {
        i += 1;
        continue;
      }
      var keyword;
      var rest;
      var km = stripped.match(LINE_KEYWORD_RE);
      if (km) {
        keyword = km[1];
        rest = km[2].trim();
      } else {
        var hm = stripped.match(HEADER_LINE_RE);
        if (hm) {
          keyword = "HEADER";
          rest = hm[1] + ": " + hm[2];
        } else {
          var spaced = stripped.match(SPACED_KEYWORD_RE);
          if (spaced) {
            throw new ParseError(unknownKeywordMessage(spaced[1]), lineno);
          }
          throw new ParseError("Invalid line (expected KEYWORD: value)", lineno);
        }
      }
      if (UNSUPPORTED[keyword]) throw new ParseError(UNSUPPORTED[keyword], lineno);
      if (!KNOWN[keyword]) throw new ParseError(unknownKeywordMessage(keyword), lineno);

      if (keyword === "SUITE") {
        requireNoTest(keyword, lineno);
        suite.name = rest;
      } else if (keyword === "DESC") {
        if (currentTest) currentTest.description = rest;
        else suite.description = rest;
      } else if (keyword === "URL") {
        if (currentTest) currentTest.baseUrl = rest;
        else suite.baseUrl = rest;
      } else if (keyword === "OPTIONS") {
        requireNoTest(keyword, lineno);
        var opt = readJson(rest, lines, i, lineno);
        if (!opt.value || typeof opt.value !== "object" || Array.isArray(opt.value)) {
          throw new ParseError("OPTIONS must be a JSON object", lineno);
        }
        Object.keys(opt.value).forEach(function (k) {
          if (k === "STOP-ON-FAILURE") return;
          suite.options[k] = opt.value[k];
        });
        i = opt.last;
      } else if (keyword === "TIMEOUT") {
        requireNoTest(keyword, lineno);
        suite.options.TIMEOUT = parseFloat(rest);
      } else if (keyword === "FOLLOW-REDIRECTS") {
        parseBool(rest, keyword, lineno);
      } else if (keyword === "SUITE-SETUP") {
        requireNoTest(keyword, lineno);
        suite.setup = rest;
      } else if (keyword === "SUITE-TEARDOWN") {
        requireNoTest(keyword, lineno);
        suite.teardown = rest;
      } else if (keyword === "TEST" || keyword === "HELPER") {
        if (currentTest) tests.push(currentTest);
        if (!rest) throw new ParseError(keyword + " name is required", lineno);
        if (testMap[rest]) throw new ParseError("Duplicate name '" + rest + "'", lineno);
        currentTest = {
          name: rest,
          kind: keyword === "HELPER" ? "helper" : "test",
          description: null,
          tags: [],
          baseUrl: suite.baseUrl,
          headers: Object.assign({}, suite.headers),
          setup: null,
          teardown: null,
          depends: [],
          steps: [],
          examples: [],
          sets: [],
          lineno: lineno,
        };
        currentStep = null;
        testMap[rest] = currentTest;
      } else if (keyword === "TAG") {
        requireTest(keyword, lineno);
        currentTest.tags = rest.split(/[,\s]+/).filter(Boolean);
      } else if (keyword === "SETUP") {
        requireTest(keyword, lineno);
        currentTest.setup = rest;
      } else if (keyword === "TEARDOWN") {
        requireTest(keyword, lineno);
        currentTest.teardown = rest;
      } else if (keyword === "DEPENDS") {
        requirePrimary(keyword, lineno);
        var depNames = rest.split(",").map(function (item) { return item.trim(); }).filter(Boolean);
        if (!depNames.length) throw new ParseError("DEPENDS requires a test name", lineno);
        depNames.forEach(function (name) {
          if (currentTest.depends.indexOf(name) < 0) currentTest.depends.push(name);
        });
      } else if (keyword === "REQUEST") {
        requireTest(keyword, lineno);
        var reqParts = rest.split(/\s+/);
        if (reqParts.length < 2) throw new ParseError("REQUEST must be in the form: METHOD /path", lineno);
        var method = reqParts[0].toUpperCase();
        var endpoint = rest.slice(reqParts[0].length).trim();
        if (!HTTP_METHODS[method]) throw new ParseError("Unknown HTTP method '" + method + "'", lineno);
        currentStep = newStep(method, endpoint, lineno);
        currentTest.steps.push(currentStep);
      } else if (keyword === "HEAD" || (HTTP_METHODS[keyword] && keyword !== "OPTIONS")) {
        requireTest(keyword, lineno);
        if (!rest) throw new ParseError(keyword + " requires a path", lineno);
        currentStep = newStep(keyword, rest, lineno);
        currentTest.steps.push(currentStep);
      } else if (keyword === "DATA" || keyword === "BODY") {
        requireStep(keyword, lineno);
        var lowered = rest.replace(/^\s+/, "").toLowerCase();
        if (lowered.indexOf("form") === 0) {
          var formPayload = rest.replace(/^\s*form\s*/i, "");
          if (!formPayload) throw new ParseError("BODY form requires key=value pairs", lineno);
          currentStep.bodyType = "form";
          currentStep.data = parseQueryString(formPayload, lineno);
        } else if (lowered.indexOf("raw") === 0) {
          var rawRest = rest.replace(/^\s*raw\s*/i, "").trim();
          var rawParts = rawRest.split(/\s+/);
          if (!rawParts[0]) throw new ParseError("BODY raw requires a content type and body", lineno);
          currentStep.bodyType = "raw";
          currentStep.contentType = rawParts[0];
          currentStep.rawBody = rawRest.slice(rawParts[0].length).trim();
        } else {
          var body = readJson(rest, lines, i, lineno);
          currentStep.bodyType = "json";
          currentStep.data = body.value;
          i = body.last;
        }
      } else if (keyword === "EXAMPLES") {
        requireTest(keyword, lineno);
        if (rest && /\.csv$/i.test(rest) && rest.indexOf("{") === -1) {
          throw new ParseError("EXAMPLES CSV files are not available in the playground. Paste an inline table instead.", lineno);
        }
        var buf = rest ? [rest] : [];
        var last = i;
        while (last + 1 < lines.length) {
          var nxt = lines[last + 1];
          var ns = nxt.trim();
          if (!ns || ns.indexOf("//") === 0) {
            last += 1;
            continue;
          }
          if (LINE_KEYWORD_RE.test(ns) || HEADER_LINE_RE.test(ns)) break;
          buf.push(ns);
          last += 1;
        }
        var csvText = buf.filter(Boolean).join("\n");
        if (!csvText) throw new ParseError("EXAMPLES requires a CSV file or inline table", lineno);
        currentTest.examples = parseCsv(csvText);
        i = last;
      } else if (keyword === "HEADERS") {
        var hdrs = readJson(rest, lines, i, lineno);
        if (!hdrs.value || typeof hdrs.value !== "object" || Array.isArray(hdrs.value)) {
          throw new ParseError("HEADERS must be a JSON object", lineno);
        }
        applyHeaders(hdrs.value, suite, currentTest, currentStep);
        i = hdrs.last;
      } else if (keyword === "HEADER") {
        if (rest.indexOf(":") === -1) throw new ParseError("HEADER must look like: HEADER Name: value", lineno);
        var colon = rest.indexOf(":");
        var hname = rest.slice(0, colon).trim();
        var hval = rest.slice(colon + 1).trim();
        if (!hname) throw new ParseError("HEADER must look like: HEADER Name: value", lineno);
        var one = {};
        one[hname] = hval;
        applyHeaders(one, suite, currentTest, currentStep);
      } else if (keyword === "QUERY") {
        requireStep(keyword, lineno);
        Object.assign(currentStep.query, parseQueryString(rest, lineno));
      } else if (keyword === "PARAM") {
        requireStep(keyword, lineno);
        var pname;
        var pval;
        if (rest.indexOf("=") !== -1 && rest.split("=")[0].indexOf(" ") === -1) {
          var eq = rest.indexOf("=");
          pname = rest.slice(0, eq).trim();
          pval = rest.slice(eq + 1).trim();
        } else {
          var pp = rest.split(/\s+/);
          if (pp.length < 2) throw new ParseError("PARAM must look like: PARAM: page 2", lineno);
          pname = pp[0];
          pval = rest.slice(pp[0].length).trim();
        }
        currentStep.query[pname] = pval;
      } else if (keyword === "AUTH") {
        applyHeaders(parseAuth(rest, lineno), suite, currentTest, currentStep);
      } else if (keyword === "EXPECT") {
        requireStep(keyword, lineno);
        currentStep.checks.push(parseExpect(rest, lineno));
      } else if (keyword === "SAVE") {
        requireStep(keyword, lineno);
        currentStep.saves.push(parseSave(rest, lineno));
      } else if (keyword === "WAIT") {
        requireStep(keyword, lineno);
        currentStep.wait = parseWait(rest, lineno);
      } else if (keyword === "SET") {
        var setParts = rest.split(/\s+/);
        if (setParts.length < 2) throw new ParseError("SET must look like: SET: name value", lineno);
        var item = { name: setParts[0], value: rest.slice(setParts[0].length).trim() };
        if (currentStep) currentStep.sets.push(item);
        else if (currentTest) currentTest.sets.push(item);
        else suite.sets.push(item);
      }
      i += 1;
    }
    if (currentTest) tests.push(currentTest);
    if (!suite.name) suite.name = "Unnamed suite";
    suite.tests = tests;
    suite.testMap = testMap;
    Object.keys(testMap).forEach(function (name) {
      var test = testMap[name];
      ["setup", "teardown"].forEach(function (field) {
        if (test[field] && !testMap[test[field]]) {
          throw new ParseError("Unknown " + field.toUpperCase() + " test '" + test[field] + "' referenced by '" + name + "'", test.lineno);
        }
      });
      (test.depends || []).forEach(function (dep) {
        if (!testMap[dep]) {
          throw new ParseError("Unknown DEPENDS test '" + dep + "' referenced by '" + name + "'", test.lineno);
        }
        if (dep === name) {
          throw new ParseError("TEST '" + name + "' cannot DEPENDS on itself", test.lineno);
        }
      });
    });
    var helpers = {};
    if (suite.setup) helpers[suite.setup] = 1;
    if (suite.teardown) helpers[suite.teardown] = 1;
    tests.forEach(function (test) {
      if (test.kind === "helper") helpers[test.name] = 1;
      if (test.setup) helpers[test.setup] = 1;
      if (test.teardown) helpers[test.teardown] = 1;
    });
    Object.keys(testMap).forEach(function (name) {
      (testMap[name].depends || []).forEach(function (dep) {
        if (helpers[dep] || (testMap[dep] && testMap[dep].kind === "helper")) {
          throw new ParseError("DEPENDS '" + dep + "' is a HELPER, not a primary TEST", testMap[name].lineno);
        }
      });
    });
    detectDependsCycles(testMap);
    ["setup", "teardown"].forEach(function (field) {
      if (suite[field] && !testMap[suite[field]]) {
        throw new ParseError("Unknown SUITE " + field.toUpperCase() + " test '" + suite[field] + "'");
      }
    });
    return suite;
  }

  function tokenizePath(remainder) {
    var tokens = [];
    var i = 0;
    while (i < remainder.length) {
      var ch = remainder[i];
      if (ch === ".") {
        i += 1;
        if (i >= remainder.length) throw new Error("Trailing '.' in JSONPath");
        if (remainder[i] === "[") continue;
        var start = i;
        while (i < remainder.length && remainder[i] !== "." && remainder[i] !== "[") i += 1;
        var key = remainder.slice(start, i);
        if (!key) throw new Error("Empty path segment in JSONPath");
        tokens.push(key === "*" ? "*" : /^\d+$/.test(key) ? parseInt(key, 10) : key);
      } else if (ch === "[") {
        i += 1;
        var depth = 1;
        var innerStart = i;
        var inStr = null;
        while (i < remainder.length && depth) {
          var c = remainder[i];
          if (inStr) {
            if (c === "\\" && i + 1 < remainder.length) i += 2;
            else {
              if (c === inStr) inStr = null;
              i += 1;
            }
            continue;
          }
          if (c === "'" || c === '"') {
            inStr = c;
            i += 1;
            continue;
          }
          if (c === "[") depth += 1;
          else if (c === "]") {
            depth -= 1;
            if (depth === 0) break;
          }
          i += 1;
        }
        var inner = remainder.slice(innerStart, i).trim();
        i += 1;
        if (inner === "*") tokens.push("*");
        else if (inner.charAt(0) === "?") {
          var fm = inner.match(/^\?\(\s*@\.([A-Za-z_][\w.]*)\s*==\s*(.*?)\s*\)$/);
          if (!fm) throw new Error("Unsupported JSONPath filter " + inner);
          tokens.push({ filter: true, field: fm[1], expected: parseExpectValue(fm[2]) });
        } else if (/^-?\d+$/.test(inner)) tokens.push(parseInt(inner, 10));
        else tokens.push(stripQuotes(inner));
      } else {
        throw new Error("Invalid JSONPath near " + remainder.slice(i));
      }
    }
    return tokens;
  }

  function extract(data, path) {
    if (path == null || path === "" || path === "$") return data;
    if (typeof path !== "string" || path.charAt(0) !== "$") {
      throw new Error("JSONPath must start with $: " + JSON.stringify(path));
    }
    return extractTokens(data, tokenizePath(path.slice(1)), path);
  }

  function countMatches(data, path) {
    if (path == null || path === "" || path === "$") return 1;
    if (typeof path !== "string" || path.charAt(0) !== "$") {
      throw new Error("JSONPath must start with $: " + JSON.stringify(path));
    }
    return countTokens(data, tokenizePath(path.slice(1)), path);
  }

  function countTokens(current, tokens, path) {
    if (!tokens.length) return 1;
    var token = tokens[0];
    var rest = tokens.slice(1);
    if (token && token.filter) {
      var items = Array.isArray(current) ? current : [current];
      var filtered = items.filter(function (item) {
        if (!item || typeof item !== "object") return false;
        var cur = item;
        var parts = token.field.split(".");
        for (var p = 0; p < parts.length; p++) {
          if (!cur || typeof cur !== "object" || !(parts[p] in cur)) return false;
          cur = cur[parts[p]];
        }
        return cur === token.expected;
      });
      if (!rest.length) return filtered.length;
      var filterTotal = 0;
      for (var f = 0; f < filtered.length; f++) {
        filterTotal += countTokens(filtered[f], rest, path);
      }
      return filterTotal;
    }
    if (token === "*") {
      if (!Array.isArray(current)) return 0;
      if (!rest.length) return current.length;
      var wildTotal = 0;
      for (var w = 0; w < current.length; w++) {
        wildTotal += countTokens(current[w], rest, path);
      }
      return wildTotal;
    }
    if (typeof token === "number") {
      if (current && typeof current === "object" && !Array.isArray(current)) {
        if (Object.prototype.hasOwnProperty.call(current, String(token))) {
          return countTokens(current[String(token)], rest, path);
        }
        if (Object.prototype.hasOwnProperty.call(current, token)) {
          return countTokens(current[token], rest, path);
        }
        return 0;
      }
      if (!Array.isArray(current) || token < 0 || token >= current.length) return 0;
      return countTokens(current[token], rest, path);
    }
    if (current && typeof current === "object" && !Array.isArray(current) && Object.prototype.hasOwnProperty.call(current, token)) {
      return countTokens(current[token], rest, path);
    }
    return 0;
  }

  function extractTokens(current, tokens, path) {
    if (!tokens.length) return current;
    var token = tokens[0];
    var rest = tokens.slice(1);
    if (token && token.filter) {
      var items = Array.isArray(current) ? current : [current];
      var filtered = items.filter(function (item) {
        if (!item || typeof item !== "object") return false;
        var cur = item;
        var parts = token.field.split(".");
        for (var p = 0; p < parts.length; p++) {
          if (!cur || typeof cur !== "object" || !(parts[p] in cur)) return false;
          cur = cur[parts[p]];
        }
        return cur === token.expected;
      });
      if (!rest.length) return filtered;
      return filtered.map(function (item) {
        return extractTokens(item, rest, path);
      });
    }
    if (token === "*") {
      if (!Array.isArray(current)) throw new Error("Path " + path + " not found (at '*')");
      return current.map(function (item) {
        return extractTokens(item, rest, path);
      });
    }
    return extractTokens(stepPath(current, token, path), rest, path);
  }

  function stepPath(current, token, path) {
    try {
      if (typeof token === "number") {
        if (current && typeof current === "object" && !Array.isArray(current)) {
          if (String(token) in current) return current[String(token)];
          if (token in current) return current[token];
        }
        return current[token];
      }
      if (current && typeof current === "object" && !Array.isArray(current)) return current[token];
      throw new Error("type");
    } catch (err) {
      throw new Error("Path " + path + " not found (at " + JSON.stringify(token) + ")");
    }
  }

  function expandHelpers(value) {
    if (typeof value !== "string") {
      if (Array.isArray(value)) return value.map(expandHelpers);
      if (value && typeof value === "object") {
        var out = {};
        Object.keys(value).forEach(function (k) {
          out[expandHelpers(k)] = expandHelpers(value[k]);
        });
        return out;
      }
      return value;
    }
    return value.replace(HELPER_RE, function (all, name, rawArgs) {
      if (rawArgs == null && name !== "uuid" && name !== "now") return all;
      if (name === "uuid") {
        return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
          var r = (Math.random() * 16) | 0;
          return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
        });
      }
      if (name === "now") return new Date().toISOString();
      if (name === "random.int") {
        var args = String(rawArgs || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
        if (args.length !== 2) throw new RunError("random.int requires two arguments: ${random.int(min,max)}");
        var low = parseInt(args[0], 10);
        var high = parseInt(args[1], 10);
        return String(low + Math.floor(Math.random() * (high - low + 1)));
      }
      throw new RunError("Unknown helper ${" + name + "()}");
    });
  }

  function interpolate(value, variables) {
    value = expandHelpers(value);
    if (typeof value === "string") {
      return value.replace(VAR_RE, function (all, name) {
        if (!Object.prototype.hasOwnProperty.call(variables, name) || variables[name] == null) {
          throw new RunError("Undefined variable ${" + name + "}");
        }
        return String(variables[name]);
      });
    }
    if (Array.isArray(value)) return value.map(function (item) { return interpolate(item, variables); });
    if (value && typeof value === "object") {
      var out = {};
      Object.keys(value).forEach(function (k) {
        out[interpolate(k, variables)] = interpolate(value[k], variables);
      });
      return out;
    }
    return value;
  }

  function mergeQuery(endpoint, params) {
    if (!params || !Object.keys(params).length) return endpoint;
    var qIndex = endpoint.indexOf("?");
    var path = qIndex === -1 ? endpoint : endpoint.slice(0, qIndex);
    var existing = {};
    if (qIndex !== -1) Object.assign(existing, parseQueryString(endpoint.slice(qIndex + 1), 0));
    Object.keys(params).forEach(function (k) {
      existing[k] = params[k] == null ? "" : String(params[k]);
    });
    var qs = Object.keys(existing).map(function (k) {
      return encodeURIComponent(k) + "=" + encodeURIComponent(existing[k]);
    }).join("&");
    return path + "?" + qs;
  }

  function headerGet(headers, name) {
    if (!headers) return undefined;
    var want = String(name).toLowerCase();
    var keys = Object.keys(headers);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].toLowerCase() === want) return headers[keys[i]];
    }
    return undefined;
  }

  function formatDuration(ms) {
    if (ms == null) return "0ms";
    if (ms < 1000) return Math.round(ms) + "ms";
    return (ms / 1000).toFixed(2) + "s";
  }

  function padRequest(text) {
    if (text.length >= REQUEST_COL) return text;
    return text + Array(REQUEST_COL - text.length + 1).join(" ");
  }

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function compare(actual, op, expected, label) {
    var ok =
      (op === "==" && actual === expected) ||
      (op === "!=" && actual !== expected) ||
      (op === ">" && actual > expected) ||
      (op === ">=" && actual >= expected) ||
      (op === "<" && actual < expected) ||
      (op === "<=" && actual <= expected);
    if (!ok) throw new Error(label + " expected " + op + " " + expected + ", got " + actual);
  }

  function asList(value) {
    return Array.isArray(value) ? value : [value];
  }

  function normOp(operator) {
    return String(operator || "").replace(/\s+/g, " ").trim().toUpperCase();
  }

  function isEmpty(value) {
    if (value == null) return true;
    if (typeof value === "string" || Array.isArray(value)) return value.length === 0;
    if (value && typeof value === "object") return Object.keys(value).length === 0;
    return false;
  }

  function jsonTypeName(value) {
    if (value === null) return "null";
    if (Array.isArray(value)) return "array";
    var t = typeof value;
    if (t === "string") return "string";
    if (t === "number") return "number";
    if (t === "boolean") return "boolean";
    if (t === "object") return "object";
    return t;
  }

  function sameSet(left, right) {
    var leftItems = asList(left);
    var rightItems = asList(right);
    function has(list, item) {
      for (var i = 0; i < list.length; i++) {
        if (list[i] === item) return true;
      }
      return false;
    }
    for (var i = 0; i < leftItems.length; i++) if (!has(rightItems, leftItems[i])) return false;
    for (var j = 0; j < rightItems.length; j++) if (!has(leftItems, rightItems[j])) return false;
    return true;
  }

  function compareValues(a, b) {
    if (a === b) return 0;
    if (typeof a === "number" && typeof b === "number") return a - b;
    return String(a) < String(b) ? -1 : 1;
  }

  function assertValue(actual, operator, expected, label, delta) {
    var op = normOp(operator);
    if (op === "==") {
      if (actual !== expected) throw new Error(label + " expected " + JSON.stringify(expected) + ", got " + JSON.stringify(actual));
    } else if (op === "!=") {
      if (actual === expected) throw new Error(label + " expected not " + JSON.stringify(expected) + ", got " + JSON.stringify(actual));
    } else if (op === "CONTAINS") {
      if (Array.isArray(actual)) {
        if (actual.indexOf(expected) === -1) throw new Error(label + " value " + JSON.stringify(actual) + " does not contain " + JSON.stringify(expected));
      } else if (String(actual).indexOf(String(expected)) === -1) {
        throw new Error(label + " value " + JSON.stringify(actual) + " does not contain " + JSON.stringify(expected));
      }
    } else if (op === "NOT CONTAINS") {
      if (Array.isArray(actual)) {
        if (actual.indexOf(expected) !== -1) throw new Error(label + " value " + JSON.stringify(actual) + " unexpectedly contains " + JSON.stringify(expected));
      } else if (String(actual).indexOf(String(expected)) !== -1) {
        throw new Error(label + " value " + JSON.stringify(actual) + " unexpectedly contains " + JSON.stringify(expected));
      }
    } else if (op === "MATCHES") {
      if (!new RegExp(String(expected)).test(String(actual))) {
        throw new Error(label + " value " + JSON.stringify(actual) + " does not match " + JSON.stringify(expected));
      }
    } else if (op === "NOT MATCHES") {
      if (new RegExp(String(expected)).test(String(actual))) {
        throw new Error(label + " value " + JSON.stringify(actual) + " unexpectedly matches " + JSON.stringify(expected));
      }
    } else if (op === "STARTS-WITH") {
      if (String(actual).indexOf(String(expected)) !== 0) {
        throw new Error(label + " value " + JSON.stringify(actual) + " does not start with " + JSON.stringify(expected));
      }
    } else if (op === "ENDS-WITH") {
      if (String(actual).slice(-String(expected).length) !== String(expected)) {
        throw new Error(label + " value " + JSON.stringify(actual) + " does not end with " + JSON.stringify(expected));
      }
    } else if (op === "EMPTY") {
      if (!isEmpty(actual)) throw new Error(label + " expected empty, got " + JSON.stringify(actual));
    } else if (op === "NOT EMPTY") {
      if (isEmpty(actual)) throw new Error(label + " is unexpectedly empty");
    } else if (op === "UNIQUE") {
      if (!Array.isArray(actual)) throw new Error(label + " unique requires an array, got " + JSON.stringify(actual));
      var seen = [];
      var dupes = [];
      for (var u = 0; u < actual.length; u++) {
        if (seen.indexOf(actual[u]) !== -1 && dupes.indexOf(actual[u]) === -1) dupes.push(actual[u]);
        seen.push(actual[u]);
      }
      if (dupes.length) throw new Error(label + " expected unique values, got duplicates " + JSON.stringify(dupes) + " in " + JSON.stringify(actual));
    } else if (op === "TYPE") {
      var actualType = jsonTypeName(actual);
      if (actualType !== String(expected).toLowerCase()) {
        throw new Error(label + " expected type " + expected + ", got " + actualType + " (" + JSON.stringify(actual) + ")");
      }
    } else if (op === "IN") {
      var options = asList(expected);
      if (options.indexOf(actual) === -1) throw new Error(label + " value " + JSON.stringify(actual) + " not in " + JSON.stringify(expected));
    } else if (op === "NOT IN") {
      var notOptions = asList(expected);
      if (notOptions.indexOf(actual) !== -1) throw new Error(label + " value " + JSON.stringify(actual) + " unexpectedly in " + JSON.stringify(expected));
    } else if (op === "CONTAINS-ALL") {
      if (!Array.isArray(actual)) throw new Error(label + " contains-all requires an array, got " + JSON.stringify(actual));
      var missing = asList(expected).filter(function (item) { return actual.indexOf(item) === -1; });
      if (missing.length) throw new Error(label + " value " + JSON.stringify(actual) + " does not contain all of " + JSON.stringify(expected) + " (missing " + JSON.stringify(missing) + ")");
    } else if (op === "CONTAINS-ONLY") {
      if (!Array.isArray(actual)) throw new Error(label + " contains-only requires an array, got " + JSON.stringify(actual));
      if (!sameSet(actual, expected)) throw new Error(label + " value " + JSON.stringify(actual) + " is not the set " + JSON.stringify(asList(expected)));
    } else if (op === "CONTAINS-ANY") {
      var wanted = asList(expected);
      var found = Array.isArray(actual)
        ? wanted.some(function (item) { return actual.indexOf(item) !== -1; })
        : wanted.some(function (item) { return String(actual).indexOf(String(item)) !== -1; });
      if (!found) throw new Error(label + " value " + JSON.stringify(actual) + " does not contain any of " + JSON.stringify(expected));
    } else if (op === "CONTAINS-SEQUENCE") {
      if (!Array.isArray(actual)) throw new Error(label + " contains-sequence requires an array, got " + JSON.stringify(actual));
      var seq = asList(expected);
      if (!seq.length) throw new Error(label + " contains-sequence requires a non-empty sequence");
      var seqFound = false;
      for (var si = 0; si <= actual.length - seq.length; si++) {
        var ok = true;
        for (var sj = 0; sj < seq.length; sj++) {
          if (actual[si + sj] !== seq[sj]) { ok = false; break; }
        }
        if (ok) { seqFound = true; break; }
      }
      if (!seqFound) throw new Error(label + " value " + JSON.stringify(actual) + " does not contain sequence " + JSON.stringify(seq));
    } else if (op === "SUBSET-OF") {
      if (!Array.isArray(actual)) throw new Error(label + " subset-of requires an array, got " + JSON.stringify(actual));
      var universe = asList(expected);
      var extra = actual.filter(function (item) { return universe.indexOf(item) === -1; });
      if (extra.length) throw new Error(label + " value " + JSON.stringify(actual) + " is not a subset of " + JSON.stringify(universe) + " (extra " + JSON.stringify(extra) + ")");
    } else if (op === "CONTAINS-KEYS") {
      if (!actual || typeof actual !== "object" || Array.isArray(actual)) throw new Error(label + " contains-keys requires an object, got " + JSON.stringify(actual));
      var keyWanted = asList(expected).map(String);
      var keyMissing = keyWanted.filter(function (k) { return !(k in actual); });
      if (keyMissing.length) throw new Error(label + " missing keys " + JSON.stringify(keyMissing));
    } else if (op === "NOT CONTAINS-KEYS") {
      if (!actual || typeof actual !== "object" || Array.isArray(actual)) throw new Error(label + " not contains-keys requires an object, got " + JSON.stringify(actual));
      var keyUnwanted = asList(expected).map(String);
      var keyPresent = keyUnwanted.filter(function (k) { return k in actual; });
      if (keyPresent.length) throw new Error(label + " unexpectedly has keys " + JSON.stringify(keyPresent));
    } else if (op === "SORTED") {
      if (!Array.isArray(actual)) throw new Error(label + " sorted requires an array, got " + JSON.stringify(actual));
      var asc = actual.slice().sort(compareValues);
      if (JSON.stringify(actual) !== JSON.stringify(asc)) throw new Error(label + " expected ascending sort, got " + JSON.stringify(actual));
    } else if (op === "SORTED DESC") {
      if (!Array.isArray(actual)) throw new Error(label + " sorted desc requires an array, got " + JSON.stringify(actual));
      var desc = actual.slice().sort(compareValues).reverse();
      if (JSON.stringify(actual) !== JSON.stringify(desc)) throw new Error(label + " expected descending sort, got " + JSON.stringify(actual));
    } else if (op === "ZERO") {
      if (Number(actual) !== 0) throw new Error(label + " expected 0, got " + JSON.stringify(actual));
    } else if (op === "POSITIVE") {
      if (!(Number(actual) > 0)) throw new Error(label + " expected > 0, got " + JSON.stringify(actual));
    } else if (op === "NEGATIVE") {
      if (!(Number(actual) < 0)) throw new Error(label + " expected < 0, got " + JSON.stringify(actual));
    } else if (op === "EQUALS-IGNORING-CASE") {
      if (String(actual).toLowerCase() !== String(expected).toLowerCase()) {
        throw new Error(label + " expected " + JSON.stringify(expected) + " ignoring case, got " + JSON.stringify(actual));
      }
    } else if (op === "CONTAINS-IGNORING-CASE") {
      if (String(actual).toLowerCase().indexOf(String(expected).toLowerCase()) === -1) {
        throw new Error(label + " value " + JSON.stringify(actual) + " does not contain " + JSON.stringify(expected) + " ignoring case");
      }
    } else if (op === "BETWEEN") {
      var bounds = asList(expected);
      if (bounds.length !== 2) throw new Error(label + " between requires two numbers, got " + JSON.stringify(expected));
      var num = Number(actual);
      var low = Number(bounds[0]);
      var high = Number(bounds[1]);
      if (!(low <= num && num <= high)) throw new Error(label + " expected between " + low + " and " + high + ", got " + num);
    } else if (op === "CLOSE-TO") {
      var value = Number(actual);
      var target = Number(expected);
      var tolerance = Number(delta);
      if (!(Math.abs(value - target) <= tolerance)) throw new Error(label + " expected " + target + " ± " + tolerance + ", got " + value);
    } else if ("> >= < <=".indexOf(op) !== -1) {
      compare(actual, operator, expected, label);
    } else {
      throw new Error("Unknown operator " + operator);
    }
  }

  function assertJsonValue(actual, operator, expected, label) {
    assertValue(actual, operator, expected, label);
  }

  function executeCheck(check, response, variables) {
    try {
      runCheck(check, response, variables);
    } catch (err) {
      if (check.because) throw new Error(check.because + ": " + (err && err.message ? err.message : String(err)));
      throw err;
    }
  }

  function runCheck(check, response, variables) {
    if (check.type === "AND") {
      for (var a = 0; a < (check.terms || []).length; a++) {
        executeCheck(check.terms[a], response, variables);
      }
      return;
    }
    if (check.type === "OR") {
      var errors = [];
      for (var o = 0; o < (check.terms || []).length; o++) {
        try {
          executeCheck(check.terms[o], response, variables);
          return;
        } catch (err) {
          errors.push(err && err.message ? err.message : String(err));
        }
      }
      throw new Error("OR expected at least one check to pass: " + (errors.join("; ") || "no alternatives"));
    }
    if (check.type === "STATUS") {
      var expected = parseInt(interpolate(String(check.value), variables), 10);
      var actual = response.status;
      var operator = check.operator || "==";
      if (operator === "!=") {
        if (actual === expected) throw new Error("Status code expected not " + expected + ", got " + actual);
      } else if (actual !== expected) {
        throw new Error("Status code expected " + expected + ", got " + actual);
      }
    } else if (check.type === "CONTAINS") {
      var bodyOp = normOp(check.operator || "CONTAINS");
      if (check.negated && bodyOp === "CONTAINS") bodyOp = "NOT CONTAINS";
      var text = response.text || "";
      if (bodyOp === "CONTAINS" || bodyOp === "NOT CONTAINS") {
        var needle = interpolate(String(check.value), variables);
        var present = text.indexOf(needle) !== -1;
        if (bodyOp === "NOT CONTAINS") {
          if (present) throw new Error("Response unexpectedly contains " + needle);
        } else if (!present) {
          throw new Error("Response does not contain " + needle);
        }
      } else {
        assertValue(text, bodyOp, interpolate(check.value, variables), "Response body");
      }
    } else if (check.type === "JSON") {
      if (response.json == null) throw new Error("Response is not JSON");
      var jpath = interpolate(check.path, variables);
      var jop = normOp(check.operator);
      if (jop === "ABSENT" || jop === "EXISTS") {
        var matchCount = countMatches(response.json, jpath);
        if (jop === "ABSENT") {
          if (matchCount !== 0) throw new Error("JSON " + jpath + " should be absent, found " + matchCount + " match(es)");
        } else if (matchCount < 1) {
          throw new Error("JSON " + jpath + " should exist");
        }
        return;
      }
      var got = extract(response.json, jpath);
      var want = check.value != null ? interpolate(check.value, variables) : null;
      var op = check.operator;
      if (String(op).indexOf("length ") === 0) {
        compare(got.length, op.split(" ")[1], want, "JSON " + jpath + " length");
      } else if (normOp(op) === "CLOSE-TO") {
        assertValue(got, "CLOSE-TO", want, "JSON " + jpath, interpolate(check.delta, variables));
      } else {
        assertValue(got, op, want, "JSON " + jpath);
      }
    } else if (check.type === "HEADER") {
      var hname = interpolate(check.name, variables);
      var hop = normOp(check.operator);
      var hactual = headerGet(response.headers, hname);
      if (hop === "ABSENT" || hop === "EXISTS") {
        var hfound = hactual != null;
        if (hop === "ABSENT") {
          if (hfound) throw new Error("Header " + hname + " should be absent, got " + JSON.stringify(hactual));
        } else if (!hfound) {
          throw new Error("Header " + hname + " should exist");
        }
        return;
      }
      if (hop === "EMPTY" || hop === "NOT EMPTY") {
        assertValue(hactual == null ? "" : hactual, hop, null, "Header " + hname);
        return;
      }
      if (hactual == null) throw new Error("Header " + hname + " missing");
      var hexpected = interpolate(check.value, variables);
      if (hop === "==") {
        if (hactual !== hexpected) throw new Error("Header " + hname + " expected " + JSON.stringify(hexpected) + ", got " + JSON.stringify(hactual));
      } else if (hop === "!=") {
        if (hactual === hexpected) throw new Error("Header " + hname + " expected not " + JSON.stringify(hexpected));
      } else {
        assertValue(hactual, hop, hexpected, "Header " + hname);
      }
    } else {
      throw new Error("Unknown check type " + check.type);
    }
  }

  function saveValue(save, response, variables) {
    var value;
    if (save.source === "header") {
      value = headerGet(response.headers, save.path);
      if (value == null) throw new Error("SAVE header " + save.path + " missing");
    } else {
      if (response.json == null) throw new Error("SAVE requires a JSON response");
      value = extract(response.json, interpolate(save.path, variables));
    }
    variables[save.name] = value;
    return value;
  }

  function createMockApi() {
    var users = {
      1: { id: 1, email: "george.bluth@reqres.in", first_name: "George", last_name: "Bluth", name: "George Bluth" },
      2: { id: 2, email: "janet.weaver@reqres.in", first_name: "Janet", last_name: "Weaver", name: "Janet Weaver" },
    };
    var nextId = 100;
    var jobTicks = {};
    var createdAt = Date.now();

    function listPayload(page) {
      page = parseInt(page || "1", 10) || 1;
      var all = Object.keys(users).map(function (k) { return users[k]; });
      return { page: page, per_page: 6, total: all.length, data: all };
    }

    function wrapUser(user) {
      return Object.assign({}, user, { data: Object.assign({}, user) });
    }

    function json(status, body, extraHeaders) {
      var headers = Object.assign({ "Content-Type": "application/json; charset=utf-8" }, extraHeaders || {});
      var text = body == null ? "" : JSON.stringify(body);
      return { status: status, headers: headers, text: text, json: body };
    }

    function match(method, path, query, body) {
      var m = method.toUpperCase();
      if (path === "/health" || path === "/ping") {
        if (m === "GET" || m === "HEAD") return json(200, { ok: true });
      }
      if (path === "/api/users" || path === "/users") {
        if (m === "GET" || m === "HEAD") return json(200, listPayload(query.page));
        if (m === "POST") {
          var payload = body && typeof body === "object" ? body : {};
          var id = nextId++;
          var user = {
            id: id,
            name: payload.name || "User " + id,
            email: payload.email || "user" + id + "@example.com",
            job: payload.job || null,
            createdAt: new Date(createdAt + id).toISOString(),
          };
          users[id] = user;
          return json(201, user);
        }
        if (m === "OPTIONS") return json(204, null);
      }
      var userMatch = path.match(/^\/(?:api\/)?users\/([^/]+)$/);
      if (userMatch) {
        var uid = userMatch[1];
        var numeric = String(parseInt(uid, 10)) === uid ? parseInt(uid, 10) : uid;
        var existing = users[numeric] || users[uid];
        if (m === "GET" || m === "HEAD") {
          if (!existing) return json(404, { error: "Not found" });
          return json(200, wrapUser(existing));
        }
        if (m === "PUT" || m === "PATCH") {
          if (!existing) return json(404, { error: "Not found" });
          var update = body && typeof body === "object" ? body : {};
          Object.keys(update).forEach(function (k) { existing[k] = update[k]; });
          existing.updatedAt = new Date().toISOString();
          return json(200, existing);
        }
        if (m === "DELETE") {
          if (!existing) return json(404, { error: "Not found" });
          delete users[numeric];
          delete users[uid];
          return { status: 204, headers: { "Content-Type": "application/json" }, text: "", json: null };
        }
      }
      var jobMatch = path.match(/^\/jobs\/([^/]+)$/);
      if (jobMatch && (m === "GET" || m === "HEAD")) {
        var jobId = jobMatch[1];
        jobTicks[jobId] = (jobTicks[jobId] || 0) + 1;
        if (jobTicks[jobId] < 3) return json(200, { id: jobId, status: "pending" });
        return json(200, { id: jobId, status: "ready" });
      }
      return json(404, { error: "Mock route not found", method: m, path: path });
    }

    return {
      request: function (method, path, opts) {
        opts = opts || {};
        var started = nowMs();
        var result = match(method, path, opts.query || {}, opts.body);
        if (method.toUpperCase() === "HEAD") {
          result = Object.assign({}, result, { text: "", json: result.json });
        }
        result.durationMs = Math.max(1, nowMs() - started);
        return result;
      },
    };
  }

  function nowMs() {
    return typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
  }

  function isMockUrl(url) {
    if (!url) return true;
    var lower = String(url).trim().toLowerCase();
    return !lower || lower.indexOf("mock://") === 0 || lower.indexOf("playground://") === 0;
  }

  function joinUrl(base, path) {
    if (/^https?:\/\//i.test(path)) return path;
    var origin = (base || "").replace(/\/+$/, "");
    if (!origin) return path;
    if (path.charAt(0) !== "/") path = "/" + path;
    try {
      var parsed = /^https?:\/\//i.test(origin) ? new URL(origin) : null;
      if (parsed && parsed.pathname && parsed.pathname !== "/" && path.indexOf(parsed.pathname) === 0) {
        return origin.split("/").slice(0, 3).join("/") + path;
      }
    } catch (err) { /* ignore */ }
    return origin + path;
  }

  function pathOnly(urlOrPath) {
    var text = urlOrPath || "/";
    try {
      if (/^https?:\/\//i.test(text)) text = new URL(text).pathname + (new URL(text).search || "");
    } catch (err) { /* ignore */ }
    var q = text.indexOf("?");
    return q === -1 ? text : text.slice(0, q);
  }

  function queryOnly(urlOrPath, extra) {
    var text = urlOrPath || "";
    var q = {};
    try {
      if (/^https?:\/\//i.test(text)) {
        new URL(text).searchParams.forEach(function (v, k) { q[k] = v; });
      } else if (text.indexOf("?") !== -1) {
        Object.assign(q, parseQueryString(text.split("?")[1], 0));
      }
    } catch (err) { /* ignore */ }
    return Object.assign(q, extra || {});
  }

  async function liveFetch(method, url, opts) {
    opts = opts || {};
    var headers = Object.assign({}, opts.headers || {});
    var init = { method: method, headers: headers };
    if (opts.body != null && method !== "GET" && method !== "HEAD") {
      if (opts.bodyType === "form") {
        headers["Content-Type"] = headers["Content-Type"] || "application/x-www-form-urlencoded";
        init.body = Object.keys(opts.body).map(function (k) {
          return encodeURIComponent(k) + "=" + encodeURIComponent(opts.body[k]);
        }).join("&");
      } else if (opts.bodyType === "raw") {
        if (opts.contentType) headers["Content-Type"] = opts.contentType;
        init.body = opts.body;
      } else {
        headers["Content-Type"] = headers["Content-Type"] || "application/json";
        init.body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
      }
    }
    init.headers = headers;
    var res;
    try {
      res = await fetch(url, init);
    } catch (err) {
      throw new RunError(
        "Live fetch failed for " + method + " " + url + " — " + (err.message || err) +
        ". Browsers block many cross-origin calls (CORS). Turn Live fetch off to use the built-in mock."
      );
    }
    var text = method === "HEAD" ? "" : await res.text();
    var json = null;
    if (text) {
      try { json = JSON.parse(text); } catch (err) { json = null; }
    }
    var hdrs = {};
    if (res.headers && res.headers.forEach) {
      res.headers.forEach(function (v, k) { hdrs[k] = v; });
    }
    return { status: res.status, headers: hdrs, text: text, json: json };
  }

  function helperNames(suite) {
    var names = {};
    if (suite.setup) names[suite.setup] = 1;
    if (suite.teardown) names[suite.teardown] = 1;
    suite.tests.forEach(function (test) {
      if (test.kind === "helper") names[test.name] = 1;
      if (test.setup) names[test.setup] = 1;
      if (test.teardown) names[test.teardown] = 1;
    });
    return names;
  }

  function detectDependsCycles(testMap) {
    var visiting = [];
    var seen = {};
    function visit(name) {
      if (visiting.indexOf(name) >= 0) {
        throw new ParseError("DEPENDS cycle detected: " + visiting.slice(visiting.indexOf(name)).concat([name]).join(" -> "));
      }
      if (seen[name] || !testMap[name]) return;
      visiting.push(name);
      (testMap[name].depends || []).forEach(visit);
      visiting.pop();
      seen[name] = 1;
    }
    Object.keys(testMap).forEach(visit);
  }

  function orderByDepends(tests) {
    var hasDepends = tests.some(function (test) { return (test.depends || []).length; });
    if (!hasDepends) return tests;
    var byName = {};
    var index = {};
    tests.forEach(function (test, i) {
      byName[test.name] = test;
      index[test.name] = i;
    });
    var seen = {};
    var visiting = {};
    var ordered = [];
    function visit(name) {
      if (seen[name] || !byName[name] || visiting[name]) return;
      visiting[name] = 1;
      var deps = (byName[name].depends || []).filter(function (dep) { return byName[dep]; });
      deps.sort(function (a, b) { return index[a] - index[b]; });
      deps.forEach(visit);
      delete visiting[name];
      seen[name] = 1;
      ordered.push(byName[name]);
    }
    tests.forEach(function (test) { visit(test.name); });
    return ordered;
  }

  function dependsReason(test, depStatus) {
    var deps = test.depends || [];
    for (var i = 0; i < deps.length; i++) {
      var status = depStatus[deps[i]];
      if (!status) return "depends on '" + deps[i] + "' which has not run";
      if (status === "failed") return "depends on '" + deps[i] + "' which failed";
      if (status === "skipped") return "depends on '" + deps[i] + "' which was skipped";
    }
    return null;
  }

  function applySets(sets, variables) {
    (sets || []).forEach(function (item) {
      variables[item.name] = interpolate(item.value, variables);
    });
  }

  async function run(suite, options) {
    options = options || {};
    var live = !!options.live;
    var variables = Object.assign({ TOKEN: "playground-token" }, options.variables || {});
    var mock = options.mock || createMockApi();
    var lines = [];
    var passed = 0;
    var failed = 0;
    var skipped = 0;
    var failures = [];
    var stopOnFailure = false;
    var helpers = helperNames(suite);
    var stack = [];
    var started = nowMs();
    var depStatus = {};

    function emit(text, cls) {
      lines.push({ text: text, cls: cls || "" });
    }

    emit("SnapAPI  " + suite.name, "brand");
    if (suite.description) emit(suite.description, "dim");
    if (live && !isMockUrl(suite.baseUrl)) {
      emit("Live fetch is on — requests go to " + suite.baseUrl + " (CORS may block them).", "warn");
    } else {
      emit("Using in-memory mock API  (GET /users or /api/users, /health, /jobs/:id)", "dim");
    }

    applySets(suite.sets, variables);

    function indent(extra) {
      return Array((stack.length || 0) + (extra || 0) + 1).join("  ");
    }

    async function dispatch(method, endpoint, step, test) {
      var query = interpolate(step.query || {}, variables);
      var merged = mergeQuery(endpoint, query);
      var data = step.data != null ? interpolate(step.data, variables) : null;
      var rawBody = step.rawBody != null ? interpolate(step.rawBody, variables) : null;
      var headers = Object.assign({}, test.headers || {}, step.headers || {});
      headers = interpolate(headers, variables);
      var base = interpolate(test.baseUrl || suite.baseUrl || "", variables);
      var useLive = live && !isMockUrl(base);
      var t0 = nowMs();
      var response;
      if (useLive) {
        var url = joinUrl(base, merged);
        response = await liveFetch(method, url, {
          headers: headers,
          body: rawBody != null ? rawBody : data,
          bodyType: step.bodyType,
          contentType: step.contentType,
        });
      } else {
        response = mock.request(method, pathOnly(merged), {
          query: queryOnly(merged, query),
          body: rawBody != null ? rawBody : data,
          headers: headers,
        });
      }
      response.durationMs = response.durationMs || Math.max(1, nowMs() - t0);
      return { response: response, endpoint: merged, headers: headers, data: rawBody != null ? rawBody : data };
    }

    async function executeStep(step, test) {
      applySets(step.sets, variables);
      var method = step.action;
      var endpoint = interpolate(step.endpoint, variables);
      var wait = step.wait;
      var checks = step.checks || [];
      var attempts = 1;
      checks.forEach(function (c) {
        if (c.retry) attempts = Math.max(attempts, c.retry);
      });
      var timeout = wait ? Math.min(wait.timeout, WAIT_CAP_S) : 0;
      var backoff = wait ? wait.backoff : 0.05;
      var deadline = wait ? nowMs() + timeout * 1000 : 0;
      if (wait) attempts = 10000;
      var lastError = null;
      var lastRecorded = null;
      for (var attempt = 1; attempt <= attempts; attempt++) {
        var dispatched = await dispatch(method, endpoint, step, test);
        var response = dispatched.response;
        lastRecorded = dispatched;
        emit(indent(1) + padRequest(method + " " + dispatched.endpoint) + "  " + response.status + "  " + formatDuration(response.durationMs), statusClass(response.status));
        try {
          if (wait) executeCheck(wait.check, response, variables);
          var failures = [];
          for (var c = 0; c < checks.length; c++) {
            try {
              executeCheck(checks[c], response, variables);
            } catch (checkErr) {
              failures.push(checkErr.message || String(checkErr));
            }
          }
          if (failures.length) throw new Error(failures.join("\n"));
          for (var s = 0; s < (step.saves || []).length; s++) {
            var saved = saveValue(step.saves[s], response, variables);
            emit(indent(1) + "saved " + step.saves[s].name + "=" + saved, "dim");
          }
          return { ok: true, response: response };
        } catch (err) {
          lastError = err.message || String(err);
          if (wait && nowMs() + backoff * 1000 <= deadline) {
            await sleep(backoff * 1000);
            continue;
          }
          if (wait) break;
          if (attempt < attempts) {
            await sleep((checks[0] && checks[0].retryBackoff ? checks[0].retryBackoff : 0.05) * 1000);
            continue;
          }
        }
      }
      lastError.split("\n").forEach(function (line) {
        emit(indent(2) + line, "fail");
      });
      if (lastRecorded && lastRecorded.response) {
        emit(indent(2) + "response:", "dim");
        emit(indent(2) + "  " + (lastRecorded.response.text || "").slice(0, 400), "dim");
      }
      return { ok: false, error: lastError };
    }

    async function runTest(name, role, record) {
      var test = suite.testMap[name];
      if (!test) throw new RunError("Unknown test '" + name + "'");
      if (stack.indexOf(name) !== -1) throw new RunError("SETUP/TEARDOWN cycle detected: " + stack.concat([name]).join(" -> "));
      stack.push(name);
      var t0 = nowMs();
      var error = null;
      try {
        applySets(test.sets, variables);
        if (role === "test") {
          var tagPart = test.tags.length ? "  [" + test.tags.join(", ") + "]" : "";
          emit("");
          emit(indent(0) + test.name + tagPart, "test");
        } else {
          emit(indent(0) + role + " " + test.name, "dim");
        }
        if (test.setup) {
          var setupResult = await runTest(test.setup, "setup", false);
          if (!setupResult.ok) error = "Setup '" + test.setup + "' failed: " + (setupResult.error || "failed");
        }
        if (!error) {
          for (var s = 0; s < test.steps.length; s++) {
            var stepResult = await executeStep(test.steps[s], test);
            if (!stepResult.ok) {
              error = stepResult.error;
              break;
            }
          }
        }
        if (test.teardown) {
          var teardownResult = await runTest(test.teardown, "teardown", false);
          if (!teardownResult.ok && !error) error = "Teardown '" + test.teardown + "' failed: " + (teardownResult.error || "failed");
        }
        var duration = nowMs() - t0;
        if (role === "test" && record !== false) {
          emit(indent(1) + (error ? "FAIL" : "PASS") + "  " + formatDuration(duration), error ? "fail" : "pass");
        }
        return { ok: !error, error: error, duration: duration, name: name };
      } finally {
        stack.pop();
      }
    }

    var primaries = orderByDepends(suite.tests.filter(function (test) { return !helpers[test.name]; }));
    if (suite.setup) {
      var suiteSetup = await runTest(suite.setup, "setup", false);
      if (!suiteSetup.ok) {
        emit(indent(1) + "FAIL  " + formatDuration(suiteSetup.duration || 0), "fail");
        var setupReason = "suite setup '" + suite.setup + "' failed";
        primaries.forEach(function (test) {
          emit("");
          emit(indent(0) + test.name, "test");
          emit(indent(1) + "SKIP  " + setupReason, "warn");
          skipped += 1;
        });
        emit("");
        var setupSummary = "  " + passed + " passed  " + failed + " failed";
        if (skipped) setupSummary += "  " + skipped + " skipped";
        setupSummary += "  " + formatDuration(nowMs() - started);
        emit(setupSummary, "fail");
        emit("  Failed:", "fail");
        emit("    - SUITE-SETUP (" + suite.setup + "): " + (suiteSetup.error || "failed"), "fail");
        return resultPayload(false);
      }
    }

    for (var p = 0; p < primaries.length; p++) {
      var test = primaries[p];
      var depReason = dependsReason(test, depStatus);
      if (depReason) {
        emit("");
        emit(indent(0) + test.name, "test");
        emit(indent(1) + "SKIP  " + depReason, "warn");
        skipped += 1;
        depStatus[test.name] = "skipped";
        continue;
      }
      var rows = test.examples && test.examples.length ? test.examples : [null];
      var anyFail = false;
      var anyPass = false;
      for (var r = 0; r < rows.length; r++) {
        var snapshot = Object.assign({}, variables);
        if (rows[r]) Object.assign(variables, rows[r]);
        var result = await runTest(test.name, "test", true);
        var label = test.name;
        if (rows[r]) {
          var firstVal = Object.keys(rows[r]).map(function (k) { return rows[r][k]; }).filter(Boolean)[0] || "row";
          label = test.name + " [" + firstVal + "]";
          result.name = label;
        }
        if (result.ok) {
          passed += 1;
          anyPass = true;
        } else {
          failed += 1;
          anyFail = true;
          failures.push({ name: label, error: result.error });
        }
        if (rows[r]) variables = snapshot;
      }
      depStatus[test.name] = anyFail ? "failed" : (anyPass ? "passed" : "skipped");
      if (failed && stopOnFailure) {
        emit("  stopped after '" + test.name + "'  (omit -x / --stop-on-failure to continue)", "fail");
        break;
      }
    }

    if (suite.teardown) await runTest(suite.teardown, "teardown", false);

    emit("");
    var summary = "  " + passed + " passed  " + failed + " failed";
    if (skipped) summary += "  " + skipped + " skipped";
    summary += "  " + formatDuration(nowMs() - started);
    emit(summary, failed ? "fail" : "pass");
    if (failures.length) {
      emit("  Failed:", "fail");
      failures.forEach(function (row) {
        emit("    - " + row.name + ": " + (row.error || "failed"), "fail");
      });
    }

    function resultPayload(ok) {
      return {
        ok: ok == null ? failed === 0 : !!ok,
        passed: passed,
        failed: failed,
        skipped: skipped,
        durationMs: nowMs() - started,
        lines: lines,
        text: lines.map(function (line) { return line.text; }).join("\n"),
        failures: failures,
      };
    }
    return resultPayload();
  }

  function statusClass(code) {
    if (code == null) return "fail";
    var n = parseInt(code, 10);
    return n >= 200 && n < 300 ? "ok" : "warn";
  }

  var SAMPLES = {
    hello: {
      label: "Hello (one GET)",
      text:
        "SUITE: Hello API\n" +
        "URL: mock://api\n" +
        "\n" +
        "TEST: Get Users\n" +
        "  GET: /users\n" +
        "  EXPECT: status == 200\n",
    },
    post: {
      label: "POST",
      text:
        "SUITE: Users\n" +
        "URL: mock://api\n" +
        "\n" +
        "TEST: Create User\n" +
        "  POST: /users\n" +
        "  BODY: {\"name\": \"Jane\", \"email\": \"jane@example.com\"}\n" +
        "  EXPECT: status == 201\n",
    },
    save: {
      label: "SAVE then GET",
      text:
        "SUITE: Users\n" +
        "URL: mock://api\n" +
        "\n" +
        "TEST: Create then fetch\n" +
        "  POST: /users\n" +
        "  BODY: {\"name\": \"Jane\", \"email\": \"jane@example.com\"}\n" +
        "  EXPECT: status == 201\n" +
        "  SAVE: userId FROM $.id\n" +
        "  GET: /users/${userId}\n" +
        "  EXPECT: status == 200\n" +
        "  EXPECT: json $.email == \"jane@example.com\"\n",
    },
    list: {
      label: "List users",
      text:
        "// Built-in mock — no install, no network.\n" +
        "SUITE: Playground\n" +
        "DESC: List users from the in-memory API\n" +
        "URL: mock://api\n" +
        "HEADER Content-Type: application/json\n" +
        "\n" +
        "TEST: List Users\n" +
        "TAG: users\n" +
        "  GET: /api/users\n" +
        "  QUERY: page=1&limit=10\n" +
        "  EXPECT: status == 200\n" +
        "  EXPECT: body contains data\n" +
        "  EXPECT: json $.page == 1\n" +
        "  EXPECT: header Content-Type contains json\n",
    },
    create: {
      label: "Create + SAVE",
      text:
        "SUITE: Create then fetch\n" +
        "DESC: POST a user, SAVE the id, GET it back\n" +
        "URL: mock://api\n" +
        "HEADER Content-Type: application/json\n" +
        "\n" +
        "TEST: Create User\n" +
        "DESC: Create a user and keep the id\n" +
        "TAG: users write\n" +
        "  POST: /api/users\n" +
        "  AUTH: bearer ${TOKEN}\n" +
        "  BODY: {\n" +
        "    \"name\": \"Jane\",\n" +
        "    \"email\": \"jane@example.com\"\n" +
        "  }\n" +
        "  EXPECT: status == 201\n" +
        "  EXPECT: body contains id\n" +
        "  SAVE: userId FROM $.id\n" +
        "\n" +
        "TEST: Get User\n" +
        "TAG: users\n" +
        "SETUP: Create User\n" +
        "  GET: /api/users/${userId}\n" +
        "  EXPECT: status == 200\n" +
        "  EXPECT: json $.email == \"jane@example.com\"\n" +
        "  EXPECT: header Content-Type contains json\n",
    },
    fail: {
      label: "JSON expect fail",
      text:
        "SUITE: Expected failure\n" +
        "DESC: This suite is supposed to FAIL — compare the tree output\n" +
        "URL: mock://api\n" +
        "\n" +
        "TEST: Wrong email\n" +
        "  GET: /api/users/2\n" +
        "  EXPECT: status == 200\n" +
        "  EXPECT: json $.data.email == \"not-this@example.com\"\n" +
        "\n" +
        "TEST: List still runs\n" +
        "  GET: /api/users\n" +
        "  EXPECT: STATUS 200\n" +
        "  EXPECT: CONTAINS data\n",
    },
    wait: {
      label: "WAIT demo",
      text:
        "SUITE: Wait for ready\n" +
        "DESC: Reissues GET /jobs/demo until status becomes ready\n" +
        "URL: mock://api\n" +
        "\n" +
        "TEST: Wait for job\n" +
        "  GET: /jobs/demo\n" +
        "  WAIT: json $.status == \"ready\" TIMEOUT 2s BACKOFF 40ms\n" +
        "  EXPECT: status == 200\n" +
        "  EXPECT: json $.status == \"ready\"\n",
    },
  };

  async function runText(text, options) {
    var suite = parse(text);
    return run(suite, options);
  }

  return {
    parse: parse,
    run: run,
    runText: runText,
    extract: extract,
    countMatches: countMatches,
    createMockApi: createMockApi,
    SAMPLES: SAMPLES,
    formatDuration: formatDuration,
    ParseError: ParseError,
    VERSION: "0.3.0-playground",
  };
});
