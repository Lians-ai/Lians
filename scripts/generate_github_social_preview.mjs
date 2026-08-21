import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = resolve(root, "docs/assets/github-social-preview.png");
const logo = (await readFile(resolve(root, "docs/images/logo.png"))).toString("base64");
const lotus = (await readFile(resolve(root, "docs/assets/lians-lotus.svg"))).toString("base64");
const logoUrl = `data:image/png;base64,${logo}`;
const lotusUrl = `data:image/svg+xml;base64,${lotus}`;
const fontPath = resolve(root, "packages/lians-easy/lians_easy/app/fonts/sora-latin.woff2");
const font = (await readFile(fontPath)).toString("base64");

const executablePath = [
  process.env.CHROME_PATH,
  chromium.executablePath(),
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
].find((candidate) => candidate && existsSync(candidate));

const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({ viewport: { width: 1280, height: 640 }, deviceScaleFactor: 1 });

await page.setContent(`
  <!doctype html>
  <html>
    <head>
      <meta charset="utf-8">
      <style>
        @font-face {
          font-family: "Sora";
          src: url(data:font/woff2;base64,${font}) format("woff2");
          font-style: normal;
          font-weight: 100 800;
        }

        * { box-sizing: border-box; }

        html, body {
          width: 1280px;
          height: 640px;
          margin: 0;
          overflow: hidden;
          background: #05070b;
          color: #ffffff;
          font-family: "Sora", sans-serif;
        }

        body {
          position: relative;
          padding: 66px 72px 60px;
        }

        body::before {
          content: "";
          position: absolute;
          width: 540px;
          height: 540px;
          right: -90px;
          top: 50px;
          border-radius: 50%;
          background: radial-gradient(circle, rgba(36, 84, 170, 0.23) 0%, rgba(36, 84, 170, 0) 70%);
        }

        .logo {
          width: 190px;
          height: auto;
          display: block;
        }

        h1 {
          max-width: 760px;
          margin: 62px 0 24px;
          font-size: 72px;
          line-height: 1.03;
          letter-spacing: -4px;
          font-weight: 650;
        }

        h1 span { color: #2454aa; }

        p {
          width: 760px;
          margin: 0;
          color: #c7ceda;
          font-size: 25px;
          line-height: 1.42;
          letter-spacing: -0.5px;
          font-weight: 400;
        }

        .lotus {
          position: absolute;
          width: 380px;
          height: auto;
          right: 48px;
          top: 214px;
          display: block;
          filter: drop-shadow(0 26px 60px rgba(20, 59, 127, 0.32));
        }

        .rule {
          position: absolute;
          left: 72px;
          right: 72px;
          bottom: 48px;
          height: 2px;
          background: linear-gradient(90deg, #2454aa 0 34%, #171d27 34% 100%);
        }
      </style>
    </head>
    <body>
      <img class="logo" src="${logoUrl}" alt="Lians">
      <h1>AI said done.<br><span>Lians checked.</span></h1>
      <p>Evidence-backed proof of done for Claude Code, Codex, Cursor, and every Git repository.</p>
      <img class="lotus" src="${lotusUrl}" alt="">
      <div class="rule"></div>
    </body>
  </html>
`);

await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: output, type: "png" });
await browser.close();

console.log(output);
