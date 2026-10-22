(function () {
  "use strict";

  var form = document.getElementById("inspect-form");
  var input = document.getElementById("url-input");
  var pasteBtn = document.getElementById("paste-btn");
  var clearBtn = document.getElementById("clear-btn");
  var inspectBtn = document.getElementById("inspect-btn");
  var loading = document.getElementById("loading");
  var resultCard = document.getElementById("result-card");
  var errorCard = document.getElementById("error-card");
  var errorMessage = document.getElementById("error-message");
  var thumb = document.getElementById("result-thumb");
  var title = document.getElementById("result-title");
  var channel = document.getElementById("result-channel");
  var duration = document.getElementById("result-duration");
  var badge = document.getElementById("availability-badge");
  var embedBox = document.getElementById("embed-box");
  var embedFrame = document.getElementById("embed-frame");
  var copyBtn = document.getElementById("copy-btn");
  var copyFeedback = document.getElementById("copy-feedback");
  var themeToggle = document.getElementById("theme-toggle");

  var currentCanonicalUrl = "";

  var ERROR_COPY = {
    INVALID_URL: "That link doesn't contain a valid YouTube video ID. Check the link and try again.",
    UNSUPPORTED_HOST: "That isn't a YouTube link. This checker only accepts links from youtube.com and youtu.be.",
    MALFORMED_URL: "That doesn't look like a complete link. Paste the full URL, starting with https://.",
    RATE_LIMITED: "You're checking links very quickly — give it a few seconds and try again.",
    PROVIDER_TIMEOUT: "YouTube took too long to answer. Please try again in a moment.",
    PROVIDER_ERROR: "YouTube's service couldn't answer just now. Please try again in a moment."
  };

  function formatDuration(totalSeconds) {
    if (totalSeconds === null || totalSeconds === undefined) return "";
    var s = Math.floor(totalSeconds);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var sec = s % 60;
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    return h > 0 ? h + ":" + pad(m) + ":" + pad(sec) : m + ":" + pad(sec);
  }

  function showOnly(el) {
    [loading, resultCard, errorCard].forEach(function (node) {
      node.hidden = node !== el;
    });
  }

  function showError(code, serverMessage) {
    errorMessage.textContent = ERROR_COPY[code] || serverMessage || "Something went wrong. Please try again.";
    showOnly(errorCard);
    errorCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function showResult(data) {
    var video = data.video || {};
    currentCanonicalUrl = video.canonical_url || "";

    badge.textContent = data.status === "ok" ? "Available" : "Unavailable";
    badge.className = "badge " + (data.status === "ok" ? "badge-ok" : "badge-warn");

    if (video.thumbnail_url) {
      thumb.src = video.thumbnail_url;
      thumb.hidden = false;
    } else {
      thumb.hidden = true;
      thumb.removeAttribute("src");
    }

    title.textContent = video.title || "No details available for this video";
    channel.textContent = video.channel
      ? "by " + video.channel
      : (data.status === "ok" ? "" : "YouTube's official API returned no information for this link — the video may be private, removed, or restricted for API access (some music videos are).");
    var dur = formatDuration(video.duration_seconds);
    duration.textContent = dur ? "⏱ " + dur : "";

    if (data.status === "ok" && video.id) {
      embedFrame.src = "https://www.youtube-nocookie.com/embed/" + video.id;
      embedBox.hidden = false;
    } else {
      embedFrame.removeAttribute("src");
      embedBox.hidden = true;
    }

    copyFeedback.textContent = "";
    showOnly(resultCard);
    resultCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var url = input.value.trim();
    if (!url) {
      showError("MALFORMED_URL");
      input.focus();
      return;
    }
    inspectBtn.disabled = true;
    showOnly(loading);

    fetch("/api/v1/youtube/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url })
    })
      .then(function (resp) {
        return resp.json().then(function (data) { return { ok: resp.ok, data: data }; });
      })
      .then(function (result) {
        if (!result.ok) {
          showError(result.data.code, result.data.message);
        } else {
          showResult(result.data);
        }
      })
      .catch(function () {
        showError("PROVIDER_ERROR", "The checker could not be reached.");
      })
      .finally(function () {
        inspectBtn.disabled = false;
      });
  });

  pasteBtn.addEventListener("click", function () {
    if (navigator.clipboard && navigator.clipboard.readText) {
      navigator.clipboard.readText().then(function (text) {
        if (text) input.value = text;
        input.focus();
      }).catch(function () { input.focus(); });
    } else {
      input.focus();
    }
  });

  clearBtn.addEventListener("click", function () {
    input.value = "";
    [loading, resultCard, errorCard].forEach(function (node) { node.hidden = true; });
    input.focus();
  });

  copyBtn.addEventListener("click", function () {
    if (!currentCanonicalUrl) return;
    function done() { copyFeedback.textContent = "Copied ✓"; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(currentCanonicalUrl).then(done).catch(function () {
        copyFeedback.textContent = currentCanonicalUrl;
      });
    } else {
      copyFeedback.textContent = currentCanonicalUrl;
    }
  });

  // Theme: honour saved choice, else OS preference; CSS handles the default.
  function applyTheme(theme) {
    if (theme) {
      document.documentElement.setAttribute("data-theme", theme);
      themeToggle.textContent = theme === "dark" ? "☀️ Light" : "🌙 Dark";
    }
  }
  var savedTheme = null;
  try { savedTheme = window.localStorage.getItem("yi-theme"); } catch (e) { /* storage unavailable */ }
  applyTheme(savedTheme);
  themeToggle.addEventListener("click", function () {
    var current = document.documentElement.getAttribute("data-theme");
    if (!current) {
      current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    var next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    try { window.localStorage.setItem("yi-theme", next); } catch (e) { /* ignore */ }
  });
})();
