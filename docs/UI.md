# 界面层 (UI) —— 一套骨架, 一套皮肤

云阅 Recto 的每个分区 (书架 / 云端同步 / 摘录 / 语料 / 外观 / 设置) 共用**同一副外壳**
和**同一份样式表**, 骨架是 **Material 3**:

* 形状 —— 默认**直角**（`radius_scale = 0`）: 卡片 / 按钮 / 输入框 / 芯片全部 `0px`；
  想要圆角就在「外观 → 细调 → 圆角」把滑杆拉起来（`--rc-radius*` 全部由它派生）
* 高度 —— 卡片 e1、悬停 e2/e3、弹层 e4; 深色皮肤的阴影更重
* 状态 —— 悬停 / 按下不是换色, 是叠一层 `--rc-state` 半透明状态层
* 交互 —— 按钮与链接带水波纹 (纯装饰, 500ms 自删, 不改变点击行为)
* 排版 —— Roboto / 系统无衬线, 标题字重 400–500 (不再用粗黑)

换肤只改变量, 不改代码。

```
src/recto/app/ui/
  skins.py            皮肤的 token 定义 / 生成 CSS / 读写外观配置
  shell.py            整页骨架 (顶栏 / 导航 / 页脚) + 登录页 + 静态资源白名单
  assets/recto.css    设计系统: 只认 --rc-* 变量, 不写死颜色
src/recto/app/appearance.py   /appearance 页面 + /api/appearance
src/recto/app/settings_page.py /settings 页面 + /api/settings + /api/mark (登录名 / 昵称 / 口令 / 书库目录 / 阅读高亮; 外观只在外观分区)
```

## 1 三层结构

| 层 | 是什么 | 改它会发生什么 |
|---|---|---|
| token | 一组 `--rc-*` 变量 (色板 / 字体 / 圆角 / 疏密 / 高度) | 只影响颜色与密度 |
| 样式 | `assets/recto.css` | 影响所有页面的骨架与组件 |
| 外壳 | `shell.py` 的 `NAV` | 影响导航项与页面结构 |

页面本身**不写颜色**, 只写结构。任何硬编码的 `#fff` 都是 bug。

## 1.1 Material 3 角色

`skins.py` 里一张 `_M3_ROLES` 表把色板映射成 Material 角色 —— 皮肤只要给老的
`accent / surface / line ...`, 角色自动派生:

| 角色 | 来自色板 | 用在哪 |
|---|---|---|
| `--rc-primary` / `--rc-on-primary` | `accent` / `accent-fg` | 主按钮 (filled)、进度条、激活导航 |
| `--rc-primary-container` | `accent-soft` | 选中态背景、柱状图底色 |
| `--rc-secondary-container` | `accent` 15% + `surface-2` | 默认按钮 (tonal)、选中芯片 |
| `--rc-outline` / `--rc-outline-variant` | `line-2` / `line` | 输入框描边 / 分隔线 |
| `--rc-elev-1..4` | 按 `mode` 生成 | 卡片 / 悬停 / 弹层高度 |
| `--rc-state` / `--rc-state-strong` | 按 `mode` 生成 | 状态层 (悬停 / 按下 / 水波纹) |

皮肤自己写了同名 token (例如 `"primary": "#005cbb"`) 就不再派生, 直接用它。

## 1.2 书架卡片

`<section data-pane="library">` 是落地页: 顶部统计条 (`#shelf-stat`) + 搜索 (`#shelf-q`)
+ 卡片网格 (`#shelf`)。卡片是 `<a class="bk">`: 封面 (`aspect-ratio:3/4`) / 书名两行截断 /
作者 / 线性进度条 / `%` 与「划线 · 时长」。封面走阅读器的资产接口
(`/read/api/book/<key>/asset?p=...`), 没有封面时退化成首字色块。
封面路径在**扫描时**就解析进 `rbook.cover`（EPUB3 `properties="cover-image"` /
EPUB2 `<meta name="cover">`，取不到再按文件名带 `cover` 的图兜底）；
不是"打开过才有"的附带效果 —— 重扫只覆盖解析成功的字段，不会清空已有值。

卡片与按钮的事件统一走 **document 委托** —— 脚本在 `<head>` 里, 不保证执行时
元素已解析完 (曾经因此"搜索框不响应")。

### 1.2.2 删除一本书

卡片外层是 `<div class="bk-cell">`, 里面才是 `<a class="bk">` + 删除键 `<button class="bk-del">`
—— 交互元素不能嵌在链接里, 所以按钮是**定位容器的子元素**, 贴封面右上角 (30×30 圆钮):

| 状态 | 表现 |
|---|---|
| 桌面 (有 hover) | 默认透明 + `pointer-events: none`; 悬停卡片才淡入 (`:focus-within` 也给键盘) |
| 触屏 / 窄窗口 | 常显 (`@media (hover: none)` 与 `@media (max-width:860px)` 两条兜底) |

点一下先弹二次确认 (原生 `confirm`, 跟语料/摘录那边一致), 文案写明"文件会移到
`data/trash/`, 不是彻底删除", 并报出这本书已有的划线 / 书签数量。

删的语义 (`reader_state.delete_book`, 路由 `POST /read/api/book/<key>/delete`):

* 文件搬到 `<data_dir>/trash/<时间戳>/<书库内相对路径>` —— **不是真删**, 搬回去就复活
* `.recto.json` / `.keywords.json` 边车跟着书一起搬走, 不在书库留孤儿
* **只删 `rbook` 那一行**: 进度 / 书签 / 划线 / 时长留在库里 → 文件放回原位 (路径不变 ⇒ key 不变) 时都还在
* 下载索引 (云书目录) 里登记过的路径一并摘掉; 书库目录之外的文件直接拒 (403)
* 删完立刻把卡片从本地列表摘掉并回执, 不等下次重扫 (重扫也不会复活)

## 1.2.1 排列顺序

搜索框右侧是 `.shelf-sort` 两个 filter chip (`aria-pressed` 即选中态, 选中实心):

| 值 | 文案 | 顺序 |
|---|---|---|
| `recent` (默认) | 最近阅读 | `updated_at`（阅读进度时间）倒序, 没读过退到 `added_at`（入库时间） |
| `title` | 书名 | `Intl.Collator("zh-Hans-CN", {numeric:true})` —— 中文按拼音, 数字按数值 |

* `seen_at` **不能**当排序依据: 它是"上次扫描看到这本书"的时间, 全库同一刻。
* 时间相同的书按书名兜底 —— 否则每次刷新卡片顺序会跳。
* 选择逐设备记在 `localStorage["recto.shelf.sort"]`（桌面按书名找书 / 手机接着上次读, 互不覆盖）。
* 排序纯前端（`sortShelf()` 在 `renderShelf()` 里、搜索之后）, 不动服务端 `ORDER BY`。
* **时序坑**: 本页 `<script>` 挂在 `<head>`（`head_extra`）, 首屏执行时 `<body>` 还没解析完 →
  `setShelfSort()` 查不到 chip。所以 `renderShelf()` 开头会再刷一次选中态,
  另有一条 `DOMContentLoaded` 兜底。

## 1.3 标志 ( `.rc-logo` / `.rc-lockup` )

`logo.png`（仓库根）经 `tools/make_logo.py` 变成两张透明位图：

* `logo-mark.png` — 只有图徽（179×66，宽高比 2.7:1），顶栏用，跟 `云阅 Recto` 文字并排（`shell.brand()`）。
* `logo-full.png` — 图徽 + 文字（300×192），登录卡片顶部用（`shell.login_body()` 的 `.rc-lockup`）。
  两张都是**显示尺寸的 3 倍**，给高 DPR 屏留余量。

规则：

```css
.rc-brand .rc-logo { display:block; height:22px; width:auto; }   /* 手机 20px, ≤380 18px */
.rc-logo-full       { display:block; height:64px; width:auto; }
```

* **只给高度，宽度 auto** —— 两个文件比例不同（图徽 2.7:1 / 完整 1.55:1），任何
  `width` 定值都会把其中一个压扁。
* 图徽 `alt=""`（紧跟文字品牌，读屏不必念两遍）；完整标志 `alt="云阅 Recto"`。
* 深色皮肤靠 `skin.css` 输出的 `html img.rc-logo{filter:invert(1)}` 反相；
  位图本身是**纯黑 + alpha**，所以反相不会带出底块。
* 加新尺寸/新位置时，别改这两条基础规则，用局部覆盖（例如 `.rc-auth-card` 里）。

## 2 换肤 / 细调 (不改代码)

打开 `http://<host>:5015/appearance`:

* **皮肤** — 内置 6 套: **`material` Material (默认)** / `material-dark` Material 深色 /
  `paper` 纸 / `slate` 石板 / `mist` 薄雾 / `sepia` 旧书。
  点一下即时预览, 点「保存」写盘。**换的是色板, 不换骨架** —— 六套都是 Material 组件。
* **细调** — 字号缩放 (0.85–1.5×) / 圆角 (0–2×, **默认 0 = 直角**) / 疏密 (紧凑·舒适·宽松) / 标题字体。
* **自制皮肤** — 选一套皮肤 + 改主色与底色, 存成新皮肤; 之后还能继续调。
* **自定义 CSS** — 追加在 `skin.css` 之后, 想覆盖什么写什么。

配置落在 `<data_dir>/appearance.json` (默认 `<项目根>/data/appearance.json`)。
`/assets/skin.css` 每次请求现生成, 所以保存后**刷新即生效**, 不需要重启进程。

## 3 加一套皮肤 (扩展点)

### 3.1 文件皮肤 (推荐给第三方 / 手工精修)

放一个 JSON 到 `<data_dir>/skins/<id>.json`, 刷新 `/appearance` 就能看到:

```json
{
  "label": "苔",
  "base": "mist",
  "mode": "light",
  "tokens": {
    "accent": "#3d6b3a",
    "bg": "#eef2ee",
    "surface": "#f8fbf8"
  },
  "extra_css": ".rc-brand{letter-spacing:.08em}"
}
```

* 文件名 (去 `.json`) 就是 id, 必须匹配 `[a-z0-9][a-z0-9_-]{0,31}`
* `base` 决定没写到的 token 从哪套继承
* `mode` = `light` / `dark`, 决定 `color-scheme` 与阴影强度
* `extra_css` 原样追加到这一套皮肤的 CSS 后面

可用 token (全部有默认值):

| token | 用途 |
|---|---|
| `bg` `surface` `surface-2` `sunk` | 页面底 / 卡片 / 次级面 / 代码块底 |
| `fg` `dim` `mute` | 主文字 / 次要文字 / 提示文字 |
| `line` `line-2` | 分隔线 / 控件描边 |
| `accent` `accent-fg` `accent-soft` | 主色 / 主色上的文字 / 主色淡底 |
| `ok` `warn` `err` | 状态色 |

### 3.2 代码里加内置皮肤

`ui/skins.py` 的 `BUILTIN_SKINS` 加一项, 值只有颜色 —— 不要写 CSS。

## 4 加一个分区 / 一个面板

**原则: 能做成面板就别做成新页面。** 分区之间是"切视图", 不是"打开另一个网站" ——
跳页会丢掉阅读位置、筛选条件和上下文, 也会让每个页面各长各的样子。

1. 顶层入口: 在 `shell.NAV` 加一行 `(key, 文案, href)`。
   - 控制台单页内的入口把 key 加进 `PANE_KEYS`, href 写 `/#<key>`, 页面里对应区块加
     `data-pane="<key>"` (无需写 JS)。
   - 摘录后台那种带二级导航的: `show_subnav=True, sub_active=<key>`,
     二级项在 `shell.SUB_NAV`, href 写 `/admin/?pane=<key>` (无 JS 时的退路)。
2. **面板 = 服务端可单独渲染的一段 HTML**。外壳只负责显隐:
   - 整页里每个面板是
     `<section class="pane-sec" data-pane="KEY" data-pane-src="/admin/pane/KEY">`;
     当前面板服务端直接渲染好并标 `data-pane-loaded="1"`, 其余等第一次切过去再取
     (外壳脚本会 fetch `data-pane-src`, 并把取回的 `<script>` 重新执行一遍 ——
     `innerHTML` 插进去的 script 不会自己跑)。
   - 面板内的链接 / 表单要换数据: `href` 仍写整页 URL (`/admin/?pane=...`), JS 自动
     改走 `/admin/pane/KEY?...` 只换面板那一段, 并 `history.replaceState` 同步地址。
     没有 JS 时就退化成一次整页请求, 行为一致。
3. 真需要独立页面 (登录 / 外观 / 语料反代): `shell.document(...)`,
   页头统一 `shell.head(kicker, title, sub, actions)`。

### 4.1 表格与图表的排版约定

- 表格**一律**套 `.table-wrap` (窄屏横向滚动, 而不是把每列压成一条竖排字);
  列宽用 `<colgroup><col class="col-num|col-tag|col-ts|col-grow|col-deck">` 给下限,
  数字列 `class="num"`, 时间/状态 `class="nowrap"`。表格别塞进 1/3 宽的多列网格 ——
  6 列挤进 340px 必然折成竖排, 用 `.tool-card.wide` 让它跨整行。
- **表格视觉: 一根线都不画**。没有外框、没有列分隔、没有行下划线; 行的区间只靠
  **隔行底色** (`tbody tr:nth-child(even)` 一层 `--rc-state` 高的浅色) 区分,
  表头靠字重与字色 (不再画底线)。`.bordered` 是历史类名, 现在为空壳 ——
  新代码不用它, 老模板留着自己也不用改。语料反代页由 `corpus._BRIDGE_CSS`
  把上游的 `border-bottom: #f0f2f4` 一并清掉, 口径一致。
- 柱状图: `.bar-chart > .bar[style=height]`, 标签 `.v` (数值) / `.lbl` (刻度),
  0 值那根给 `.zero` (留一条地平线, 否则那天在图上凭空消失), 今天给 `.today`。
- 时间轴: 月份分隔 `.tl-date` (`.year` + `.ymd` + `.count`), 条目 `.tl-item`,
  时间列 `.tl-time` (`.h` 时刻 + `.d` 日期, 两行显示, 手机上移到正文上方)。
- 筛选芯片 `.chip` (`.chip.active`), 筛选栏 `.filter-bar` 是一张卡片, 不是一条下划线。

### 4.2 页头动作: 悬浮操作轨 ( `.rc-rail` )

页头只剩"维护类"动作时, 别再摆一排在标题右边 —— 收成竖排圆钮贴在屏幕右侧。

```html
<div class="rc-head-row">
  <div class="rc-rail" role="toolbar" aria-label="书库操作">
    <button id="shelf-rescan" class="rc-rail-btn" aria-label="重扫书库">
      <span class="rc-rail-ico" aria-hidden="true"><svg …/></span>
      <span class="rc-rail-name">重扫书库</span>
    </button>
    <button class="rc-rail-btn" data-act="links" aria-label="对齐云端映射">…</button>
  </div>
</div>
```

- **桌面 (>860px)**: `position: fixed` 竖排贴屏幕右侧 (滚动不动), 48px 圆钮 +
  高度阴影; 名字 `.rc-rail-name` 绝对定位在圆钮左侧, `hover` / 键盘 `focus-visible`
  才淡入 —— 平时只占 48px。页头行因此会塌成 0 高 (由 CSS 抹掉 padding)。
- **窄屏 (≤860px)**: 取消悬浮, 回到页头里横排, 文字常显 (手机上不做 icon-only)。
- 圆钮**刻意不放水波纹**: 波纹靠 `overflow:hidden` 裁形状, 会把悬浮名字一起裁掉。
  按钮里改文字请改 `.rc-rail-name` 的 `textContent`, 别用 `btn.textContent`
  (那会把图标抹掉; 忙碌时给按钮加 `.is-busy`, 图标自己转)。
- 动作结果落在**别的面板**时 (`#out` 在「云端同步」里), 页面会用 `.rc-toast`
  在底部给一句回执, 3.6s 自消; 不挡点击。

### 4.3 顶栏右侧: 头像 = 设置入口 ( `.rc-avatar` )

顶栏右上那个圆点**不是**换肤按钮, 是**设置页**的入口:

```html
<div class="rc-user">
  <span class="rc-user-name">昵称</span>
  <a class="rc-avatar" href="/settings" title="设置" aria-label="设置">青</a>
  <a href="/logout">退出</a>
</div>
```

- 头像里是**昵称首字**; 没昵称就退回首字登录名, 连登录名都没有时显示 `⚙`。
- 显示名走 `core/settings.display_name()` —— **昵称优先, 回退登录名**;
  登录名 (账号本身) 不变, 只影响显示。
- 换肤 (`/appearance`) 从设置页里进, 不再抢顶栏这个位置。

## 5 组件 (直接照抄 class)

| 类型 | 写法 |
|---|---|
| 卡片 | `<div class="card">` / 可折叠 `<details class="sec"><summary>…</summary><div class="sec-body">…</div></details>` |
| 按钮 | `<button>` / `<button class="primary">` / `<button class="ghost small">` |
| 徽标 | `<span class="pill ok\|warn\|err\|mute">` |
| 悬浮操作轨 | `.rc-rail > .rc-rail-btn > (.rc-rail-ico + .rc-rail-name)` (见 4.2) |
| 回执条 | 页面 JS 调 `toast("一句话")` 插入 `.rc-toast`, 3.6s 自消, 不挡点击 |
| 表格 | `<div class="table-wrap"><table class="cols-*">…</table></div>` (必须套, 窄屏横滚) |
| 统计数字 | `.cards > .card > .kicker-sm + .n + .l` |
| 筛选芯片 | `.chip` / `.chip.active` (放在 `.filter-bar` 里) |
| 柱状图 | `.bar-chart > .bar[style=height] > .v + .lbl` |
| 表单 | `.field > span + input` |
| 分组 | `.row` / `.grid.two` / `.grid.three` |
| 空态 | `.empty` |
| 代码 / 输出 | `<pre>` 或 `<pre class="out">` |
| 执行结果 / 日志 | `.rs > .rs-head(.rs-act + .pill + .rs-meta) + .rs-chips > .chip` + `.rs-list > li`；原始数据放 `details.rs-raw`。日志用 `.log-list > .log-row > (.log-ts + .log-lv + .log-msg)` |

**别把后端 JSON 直接倒进界面。** 同步 / 下载这类动作的返回值是给程序看的
（pct / why / skipped），界面这一层必须翻成一句话：结论行（做了什么、成不成）、
计数 chip（更新 / 跳过 / 失败）、每本书一行（书名 + 一句人话），原始 JSON 收进
`details.rs-raw`。新增动作时在 `console.py` 的 `renderRun()` 里加一条分支，
不要退回 JSON.stringify(...)。

日志同理：`_tail_log()` 按 asctime 的正则切（**不能按空格切** —— 时间戳自己带空格，
会把整行判成"原文"），等级在界面上写中文（信息 / 注意 / 失败），消息体写成
完整句子（例如 推送成功 · 书名 · 进度 31.7% · 第 159 章）。

## 6 语料服务: 原生能力 (不再是反代)

语料 (EPUB 关键字精炼) 原本是一个独立进程 (`:5013` Flask), 由这里反代 + 注入外壳。
2026-11-06 起整个能力搬进来了 —— 反代层不再存在, 因为独立服务带来的问题
(多一个要自启/自检/挂了的端口、两套登录、两套日志、注入式改 HTML) 都比收益大。

分层:

| 层 | 位置 | 职责 |
|---|---|---|
| 业务 | `src/recto/corpus/` | 解析 EPUB / 分块 / LLM 抽词 / 断点续跑 / 固定导出 / 模型库 |
| 服务 | `src/recto/corpus/service.py` | 每个用例一个函数, 返回 `(状态码, JSON)`, 与 HTTP 无关 |
| 路由 | `src/recto/app/corpus.py` | `/corpus/*` 的路径与方法分派 (薄薄一层) |
| 页面 | `src/recto/app/ui/assets/corpus.{html,css,js}` | 外壳 + 分区正文 (资产见下) |

- **一套登录**: 走 `server.Handler._gate`, 没有第二套账号; `/corpus/api/*` 未登录返 401 JSON
- **一套皮肤**: `corpus.css` 全部走 `--rc-*` token, 换肤/深色自动跟随
- **一套日志**: `logs/corpus.log` (超 512KB 轮转), 按句写人话

### 6.1 页面资产是"机械平移 + 手工打磨"

页面本体 (六张卡) 与 1000 行前端脚本经过多轮打磨, 重写只会丢功能。所以:

    python3 tools/build_corpus_assets.py [上游仓库根]

- `corpus.html` — 取上游 `index.html` 的 `<main>` 片段 (外壳由 `shell.document` 给)
- `corpus.js` — 上游 `app.js` + `CORPUS_BASE` 前缀 (所有接口都走同一个 `api()` 包装)
- `corpus.css` — **只在缺失时 bootstrap**, 之后是仓内文件, 手工维护
  (填充式字段 / 单层卡片 / 徽标定位 / 表格口径这些规则都在里面)

### 6.2 数据布局

语料根 = `config.toml` 的 `[corpus] dir` (默认 `<data_dir>/corpus`):

    config.json         LLM 端点 / 模型库 / 分类定义 (含 api_key, 权限 600)
    state.json          书目状态 (book_id → 路径 / 标题 / 进度)
    outputs/<bid>/      中间产物 (meta / blocks / keywords / status / 断点)
    <书名>-<hash>.json  固定导出 —— 阅读高亮的语料扫描就是这一层
    index.json          导出清单 (latest_slug / latest_pinned)
    latest.json         最近一次成功产物 (下游只需读这一个)

固定导出**平铺在语料根**是刻意的: `core/reading_mark.py` 用 `glob("*.json")` 扫
这一层, 放子目录会断。`config.json` / `state.json` / `index.json` 被 `load_corpus()`
自动跳过 (没有 `categories` 字典), 所以不冲突。

### 6.3 表单: 无边框填充式字段 (2026-11-04)

上游是「白底 + 1px 灰描边」的方框, 一页几十个框贴在同样有描边的卡片上, 线条互相打架。
`_BRIDGE_CSS` 把所有规则限定在 `body.rc-corpus` 作用域内 (反代时给 `<body>` 补这个类),
只改语料页, 不动其它分区:

- **字段** — 无描边, 一层 `color-mix(fg 6%, surface)` 的填充底色; 悬停加深, 聚焦
  加深 + 底部 2px 主色指示线 (`inset 0 -2px 0`)。高度 40px = 按钮同高,
  `textarea` 走同款填充 + 10/12px 内边距。
- **对齐** — 标签 → 输入框 6px; `.grid` 顶对齐 + 列距 16 / 行距 16;
  `.row` 内输入/按钮/状态按中线对齐 (探针量到中线偏差 0px);
  「严格避免推理」这类勾选项 `grid-column: 1/-1` 跨整行, 不再撑成一格两行。
  它**默认不勾**: 关不掉思考的端点允许带着思考跑完, 勾上才「一发现就中止」。
- **卡片** — 上游三层 (卡片描边 + 卡头浅灰底 + 卡身 16px) 合并成一层: `.card{padding:0}`,
  卡头 14px + 一条 `--rc-outline-variant` 分隔线, 卡身 `--rc-pad`; 卡头与卡身左缘对齐。
- **提示条** — 上游写死的浅色底 (`#f7f9fc / #fff7e6 / #fff3d6`) 全部改 token 混色,
  深色皮肤下不再出现发光的白板。

回归: `tests/test_corpus_proxy.py::test_form_styles` (纯函数断言 CSS 口径) +
`tools/probe_corpus_fields.mjs` (真浏览器 18 项: 零描边 / 40px / 6px 间距 / 行中线 /
深色皮肤 / 390px 不出界)。

要关掉这一节: 从 `shell.NAV` 去掉 `corpus` 一行即可, 反代不影响其它路由。

### 6.2 表格里的行内徽标 (2026-11-04)

上游把推理标记写成 `<input width:100%> <span class="badge-warn">推理</span>` ——
inline 排布在 100% 宽的字段后面**必然换行**, 该行比相邻行高 10.4px(桌面) /
27.2px(手机, 徽标自己还折两行), 整张表行高参差。

口径: **徽标绝对定位在「该列预留的右侧空间」里**, 而不是待在被挤出来的下一行。

- `table.models td:nth-child(6){position:relative;padding-right:46px}` —
  模型列统一预留 (所有行输入框等宽, 列内右边缘 spread 0px), 徽标
  `position:absolute; right:6px; top:50%; translateY(-50%)` 竖直居中, 行高恒等。
- 列序与上游 `renderModels` 耦合 (`模型` 是第 6 个 td); 另挂一条 `td:has(> .badge-warn)`
  兜底 —— 万一对不上, 至少不会压住输入框。
- 窄屏 (`≤860px`) 十列必被压扁 (390px 下模型输入框只剩 12px): 表格给 `min-width:980px`
  + 模型列 `min-width:150px`, 由 `.models-wrap` 横向滚动承接。
  **探针注意**: 滚动容器里的字段本来就在视口外, 判「出界」要排掉这类祖先。

回归: `tests/test_corpus_proxy.py::test_badge_warn_does_not_push_input` +
`tools/probe_badge_warn.mjs` (双视口: 不重叠 / 中线对齐 / 不换行 / 列宽一致 / 行高一致)。

## 7 什么时候不该用这套

内置阅读器 (`app/reader.py` + `assets/reader.css`) 有自己的正文排版，不套这里的
外壳 token —— 它只借用皮肤变量里的字体/背景，其余按阅读体验单独调。

## 8 排障

| 现象 | 查什么 |
|---|---|
| 颜色没变 | 强刷; 看 `/assets/skin.css` 返回的 `--rc-bg` 是否已经是新值 |
| 页面一半是新样式一半不是 | 该页还没接 `shell.document` (看有没有 `rc-shell`) |
| 某一项导航不高亮 | `active` 传的 key 与 `shell.NAV` 里的 key 不一致 |
| 皮肤不出现 | `/appearance` 的「皮肤」计数; 文件是否在 `<data_dir>/skins/` 且 id 合法 |
| 外观"每次重启就被重置" | 先看 `data/recto.log` 里的 `[appearance] save ... from=... ua=...` —— 非浏览器 UA (如 `ua=node`) = 探针在改; 探针必须用 `tools/_skins.mjs` 的快照/还原 |

## 9 探针卫生 (外观)

探针要扫多套皮肤, 但**不许留下痕迹**:

```js
import { skinSnapshot, setSkin, restoreSkin } from './_skins.mjs';

const snap = await skinSnapshot();          // 用户当前的外观
try {
  await setSkin('material-dark');           // 随便切
  /* ... 量 ... */
} finally {
  await restoreSkin(snap);                  // 还原成用户的原值 (不是硬编码 'material')
}
```

* 还原走 node 侧 `fetch`, 浏览器崩了也照样还原。
* `tests/test_probe_hygiene.py` 会扫 `tools/*.mjs`: 写了 `/api/appearance` 就必须快照 + 还原,
  且最后一次写必须是快照值。
* 事故背景 (2026-11-04): `probe_btn_groups.mjs` 扫完六套皮肤不还原, 用户的外观每次被顶成
  `material-dark` —— 现象看起来像"重启就重置"。

