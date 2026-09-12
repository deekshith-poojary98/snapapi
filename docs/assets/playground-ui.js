(function () {
  var pg = window.SnapAPIPlayground;
  if (!pg) return;

  var source = document.getElementById("source");
  var highlightEl = document.getElementById("source-highlight");
  var editorPane = highlightEl && highlightEl.parentElement;
  var gutter = document.getElementById("gutter");
  var output = document.getElementById("output");
  var sample = document.getElementById("sample");
  var live = document.getElementById("live");
  var runBtn = document.getElementById("run");
  var resetBtn = document.getElementById("reset");
  var copyBtn = document.getElementById("copy");
  var originals = {};
  var canHighlight = !!(window.SnapAPIHighlight && highlightEl && editorPane);

  Object.keys(pg.SAMPLES).forEach(function (key) {
    var opt = document.createElement("option");
    opt.value = key;
    opt.textContent = pg.SAMPLES[key].label;
    sample.appendChild(opt);
    originals[key] = pg.SAMPLES[key].text;
  });

  function currentKey() {
    return sample.value || "list";
  }

  function setSource(text) {
    source.value = text;
    refreshEditor();
  }

  function updateGutter() {
    var n = source.value.split("\n").length;
    var lines = [];
    for (var i = 1; i <= Math.max(n, 12); i++) lines.push(String(i));
    gutter.textContent = lines.join("\n");
  }

  function syncHighlight() {
    if (!canHighlight) return;
    editorPane.classList.add("is-highlighted");
    highlightEl.innerHTML = window.SnapAPIHighlight.highlightSnaptest(source.value) + "\n";
  }

  function refreshEditor() {
    updateGutter();
    syncHighlight();
    syncScroll();
  }

  function syncScroll() {
    gutter.scrollTop = source.scrollTop;
    if (highlightEl) {
      highlightEl.scrollTop = source.scrollTop;
      highlightEl.scrollLeft = source.scrollLeft;
    }
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function renderLines(lines) {
    output.innerHTML = lines.map(function (line) {
      return '<span class="' + (line.cls || "") + '">' + escapeHtml(line.text) + "</span>";
    }).join("\n");
  }

  function showError(err) {
    renderLines([
      { text: "SnapAPI  playground", cls: "brand" },
      { text: err && err.message ? err.message : String(err), cls: "fail" },
    ]);
  }

  async function runSuite() {
    runBtn.disabled = true;
    renderLines([{ text: "SnapAPI  running…", cls: "dim" }]);
    try {
      var result = await pg.runText(source.value, { live: !!(live && live.checked) });
      renderLines(result.lines);
    } catch (err) {
      showError(err);
    } finally {
      runBtn.disabled = false;
    }
  }

  sample.addEventListener("change", function () {
    setSource(originals[currentKey()]);
    runSuite();
  });
  source.addEventListener("input", refreshEditor);
  source.addEventListener("scroll", syncScroll);
  source.addEventListener("keydown", function (event) {
    if (event.key === "Tab") {
      event.preventDefault();
      var start = source.selectionStart;
      var end = source.selectionEnd;
      source.value = source.value.slice(0, start) + "  " + source.value.slice(end);
      source.selectionStart = source.selectionEnd = start + 2;
      refreshEditor();
    }
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      runSuite();
    }
  });
  runBtn.addEventListener("click", runSuite);
  resetBtn.addEventListener("click", function () {
    setSource(originals[currentKey()]);
    output.textContent = "Reset. Click Run to execute this sample.";
  });
  copyBtn.addEventListener("click", function () {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(source.value);
      copyBtn.textContent = "Copied";
      setTimeout(function () { copyBtn.textContent = "Copy"; }, 1200);
    }
  });

  setSource(originals.list);
  runSuite();
})();
