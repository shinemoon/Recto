/**
 * 探针的「外观」卫生工具 —— 探针不许留下痕迹。
 *
 * 背景 (2026-11-04): `probe_btn_groups.mjs` 为了扫六套皮肤, 直接 POST
 * `/api/appearance` 切皮肤, 扫完不还原 → 每次跑完, 用户自己选的外观都被
 * 顶成 `material-dark`; 用户看到的现象是"每次重启外观都重置成 Material 深色"。
 *
 * 规则 (所有探针都必须遵守):
 *   进入前 `const snap = await skinSnapshot()`; 用 try/finally 包住整段;
 *   finally 里 `await restoreSkin(snap)` —— **还原成用户原来的值**, 不是硬编码的 'material'。
 *
 * 还原走 node 侧 fetch (不是 page.evaluate), 这样浏览器崩了 / 页面跳飞了也照样还原。
 */
import { execSync } from 'node:child_process';
import { REPO } from './_env.mjs';

export const BASE = process.env.RECTO_BASE || 'http://127.0.0.1:5015';

let _cookie = null;

/** Recto 会话 cookie (探针都跑在本机, 用 webauth 现签一个)。 */
export function cookieValue() {
  if (_cookie) return _cookie;
  if (process.env.RECTO_COOKIE) {
    _cookie = process.env.RECTO_COOKIE;
    return _cookie;
  }
  _cookie = execSync(
    'cd ' + REPO + ' && PYTHONPATH=src python3 -c "from recto.app import webauth;'
    + 'print(webauth.set_cookie(\'recto\').split(\';\')[0].split(\'=\',1)[1])"',
  ).toString().trim();
  return _cookie;
}

function headers(extra = {}) {
  return { 'Content-Type': 'application/json', Cookie: `zy_webauth=${cookieValue()}`, ...extra };
}

/** 当前外观配置 (可直接回灌 restoreSkin)。 */
export async function skinSnapshot(base = BASE) {
  const r = await fetch(`${base}/api/appearance`, { headers: headers() });
  if (!r.ok) throw new Error(`skinSnapshot ${r.status}`);
  const j = await r.json();
  const c = j.conf || {};
  return {
    skin: c.skin ?? j.current ?? 'material',
    font_scale: c.font_scale ?? 1,
    density: c.density ?? 'comfortable',
    radius_scale: c.radius_scale ?? 0,
    heading: c.heading ?? 'auto',
    custom_css: c.custom_css ?? '',
  };
}

/** 切换皮肤 (探针用; 记得最后 restoreSkin)。 */
export async function setSkin(skin, base = BASE) {
  const r = await fetch(`${base}/api/appearance`, {
    method: 'POST', headers: headers(), body: JSON.stringify({ skin }),
  });
  return r.status;
}

/** 还原到 skinSnapshot() 的值。 */
export async function restoreSkin(conf, base = BASE) {
  const r = await fetch(`${base}/api/appearance`, {
    method: 'POST', headers: headers(), body: JSON.stringify(conf),
  });
  return r.status;
}

/**
 * 包一层: 自动快照 + 无条件还原。
 *
 *   await withSkinGuard(async () => { ...sweep... });
 */
export async function withSkinGuard(fn, { base = BASE } = {}) {
  const snap = await skinSnapshot(base);
  try {
    return await fn(snap);
  } finally {
    try {
      await restoreSkin(snap, base);
    } catch (e) {
      console.error(`[skin-guard] 还原失败: ${e.message}; 原值 = ${JSON.stringify(snap)}`);
    }
  }
}
