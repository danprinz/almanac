/* almanac prototype — boot shim.
   The only script in this prototype. Two jobs:

   1. Select a theme before first paint, so a headless screenshot run can ask
      for one:   page.html?theme=dark   ·   page.html?shot=1  (hides the
      prototype's own footer bar).

   2. Inject the icon sprite, so every page can use <svg><use href="#i-clock">
      without each file carrying its own copy.

   NOTHING HERE IS PART OF THE PRODUCT. The real card and panel are Lit
   elements consuming `hass.themes`, and the real icons are filled MDI paths
   from @mdi/js. The glyphs below are stroke icons authored for this prototype
   — they are deliberately NOT claimed to be MDI paths, because this project
   does not assert third-party source from memory. See FINDINGS.md, "Icons". */
(function () {
  var q = new URLSearchParams(location.search);
  var theme = q.get("theme") === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", theme);

  var I = {
    clock:    '<circle cx="12" cy="12" r="8.6"/><path d="M12 6.8V12l3.4 2"/>',
    sun:      '<circle cx="12" cy="12" r="4.2"/><path d="M12 2.4v2.2M12 19.4v2.2M2.4 12h2.2M19.4 12h2.2M5.2 5.2l1.6 1.6M17.2 17.2l1.6 1.6M18.8 5.2l-1.6 1.6M6.8 17.2l-1.6 1.6"/>',
    flame:    '<path d="M12 21.2c3.2 0 5.4-2.2 5.4-5.1 0-4-4.2-5.5-3.3-10.3-2.4.9-4 3-4 5.1 0 1.3.5 2.1.5 2.9 0 .9-.7 1.5-1.5 1.5-1 0-1.6-.9-1.6-2.2-1.1 1.1-1.9 2.6-1.9 4.3 0 2.7 2.3 3.8 6.4 3.8Z"/>',
    calendar: '<rect x="3.4" y="5" width="17.2" height="15.6" rx="2"/><path d="M3.4 9.6h17.2M8 3.4v3.2M16 3.4v3.2"/>',
    bulb:     '<path d="M9.4 17.6h5.2M10 20.6h4M12 3.4a5.6 5.6 0 0 0-3.3 10.1c.6.5.9 1.1.9 1.8v.3h4.8v-.3c0-.7.3-1.3.9-1.8A5.6 5.6 0 0 0 12 3.4Z"/>',
    thermo:   '<path d="M14 13.4V5a2 2 0 1 0-4 0v8.4a4.2 4.2 0 1 0 4 0Z"/><path d="M12 9.6v6"/>',
    speaker:  '<path d="M4 9.4h3.4L12.6 5v14L7.4 14.6H4Z"/><path d="M16 9.2a4 4 0 0 1 0 5.6M18.6 6.6a7.6 7.6 0 0 1 0 10.8"/>',
    check:    '<path d="M4.6 12.6l4.8 4.8L19.4 7.4"/>',
    minus:    '<path d="M5 12h14"/>',
    plus:     '<path d="M12 5v14M5 12h14"/>',
    close:    '<path d="M6 6l12 12M18 6L6 18"/>',
    alert:    '<path d="M12 3.6 1.9 20.4h20.2L12 3.6Z"/><path d="M12 9.6v4.6M12 17.1v.1"/>',
    info:     '<circle cx="12" cy="12" r="8.6"/><path d="M12 11v5.4M12 7.7v.1"/>',
    chevd:    '<path d="M6.4 9.4 12 15l5.6-5.6"/>',
    chevr:    '<path d="M9.4 6.4 15 12l-5.6 5.6"/>',
    chevl:    '<path d="M14.6 6.4 9 12l5.6 5.6"/>',
    menu:     '<path d="M4 7h16M4 12h16M4 17h16"/>',
    dots:     '<circle cx="12" cy="5.2" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="18.8" r="1.5"/>',
    pencil:   '<path d="M4 20h4L19.3 8.7a2 2 0 0 0 0-2.8l-1.2-1.2a2 2 0 0 0-2.8 0L4 16v4Z"/>',
    home:     '<path d="M4 10.6 12 4l8 6.6V20H4v-9.4Z"/>',
    timeline: '<path d="M4 6.6h16M4 12h16M4 17.4h16"/><circle cx="8.6" cy="6.6" r="2.1" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="2.1" fill="currentColor" stroke="none"/><circle cx="6.8" cy="17.4" r="2.1" fill="currentColor" stroke="none"/>',
    layers:   '<path d="M12 3.4 3 8l9 4.6L21 8l-9-4.6Z"/><path d="m3 13.4 9 4.6 9-4.6"/>',
    history:  '<path d="M3.6 12a8.4 8.4 0 1 0 2.6-6.1"/><path d="M3.4 4.4v4.2h4.2"/><path d="M12 7.6V12l3 1.8"/>',
    play:     '<path d="M8 5.4 18.6 12 8 18.6V5.4Z"/>',
    search:   '<circle cx="10.8" cy="10.8" r="6.4"/><path d="m15.6 15.6 4 4"/>',
    person:   '<circle cx="12" cy="8" r="3.6"/><path d="M5 20c0-3.5 3.1-5.6 7-5.6s7 2.1 7 5.6"/>',
    code:     '<path d="m8.6 8.4-4 3.6 4 3.6M15.4 8.4l4 3.6-4 3.6M13.4 5.4l-2.8 13.2"/>',
    cog:      '<circle cx="12" cy="12" r="3.2"/><path d="M12 3.4v2.3M12 18.3v2.3M20.6 12h-2.3M5.7 12H3.4M18.1 5.9l-1.6 1.6M7.5 16.5l-1.6 1.6M18.1 18.1l-1.6-1.6M7.5 7.5 5.9 5.9"/>',
    trash:    '<path d="M4.8 6.6h14.4M9.4 6.6V4.4h5.2v2.2M6.6 6.6 7.4 20h9.2l.8-13.4"/>',
    arrowr:   '<path d="M4.6 12h14M13.4 6.6 18.8 12l-5.4 5.4"/>',
    link:     '<path d="M10.2 13.8a3.6 3.6 0 0 0 5.1 0l3-3a3.6 3.6 0 0 0-5.1-5.1l-1.2 1.2"/><path d="M13.8 10.2a3.6 3.6 0 0 0-5.1 0l-3 3a3.6 3.6 0 0 0 5.1 5.1l1.2-1.2"/>',
    drag:     '<circle cx="9.4" cy="6" r="1.4" fill="currentColor" stroke="none"/><circle cx="14.6" cy="6" r="1.4" fill="currentColor" stroke="none"/><circle cx="9.4" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="14.6" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="9.4" cy="18" r="1.4" fill="currentColor" stroke="none"/><circle cx="14.6" cy="18" r="1.4" fill="currentColor" stroke="none"/>',
    ghost:    '<circle cx="12" cy="12" r="8.6" stroke-dasharray="2.6 2.6"/>',
    script:   '<path d="M6.6 3.6h8.8l4 4v12.8H6.6V3.6Z"/><path d="M15 3.6v4.4h4.4M9.4 12.6h5.2M9.4 16h3.4"/>',
    bell:     '<path d="M6.6 17.4V11a5.4 5.4 0 1 1 10.8 0v6.4H6.6Z"/><path d="M4.6 17.4h14.8M10.4 20.2h3.2"/>',
    eye:      '<path d="M1.8 12S5.6 5.8 12 5.8 22.2 12 22.2 12 18.4 18.2 12 18.2 1.8 12 1.8 12Z"/><circle cx="12" cy="12" r="2.8"/>',
    copy:     '<rect x="8.4" y="8.4" width="11.2" height="11.2" rx="1.8"/><path d="M15.6 5.6H5.6a1.2 1.2 0 0 0-1.2 1.2v10"/>'
  };

  function sprite() {
    var s = '<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">';
    for (var k in I) {
      s += '<symbol id="i-' + k + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
           'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' + I[k] + '</symbol>';
    }
    return s + "</svg>";
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.body.insertAdjacentHTML("afterbegin", sprite());
    if (q.get("shot")) document.body.setAttribute("data-shot", "1");

    /* ?dbg=1 — names whatever is making the page wider than the viewport.
       A layout that overflows on a phone is a design failure, not a rounding
       error, so it is worth being able to find it by name. */
    if (q.get("dbg")) {
      var vw = document.documentElement.clientWidth, worst = [];
      document.querySelectorAll("*").forEach(function (el) {
        var r = el.getBoundingClientRect();
        if (r.right > vw + 1 || r.left < -1) {
          worst.push(Math.round(r.right) + "px " + el.tagName.toLowerCase() +
                     "." + (el.className.baseVal || el.className || "").toString().split(" ")[0]);
        }
      });
      document.body.insertAdjacentHTML("afterbegin",
        '<pre style="position:fixed;z-index:999;inset:0 0 auto 0;margin:0;padding:6px;' +
        'background:#000;color:#0f0;font:10px/1.35 monospace;max-height:60vh;overflow:auto">' +
        "viewport " + vw + " / scrollWidth " + document.documentElement.scrollWidth +
        "\n" + worst.slice(0, 40).join("\n") + "</pre>");
    }
    var t = document.querySelector("[data-theme-toggle]");
    if (t) {
      var q2 = new URLSearchParams(location.search);
      q2.set("theme", theme === "dark" ? "light" : "dark");
      t.setAttribute("href", location.pathname.split("/").pop() + "?" + q2.toString());
      t.textContent = theme === "dark" ? "light theme" : "dark theme";
    }
  });
})();
