(function () {
  var btn = document.querySelector("[data-menu]");
  var sidebar = document.querySelector(".sidebar");
  if (!btn || !sidebar) return;

  var backdrop = document.createElement("div");
  backdrop.className = "nav-backdrop";
  backdrop.hidden = true;
  document.body.appendChild(backdrop);

  function setOpen(open) {
    sidebar.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    backdrop.hidden = !open;
    document.body.classList.toggle("nav-open", open);
  }

  btn.addEventListener("click", function () {
    setOpen(!sidebar.classList.contains("open"));
  });
  backdrop.addEventListener("click", function () {
    setOpen(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setOpen(false);
  });
  document.addEventListener("click", function (event) {
    if (!sidebar.classList.contains("open")) return;
    if (sidebar.contains(event.target) || btn.contains(event.target)) return;
    setOpen(false);
  });
  sidebar.querySelectorAll("nav a").forEach(function (link) {
    link.addEventListener("click", function () {
      setOpen(false);
    });
  });
})();
