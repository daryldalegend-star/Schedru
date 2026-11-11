// Render the tutorial video from tools/tutorial/ (an animated web page).
//
//   node tools/make_tutorial.mjs                 the video and its poster
//   node tools/make_tutorial.mjs --stills DIR    just a few frames, to look at
//
// The page is a pure function of time (see tutorial.js), so this asks it for
// one frame at a time and hands each to ffmpeg: no screen recording, no
// dropped frames, the same result every run. Output is H.264 MP4, the one
// format the Mac app's WebKit window plays.

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(ROOT, "syllabus_cal/app/web");
const SOURCE = join(ROOT, "tools/tutorial");
const MEDIA = join(WEB, "media");

const WIDTH = 1280;
const HEIGHT = 800;
const SCALE = 1.5; // 1920x1200: sharp on a Retina screen at the size it's shown
const FPS = 30;

const TYPES = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml" };

function serve() {
  const server = createServer((request, response) => {
    const path = decodeURIComponent(new URL(request.url, "http://x").pathname);
    const [base, rest] = path.startsWith("/_tutorial/") ? [SOURCE, path.slice("/_tutorial/".length)] : [WEB, path.slice(1)];
    const file = normalize(join(base, rest));
    if (!file.startsWith(base) || !existsSync(file) || !statSync(file).isFile()) {
      response.writeHead(404).end("not found");
      return;
    }
    response.writeHead(200, { "Content-Type": TYPES[extname(file)] || "application/octet-stream" }).end(readFileSync(file));
  });
  return new Promise((done) => server.listen(0, "127.0.0.1", () => done(server)));
}

const args = process.argv.slice(2);
const stillsAt = args.indexOf("--stills");
const stillsDir = stillsAt >= 0 ? resolve(args[stillsAt + 1] || "tutorial-stills") : null;

const server = await serve();
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: stillsDir ? 1 : SCALE, colorScheme: "light" });
const errors = [];
page.on("pageerror", (error) => errors.push(String(error)));
page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
await page.goto(`http://127.0.0.1:${server.address().port}/_tutorial/index.html`);
await page.evaluate(() => document.fonts.ready);
await page.waitForFunction(() => window.tutorial);
const duration = await page.evaluate(() => window.tutorial.duration);
const seek = (t) => page.evaluate((seconds) => window.tutorial.seek(seconds), t);

try {
  if (errors.length) throw new Error("the tutorial page has errors:\n" + errors.join("\n"));

  if (stillsDir) {
    mkdirSync(stillsDir, { recursive: true });
    const times = args[stillsAt + 2] ? args[stillsAt + 2].split(",").map(Number) : [1.6, 4.0, 5.3, 6.3, 8.2, 9.3, 10.6, 12.2, 14.4, 15.4, 17.6, 19.6, 19.95, 21.8, 26.2, 26.8, 30.5, 34.4, 37.5];
    for (const t of times) {
      await seek(t);
      await page.screenshot({ path: join(stillsDir, `t${t.toFixed(2).padStart(5, "0")}.png`) });
    }
    console.log(`${times.length} stills in ${stillsDir}`);
  } else {
    mkdirSync(MEDIA, { recursive: true });
    const output = join(MEDIA, "tutorial.mp4");
    const ffmpeg = spawn(
      "ffmpeg",
      [
        "-y", "-loglevel", "error",
        "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "png", "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", "21", "-tune", "animation",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
        "-g", String(FPS * 2), "-movflags", "+faststart", "-an",
        output,
      ],
      { stdio: ["pipe", "inherit", "inherit"] }
    );
    const finished = new Promise((done, fail) => ffmpeg.on("close", (code) => (code === 0 ? done() : fail(new Error(`ffmpeg exited ${code}`)))));

    const frames = Math.round(duration * FPS);
    const started = Date.now();
    for (let frame = 0; frame < frames; frame++) {
      await seek(frame / FPS);
      const png = await page.screenshot({ type: "png" });
      if (!ffmpeg.stdin.write(png)) await new Promise((drained) => ffmpeg.stdin.once("drain", drained));
      if (frame % 150 === 0) console.log(`frame ${frame}/${frames}`);
    }
    ffmpeg.stdin.end();
    await finished;

    await seek(1.9);
    await page.screenshot({ path: join(MEDIA, "tutorial-poster.jpg"), type: "jpeg", quality: 86 });
    console.log(`${frames} frames in ${((Date.now() - started) / 1000).toFixed(0)}s -> ${output}`);
  }
} finally {
  await browser.close();
  server.close();
}
