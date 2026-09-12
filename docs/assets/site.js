(function () {
  var btn = document.querySelector("[data-menu]");
  var sidebar = document.querySelector(".sidebar");
  if (!btn || !sidebar) return;
  btn.addEventListener("click", function () {
    sidebar.classList.toggle("open");
    btn.setAttribute("aria-expanded", sidebar.classList.contains("open") ? "true" : "false");
  });
  document.addEventListener("click", function (event) {
    if (!sidebar.classList.contains("open")) return;
    if (sidebar.contains(event.target) || btn.contains(event.target)) return;
    sidebar.classList.remove("open");
  });
})();
