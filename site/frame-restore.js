/* Frame restore (loaded by every page of the site, see scripts/seo.py).
 *
 * The site was built on frames. Search engines index the inner pages and send visitors straight
 * to them, so a page opened on its own (window.top === window.self) is reloaded inside its
 * original frames, with itself in the right one: it asks the outer frameset page (or the page
 * that wraps the frames in an iframe) to show it, through the URL hash "#p=<encoded page URL>".
 * The frameset pages read that hash and load the page into the named frame; nested framesets
 * hand the hash down. location.replace keeps the back button usable, the inner pages are still
 * served as plain files (no server redirect) and a framed page never redirects again (no loop).
 */
(function () {
  "use strict";

  // Site layout: where each page sits (top-down: frameset page, frame name). Null = top-level page.
  var CFG = {
    index: "index.html",
    // /f/index.html (French) and /e/index.html (English) are the framesets: menu on the left,
    // pages on the right. Pages outside /e/ open in the French one. The demo pages are framesets
    // of their own (title + results) inside that main frame.
    up: function (p) {
      p = p.toLowerCase();
      var top = /^\/e\//.test(p) ? "/e/index.html" : "/f/index.html", m;
      if (p === "/index.html" || p === "/e/index.html" || p === "/f/index.html" ||
          /^\/[ef]\/frames2?\.htm$/.test(p)) return null;
      if (/^\/[ef]\/ojmenu\.htm$/.test(p)) return { parent: top, frame: "contents" };
      if ((m = /^(\/demos\/[^\/]+)\/(.+)$/.exec(p)) && m[2] !== "index.htm") {
        return { parent: m[1] + "/index.htm", frame: m[2] === "title.htm" ? "title" : "results" };
      }
      return { parent: top, frame: "main" };
    }
  };

  function norm(p) { return p.replace(/\/{2,}/g, "/").replace(/\/$/, "/" + CFG.index); }

  // Steps from the outermost frameset down to page p: [{parent, frame, child}, ...]
  function chain(p) {
    var steps = [], u, n = 0;
    while ((u = CFG.up(p)) && n++ < 8) {
      steps.unshift({ parent: u.parent, frame: u.frame, child: p });
      p = u.parent;
    }
    return steps;
  }

  function same(a, b) { return a.toLowerCase() === b.toLowerCase(); }

  function consume(enc) {
    var target, a = document.createElement("a");
    try { target = decodeURIComponent(enc); } catch (e) { return; }
    if (!/^\/(?![\/\\])/.test(target)) return;
    a.href = target;
    if (a.protocol !== location.protocol || a.host !== location.host) return;
    var final = norm(a.pathname), here = norm(location.pathname), steps = chain(final), step = null, i;
    for (i = 0; i < steps.length; i++) if (same(steps[i].parent, here)) { step = steps[i]; break; }
    if (!step) return;
    var url = same(step.child, final) ? target : step.child + "#p=" + enc;
    function apply() {
      var el = document.getElementsByName(step.frame)[0] || document.getElementById(step.frame) ||
        (step.frame === "*iframe" ? document.getElementsByTagName("iframe")[0] : null);
      if (el) el.src = url;
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
    else apply();
  }

  var hash = /^#p=(.+)$/.exec(location.hash);
  if (hash) {
    consume(hash[1]);
  } else if (window.top === window.self) {
    var steps = chain(norm(location.pathname));
    if (steps.length) {
      location.replace(steps[0].parent + "#p=" +
        encodeURIComponent(location.pathname + location.search + location.hash));
    }
  }
})();
