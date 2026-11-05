import { chromium } from "playwright";
import { execFileSync } from "node:child_process";

const raw = execFileSync("python3", ["/tmp/rc_cookie.py"], { encoding: "utf8" });
const ck = JSON.parse(raw.trim());
const BASE = process.env.BASE || "http://127.0.0.1:5015";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1100 } });
await ctx.addCookies([{ ...ck, domain: "127.0.0.1", path: "/" }]);
const page = await ctx.newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(String(e)));
await page.goto(BASE + "/corpus/", { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(2500);

const dump = async (sel) =>
  page.$$eval(sel, (els) =>
    els.map((b) => {
      const s = getComputedStyle(b);
      return {
        text: b.textContent.trim(),
        bg: s.backgroundColor,
        fg: s.color,
        border: s.borderColor,
        style: s.borderStyle,
      };
    }),
  );

const models = await dump('table.models .mdl-ops button');
const hist = await dump("table.hist td.acts button");
console.log(JSON.stringify({ models, hist, errs }, null, 1));
await browser.close();
