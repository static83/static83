/* Christa Loisel — shared behaviour + the living strand graphic */
(function () {
  "use strict";

  var doc = document.documentElement;
  doc.classList.add("js");

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var SVGNS = "http://www.w3.org/2000/svg";

  var year = document.querySelector("[data-year]");
  if (year) year.textContent = String(new Date().getFullYear());

  /* ---------- header hairline on scroll ---------- */
  var header = document.querySelector(".site-header");
  if (header) {
    var onScroll = function () {
      header.classList.toggle("is-scrolled", window.scrollY > 8);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ---------- reveal on scroll ---------- */
  var reveals = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && !reduceMotion) {
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) {
            e.target.classList.add("is-in");
            io.unobserve(e.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.08 }
    );
    reveals.forEach(function (el) {
      io.observe(el);
    });
  } else {
    reveals.forEach(function (el) {
      el.classList.add("is-in");
    });
  }

  /* ---------- the lock of hair ----------
     A single lock: fine strands that fall, twist once and finish
     exactly on one horizontal line — colour in the curve, precision
     in the cut. Roots are dark, ends lift to the strand's tone. */

  var hero = document.querySelector("[data-strands]");
  if (!hero) return;

  var W = 600,
    CUT = 640,
    TOP = -520,
    N = 40,
    STEPS = 46;

  // ends tone, from lifted honey through copper to espresso
  var TONES = ["#dcc29a", "#d3a865", "#c98b3e", "#bb6a2d", "#a8481f", "#8d3519", "#6b2316", "#4a1a10", "#2e1610"];
  var ROOT = [46, 22, 16];

  function hexToRgb(h) {
    var n = parseInt(h.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function mix(a, b, t) {
    return [0, 1, 2].map(function (k) {
      return Math.round(a[k] + (b[k] - a[k]) * t);
    });
  }
  function rgb(c) {
    return "rgb(" + c.join(",") + ")";
  }
  function toneAt(u) {
    var p = u * (TONES.length - 1),
      i = Math.min(Math.floor(p), TONES.length - 2);
    return mix(hexToRgb(TONES[i]), hexToRgb(TONES[i + 1]), p - i);
  }
  // deterministic jitter so the drawing is the same on every load
  function rand(i) {
    var x = Math.sin(i * 127.1 + 311.7) * 43758.5453;
    return x - Math.floor(x);
  }

  var strands = [];
  for (var i = 0; i < N; i++) {
    var u = i / (N - 1);
    // a few strands wander out of order, like real hair
    var uu = Math.min(1, Math.max(0, u + (rand(i) - 0.5) * 0.12));
    strands.push({
      u: uu,
      w: 0.7 + rand(i + 50) * 1.1,
      ph: rand(i + 99) * Math.PI * 2,
      end: toneAt(uu),
    });
  }

  function point(st, s, t, mx) {
    var y = TOP + s * (CUT - TOP);
    var c = 300 + 120 * Math.sin(Math.PI * (0.3 + 1.6 * s)); // the S of the lock
    var w = 60 + 210 * Math.pow(s, 0.9); // fans out as it falls
    var twist = Math.cos(Math.PI * (s * 1.5 - 0.2)); // crosses over once
    var off = (st.u - 0.5) * (w * twist + 16);
    var sway = (9 * Math.sin(t * 0.55 + s * 2.1) + mx * 18) * Math.pow(s, 1.6);
    var fly = 3.2 * Math.sin(s * 9 + st.ph + t * 0.8) * s;
    return [c + off + sway + fly, y];
  }

  // Catmull-Rom through the points -> smooth cubic path
  function pathFor(st, t, mx) {
    var pts = [];
    for (var k = 0; k <= STEPS; k++) pts.push(point(st, k / STEPS, t, mx));
    var d = "M" + pts[0][0].toFixed(1) + " " + pts[0][1].toFixed(1);
    for (var j = 0; j < pts.length - 1; j++) {
      var p0 = pts[j - 1] || pts[j],
        p1 = pts[j],
        p2 = pts[j + 1],
        p3 = pts[j + 2] || p2;
      d +=
        "C" +
        (p1[0] + (p2[0] - p0[0]) / 6).toFixed(1) + " " + (p1[1] + (p2[1] - p0[1]) / 6).toFixed(1) + " " +
        (p2[0] - (p3[0] - p1[0]) / 6).toFixed(1) + " " + (p2[1] - (p3[1] - p1[1]) / 6).toFixed(1) + " " +
        p2[0].toFixed(1) + " " + p2[1].toFixed(1);
    }
    return d;
  }

  function el(name, attrs, parent) {
    var n = document.createElementNS(SVGNS, name);
    for (var a in attrs) n.setAttribute(a, attrs[a]);
    if (parent) parent.appendChild(n);
    return n;
  }

  var svg = el("svg", {
    class: "strands",
    viewBox: "0 -40 " + W + " " + (CUT + 160),
    preserveAspectRatio: "xMidYMax meet",
    "aria-hidden": "true",
    focusable: "false",
  });
  var defs = el("defs", {}, svg);
  var g = el("g", {}, svg);

  strands.forEach(function (st, idx) {
    var gr = el(
      "linearGradient",
      { id: "cl-s" + idx, gradientUnits: "userSpaceOnUse", x1: 0, y1: TOP, x2: 0, y2: CUT },
      defs
    );
    el("stop", { offset: "0", "stop-color": rgb(mix(ROOT, st.end, 0.15)) }, gr);
    el("stop", { offset: "0.42", "stop-color": rgb(mix(ROOT, st.end, 0.55)) }, gr);
    el("stop", { offset: "1", "stop-color": rgb(st.end) }, gr);
    st.node = el(
      "path",
      {
        class: "strand",
        pathLength: "1",
        stroke: "url(#cl-s" + idx + ")",
        "stroke-width": st.w.toFixed(2),
      },
      g
    );
    st.node.style.animationDelay = (0.15 + st.u * 0.5 + rand(idx + 7) * 0.25).toFixed(2) + "s";
  });

  // the line
  // the line runs the full width of the screen (the svg overflows; the hero clips it)
  el("line", { class: "cut", x1: -4000, x2: W + 4000, y1: CUT, y2: CUT }, svg);
  var anno = el("g", { class: "anno" }, svg);

  // three tone call-outs that follow the ends of their strands
  var calls = [
    { u: 0.06, label: "9.3 Honey" },
    { u: 0.5, label: "7.4 Copper" },
    { u: 0.86, label: "5.5 Auburn" },
  ].map(function (c, k) {
    var grp = el("g", { class: "tone" }, anno);
    var ln = el("line", {}, grp);
    var dot = el("circle", { r: 2.6 }, grp);
    var tx = el("text", {}, grp);
    tx.textContent = c.label;
    c.st = strands.reduce(function (best, s) {
      return Math.abs(s.u - c.u) < Math.abs(best.u - c.u) ? s : best;
    }, strands[0]);
    c.ln = ln;
    c.dot = dot;
    c.tx = tx;
    c.drop = 34 + k * 26;
    return c;
  });

  function render(t, mx) {
    strands.forEach(function (st) {
      st.node.setAttribute("d", pathFor(st, t, mx));
    });
    calls.forEach(function (c) {
      var p = point(c.st, 1, t, mx);
      c.dot.setAttribute("cx", p[0].toFixed(1));
      c.dot.setAttribute("cy", CUT);
      c.ln.setAttribute("x1", p[0].toFixed(1));
      c.ln.setAttribute("x2", p[0].toFixed(1));
      c.ln.setAttribute("y1", CUT + 4);
      c.ln.setAttribute("y2", CUT + c.drop);
      c.tx.setAttribute("x", (p[0] + 8).toFixed(1));
      c.tx.setAttribute("y", CUT + c.drop);
    });
  }

  render(0, 0);
  hero.appendChild(svg);

  if (reduceMotion) return;

  var running = false,
    t0 = performance.now(),
    target = 0,
    mx = 0;

  window.addEventListener(
    "pointermove",
    function (e) {
      target = (e.clientX / window.innerWidth - 0.5) * 2;
    },
    { passive: true }
  );

  function frame(now) {
    if (!running) return;
    mx += (target - mx) * 0.03;
    render((now - t0) / 1000, mx);
    requestAnimationFrame(frame);
  }

  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      var vis = entries[0].isIntersecting;
      if (vis && !running) {
        running = true;
        requestAnimationFrame(frame);
      } else if (!vis) {
        running = false;
      }
    }).observe(hero);
  }
})();
