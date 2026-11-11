// Draw the sample screenshots used to try the real Claude API
// (tools/real_api_check.py): a club flyer, a syllabus, and an email.
//
//   node tools/make_samples.mjs
//
// They are made-up but shaped like the real thing, including the gaps a
// person fills in without noticing: a bare "7:30", "BSC 120 C", "this Monday".

import { createServer } from "node:http";
import { existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(ROOT, "syllabus_cal/app/web");
const TUTORIAL = join(ROOT, "tools/tutorial");
const OUT = join(ROOT, "tests/fixtures/screens");
const TYPES = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".woff2": "font/woff2" };

const server = createServer((request, response) => {
  const path = decodeURIComponent(new URL(request.url, "http://x").pathname);
  const [base, rest] = path.startsWith("/_tutorial/") ? [TUTORIAL, path.slice(11)] : [WEB, path.slice(1)];
  const file = normalize(join(base, rest));
  if (!file.startsWith(base) || !existsSync(file) || !statSync(file).isFile()) return response.writeHead(404).end();
  response.writeHead(200, { "Content-Type": TYPES[extname(file)] || "application/octet-stream" }).end(readFileSync(file));
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const origin = `http://127.0.0.1:${server.address().port}`;

const SYLLABUS = `<body style="margin:0;background:#fff;font:15px/1.5 Georgia,serif;color:#111">
<div style="width:820px;padding:44px 56px">
  <p style="margin:0;font:700 13px/1 Arial;letter-spacing:.08em;color:#c03">RUTGERS UNIVERSITY · SCHOOL OF ARTS AND SCIENCES</p>
  <h1 style="margin:10px 0 2px;font-size:30px">01:119:115 General Biology I</h1>
  <p style="margin:0 0 18px;color:#444">Fall 2026 · Section 04 · Instructor: Dr. A. Rivera</p>
  <table style="border-collapse:collapse;width:100%;font:14.5px/1.45 Arial">
    <tr><td style="border:1px solid #bbb;padding:8px 10px;width:150px;background:#f4f4f4"><b>Lecture</b></td><td style="border:1px solid #bbb;padding:8px 10px">Tue / Thu 9:50 – 11:10, Tillett Hall 116 (Livingston)</td></tr>
    <tr><td style="border:1px solid #bbb;padding:8px 10px;background:#f4f4f4"><b>Office hours</b></td><td style="border:1px solid #bbb;padding:8px 10px">Wed 2:00 – 3:00, Nelson Biological Labs B-210</td></tr>
    <tr><td style="border:1px solid #bbb;padding:8px 10px;background:#f4f4f4"><b>Term</b></td><td style="border:1px solid #bbb;padding:8px 10px">Classes run Sept 1 – Dec 10, 2026. No class Thu Nov 26 (Thanksgiving).</td></tr>
  </table>
  <h2 style="margin:22px 0 8px;font-size:19px">Exams</h2>
  <table style="border-collapse:collapse;width:100%;font:14.5px/1.45 Arial">
    <tr style="background:#f4f4f4"><th style="border:1px solid #bbb;padding:7px 10px;text-align:left">Exam</th><th style="border:1px solid #bbb;padding:7px 10px;text-align:left">Date</th><th style="border:1px solid #bbb;padding:7px 10px;text-align:left">Time</th><th style="border:1px solid #bbb;padding:7px 10px;text-align:left">Room</th></tr>
    <tr><td style="border:1px solid #bbb;padding:7px 10px">Midterm 2 (Ch. 9–15)</td><td style="border:1px solid #bbb;padding:7px 10px">Thu Nov 19</td><td style="border:1px solid #bbb;padding:7px 10px">7:40 – 9:00 PM</td><td style="border:1px solid #bbb;padding:7px 10px">ARC 103</td></tr>
    <tr><td style="border:1px solid #bbb;padding:7px 10px">Final exam (cumulative)</td><td style="border:1px solid #bbb;padding:7px 10px">Tue Dec 15</td><td style="border:1px solid #bbb;padding:7px 10px">8:00 – 11:00 AM</td><td style="border:1px solid #bbb;padding:7px 10px">College Ave Gym</td></tr>
  </table>
  <h2 style="margin:22px 0 8px;font-size:19px">Due dates</h2>
  <ul style="margin:0;padding-left:22px;font:14.5px/1.7 Arial">
    <li>Lab report 4 — due Fri Nov 20 by 11:59 PM, submit on Canvas</li>
    <li>Problem set 9 — due Mon Nov 30 at the start of lecture</li>
  </ul>
</div></body>`;

const EMAIL = `<body style="margin:0;background:#f2f3f5;font:15px/1.55 -apple-system,Inter,Arial,sans-serif;color:#1c1e21">
<div style="width:760px;margin:22px;background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.15);padding:26px 30px">
  <h1 style="margin:0 0 14px;font-size:22px">Review session before Midterm 2</h1>
  <div style="display:flex;gap:12px;align-items:center;margin-bottom:18px">
    <span style="width:40px;height:40px;border-radius:50%;background:#3b6fe0;color:#fff;display:grid;place-items:center;font-weight:700">TA</span>
    <div><b>Bio 115 TA Team</b><br><span style="color:#65676b;font-size:13px">to Section 04 · Wed, Nov 11, 2026, 8:14 AM</span></div>
  </div>
  <p>Hi all,</p>
  <p>We're holding a review session for Midterm 2 <b>this Monday from 6 to 7:30 in SEC 111</b>. We'll go through the practice exam, so bring your attempt and your questions. Pizza if you get there early.</p>
  <p>Can't make it in person? Join on Zoom: <span style="color:#1a5fd0">https://rutgers.zoom.us/j/95500112233</span></p>
  <p>Slides will be posted on Canvas afterwards.</p>
  <p>— Priya &amp; Marcus</p>
</div></body>`;

mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
try {
  // 1. The flyer from the tutorial, as a screenshot of the post it sits in.
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2, colorScheme: "light" });
  await page.goto(`${origin}/_tutorial/index.html`);
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => window.tutorial.seek(3.9));
  await page.locator(".post").screenshot({ path: join(OUT, "flyer.png") });
  await page.close();

  for (const [name, html, width] of [["syllabus", SYLLABUS, 932], ["email", EMAIL, 804]]) {
    const sample = await browser.newPage({ viewport: { width, height: 600 }, deviceScaleFactor: 2 });
    await sample.setContent(html);
    await sample.screenshot({ path: join(OUT, `${name}.png`), fullPage: true });
    await sample.close();
  }
} finally {
  await browser.close();
  server.close();
}
console.log(`samples in ${OUT}`);
