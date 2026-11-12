/* 书宠页面截图 (给用户看改版效果)。
 * 跑法: cd tools && node shot_pet_story.mjs [输出目录]
 */
import { chromium, cookie } from './_env.mjs';
import fs from 'node:fs';
import path from 'node:path';

const BASE = process.env.RECTO_BASE || '5015';
const OUT = process.argv[2] || '/tmp/pet-shots';
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 },
  deviceScaleFactor: 1.5 });
await ctx.addCookies([{ name: 'zy_webauth', value: cookie(), domain: '127.0.0.1', path: '/' }]);
const page = await ctx.newPage();
await page.goto(`http://127.0.0.1:${BASE}/pet/`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(1200);

// 顶部 (故事卡 + 人物卡)
await page.screenshot({ path: path.join(OUT, 'pet-top.png') });

// 只截故事卡
const story = await page.$('#card-chronicle');
if (story) await story.screenshot({ path: path.join(OUT, 'pet-story.png') });

// 技能折叠 (收起状态) + 展开状态
const skills = await page.$('#card-skills');
if (skills) await skills.screenshot({ path: path.join(OUT, 'pet-skills-folded.png') });
await page.$eval('#card-skills .cs-fold', (d) => { d.open = true; });
await page.waitForTimeout(200);
if (skills) await skills.screenshot({ path: path.join(OUT, 'pet-skills-open.png') });

// 手机视口顶部
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(400);
await page.screenshot({ path: path.join(OUT, 'pet-mobile.png'), fullPage: false });

await browser.close();
console.log('saved to', OUT);
