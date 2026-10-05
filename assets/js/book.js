/* Booking request — composes a text message on the visitor's own device.
   Nothing is sent to or stored on any server. */
(function () {
  "use strict";

  var form = document.getElementById("booking-form");
  if (!form) return;

  var PHONE = "+642040641495";
  var send = document.getElementById("send");
  var copyBtn = document.getElementById("copy");
  var status = document.getElementById("status");
  var errorBox = document.getElementById("form-error");
  var dateInput = document.getElementById("date");
  var dateHint = document.getElementById("date-hint");
  var defaultHint = dateHint.textContent;
  var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // earliest date is today (local time)
  var now = new Date();
  function iso(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  dateInput.min = iso(now);
  var max = new Date(now);
  max.setMonth(max.getMonth() + 6);
  dateInput.max = iso(max);

  // ?service=colour preselects a service — only known keys are accepted
  try {
    var key = new URLSearchParams(location.search).get("service");
    if (key) {
      var match = form.querySelector('input[name="service"][data-key="' + key.replace(/[^a-z-]/g, "") + '"]');
      if (match) match.checked = true;
    }
  } catch (e) {}

  function val(name) {
    var f = form.elements[name];
    if (!f) return "";
    if (typeof RadioNodeList !== "undefined" && f instanceof RadioNodeList) return f.value || "";
    return (f.value || "").trim();
  }

  function clean(s, n) {
    // strip control characters and collapse whitespace
    var t = String(s).replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
    // cut by code point, never through the middle of an emoji
    return Array.from(t).slice(0, n).join("");
  }

  function parseDate(v) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
    if (!m) return null;
    var d = new Date(+m[1], +m[2] - 1, +m[3]);
    return isNaN(d) ? null : d;
  }

  function whenText() {
    var parts = [];
    var d = parseDate(val("date"));
    if (d) parts.push(DAYS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()]);
    if (val("time")) parts.push(val("time").toLowerCase());
    return parts.join(", ");
  }

  function message() {
    var lines = ["Hi Christa, I'd like to request an appointment."];
    lines.push("");
    if (val("name")) lines.push("Name: " + clean(val("name"), 60));
    if (val("service")) lines.push("Service: " + val("service"));
    if (val("studio")) lines.push("Studio: " + val("studio"));
    lines.push("Preferred: " + (whenText() || "flexible"));
    if (val("visit")) lines.push(val("visit"));
    if (val("notes")) lines.push("Notes: " + clean(val("notes"), 400));
    lines.push("");
    lines.push("Thank you!");
    return lines.join("\n");
  }

  function smsHref() {
    // "?&body=" is understood by both iOS and Android messaging apps
    return "sms:" + PHONE + "?&body=" + encodeURIComponent(message());
  }

  function setOut(key, text, empty) {
    var dd = document.querySelector('[data-out="' + key + '"]');
    if (!dd) return;
    dd.textContent = text || empty;
    dd.classList.toggle("is-empty", !text);
  }

  function checkDay() {
    var studio = form.querySelector('input[name="studio"]:checked');
    var d = parseDate(val("date"));
    var allowed = studio && studio.getAttribute("data-days");
    if (d && allowed && allowed.split(",").indexOf(String(d.getDay())) === -1) {
      dateHint.textContent =
        "Grey Lynn appointments are on Sundays and Mondays — Christa will suggest the closest one to " + DAYS[d.getDay()] + ".";
    } else {
      dateHint.textContent = defaultHint;
    }
  }

  function update() {
    setOut("service", val("service"), "Choose a service");
    setOut("studio", val("studio"), "Choose a studio");
    setOut("when", whenText(), "Flexible");
    setOut("name", clean(val("name"), 60), "—");
    send.href = smsHref();
    checkDay();
    if (errorBox.classList.contains("is-shown") && !missing().length) {
      errorBox.classList.remove("is-shown");
    }
  }

  function missing() {
    var m = [];
    if (!val("service")) m.push({ label: "a service", el: form.querySelector('input[name="service"]') });
    if (!val("studio")) m.push({ label: "a studio", el: form.querySelector('input[name="studio"]') });
    if (!clean(val("name"), 60)) m.push({ label: "your name", el: document.getElementById("name") });
    return m;
  }

  function validate() {
    var m = missing();
    if (!m.length) return true;
    var labels = m.map(function (x) {
      return x.label;
    });
    var last = labels.pop();
    errorBox.textContent = "Almost there — please add " + (labels.length ? labels.join(", ") + " and " : "") + last + ".";
    errorBox.classList.add("is-shown");
    m[0].el.focus({ preventScroll: true });
    m[0].el.closest("fieldset").scrollIntoView({ behavior: "smooth", block: "start" });
    return false;
  }

  form.addEventListener("input", update);
  form.addEventListener("change", update);
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (validate()) window.location.href = smsHref();
  });

  send.addEventListener("click", function (e) {
    if (!validate()) {
      e.preventDefault();
      return;
    }
    send.href = smsHref();
    status.textContent = "Opening your messages app…";
  });

  copyBtn.addEventListener("click", function () {
    if (!validate()) return;
    var text = message();
    var done = function () {
      status.textContent = "Copied. Text it to 020 4064 1495.";
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, fallback);
    } else {
      fallback();
    }
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.className = "visually-hidden";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        done();
      } catch (err) {
        status.textContent = "Couldn't copy — please text 020 4064 1495.";
      }
      ta.remove();
    }
  });

  update();
})();
