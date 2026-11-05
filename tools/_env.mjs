// 探针脚本共用的小工具 —— 不写死任何本机路径。
//
// 用法 (ESM):
//   import { chromium, REPO, MAKE_COOKIE, cookie } from './_env.mjs';
//
// playwright 的解析顺序: $PLAYWRIGHT_MODULE → 默认解析 → <repo>/tools/node_modules/playwright
// 本机没装时: cd tools && npm i playwright@1.55.0 (或设 PLAYWRIGHT_MODULE 指到别处的安装)
import { createRequire } from 'module';
import { execFileSync } from 'child_process';
import { fileURLToPath } from 'url';
import path from 'path';

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const REPO = path.resolve(HERE, '..');
export const MAKE_COOKIE = path.join(HERE, 'make_cookie.py');

const req = createRequire(import.meta.url);

function loadPlaywright() {
  const explicit = process.env.PLAYWRIGHT_MODULE;
  if (explicit) return req(explicit);
  try {
    return req('playwright');
  } catch {
    return req(path.join(HERE, 'node_modules', 'playwright'));
  }
}

export const { chromium } = loadPlaywright();

/** 签一个控制台会话 cookie (playwright addCookies 用)。 */
export function cookie(user = process.env.RV_USER || '') {
  const args = [MAKE_COOKIE];
  if (user) args.push(user);
  return execFileSync('python3', args, { encoding: 'utf8', cwd: REPO }).trim();
}
