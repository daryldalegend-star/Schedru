// Walk the whole UI in a real browser and photograph every screen.
//
//   node tools/ui_shots.mjs [output-folder]
//
// Starts the app with its demo engine (no network, no accounts) in a throwaway
// data folder, drives it the way a person would, and saves one PNG per screen
// in light and dark. It also checks what each screen says along the way, so it
// fails loudly if a flow breaks. This is how the interface gets looked at
// without a Mac: the same pages run in the app's window there.

import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = resolve(process.argv[2] || join(ROOT, "ui-shots"));
const SCALE = Number(process.env.SHOT_SCALE || 1);
const PYTHON = process.env.PYTHON || (existsSync(join(ROOT, ".venv/bin/python")) ? join(ROOT, ".venv/bin/python") : "python3");

const GOOD_KEY = "sk-ant-api03-" + "A1b2C3d4".repeat(10) + "-hAAA";
const CLIENT_FILE = JSON.stringify({
  installed: {
    client_id: "1234-abc.apps.googleusercontent.com",
    auth_uri: "https://accounts.google.com/o/oauth2/auth",
    token_uri: "https://oauth2.googleapis.com/token",
    client_secret: "not-a-real-secret",
  },
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const problems = [];
function check(condition, message) {
  if (!condition) {
    problems.push(message);
    console.log("  FAIL  " + message);
  }
}

async function startApp(work) {
  const data = join(work, "data");
  mkdirSync(data, { recursive: true });
  const hook = join(work, "hook.json");
  const child = spawn(PYTHON, ["-W", "ignore", "-m", "syllabus_cal.app", "--browser", "--no-open"], {
    cwd: ROOT,
    env: {
      ...process.env,
      SCHEDRU_DATA_DIR: data,
      SCHEDRU_ENGINE: "demo",
      SCHEDRU_DEBUG_HOOKS: "1",
      SCHEDRU_TEST_HOOK: hook,
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  child.stdout.on("data", (d) => (log += d));
  child.stderr.on("data", (d) => (log += d));
  for (let i = 0; i < 150 && !existsSync(hook); i++) await sleep(100);
  if (!existsSync(hook)) throw new Error("the app didn't start:\n" + log);
  const info = JSON.parse(readFileSync(hook, "utf8"));
  const call = async (path, body) => {
    const response = await fetch(info.url + path.replace(/^\//, ""), {
      method: body === undefined ? "GET" : "POST",
      headers: { "X-Schedru-Token": info.token, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return response.json();
  };
  return { ...info, child, call, data, stop: () => child.kill("SIGINT") };
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  const work = mkdtempSync(join(tmpdir(), "schedru-ui-"));
  const flyer = join(work, "flyer.png");
  const wrong = join(work, "wrong.json");
  const creds = join(work, "credentials.json");
  writeFileSync(wrong, JSON.stringify({ web: {} }));
  writeFileSync(creds, CLIENT_FILE);

  const browser = await chromium.launch();
  const app = await startApp(work);
  console.log("app at " + app.url);

  try {
    // A stand-in screenshot: any real image does, the demo engine ignores it.
    const flyerSource = join(ROOT, "tests/fixtures/screens/flyer.png");
    if (existsSync(flyerSource)) writeFileSync(flyer, readFileSync(flyerSource));
    else {
      const page = await browser.newPage({ viewport: { width: 520, height: 640 } });
      await page.setContent(`<body style="margin:0;background:#c8102e;color:#fff;font:700 44px system-ui;display:grid;place-items:center;height:100vh;text-align:center">
        <div>Scarlet Robotics Club<br><span style="font-size:26px;font-weight:500">Kickoff Meeting · Thu 7:30 · BSC 120 C<br>Free pizza</span></div></body>`);
      await page.screenshot({ path: flyer });
      await page.close();
    }

    for (const scheme of ["light", "dark"]) {
      // Each colour scheme starts from a clean install.
      await app.call("/api/google/signout", {});
      for (const name of ["credentials.json", "token.json", ".env", "settings.json"]) rmSync(join(app.data, name), { force: true });
      await app.call("/api/debug/demo", { scenario: "default", delay: 0.4 });
      await app.call("/api/google/retry", {});

      const context = await browser.newContext({
        viewport: { width: 1040, height: 760 },
        deviceScaleFactor: SCALE,
        colorScheme: scheme,
      });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      // Refused requests (a wrong file, a bad key) are part of the tour and the
      // browser logs each one; only real script errors count.
      page.on("console", (m) => {
        if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) errors.push(m.text());
      });

      const shot = async (name) => {
        await sleep(450); // let the entrance animation finish
        await page.screenshot({ path: join(OUT, `${scheme}-${name}.png`) });
        console.log(`  ${scheme}-${name}`);
      };
      const routeIs = (name) => page.waitForFunction((n) => window.__schedru && window.__schedru.route() === n, name, { timeout: 15000 });

      await page.goto(app.url);
      await routeIs("welcome");
      check((await page.textContent(".wordmark")) === "Schedru", "welcome shows the name");
      check((await page.textContent(".tagline")) === "Your Schedule Optimizer", "the tagline sits under the name");
      await shot("01-welcome");

      // No credentials file yet: the button leads to the one-time setup.
      await page.click("#google-button");
      await routeIs("google-setup");
      await shot("02-google-setup");
      await page.setInputFiles("#file-json", wrong);
      await page.waitForFunction(() => document.querySelector(".field-error").textContent.includes("Desktop app"));
      await shot("03-google-setup-wrong-file");

      // The right file goes straight on to Google's sign-in.
      await app.call("/api/debug/demo", { delay: 3 });
      await page.setInputFiles("#file-json", creds);
      await routeIs("signing");
      await shot("04-signing-in");
      await app.call("/api/google/cancel", {});
      await routeIs("welcome");
      await app.call("/api/debug/demo", { delay: 0.4 });
      await page.click("#google-button");
      await routeIs("tutorial");
      await shot("05-tutorial");

      await page.click("#tutorial-skip");
      await routeIs("key");
      await shot("06-key");
      await page.fill("#key-input", "sk-ant-api03-Ggj...hAAA");
      await page.click("#key-save");
      await page.waitForFunction(() => document.querySelector("#key-error").textContent.includes("shortened preview"));
      await shot("07-key-masked");
      await page.fill("#key-input", GOOD_KEY);
      await page.click("#key-save");
      await routeIs("home");
      check(await page.isVisible("#image-drop"), "home offers a place to drop a screenshot");
      await shot("08-home");

      await page.click("#menu-button");
      await page.waitForSelector("#menu");
      await shot("09-menu");
      await page.keyboard.press("Escape");

      // What home looks like on a Mac, where the screen can be captured. The
      // browser can't capture, so the page is told it can for one photograph.
      await page.route("**/api/status", async (route) => {
        const response = await route.fetch();
        const body = await response.json();
        body.capture = { available: true, shortcut: "⌘⇧2", busy: false };
        await route.fulfill({ response, json: body });
      });
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page.waitForSelector("#capture-button");
      check((await page.textContent("#capture-button")).includes("Capture from screen"), "home leads with the capture button on a Mac");
      await shot("09b-home-mac");
      await page.unroute("**/api/status");
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await page.waitForSelector("#capture-button", { state: "detached" });

      // Reading, slowed down so it can be photographed.
      await app.call("/api/debug/demo", { delay: 2.5 });
      await page.setInputFiles("#file-image", flyer);
      await routeIs("reading");
      await shot("10-reading");
      await routeIs("review");
      await app.call("/api/debug/demo", { delay: 0.3 });

      check((await page.textContent(".review-head .title")) === "Found 2 events", "review counts the events");
      check(await page.isChecked("#pick-e1"), "an event with only assumptions is ticked");
      check(!(await page.isChecked("#pick-e2")), "a flagged event is left unticked");
      check((await page.textContent("#add-button")) === "Add 1 event to Google Calendar", "the button says how many");
      check((await app.call("/api/status")).job.events.every((e) => !e.added), "nothing is added by reading");
      await shot("11-review");

      await page.click('[data-event="e1"] [data-action="edit"]');
      await page.fill('[data-editor="e1"] [data-field="title"]', "Robotics kickoff (bring laptop)");
      await page.fill('[data-editor="e1"] [data-field="start_time"]', "18:00");
      await page.fill('[data-editor="e1"] [data-field="end_time"]', "19:30");
      await shot("12-review-editing");
      await page.click('[data-editor="e1"] [data-action="done-editing"]');
      check((await page.textContent('[data-event="e1"] .event-title')) === "Robotics kickoff (bring laptop)", "an edit shows on the card");
      check((await page.textContent('[data-event="e1"] [data-fact="when"]')).endsWith("6 – 7:30 PM"), "the edited time shows on the card");

      await page.click("#add-button");
      await routeIs("done");
      check((await page.textContent("#done-title")) === "It's on your calendar", "done says so");
      const status = await app.call("/api/status");
      check(status.job.results.length === 1 && status.job.results[0].title === "Robotics kickoff (bring laptop)", "exactly the ticked, edited event was added");
      await shot("13-done");

      // Back for the flagged one, with Google failing it.
      await page.click("#review-rest");
      await routeIs("review");
      check((await page.textContent(".review-head .title")) === "1 left to add", "review shows what's left");
      await shot("14-review-rest");
      await page.check("#pick-e2", { force: true });
      await app.call("/api/debug/demo", { scenario: "add_partial" });
      // add_partial fails the second event of a batch, so a batch of one succeeds.
      await page.click("#add-button");
      await routeIs("done");
      await shot("15-done-both");
      await page.click("#capture-another");
      await routeIs("home");

      // The read finds nothing.
      await app.call("/api/debug/demo", { scenario: "empty" });
      await page.setInputFiles("#file-image", flyer);
      await page.waitForFunction(() => document.querySelector(".title") && document.querySelector(".title").textContent === "No dates in that one");
      await shot("16-no-events");
      await page.click(".btn-primary");
      await routeIs("home");

      // The read fails.
      await app.call("/api/debug/demo", { scenario: "read_fail" });
      await page.setInputFiles("#file-image", flyer);
      await routeIs("failed");
      check((await page.textContent("#failed-message")).includes("internet connection"), "a failed read says why");
      await shot("17-read-failed");
      await page.click(".btn-primary");
      await routeIs("home");

      // Something that isn't an image.
      await page.setInputFiles("#file-image", wrong);
      await page.waitForSelector(".home .notice");
      await shot("18-not-an-image");

      // Google rejects one of two.
      await app.call("/api/debug/demo", { scenario: "add_partial" });
      await page.setInputFiles("#file-image", flyer);
      await routeIs("review");
      await page.check("#pick-e2", { force: true });
      await page.click("#add-button");
      await routeIs("done");
      check((await page.textContent("#done-title")).startsWith("Added 1, but 1"), "a partial failure is spelled out");
      await shot("19-done-partial");
      await page.click("#capture-another");
      await routeIs("home");
      await app.call("/api/debug/demo", { scenario: "default" });

      // The permission sheet, which only a Mac can trigger for real.
      await page.evaluate(() => { window.__schedru.ui.sheet = "permission"; window.dispatchEvent(new Event("focus")); });
      await page.waitForSelector("#permission-sheet");
      await shot("20-permission-sheet");
      await page.click("#permission-later");

      // The smallest size the window allows.
      await page.setViewportSize({ width: 820, height: 620 });
      await shot("21-home-small");
      await page.setInputFiles("#file-image", flyer);
      await routeIs("review");
      await shot("22-review-small");
      await page.click("#start-over");
      await routeIs("home");
      await page.setViewportSize({ width: 1040, height: 760 });

      // Replay the tutorial, then change the key, from the menu.
      await page.click("#menu-button");
      await page.click('#menu button:has-text("Watch the tutorial")');
      await routeIs("tutorial");
      await shot("23-tutorial-replay");
      await page.click("#tutorial-done");
      await routeIs("home");
      await page.click("#menu-button");
      await page.click('#menu button:has-text("Change Claude key")');
      await routeIs("key");
      await shot("24-key-change");
      await page.click(".back");
      await routeIs("home");

      // Signing out returns to the welcome page.
      await page.click("#menu-button");
      await page.click('#menu button:has-text("Sign out")');
      await routeIs("welcome");

      check(errors.length === 0, `no script errors in ${scheme} (${errors.join(" | ")})`);
      await context.close();
    }
  } finally {
    app.stop();
    await browser.close();
    rmSync(work, { recursive: true, force: true });
  }

  if (problems.length) {
    console.log(`\n${problems.length} problem(s).`);
    process.exit(1);
  }
  console.log(`\nAll screens drawn and checked. Screenshots in ${OUT}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
