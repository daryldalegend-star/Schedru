/* Schedru's window.

   One page, no framework. The server holds the truth (is there a key, is
   Google connected, what is being read); this file asks for it, decides which
   screen that means, and draws it. It hears about changes through a long poll.

   Text that came out of a screenshot is never treated as HTML: every node is
   built with createElement/textContent via h() below.
*/
(() => {
  "use strict";

  const TOKEN = document.querySelector('meta[name="schedru-token"]').content;
  const GUIDE_URL = "https://github.com/daryldalegend-star/Schedru/blob/HEAD/SETUP.md";
  const ANTHROPIC_KEYS_URL = "https://console.anthropic.com/settings/keys";
  const GOOGLE_CLOUD_URL = "https://console.cloud.google.com/apis/credentials";
  const CALENDAR_URL = "https://calendar.google.com/calendar/";

  // ---- building DOM ------------------------------------------------------

  function append(parent, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) child.forEach((c) => append(parent, c));
    else parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }

  function h(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (name === "class") node.className = value;
      else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
      else if (name === "value") node.value = value;
      else if (name === "checked" || name === "disabled" || name === "hidden" || name === "open") node[name] = Boolean(value);
      else node.setAttribute(name, value === true ? "" : value);
    }
    append(node, children);
    return node;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, ...children) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
    append(node, children);
    return node;
  }

  const ICONS = {
    capture: "M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2M9.5 12a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0",
    calendar: "M7 3v3M17 3v3M4 9.5h16M6 5h12a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z",
    repeat: "M17 2l3 3-3 3M4 11V9a4 4 0 0 1 4-4h12M7 22l-3-3 3-3M20 13v2a4 4 0 0 1-4 4H4",
    pin: "M12 21s-6.5-5.6-6.5-10.5a6.5 6.5 0 0 1 13 0C18.5 15.4 12 21 12 21zM9.8 10.5a2.2 2.2 0 1 0 4.4 0a2.2 2.2 0 1 0-4.4 0",
    note: "M5 6.5h14M5 12h14M5 17.5h9",
    check: "M5 12.5l4.5 4.5L19 7.5",
    alert: "M12 8.5v5M12 16.6v.4M10.3 3.9 2.6 17.2A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.8L13.7 3.9a2 2 0 0 0-3.4 0z",
    info: "M12 11v6M12 7.4v.4M3 12a9 9 0 1 0 18 0a9 9 0 1 0-18 0",
    upload: "M12 16V4m0 0-4 4m4-4 4 4M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3",
    external: "M14 5h5v5M19 5l-8 8M11 7H7a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2v-4",
    more: "M5 12h.01M12 12h.01M19 12h.01",
    back: "M15 6l-6 6 6 6",
    edit: "M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4",
    x: "M6 6l12 12M18 6 6 18",
  };

  function icon(name, extra) {
    const node = s("svg", { class: `icon ${extra || ""}`.trim(), viewBox: "0 0 24 24", "aria-hidden": "true" });
    node.append(s("path", { d: ICONS[name] }));
    if (name === "more") node.setAttribute("stroke-width", "3");
    return node;
  }

  // The mark: a tear-off calendar page with a screenshot's corner brackets on it.
  function logoMark(extra) {
    return s(
      "svg",
      { class: `mark ${extra || ""}`.trim(), viewBox: "0 0 64 64", "aria-hidden": "true" },
      s("rect", { x: 7, y: 11, width: 50, height: 46, rx: 11, fill: "#fffdf8", stroke: "#cfc5b1", "stroke-width": 1.5 }),
      s("path", { d: "M7 22a11 11 0 0 1 11-11h28a11 11 0 0 1 11 11v5H7z", fill: "#d5402b" }),
      s("rect", { x: 19.5, y: 5, width: 5.5, height: 13, rx: 2.75, fill: "#1d1b18", stroke: "#fffdf8", "stroke-width": 1.2 }),
      s("rect", { x: 39, y: 5, width: 5.5, height: 13, rx: 2.75, fill: "#1d1b18", stroke: "#fffdf8", "stroke-width": 1.2 }),
      s("path", {
        d: "M21 39v-4.5h4.5M43 39v-4.5h-4.5M21 45.5V50h4.5M43 45.5V50h-4.5",
        fill: "none", stroke: "#1d1b18", "stroke-width": 2.6, "stroke-linecap": "round", "stroke-linejoin": "round",
      }),
      s("circle", { cx: 32, cy: 42.2, r: 3.1, fill: "#d5402b" })
    );
  }

  function googleG() {
    return s(
      "svg",
      { class: "g", viewBox: "0 0 18 18", "aria-hidden": "true" },
      s("path", { fill: "#4285F4", d: "M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" }),
      s("path", { fill: "#34A853", d: "M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" }),
      s("path", { fill: "#FBBC05", d: "M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" }),
      s("path", { fill: "#EA4335", d: "M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" })
    );
  }

  function keys(labels) {
    return h("span", { class: "keys", "aria-label": labels.join(" ") }, labels.map((label) => h("span", { class: "kbd" }, label)));
  }

  function notice(text, kind, action) {
    return h(
      "div",
      { class: `notice ${kind === "info" ? "info" : ""}`.trim(), role: kind === "info" ? null : "alert" },
      icon(kind === "info" ? "info" : "alert"),
      h("div", { class: "selectable" }, text, action ? [" ", action] : null)
    );
  }

  // ---- talking to the app ------------------------------------------------

  class ApiFailure extends Error {
    constructor(status, error) {
      super((error && error.message) || "Something went wrong.");
      this.status = status;
      this.code = (error && error.code) || "unknown";
      this.detail = error || {};
    }
  }

  async function api(path, options) {
    const opts = options || {};
    const headers = { "X-Schedru-Token": TOKEN };
    let body = opts.body;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.json);
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/octet-stream";
    }
    const method = opts.method || (body !== undefined ? "POST" : "GET");
    const response = await fetch(path, { method, headers, body, cache: "no-store" });
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    if (!response.ok) throw new ApiFailure(response.status, payload && payload.error);
    return payload;
  }

  const post = (path, json) => api(path, { method: "POST", json: json || {} });

  function openExternal(url) {
    post("/api/open", { url }).catch((error) => toast(error.message));
  }

  // ---- state -------------------------------------------------------------

  const state = {
    status: null,
    seq: 0,
    resolved: false, // has Google's state ever been known?
    offline: false,
  };

  const ui = {
    googleSetup: false,
    welcomeError: null,
    welcomeBusy: false,
    credError: null,
    credBusy: false,
    keyError: null,
    keyBusy: false,
    wantKey: false,
    tutorialReplay: false,
    uploading: false,
    homeError: null,
    menuOpen: false,
    sheet: null, // "permission" | "lightbox"
    // review
    jobId: null,
    selected: {},
    edits: {},
    editing: null,
    fieldError: null, // {id, field, message}
    reviewing: false,
    adding: false,
    addError: null,
    rev: 0,
  };

  let currentRoute = null;
  let currentSignature = null;
  let readingTimer = null;

  function job() {
    return state.status && state.status.job;
  }

  function syncJob() {
    const current = job();
    const id = current ? current.id : null;
    if (id === ui.jobId) return;
    ui.jobId = id;
    ui.selected = {};
    ui.edits = {};
    ui.editing = null;
    ui.fieldError = null;
    ui.reviewing = false;
    ui.adding = false;
    ui.addError = null;
    ui.sheet = ui.sheet === "lightbox" ? null : ui.sheet;
  }

  function ensureSelection() {
    const current = job();
    if (!current) return;
    for (const event of current.events) {
      if (!(event.id in ui.selected)) ui.selected[event.id] = !event.needs_review;
    }
  }

  function route() {
    const status = state.status;
    if (!status) return "splash";
    const google = status.google;

    if (google.state === "checking") return state.resolved ? "welcome" : "splash";
    if (google.state === "signing_in") return "signing";
    if (google.state !== "connected") return ui.googleSetup ? "google-setup" : "welcome";

    if (ui.tutorialReplay) return "tutorial";
    if (ui.wantKey) return "key";

    const current = status.job;
    if (current) {
      if (current.state === "reading") return "reading";
      if (current.state === "failed") return "failed";
      if (current.state === "done" && !ui.reviewing) return "done";
      return "review";
    }
    if (!status.tutorial_seen) return "tutorial";
    if (!status.key.present) return "key";
    return "home";
  }

  function signature(name) {
    const status = state.status;
    if (!status) return "";
    const google = status.google;
    const current = status.job;
    switch (name) {
      case "welcome":
        return [google.state, google.message, google.has_credentials, ui.welcomeError, ui.welcomeBusy];
      case "google-setup":
        return [ui.credError, ui.credBusy];
      case "signing":
        return [google.browser_opened];
      case "tutorial":
        return [ui.tutorialReplay, status.tutorial_seen];
      case "key":
        return [ui.keyError, ui.keyBusy, status.key.present, status.key.hint, ui.wantKey];
      case "home":
        return [status.capture.available, status.capture.shortcut, status.capture.busy, ui.uploading, ui.homeError];
      case "reading":
      case "failed":
        return [current && current.id];
      case "review":
        return [current.id, current.state, current.events.map((e) => e.added), ui.rev, ui.adding, ui.addError];
      case "done":
        return [current.id, current.results, ui.rev];
      default:
        return [];
    }
  }

  // ---- drawing -----------------------------------------------------------

  const app = document.getElementById("app");
  const topbar = document.getElementById("topbar");
  const sheetRoot = document.getElementById("sheet-root");

  function render(force) {
    syncJob();
    ensureSelection();

    const name = route();
    const sig = JSON.stringify([name, signature(name)]);
    const routeChanged = name !== currentRoute;

    if (force || sig !== currentSignature) {
      if (readingTimer) {
        clearInterval(readingTimer);
        readingTimer = null;
      }
      const scroll = routeChanged ? 0 : app.scrollTop;
      const view = VIEWS[name]();
      if (!routeChanged) view.style.animation = "none";
      app.replaceChildren(view);
      app.scrollTop = scroll;
      currentRoute = name;
      currentSignature = sig;
      document.body.dataset.route = name;
      if (routeChanged) focusFirst();
    }
    renderTopbar(name);
    renderSheet();
  }

  function focusFirst() {
    const field = app.querySelector("input[data-autofocus]");
    (field || app).focus({ preventScroll: true });
  }

  function view(classes, ...children) {
    return h("section", { class: `view ${classes}` }, children);
  }

  // ---- top bar and menu --------------------------------------------------

  let topbarSignature = null;

  function renderTopbar(name) {
    const status = state.status;
    const connected = Boolean(status && status.google.state === "connected");
    const firstRun = name === "tutorial" && !ui.tutorialReplay;
    const show = connected && !firstRun && name !== "splash";
    const sig = JSON.stringify([show, connected && status.google.calendar, ui.menuOpen]);
    if (sig === topbarSignature) return;
    topbarSignature = sig;

    topbar.hidden = !show;
    if (!show) {
      topbar.replaceChildren();
      return;
    }

    const menuButton = h(
      "button",
      {
        class: "icon-btn",
        id: "menu-button",
        "aria-label": "Menu",
        "aria-haspopup": "menu",
        "aria-expanded": String(ui.menuOpen),
        onclick: (event) => {
          event.stopPropagation();
          ui.menuOpen = !ui.menuOpen;
          render();
        },
      },
      icon("more")
    );

    const item = (label, action) =>
      h("button", { role: "menuitem", onclick: () => { ui.menuOpen = false; action(); } }, label);

    const menu = ui.menuOpen
      ? h(
          "div",
          { class: "menu", role: "menu", id: "menu" },
          item("Watch the tutorial", () => { ui.tutorialReplay = true; render(); }),
          item("Change Claude key…", () => { ui.wantKey = true; ui.keyError = null; render(); }),
          h("hr"),
          item("Use a different Google account…", switchAccount),
          item("Sign out", signOut),
          h("hr"),
          h("div", { class: "menu-note" }, `Schedru ${status.app.version}${status.app.engine === "demo" ? " · demo" : ""}`)
        )
      : null;

    topbar.replaceChildren(
      h("div", { class: "brand" }, logoMark(), "Schedru"),
      h("div", { class: "topbar-space" }),
      h("div", { class: "account", title: "Events are added to this calendar" }, h("span", { class: "selectable" }, status.google.calendar || "Google Calendar")),
      h("div", { class: "menu-wrap" }, menuButton, menu)
    );
  }

  document.addEventListener("click", (event) => {
    if (ui.menuOpen && !event.target.closest(".menu-wrap")) {
      ui.menuOpen = false;
      render();
    }
  });

  async function signOut() {
    try {
      await post("/api/google/signout");
    } catch (error) {
      toast(error.message);
    }
    ui.googleSetup = false;
    await refresh();
  }

  async function switchAccount() {
    await signOut();
    await continueWithGoogle();
  }

  // ---- welcome -----------------------------------------------------------

  async function continueWithGoogle() {
    const google = state.status.google;
    ui.welcomeError = null;
    if (!google.has_credentials) {
      ui.googleSetup = true;
      ui.credError = null;
      render();
      return;
    }
    ui.welcomeBusy = true;
    render();
    try {
      await post("/api/google/signin");
    } catch (error) {
      if (error.code === "no_credentials") ui.googleSetup = true;
      else ui.welcomeError = error.message;
    }
    ui.welcomeBusy = false;
    await refresh();
  }

  const VIEWS = {};

  VIEWS.splash = () =>
    view(
      "welcome view-middle",
      logoMark("mark-hero"),
      h("h1", { class: "display wordmark" }, "Schedru"),
      h("p", { class: "tagline" }, "Your Schedule Optimizer"),
      h("div", { class: "cta" }, h("span", { class: "pulse", "aria-label": "Starting" }, h("i"), h("i"), h("i")))
    );

  VIEWS.welcome = () => {
    const google = state.status.google;
    const checking = google.state === "checking";
    const message = ui.welcomeError || google.message;

    let problem = null;
    if (google.state === "error") {
      problem = notice(
        google.message || "Couldn't reach Google.",
        "alert",
        h("button", { class: "link", onclick: retryGoogle }, "Try again")
      );
    } else if (message) {
      problem = notice(message, "alert");
    }

    return view(
      "welcome view-middle",
      logoMark("mark-hero"),
      h("h1", { class: "display wordmark" }, "Schedru"),
      h("p", { class: "tagline" }, "Your Schedule Optimizer"),
      h(
        "p",
        { class: "lede" },
        "Screenshot anything with a date on it. Schedru reads it and puts it on your Google Calendar."
      ),
      h(
        "div",
        { class: "cta" },
        h(
          "button",
          {
            class: "btn btn-google",
            id: "google-button",
            "data-autofocus": true,
            disabled: checking || ui.welcomeBusy,
            onclick: continueWithGoogle,
          },
          googleG(),
          "Continue with Google"
        ),
        h(
          "p",
          { class: "fine" },
          h("strong", null, "Sign in or sign up"),
          " with the same button. Your Google account is your Schedru account, so there's no new password to remember."
        )
      ),
      problem
    );
  };

  async function retryGoogle() {
    try {
      await post("/api/google/retry");
    } catch (error) {
      toast(error.message);
    }
    await refresh();
  }

  // ---- one-time Google setup --------------------------------------------

  async function sendCredentials(file) {
    if (!file) return;
    ui.credBusy = true;
    ui.credError = null;
    render();
    try {
      await api("/api/credentials", { method: "POST", body: file });
      ui.googleSetup = false;
      ui.credBusy = false;
      await refresh();
      await continueWithGoogle();
      return;
    } catch (error) {
      ui.credError = error.message;
    }
    ui.credBusy = false;
    render();
  }

  VIEWS["google-setup"] = () =>
    view(
      "",
      h(
        "button",
        { class: "btn btn-quiet back", onclick: () => { ui.googleSetup = false; render(); } },
        icon("back"),
        "Back"
      ),
      h("p", { class: "eyebrow" }, "One-time setup"),
      h("h1", { class: "display title" }, "Connect Schedru to Google"),
      h(
        "p",
        { class: "lede" },
        "Google gives every app that touches a calendar a small credentials file. You made one when you first set this up. Hand it to Schedru once and you won't be asked again."
      ),
      h(
        "div",
        {
          class: "dropzone big",
          id: "cred-drop",
          style: "margin-top:24px",
          tabindex: "0",
          role: "button",
          "data-autofocus": true,
          onclick: () => document.getElementById("file-json").click(),
          onkeydown: (event) => {
            if (event.key === "Enter" || event.key === " ") document.getElementById("file-json").click();
          },
        },
        icon("upload", "icon-xl"),
        h("strong", null, ui.credBusy ? "Checking the file…" : "Drop your credentials file here"),
        h("span", null, "It's named credentials.json, or starts with client_secret_"),
        h("span", { class: "btn", style: "margin-top:10px" }, "Choose the file…")
      ),
      h("div", { class: "field-error selectable", role: "alert" }, ui.credError),
      h(
        "details",
        { class: "help" },
        h("summary", null, "Where do I find it?"),
        h(
          "p",
          null,
          "Look in the folder you ran the app from before (the one with start.command in it) for credentials.json, or in Downloads for a file that starts with client_secret_."
        )
      ),
      h(
        "details",
        { class: "help" },
        h("summary", null, "I never made one"),
        h(
          "p",
          null,
          "It takes about ten minutes, once. The guide walks through every click: create a Google Cloud project, switch on the Calendar API, add yourself as a test user, and download the file for a \"Desktop app\"."
        ),
        h(
          "p",
          { class: "row", style: "margin-top:10px" },
          h("button", { class: "btn", onclick: () => openExternal(GUIDE_URL) }, "Open the step-by-step guide", icon("external", "icon-sm")),
          h("button", { class: "btn btn-quiet", onclick: () => openExternal(GOOGLE_CLOUD_URL) }, "Open Google Cloud", icon("external", "icon-sm"))
        )
      )
    );

  // ---- waiting for the browser sign-in -----------------------------------

  VIEWS.signing = () => {
    const opened = state.status.google.browser_opened;
    return view(
      "view-middle",
      h("span", { class: "pulse", style: "margin-bottom:22px" }, h("i"), h("i"), h("i")),
      h("h1", { class: "display title" }, "Finish signing in with Google"),
      h(
        "p",
        { class: "lede" },
        opened
          ? "Your browser just opened Google's sign-in page. Schedru will carry on by itself as soon as you're done there."
          : "Your browser didn't open by itself. Use the button below to open Google's sign-in page."
      ),
      h(
        "div",
        { class: "card card-pad", style: "margin-top:26px; max-width:560px; text-align:left" },
        h(
          "ol",
          { class: "steps", style: "margin-top:0" },
          h("li", null, h("div", { class: "step-body" }, h("span", null, h("strong", null, "Pick your Google account."), " Use the one whose calendar you want events on."))),
          h(
            "li",
            null,
            h(
              "div",
              { class: "step-body" },
              h("span", null, h("strong", null, "Google will say it hasn't verified this app."), " That's expected: this is your own private copy. Press ", h("strong", null, "Continue"), ".")
            )
          ),
          h("li", null, h("div", { class: "step-body" }, h("span", null, h("strong", null, "Tick the Google Calendar box"), " if Google shows one, then press ", h("strong", null, "Continue"), " again.")))
        )
      ),
      h(
        "div",
        { class: "row row-center", style: "margin-top:24px" },
        h("button", { class: "btn", id: "reopen-google", onclick: () => post("/api/google/signin").catch((e) => toast(e.message)) }, "Open Google's page again"),
        h("button", { class: "btn btn-quiet", id: "cancel-signin", onclick: () => post("/api/google/cancel").then(refresh, refresh) }, "Cancel")
      ),
      h(
        "details",
        { class: "help", style: "max-width:560px" },
        h("summary", null, "Google says \"Access blocked\""),
        h(
          "p",
          null,
          "The account you picked isn't on your app's test-user list. In Google Cloud, open Google Auth Platform, then Audience, and add that Gmail address under Test users. Then come back and try again."
        )
      )
    );
  };

  // ---- tutorial ----------------------------------------------------------

  async function finishTutorial() {
    const video = document.querySelector(".player video");
    if (video) video.pause();
    const replay = ui.tutorialReplay;
    ui.tutorialReplay = false;
    if (!state.status.tutorial_seen) {
      try {
        await post("/api/tutorial/seen");
      } catch (error) {
        toast(error.message);
      }
    }
    if (replay && state.status.tutorial_seen) render();
    await refresh();
  }

  VIEWS.tutorial = () => {
    const first = !ui.tutorialReplay;
    const primary = h(
      "button",
      { class: "btn btn-primary btn-lg", id: "tutorial-done", onclick: finishTutorial },
      first ? "Get started" : "Done"
    );

    const video = h("video", {
      src: "/media/tutorial.mp4",
      poster: "/media/tutorial-poster.jpg",
      controls: true,
      playsinline: true,
      preload: "auto",
      "aria-label": "A short video showing how to use Schedru",
    });
    video.muted = true;
    video.autoplay = true;
    const player = h("div", { class: "player" }, video);
    video.addEventListener("error", () => { player.hidden = true; });
    video.addEventListener("ended", () => primary.focus());

    return view(
      "view-wide tutorial",
      h(
        "div",
        { class: "tutorial-head" },
        h("div", null, h("p", { class: "eyebrow" }, first ? "Welcome" : "Tutorial"), h("h1", { class: "display title" }, "How Schedru works")),
        h("div", { class: "spacer" }),
        first ? h("button", { class: "btn btn-quiet", id: "tutorial-skip", onclick: finishTutorial }, "Skip") : null
      ),
      player,
      h(
        "ol",
        { class: "recap" },
        h("li", null, h("b", null, "1. Capture"), "Press ⌘⇧2 and drag over anything with a date on it."),
        h("li", null, h("b", null, "2. Check"), "Schedru fills in the time, place and details. Fix anything it got wrong."),
        h("li", null, h("b", null, "3. Add"), "One press and it's on your Google Calendar.")
      ),
      h("div", { class: "row" }, h("div", { class: "spacer" }), primary)
    );
  };

  // ---- Claude key --------------------------------------------------------

  async function saveKey() {
    const input = document.getElementById("key-input");
    if (!input) return;
    ui.keyBusy = true;
    ui.keyError = null;
    const value = input.value;
    render();
    try {
      const result = await post("/api/key", { key: value });
      ui.wantKey = false;
      if (result.note) toast(result.note);
      else toast("Claude key saved");
    } catch (error) {
      ui.keyError = error.message;
    }
    ui.keyBusy = false;
    await refresh();
    const again = document.getElementById("key-input");
    if (again && ui.keyError) {
      again.value = value;
      again.focus();
    }
  }

  VIEWS.key = () => {
    const present = state.status.key.present;
    const input = h("input", {
      class: `input mono ${ui.keyError ? "bad" : ""}`.trim(),
      id: "key-input",
      // Shown, not dotted out: the usual mistake is pasting the shortened
      // preview of a key, and that should be visible at a glance.
      type: "text",
      placeholder: "sk-ant-…",
      autocomplete: "off",
      autocapitalize: "off",
      spellcheck: "false",
      "aria-label": "Your Claude key",
      "data-autofocus": true,
      disabled: ui.keyBusy,
      onkeydown: (event) => { if (event.key === "Enter") saveKey(); },
    });

    return view(
      "",
      present
        ? h("button", { class: "btn btn-quiet back", onclick: () => { ui.wantKey = false; ui.keyError = null; render(); } }, icon("back"), "Back")
        : null,
      h("p", { class: "eyebrow" }, present ? "Settings" : "One last thing"),
      h("h1", { class: "display title" }, present ? "Change your Claude key" : "Add your Claude key"),
      h(
        "p",
        { class: "lede" },
        "Schedru uses Claude, made by Anthropic, to read your screenshots. You pay Anthropic directly for what you use: about 1 to 2 cents a screenshot, with no subscription."
      ),
      h(
        "ol",
        { class: "steps" },
        h(
          "li",
          null,
          h(
            "div",
            { class: "step-body" },
            h("span", null, h("strong", null, "Open Anthropic's console"), " and sign in. A new account is asked to add credit first; the $5 minimum covers hundreds of screenshots."),
            h("div", null, h("button", { class: "btn", id: "open-console", onclick: () => openExternal(ANTHROPIC_KEYS_URL) }, "Open console.anthropic.com", icon("external", "icon-sm")))
          )
        ),
        h("li", null, h("div", { class: "step-body" }, h("span", null, "Press ", h("strong", null, "Create Key"), ", then ", h("strong", null, "Copy"), ". The whole key is shown only this once."))),
        h(
          "li",
          null,
          h(
            "div",
            { class: "step-body" },
            h("span", null, h("strong", null, "Paste it here.")),
            h(
              "div",
              { class: "row", style: "flex-wrap:nowrap" },
              input,
              h("button", { class: "btn btn-primary", id: "key-save", disabled: ui.keyBusy, onclick: saveKey }, ui.keyBusy ? "Checking…" : "Save key")
            ),
            h("div", { class: "field-error selectable", role: "alert", id: "key-error" }, ui.keyError)
          )
        )
      ),
      h(
        "p",
        { class: "muted", style: "margin-top:18px; font-size:13.5px" },
        present ? `Current key: ${state.status.key.hint}. ` : "",
        "The key is saved on this computer only, and is sent to no one but Anthropic."
      )
    );
  };

  // ---- home --------------------------------------------------------------

  async function startCapture(force) {
    ui.homeError = null;
    try {
      const result = await post("/api/capture", { force: Boolean(force) });
      if (!result.started && result.reason === "permission") ui.sheet = "permission";
    } catch (error) {
      ui.homeError = error.message;
    }
    render();
  }

  async function upload(file) {
    if (!file) return;
    ui.homeError = null;
    ui.uploading = true;
    render();
    try {
      await api("/api/read", { method: "POST", body: file });
    } catch (error) {
      if (route() === "home") ui.homeError = error.message;
      else toast(error.message);
    }
    ui.uploading = false;
    await refresh();
  }

  function chooseImage() {
    document.getElementById("file-image").click();
  }

  VIEWS.home = () => {
    const capture = state.status.capture;
    const shortcut = capture.shortcut ? ["⌘", "⇧", "2"] : null;

    const captureCard = capture.available
      ? h(
          "div",
          { class: "card capture-card" },
          h(
            "button",
            { class: "btn btn-primary btn-xl capture-btn", id: "capture-button", "data-autofocus": true, disabled: capture.busy, onclick: () => startCapture(false) },
            icon("capture", "icon-lg"),
            capture.busy ? "Capturing…" : "Capture from screen",
            shortcut && !capture.busy ? keys(shortcut) : null
          ),
          h(
            "p",
            { class: "capture-note" },
            "This window steps aside, you drag over what you want, and Schedru reads it.",
            shortcut ? " The shortcut works from any app while Schedru is open." : ""
          )
        )
      : null;

    const dropzone = h(
      "div",
      {
        class: `dropzone ${capture.available ? "" : "big"}`.trim(),
        id: "image-drop",
        tabindex: "0",
        role: "button",
        "data-autofocus": capture.available ? null : true,
        onclick: chooseImage,
        onkeydown: (event) => { if (event.key === "Enter" || event.key === " ") chooseImage(); },
      },
      icon("upload", "icon-xl"),
      h("strong", null, ui.uploading ? "Sending it over…" : "Drop a screenshot here"),
      h("span", { class: "row row-center", style: "gap:6px" }, "or paste one with", keys(["⌘", "V"])),
      h("span", { class: "btn", style: "margin-top:10px" }, "Choose a file…")
    );

    return view(
      "home",
      h(
        "div",
        null,
        h("h1", { class: "display title" }, "What's coming up?"),
        h("p", { class: "lede" }, "Capture anything with a date on it: a flyer, a syllabus, an email, a group chat.")
      ),
      captureCard,
      capture.available ? h("div", { class: "or" }, "or") : null,
      dropzone,
      ui.homeError ? notice(ui.homeError, "alert") : null
    );
  };

  // ---- reading -----------------------------------------------------------

  const READING_LINES = [
    "Reading your screenshot…",
    "Finding the dates and times…",
    "Working out where it is…",
    "Filling in the details…",
    "Nearly there…",
  ];

  function imageUrl(current) {
    return `${current.image}?t=${encodeURIComponent(TOKEN)}`;
  }

  VIEWS.reading = () => {
    const current = job();
    const line = h("p", { class: "reading-status", id: "reading-status", "aria-live": "polite" }, READING_LINES[0]);
    let index = 0;
    readingTimer = setInterval(() => {
      index = Math.min(index + 1, READING_LINES.length - 1);
      line.textContent = READING_LINES[index];
    }, 3200);

    return view(
      "reading",
      h("div", { class: "scan" }, h("img", { src: imageUrl(current), alt: "Your screenshot" })),
      h("div", null, h("h1", { class: "display title", style: "font-size:28px" }, "One moment"), line),
      h("button", { class: "btn btn-quiet", id: "reading-cancel", onclick: () => clearJob() }, "Cancel")
    );
  };

  async function clearJob() {
    const current = job();
    if (current) {
      try {
        await post(`/api/job/${current.id}/clear`);
      } catch (error) {
        toast(error.message);
      }
    }
    await refresh();
  }

  VIEWS.failed = () => {
    const current = job();
    const error = current.error || {};
    const needsKey = error.kind === "auth" || error.kind === "permission";
    return view(
      "view-middle",
      h("div", { class: "tick warn", style: "margin-bottom:20px" }, s("svg", { viewBox: "0 0 24 24" }, s("path", { d: "M12 6v7M12 17.5v.5" }))),
      h("h1", { class: "display title" }, "That one didn't work"),
      h("p", { class: "lede selectable", id: "failed-message" }, error.message || "Something went wrong."),
      h(
        "div",
        { class: "row row-center", style: "margin-top:26px" },
        needsKey
          ? h("button", { class: "btn btn-primary btn-lg", "data-autofocus": true, onclick: async () => { ui.wantKey = true; await clearJob(); } }, "Update your Claude key")
          : h("button", { class: "btn btn-primary btn-lg", "data-autofocus": true, onclick: clearJob }, "Try another screenshot")
      )
    );
  };

  // ---- review ------------------------------------------------------------

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  function formatDate(iso) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
    if (!match) return iso || "No date";
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    let text = `${DAYS[date.getDay()]}, ${MONTHS[date.getMonth()]} ${date.getDate()}`;
    if (date.getFullYear() !== new Date().getFullYear()) text += `, ${date.getFullYear()}`;
    return text;
  }

  function formatTime(value) {
    const match = /^(\d{1,2}):(\d{2})/.exec(value || "");
    if (!match) return value || "";
    const hour = Number(match[1]);
    const suffix = hour < 12 ? "AM" : "PM";
    const shown = hour % 12 || 12;
    return match[2] === "00" ? `${shown} ${suffix}` : `${shown}:${match[2]} ${suffix}`;
  }

  function whenText(event) {
    const day = formatDate(event.start_date);
    if (!event.start_time) return `${day} · All day`;
    let start = formatTime(event.start_time);
    if (!event.end_time) return `${day} · ${start}`;
    const end = formatTime(event.end_time);
    if (start.slice(-2) === end.slice(-2)) start = start.slice(0, -3);
    return `${day} · ${start} – ${end}`;
  }

  function whereText(event) {
    const place = (event.location || "").trim();
    const address = (event.address || "").trim();
    if (place && address && place.toLowerCase().includes(address.toLowerCase())) return place;
    return [place, address].filter(Boolean).join(", ");
  }

  // The event as it stands after the person's edits.
  function current(event) {
    return Object.assign({}, event, ui.edits[event.id] || {});
  }

  function setEdit(id, field, value) {
    ui.edits[id] = Object.assign({}, ui.edits[id], { [field]: value });
    if (ui.fieldError && ui.fieldError.id === id && ui.fieldError.field === field) ui.fieldError = null;
    refreshSummary(id);
  }

  // Keep the card's summary in step with the editor without redrawing it,
  // which would throw the cursor out of the field being typed in.
  function refreshSummary(id) {
    const card = app.querySelector(`[data-event="${id}"]`);
    const event = job() && job().events.find((candidate) => candidate.id === id);
    if (!card || !event) return;
    const shown = current(event);
    const set = (selector, text) => {
      const node = card.querySelector(selector);
      if (node) node.textContent = text;
    };
    set(".event-title", shown.title || "Untitled");
    set('[data-fact="when"]', whenText(shown));
    set('[data-fact="where"]', whereText(shown));
    set('[data-fact="notes"]', shown.notes || "");
  }

  function bump() {
    ui.rev += 1;
    render();
  }

  function editor(event, shown) {
    const id = event.id;
    const bad = (field) => (ui.fieldError && ui.fieldError.id === id && ui.fieldError.field === field ? "bad" : "");
    const field = (label, name, type, extra) =>
      h(
        "label",
        { class: "label" },
        label,
        h(
          "input",
          Object.assign(
            {
              class: `input ${bad(name)}`.trim(),
              type: type || "text",
              value: shown[name] || "",
              "data-field": name,
              oninput: (e) => setEdit(id, name, e.target.value),
            },
            extra || {}
          )
        )
      );

    const allDay = !shown.start_time;
    return h(
      "div",
      { class: "editor", "data-editor": id },
      field("Name", "title", "text"),
      h(
        "div",
        { class: "grid-3" },
        field("Date", "start_date", "date"),
        field("Starts", "start_time", "time", { disabled: allDay }),
        field("Ends", "end_time", "time", { disabled: allDay })
      ),
      h(
        "label",
        { class: "switch" },
        h("input", {
          type: "checkbox",
          checked: allDay,
          "data-field": "all_day",
          onchange: (e) => {
            if (e.target.checked) {
              setEdit(id, "start_time", "");
              setEdit(id, "end_time", "");
            } else {
              setEdit(id, "start_time", event.start_time || "09:00");
              setEdit(id, "end_time", event.end_time || "");
            }
            bump();
          },
        }),
        "All day"
      ),
      field("Place", "location", "text", { placeholder: "Building and room" }),
      h(
        "label",
        { class: "label" },
        h("span", null, "Address ", h("small", null, "what Maps should look for")),
        h("input", { class: "input", type: "text", value: shown.address || "", "data-field": "address", oninput: (e) => setEdit(id, "address", e.target.value) })
      ),
      h(
        "label",
        { class: "label" },
        "Notes",
        h("textarea", { class: "textarea", "data-field": "notes", oninput: (e) => setEdit(id, "notes", e.target.value) }, shown.notes || "")
      ),
      event.repeats
        ? h(
            "div",
            { class: "repeat" },
            icon("repeat"),
            h("span", { style: "flex:1" }, shown.repeats === false ? "Won't repeat: one event on this date" : event.recurrence_text),
            h(
              "button",
              { class: "link", "data-action": "toggle-repeat", onclick: () => { setEdit(id, "repeats", shown.repeats === false); bump(); } },
              shown.repeats === false ? "Repeat again" : "Don't repeat"
            )
          )
        : null,
      ui.fieldError && ui.fieldError.id === id ? h("div", { class: "field-error", role: "alert" }, ui.fieldError.message) : null,
      h("div", { class: "row" }, h("div", { class: "spacer" }), h("button", { class: "btn", "data-action": "done-editing", onclick: () => { ui.editing = null; bump(); } }, "Done"))
    );
  }

  function eventCard(event, locked) {
    const shown = current(event);
    const added = event.added;
    const on = added || Boolean(ui.selected[event.id]);
    const editing = ui.editing === event.id && !added;
    const where = whereText(shown);
    const repeating = event.repeats && shown.repeats !== false;

    const checkbox = h("input", {
      class: "check",
      type: "checkbox",
      id: `pick-${event.id}`,
      checked: on,
      disabled: added || locked,
      "aria-label": `Add ${shown.title}`,
      onchange: (e) => { ui.selected[event.id] = e.target.checked; bump(); },
    });

    const classes = ["event", on ? "" : "off", event.needs_review && !added ? "flagged" : "", added ? "added" : ""].filter(Boolean).join(" ");

    return h(
      "article",
      { class: classes, "data-event": event.id },
      h("label", { style: "position:relative; display:block; height:26px" }, checkbox, h("span", { class: "check-box" }, icon("check"))),
      h(
        "div",
        { style: "min-width:0" },
        h(
          "div",
          { class: "event-top" },
          h("h3", { class: "event-title selectable" }, shown.title || "Untitled"),
          added ? h("span", { class: "badge ok" }, icon("check", "icon-sm"), "Added") : null,
          event.needs_review && !added ? h("span", { class: "badge flag" }, "Needs a look") : null,
          added || locked
            ? null
            : h(
                "button",
                { class: "btn btn-quiet", style: "height:30px; padding:0 10px", "data-action": "edit", onclick: () => { ui.editing = editing ? null : event.id; bump(); } },
                icon("edit", "icon-sm"),
                editing ? "Close" : "Edit"
              )
        ),
        h(
          "ul",
          { class: "facts selectable" },
          h("li", null, icon("calendar"), h("span", { "data-fact": "when" }, whenText(shown))),
          repeating ? h("li", null, icon("repeat"), h("span", { "data-fact": "repeat" }, event.recurrence_text)) : null,
          where ? h("li", null, icon("pin"), h("span", { "data-fact": "where" }, where)) : null,
          shown.notes ? h("li", { class: "notes" }, icon("note"), h("span", { "data-fact": "notes" }, shown.notes)) : null
        ),
        event.assumptions.length || event.ambiguity_flags.length
          ? h(
              "div",
              { class: "chips" },
              event.ambiguity_flags.map((text) => h("span", { class: "chip flag selectable" }, h("b", null, "!"), text)),
              event.assumptions.map((text) => h("span", { class: "chip guess selectable" }, h("b", null, "~"), text))
            )
          : null,
        editing ? editor(event, shown) : null
      )
    );
  }

  function chosenEvents() {
    const currentJob = job();
    return currentJob.events.filter((event) => !event.added && ui.selected[event.id]);
  }

  async function addChosen() {
    const currentJob = job();
    const chosen = chosenEvents();
    if (!chosen.length || ui.adding) return;

    ui.adding = true;
    ui.addError = null;
    ui.fieldError = null;
    render();
    try {
      await post(`/api/job/${currentJob.id}/add`, {
        events: chosen.map((event) => Object.assign({ id: event.id }, ui.edits[event.id] || {})),
      });
      ui.reviewing = false;
      ui.editing = null;
    } catch (error) {
      if (error.code === "invalid_event") {
        ui.fieldError = { id: error.detail.event_id, field: error.detail.field, message: error.message };
        ui.editing = error.detail.event_id;
      } else if (error.code !== "not_signed_in") {
        ui.addError = error.message;
      }
    }
    ui.adding = false;
    ui.rev += 1;
    await refresh();
  }

  VIEWS.review = () => {
    const currentJob = job();
    const events = currentJob.events;

    if (!events.length) {
      return view(
        "view-middle",
        h("h1", { class: "display title" }, "No dates in that one"),
        h(
          "p",
          { class: "lede" },
          "Schedru looks for things that happen at a time: meetings, deadlines, exams, events. Try capturing just the part that has the date on it."
        ),
        h("div", { class: "row row-center", style: "margin-top:26px" }, h("button", { class: "btn btn-primary btn-lg", "data-autofocus": true, onclick: clearJob }, "Try another screenshot"))
      );
    }

    const busy = ui.adding || currentJob.state === "adding";
    const count = chosenEvents().length;
    const pending = events.filter((event) => !event.added).length;
    const hasGuess = events.some((event) => event.assumptions.length);
    const hasFlag = events.some((event) => event.ambiguity_flags.length || event.needs_review);

    const heading = pending === events.length ? `Found ${events.length} ${events.length === 1 ? "event" : "events"}` : `${pending} left to add`;

    return view(
      "view-wide review",
      h(
        "aside",
        { class: "review-side" },
        h("button", { class: "thumb", "aria-label": "See the screenshot larger", onclick: () => { ui.sheet = "lightbox"; render(); } }, h("img", { src: imageUrl(currentJob), alt: "Your screenshot" })),
        h("p", { class: "hint" }, "Your screenshot. Click to enlarge.")
      ),
      h(
        "div",
        { class: "review-main" },
        h(
          "div",
          { class: "review-head" },
          h("h1", { class: "display title" }, heading),
          h("p", { class: "muted" }, "Look them over. Nothing goes on your calendar until you press Add.")
        ),
        hasGuess || hasFlag
          ? h(
              "div",
              { class: "legend" },
              hasGuess ? h("span", { class: "guess" }, h("b", null, "~"), "Schedru filled this in") : null,
              hasFlag ? h("span", { class: "flag" }, h("b", null, "!"), "Couldn't tell, so it's unticked") : null
            )
          : null,
        events.map((event) => eventCard(event, busy)),
        ui.addError ? notice(ui.addError, "alert") : null
      ),
      h(
        "div",
        { class: "actionbar" },
        h(
          "button",
          { class: "btn btn-primary btn-lg", id: "add-button", disabled: busy || count === 0, onclick: addChosen },
          busy ? "Adding…" : count === 0 ? "Tick an event to add it" : `Add ${count} ${count === 1 ? "event" : "events"} to Google Calendar`
        ),
        h("span", { class: "promise" }, state.status.google.calendar ? `Goes to ${state.status.google.calendar}` : ""),
        h("div", { class: "spacer" }),
        h("button", { class: "btn btn-quiet", id: "start-over", disabled: busy, onclick: clearJob }, currentJob.state === "done" ? "Finish" : "Start over")
      )
    );
  };

  // ---- done --------------------------------------------------------------

  VIEWS.done = () => {
    const currentJob = job();
    const results = currentJob.results;
    const failures = results.filter((result) => !result.ok);
    const successes = results.filter((result) => result.ok);
    const waiting = currentJob.events.filter((event) => !event.added && !failures.some((f) => f.id === event.id));
    const allFailed = successes.length === 0;

    const title = allFailed
      ? "Nothing was added"
      : failures.length
        ? `Added ${successes.length}, but ${failures.length} didn't go through`
        : successes.length === 1
          ? "It's on your calendar"
          : `${successes.length} events are on your calendar`;

    return view(
      "done",
      h(
        "div",
        { class: "done-head" },
        h(
          "div",
          { class: `tick ${allFailed ? "warn" : ""}`.trim() },
          s("svg", { viewBox: "0 0 24 24" }, s("path", { d: allFailed ? "M12 6v7M12 17.5v.5" : "M5 12.5l4.5 4.5L19 7.5" }))
        ),
        h(
          "div",
          null,
          h("h1", { class: "display title", style: "margin:0", id: "done-title" }, title),
          state.status.google.calendar ? h("p", { class: "muted selectable" }, state.status.google.calendar) : null
        )
      ),
      h(
        "div",
        { class: "card" },
        h(
          "ul",
          { class: "results" },
          results.map((result) =>
            h(
              "li",
              { "data-result": result.id },
              result.ok ? icon("check", "ok-mark") : icon("alert", "bad-mark"),
              h(
                "div",
                { class: "what selectable" },
                h("strong", null, result.title),
                result.ok ? h("span", null, result.when_text) : h("span", { class: "why" }, `Google said: ${result.error}`)
              ),
              result.ok && result.link
                ? h("button", { class: "btn btn-quiet", "data-action": "open-event", onclick: () => openExternal(result.link) }, "Open", icon("external", "icon-sm"))
                : null
            )
          )
        )
      ),
      failures.length || waiting.length
        ? notice(
            failures.length
              ? "The ones that failed are still here, with your edits."
              : `${waiting.length} other ${waiting.length === 1 ? "event" : "events"} from this screenshot ${waiting.length === 1 ? "wasn't" : "weren't"} added.`,
            "info",
            h(
              "button",
              {
                class: "link",
                id: "review-rest",
                onclick: () => {
                  for (const failure of failures) ui.selected[failure.id] = true;
                  ui.reviewing = true;
                  bump();
                },
              },
              failures.length ? "Try them again" : "Review them"
            )
          )
        : null,
      h(
        "div",
        { class: "row" },
        h("button", { class: "btn btn-primary btn-lg", id: "capture-another", "data-autofocus": true, onclick: clearJob }, "Capture another"),
        h("button", { class: "btn btn-lg", id: "open-calendar", onclick: () => openExternal(CALENDAR_URL) }, "Open Google Calendar", icon("external", "icon-sm"))
      )
    );
  };

  // ---- sheets ------------------------------------------------------------

  let sheetSignature = null;

  function closeSheet() {
    ui.sheet = null;
    render();
  }

  function renderSheet() {
    const sig = JSON.stringify([ui.sheet, ui.sheet === "lightbox" ? ui.jobId : null]);
    if (sig === sheetSignature) return;
    sheetSignature = sig;

    if (ui.sheet === "lightbox" && job()) {
      sheetRoot.replaceChildren(
        h("div", { class: "scrim lightbox", id: "lightbox", onclick: closeSheet }, h("img", { src: imageUrl(job()), alt: "Your screenshot, enlarged" }))
      );
      return;
    }

    if (ui.sheet === "permission") {
      sheetRoot.replaceChildren(
        h(
          "div",
          { class: "scrim", id: "permission-sheet", onclick: (event) => { if (event.target === event.currentTarget) closeSheet(); } },
          h(
            "div",
            { class: "sheet", role: "dialog", "aria-modal": "true", "aria-labelledby": "permission-title" },
            h("h2", { class: "display", id: "permission-title" }, "macOS needs your OK first"),
            h(
              "p",
              { class: "muted" },
              "To take a screenshot for you, Schedru needs the Mac's Screen Recording permission. It only ever looks at the area you drag over, and only when you ask."
            ),
            h(
              "ol",
              { class: "steps" },
              h("li", null, h("div", { class: "step-body" }, h("span", null, "Press ", h("strong", null, "Open System Settings"), " below."))),
              h("li", null, h("div", { class: "step-body" }, h("span", null, "Switch on ", h("strong", null, "Schedru"), " in the list. (Run from a terminal? Switch on Terminal instead.)"))),
              h("li", null, h("div", { class: "step-body" }, h("span", null, h("strong", null, "Quit Schedru"), " with ", keys(["⌘", "Q"]), " and open it again.")))
            ),
            h(
              "p",
              { class: "muted", style: "margin-top:16px; font-size:13.5px" },
              "Until then you can still paste: press ",
              keys(["⌃", "⇧", "⌘", "4"]),
              " to copy part of the screen, then ",
              keys(["⌘", "V"]),
              " here."
            ),
            h(
              "div",
              { class: "actions" },
              h("button", { class: "btn btn-quiet", id: "permission-later", onclick: closeSheet }, "Not now"),
              h("button", { class: "btn", id: "permission-try", onclick: () => { ui.sheet = null; startCapture(true); } }, "Try anyway"),
              h("button", { class: "btn btn-primary", id: "permission-open", onclick: () => post("/api/capture/settings").catch((e) => toast(e.message)) }, "Open System Settings")
            )
          )
        )
      );
      const primary = document.getElementById("permission-open");
      if (primary) primary.focus();
      return;
    }

    sheetRoot.replaceChildren();
  }

  // ---- toasts ------------------------------------------------------------

  const toasts = document.getElementById("toasts");

  function toast(message) {
    if (!message) return;
    const node = h("div", { class: "toast" }, message);
    toasts.append(node);
    setTimeout(() => {
      node.classList.add("out");
      setTimeout(() => node.remove(), 300);
    }, 3800);
  }

  // ---- files: choose, drop, paste ---------------------------------------

  function acceptsImages() {
    const status = state.status;
    if (!status || status.google.state !== "connected" || !status.key.present) return false;
    if (ui.adding) return false;
    return ["home", "review", "done", "failed"].includes(currentRoute);
  }

  document.getElementById("file-image").addEventListener("change", (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    upload(file);
  });

  document.getElementById("file-json").addEventListener("change", (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    sendCredentials(file);
  });

  const veil = document.getElementById("drop-veil");
  const veilText = document.getElementById("drop-veil-text");
  let dragDepth = 0;

  function carriesFiles(event) {
    return event.dataTransfer && Array.from(event.dataTransfer.types || []).includes("Files");
  }

  window.addEventListener("dragenter", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    dragDepth += 1;
    const forCredentials = currentRoute === "google-setup";
    if (forCredentials || acceptsImages()) {
      veilText.textContent = forCredentials ? "Drop the credentials file" : "Drop it to read it";
      veil.hidden = false;
    }
  });

  window.addEventListener("dragover", (event) => {
    if (carriesFiles(event)) event.preventDefault(); // otherwise the window navigates to the file
  });

  window.addEventListener("dragleave", (event) => {
    if (!carriesFiles(event)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) veil.hidden = true;
  });

  window.addEventListener("drop", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    veil.hidden = true;
    const file = event.dataTransfer.files[0];
    if (!file) return;
    if (currentRoute === "google-setup") sendCredentials(file);
    else if (acceptsImages()) upload(file);
    else toast("Finish setting up first, then drop a screenshot.");
  });

  document.addEventListener("paste", (event) => {
    const items = event.clipboardData ? Array.from(event.clipboardData.items || []) : [];
    const image = items.find((item) => item.kind === "file" && item.type.startsWith("image/"));
    if (!image) return; // ordinary text paste: leave it alone
    event.preventDefault();
    if (!acceptsImages()) {
      toast("Finish setting up first, then paste a screenshot.");
      return;
    }
    upload(image.getAsFile());
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (ui.sheet) closeSheet();
    else if (ui.menuOpen) {
      ui.menuOpen = false;
      render();
    }
  });

  // ---- keeping up with the app ------------------------------------------

  async function refresh() {
    try {
      state.status = await api("/api/status");
    } catch (error) {
      if (error instanceof ApiFailure && error.code === "bad_token") {
        location.reload(); // the app restarted; this page is from the old one
        return;
      }
      throw error;
    }
    if (state.status.google.state !== "checking") state.resolved = true;
    if (state.status.google.state === "connected") {
      ui.googleSetup = false;
      ui.welcomeError = null;
    }
    render();
  }

  function onNote(note) {
    if (note.kind !== "capture") return;
    if (note.outcome === "permission") ui.sheet = "permission";
    else if (note.outcome === "cancelled") toast("Capture cancelled");
    else if (note.outcome === "setup") toast("Finish setting up, then capture.");
    else if (note.outcome === "failed") toast(note.message || "That capture couldn't be read.");
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function listen() {
    let failures = 0;
    for (;;) {
      try {
        const result = await api(`/api/events?since=${state.seq}`);
        if (failures) {
          failures = 0;
          setOffline(false);
        }
        state.seq = result.seq;
        if (result.reset) {
          await refresh();
          continue;
        }
        result.events.forEach(onNote);
        if (result.events.length) await refresh();
      } catch (error) {
        failures += 1;
        if (failures >= 3) setOffline(true);
        await sleep(Math.min(4000, 400 * failures));
      }
    }
  }

  function setOffline(offline) {
    if (state.offline === offline) return;
    state.offline = offline;
    let banner = document.getElementById("offline");
    if (offline && !banner) {
      banner = h(
        "div",
        { class: "scrim", id: "offline" },
        h(
          "div",
          { class: "sheet center" },
          h("h2", { class: "display" }, "Schedru isn't running"),
          h("p", { class: "muted" }, "This page belongs to the Schedru app, which has been closed. Open Schedru again to carry on.")
        )
      );
      document.body.append(banner);
    } else if (!offline && banner) {
      banner.remove();
      refresh().catch(() => {});
    }
  }

  window.addEventListener("focus", () => {
    refresh().catch(() => {});
  });

  // For the automated end-to-end tests, which need to see what's on screen.
  window.__schedru = {
    route: () => currentRoute,
    status: () => state.status,
    ui,
  };

  render();
  refresh()
    .then(() => {
      state.seq = state.status.seq;
      listen();
    })
    .catch(() => {
      setOffline(true);
      listen();
    });
})();
