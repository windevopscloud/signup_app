(function () {
  var KEY = "theme", root = document.documentElement;
  function stored() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function systemDark() { return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches; }
  function apply(t) {
    root.setAttribute("data-theme", t);
    var b = document.getElementById("theme-toggle");
    if (b) { b.setAttribute("aria-checked", t === "dark" ? "true" : "false"); }
  }
  var s = stored();
  apply(s === "light" || s === "dark" ? s : (systemDark() ? "dark" : "light"));
  document.addEventListener("click", function (e) {
    if (!e.target.closest || !e.target.closest("#theme-toggle")) return;
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch (err) {}
    apply(next);
  });
  document.addEventListener("DOMContentLoaded", function () { apply(root.getAttribute("data-theme")); });
})();
