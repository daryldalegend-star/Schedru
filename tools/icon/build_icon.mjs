// Render tools/icon/icon.svg into the files the app needs:
//   syllabus_cal/app/web/icon.png   window / Dock / tab icon
//   packaging/Schedru.icns          the packaged app's icon (made by build_icns.py)
//
//   node tools/icon/build_icon.mjs && python tools/icon/build_icns.py
import { readFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const svg = readFileSync(join(HERE, "icon.svg"), "utf8");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1024, height: 1024 }, deviceScaleFactor: 1 });
await page.setContent(`<body style="margin:0;background:transparent">${svg}</body>`);
mkdirSync(join(ROOT, "packaging"), { recursive: true });
await page.screenshot({ path: join(ROOT, "packaging/icon-1024.png"), omitBackground: true });
await browser.close();
console.log("wrote packaging/icon-1024.png");
