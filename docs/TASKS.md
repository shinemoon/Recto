# 云阅开发任务表

> 本表是 Recto 相关开发的任务板。每条任务带状态、范围、验收口径。
> 开工前先读本表；收工后更新状态与「落在哪」。
>
> 状态: `todo` / `doing` / `blocked` / `done`

---

## T1 · 书籍本体上传掌阅云端 — `done` (2026-11-13)

**目标**：本地书不只同步进度/时长/笔记，还能把**文件本体**放到掌阅云端，
在任何一台掌阅设备/App 里下载阅读。

**结论：不需要 App 原生签名。** `pc.ireader.com` 的「传书到手机」页（`/upload`，
chunk 36）本来就在浏览器里做完整套上传，走的是另一条链路：

```
file_id = MD5(文件) 大写        file_name = "<file_id>.<ext>"
GET  bookstore/open/cloud/storage/upload/authorize?file_type=1&file_id&file_name&file_size
     code 0 + type=2 → OSS 预签名 PUT；code 200004 → 文件已在云存储，跳过上传
PUT  sign_url  (Content-MD5 = base64(MD5 原始 16 字节) / X-Oss-Callback[-var])
POST bookstore/open/cloud/bookshelf/upsert  type=1
     book_id = MD5(文件)+MD5(book_name)，is_local_book=true，book_file_id=MD5(文件)
```

早期"三条路都要签名"的结论作废：要签名的是 `icloud.zhangyue.com/cloud_book/...`
那条 App 链路，PC 网页端不用它。

**落在哪**

| 位置 | 内容 |
|---|---|
| `services/cloudlink.py` | `upload()` / `upload_all()` / `start_upload()` / `upload_capability()`；`items()` 加 `uploaded` / `in_shelf` |
| `services/acquisition.py` | `authorize_upload` / `put_object` / `file_present` / `shelf_add`；`job_begin` / `job_update` 通用任务槽 |
| `app/console.py` | `POST /api/cloud/ident/upload`；卡片挪到书架分区；`renderUploadJob`；三态「文件」列 |
| `ui/assets/recto.css` | `[hidden]{display:none!important}`（button 的 `display:inline-flex` 盖掉了 UA 规则） |
| `tests/test_cloud_upload.py` | 离线断言（授权参数 / PUT 头 / book_id / 200004 / STS 明确报错 / 任务槽） |
| `tools/probe_cloud_upload.mjs` | 12 项真浏览器（卡头 / 无清单 / 单一按钮 / 数量 / 置灰 / 窄屏不溢出） |

**验收（真账号已跑）**
* `多崎作` `海边的卡夫卡` `刺杀骑士团长` `九诗心` 四本 → 云书架出现，名字是清理过的书名，
  `book_type=5`，`book_file_size` 与本地一致
* 上传后 `storage/file/info` 能取回原文件，下载回来 MD5 逐位相同（用 4.8 KB txt 做的端到端）
* 69 MB epub 拿到的仍是 `type=2`（预签名 PUT），不是 STS 分片

**已知限制**
* `type=1`（STS 分片）未实现 → 明确报错，不假装成功
* 云书架条目的 `book_id` 含 `MD5(book_name)`，改书名会变成另一条 → 名字策略要固定
* 上传不改本地文件；书库里的文件被替换（内容变了）就是另一本书

---

**2026-11-13 收尾（用户口径：不要清单，就一个上传按钮）**

* 卡里那张六列表格整段删除；`items()` 仍保留在后端（决定"该传哪些"用它），
  前端不再渲染清单 —— 只留一句说明 + 一个 `上传到掌阅云 (N 本)` 按钮。
* 按钮 N = 书库里"不在云书架"或"还没补身份"的本数；全就绪时置灰并写「都已上传」。
* `upload_all()` 的跳过条件从"在云书架"收紧成"在云书架**且**已有身份" ——
  否则"文件传上去了但边车缺 id32"的书永远补不上，进度/划线也永远不通。
* 前端随之删掉 `identUpload` / `identAct` / `identAll`（逐本上传、接入、全部接入），
  以及 `#ident-body` / `#ident-refresh` / `#ident-hint` 之外的所有表格钩子。

## T2 · 精排书籍的注释/旁注排版 — `done` (2026-11-13)

**目标**：对带复杂注释、旁注、多类型边注的精排书（样本 `da7e470fa0c2c4b8`），
做一套灵活、可复用的排版支持，换一本书不用重新写一遍。

**落地**

| 位置 | 内容 |
|---|---|
| `core/notes.py` | `harvest()`（语义识别注释容器 → 正文池 + 需剔掉的字节区间）/ `NoteLayer.resolve()`（内容优先、id 兜底）/ `marker_html()`；`strip_inline_bodies()` 处理段落内部嵌容器 |
| `core/bookfile.py` | `_epub_blocks` 采集注释并在正文流里跳过容器；`_clean_inline` 把 `noteref` 锚点换成标记；`BookDoc.notes(idx)`；`_block_text` 不吃标记数字 |
| `app/reader.py` | 章节接口多下发 `notes[]`；排版面板新增「注释呈现 弹出/侧栏/章末」 |
| `ui/assets/reader.{js,css}` | 三种形态 + 点击/键盘/Esc/翻页联动；标记 `line-height:0` 不撑行；`__RV__.notes()` 调试口 |
| `tests/test_notes.py` | 37 项：四类写法 / 绑定优先级 / 孤立 / 段内容器 / 真书 541 条抽样 |
| `tools/probe_reader_notes.mjs` | 28 项真浏览器：标记 / 弹出 / 侧栏 / 章末 / 分页 / 手机 / 0 报错 |

**关键结论（踩出来的）**

* 本样本 541 条注释里 **52 个 `href` 被重复使用**，指向的 `<aside>` 与引用处不是同一条
  —— **id 不可信，注释正文其实写在标记图片的 `alt` / `zy-footnote` 上**。
  绑定顺序改成「内容精确匹配 → id → 标记自带内容」，541 条全部绑上（0 孤立 / 0 无正文）
* 注释容器不能留在正文流里（否则"书目标注整段插在段落之间"）；但它**在语义上算块**，
  不能直接参与 `nested_block` 判断 —— 那会让外层 `<p>` 丢掉段首段尾正文
* 「整段外层容器」式的正则扫不出内联容器（外层 `<div>` 先被咬住就跳过整段）；
  改成按标签逐个扫 + 按同名标签计深度找配对闭合
* 标记必须 `line-height:0` + 相对位移（不用 `vertical-align`），否则分页按列高算的页数会漂

**验收**

* 541 条注释 0 丢失、0 错绑；每章标记号与注释号一一对应
* 三种形态在 1280 宽（滚动 + 分页）与 390 宽（弹出变底部卡片）都正常，0 JS 报错
* 复用性：合成样本覆盖 EPUB3 尾注 / 纯 class / 段内容器 / 无注释四种结构，零改代码跑通
* 全套测试 57 个文件绿；阅读器既有探针（typo 33 / paged / layout / ux / sync）无回归

**已知边界**

* 注释正文按纯文本呈现（书里的斜体/链接被压平）；只读，不写源文件
* 形态逐设备记（`localStorage['recto.note']`）

---

## T3 · 书宠故事：成长为主，关联阅读证据 `done` (2026-11-13)

**问题**（用户原话归拢）
1. 每回推进**不一定**非要有战斗，重点是成长；故事要合理
2. 看不出每回进展与**这次读的内容**（章节、语料遭遇）的关联与启发

**落地**

| 位置 | 内容 |
|---|---|
| `pet/kind.py`（新） | 六类事 `fight/talk/observe/learn/rescue/travel` + 权重（语料类别密度 / 气血 / 等级 / 上回类型冷却 / 无素材不打架）+ `choose()`（同 seed 可复现，`RECTO_PET_FORCE_KIND` 可钉死） |
| `pet/checks.py` | `plan(prefer=)`：挑了「会」只出人的坎、「学/救/行」只出路的坎、「斗」两样都出；同趟不重复 |
| `pet/chronicle.py` | 节奏闸门之后选类型 → **只有「斗」结算交手** → 判定按类型挑池 → `echo` 落库 → `last_kind` 记进状态；`backfill` / `repair` 同口径（脏老记录按 `growth.kind` 或重挑） |
| `pet/chronicle.py` `_echo()` | 这一趟的来处（书 / **读到哪一章**（目录名）/ 进度 / 分钟 / 划线书签 / 母题 / 语料词带类别与次数 / 语料窗口天数）。**只进页面，不进提示词** |
| `pet/chronicle.py` `_echo_from_cycle()` | 老游历没存来处 → 从 `cycle.evidence` 补算（**只补来处，不补类型**：当年没掷过，补一个就是编） |
| `pet/skills/adventure.md` | 硬前提改成「`duel` 有就必须打出来，**没有就不许打**」+ 新增「这一趟是什么事由 `encounter` 定」+ 四步结构对六类都成立 |
| `pet/skills/chronicle.md` | 新增 `last_kind` 输入；「长哪门功夫」表补两行（上趟只是赶路 / 去见人且话没说成）；明写**没有打斗的回合照样能长本事** |
| `app/ui/assets/pet.*` | 故事卡与往期条目：类型徽标（悬停看理由）、「这一趟的来处」块（书 · 章 · 进度 · 分钟 · 母题 · 语料 chips，语料标「近 N 天」） |
| `tests/test_pet_kind.py`（新 42 项） | 类型池 / 权重三条硬规矩 / 可复现 / 判定池 / echo 六种输入 / 端到端（`talk` 不出 duel、提示词里没有书名与语料词、落库带 kind+echo） |
| `tools/probe_pet_kind.mjs`（新 17 项） | 真浏览器：徽标 / 来处块 / 章·进度 / 语料窗口 / 往期与记录一致 / 390px 不溢出 / 0 报错 |

**顺带修的真 bug**
- `jsonout.as_text()`：模型偶尔把分段写成**字面** `\n`（JSON 双重转义）→ 整篇没有真换行、
  页面成一整块。加了兜底（**至少有 3 处字面 `\n` 且一个真换行都没有**才解码，宁可保守）；
  已落库的第 2 / 8 回按同一规则修好（改前备份 `/tmp/pet.sqlite.bak-newline`）
- `duel_shown` 语义：没交手时不再写 `True`（闸门那条"没交手视为满足"不变，但落库字段如实写 False）

**验收**
- 真跑一回：`kind=talk`（人多的纹理 + 带着伤）→ 无交手、两道都是人的坎、故事「第八回 · 夜雨借灯」
  写的是去见一个人、成长显为「踏雪无痕」
- 全套 58 个测试文件通过；`probe_pet_kind` 17/17、`probe_pet_story` 33/33、`probe_pet` 28/28

**已知边界**
- 类型是"这一趟的骨架"，不是剧情大纲：具体写什么仍由说书人按 `shape` 与 `world` 定
- 老记录不补类型（没有真实掷骰依据），只补来处

---

## T4 · 历史回合的战斗记录可查看 `todo`

**目标**：往期游历里，每回若打过，提供折叠入口查看完整战报
（逐拍、掷骰、气血变化），与最新一回一致。

**现状**：战报只挂在最新一回（`adv-hero`）。

---

## T5 · 书架支持上传书籍 `todo`

**目标**：书架页提供一个「上传书籍」按钮，用户可从本机选文件（epub / txt 优先）
直接传进书库，落盘后立刻出现在书架，可读、可同步。

**要点**
- 上传走 `POST /api/shelf/upload`（multipart 或流式），落 `[library] dir`（或其 `download_subdir`）
- 文件名清洗：去路径、去非法字符、重名自动加序号；**绝不覆盖已有书**
- 类型白名单按扩展名（epub / txt / pdf …）+ 内容嗅探双确认（防改后缀）
- 上限可配（默认按单文件大小），超限/类型不符返回人话原因
- 落盘后自动补边车（`.recto.json`）与云端身份（可选「上传完顺手传到掌阅」）
- 上传进度与结果复用现有任务槽（`job_begin` / `job_update`），书架即时追加卡片

**验收**
- 手机上也能选文件（`accept=".epub,.txt"` 生效）
- 上传一本 epub → 书架出现、可打开、进度从 0 开始
- 重名上传不覆盖原书
- 非白名单文件被拒且提示得是人话

---

## T6 · 划线/笔记保存偶发 "fetch failed" — `done` (2026-11-16)

**病例**：公网隧道上写「划线 + 笔记」偶尔提示 `Failed to fetch`，而「只划线」看起来正常。

**结论**：这是**网络级**失败（请求没走完），不是服务端拒收。证据：服务端
`data/recto.log` 一次 `database is locked`（那是另一回事，15s 后返 500）；而库里存在
用户"报失败"那几次留下的行 —— 部分请求其实**落库成功**，只是响应在回程丢了。
带 body 的 POST 浏览器不会自动重试，所以前端必须自己补，而补之前要先让重发安全。

| 位置 | 内容 |
|---|---|
| `services/reader_state.py` | 新增 `find_note()`；`add_note()` 按 **(书 + 章 + 起止字节) 幂等**（同段重复提交只留一行，先划线后补笔记落在同一行并补上批注） |
| `app/reader.py` | `POST /note` 返回 `dupe` 标记；新增 `POST /read/api/client-error`（前端网络级失败上报，落 `recto.log`） |
| `ui/assets/reader.js` | `isNetErr` / `reportClientError` / `apiRetry(path, opts, 2)`：只在 `TypeError` 时退 500ms 重发一次；划线、书签（新增/删除）、笔记（更新/删除）都走 `apiRetry`；保存后按 id 去重；提示语分流（`网络中断…已重试` vs `保存失败: HTTP 500`） |
| `services/notes.py` | `text_of_span` 跳过落在标签内部的偏移 + 对齐 UTF-8 边界 ⇒ 云端正文字段不再带 `class="calibre7">` 前缀与 U+FFFD |
| `tests/test_note_resilience.py` | 42 项：服务层幂等 / `dupe` 标记 / 上报端点 / 前端契约 / 文本切点清洗 |
| `docs/READER.md` §6.6 | 排查口诀（先 `grep '阅读器前端报错'`，没有就是"其实落库了"） |

**顺带清理的污染**：本轮之前的探针把约 50 条测试划线推进了 掌阅云端的《九诗心》
（同步日志有 `推划线 · 划线 72 / 49 / 52`）。已按"本地删 → `notes push` 差集删云端"
清掉，云端回到 5 条；本地留一份备份 `~/.backup/reader.sqlite.bak-*-probeclean`。
**教训**：`tick_notes` 会把本地测试数据推上云端 —— 在真实账号上做探针必须
先 `notes push` 停掉 / 或把测试写进临时书，不能写进真书。

---

## 约定

- 分支：日常在 `pet` 分支工作。**`master` 上还没有 pet 模块**（书宠 / 其页面 / 其测试整套
  都只在 `pet` 分支），所以书宠相关任务只推 `pet`；不要把单个 commit cherry-pick 到 `master`
  —— 那会把 README 弄冲突、把 pet 文件做成 DU（deleted by us）。
- 非书宠的功能（阅读器 / 云端同步 / 摘录 / 语料）照旧在 `master` 上做，需要时合并进 `pet`。
- 提交前 `git status -sb` 确认分支。**切换分支前先确认生产服务能不能跟着换**
  （`checkout master` 会把 `src/recto/pet/` 从工作树删掉，5015 一重启就挂）。
- 每轮收工更新本表状态。

---

## T7 · 书宠「判定了却没关联 / 读了很多回不见长」 — `done` (2026-11-16)

**病例**（用户两问）：① 判定事件与经历过程看不到关联；② 连续十几回看不到修为/能力增长。
查下来是**机制问题，不是"时间未到"**：

| 症状 | 真因 |
|---|---|
| 十几回不长本事 | 升级曲线 `100 × level²`（4 级就要 1600），而一回合推演只给 12~30 点 → 要读几十小时才升一级 |
| 长了也没知觉 | 过了 800 故事日的技能闸门（≈ 8 小时阅读一级）；且技能上限按**升级后剩下的零头**算，一升级上限反而回落（已练的等级就此卡死） |
| 判定与成长对不上 | 顺序是「判成长 → 再掷骰」，这一趟没过的那道坎 / 打输的那场，要到**下一回**才影响成长 |
| 潜能点不显眼 | 等级不动 → 潜能不涨；涨了也只有一个小数字，没有"待分配"提示 |

**改法**

| 位置 | 内容 |
|---|---|
| `pet/chronicle.py` | 顺序改成 **掷骰 → 判成长 → 写故事**：`judge()` 新增 `this_duel` / `this_checks`（这一趟的），`last_*` 退居次要；dry 在副本上掷，不污染存档 |
| `pet/skills/chronicle.md` | 新增 `this_*` 输入与"判词必须写得出这一趟吃了什么亏、因此长了什么"；口径数字与代码对齐（3.2/日、300 故事日一级） |
| `pet/skills/adventure.md` | 新增「坎 → 吃亏 → 长进」必须成一条链，不许三件各写一句 |
| `pet/charsheet.py` | `exp_needed` 改 `24 + 10*(level-1)`；新增 `lifetime_exp()`（技能上限看一辈子挣的，不看零头）；`skill_cap` 的悟性折从 `attr/30` 改 `(10+attr)/40`（起步不再打到三分之一）；view 加 `exp_left / exp_pct / exp_total` |
| `pet/growth.py` | 新增 `settle_levels()`（幂等兑现存量修为，含潜能/声望/先天属性）；`SKILL_DAYS_PER_LEVEL` 800 → 300 |
| `cli.py` | 新命令 `recto pet settle`（曲线改过之后把悬空的修为兑现成等级） |
| `ui/assets/pet.js` | 人物卡修为显示"百分比 + 还差多少"、多一行累计修为；潜能变 "N · 待分配" 并给分配提示 |
| `tests/test_pet_growth_sync.py` | 27 项（曲线/一辈子修为/升级不压上限/settle 幂等/载荷带 this_*/判定不带 d20/故事与成长看到同一批坎） |

**生产兑现**：`recto pet settle` 把 Lv.4 → **Lv.11**（+21 潜能 / +35 声望 / 悟性+2 根骨+1），
界面立刻能看见"读过的书变成了本事"。备份 `data/pet/char.json` → `/tmp/char.bak-t7.json`。

**没做**：判定结果要不要二次影响当晚的故事走向（现在只影响成长与开场的坎）。

---

## T8 · 技能升级机制: 熟练度 + 上限与等级挂钩 — `done` (2026-11-16)

**病例**（承 T7）：T7 把曲线调顺了, 但"技能等级"这条路仍然是**说书人一句话给一级**:
判定每回合最多挑一门 +1 级, 而读者在战报里明明看着自己使了十二剑, 剑法纹丝不动。
另外上限拿"一辈子累计修为"算, 等级只是个副产品 —— Lv 对本事没有约束力。

**改法**

| 位置 | 内容 |
|---|---|
| `pet/charsheet.py` | `PROF_PER_LEVEL = 120`；`prof_of` / `add_practice`（满格升级、到上限不再吃熟练度、只增不减）；`skill_cap(等级, 悟性)` 改成**只看等级**（`level_exp` 台阶 + 原式立方根 + 悟性折）；`cap_for` 改走它；`view` 每门技能带 `prof / prof_need / cap / prof_pct`；schema 规则改写 |
| `pet/growth.py` | `PRACTICE`（出手 3 / 绝招 4 / 拆招 1 / 过坎 9 / 没过 6 / 判词 25）+ `PER_SKILL_PER_CYCLE_CAP = 50`；新增 `practice_from`（按**真用过的招**记账）与 `practice_judged`（判词那一笔）；`award_judged` 不再直接给级, 只加一笔熟练度；自动内功也受上限约束 |
| `pet/duel.py` | 战报每拍带 `sid`, 结算后给 `usage`（自己出手几拍 / 各门绝招几次 / 被攻几拍） |
| `pet/checks.py` | 每道坎带上用的是哪门功夫 |
| `pet/chronicle.py` | 判成长之后按 `duel.usage` + `checks[].skill` 记熟练度 (`applied["practice"]`), 并落进游历的 growth；预演 (dry) 也在副本上算一份 |
| `app/ui/assets/pet.{html,js,css}` | 技能表新增「熟练」列（细进度 + `N/120`，到上限写「满」）；人物卡显示"等级 → 每门最高"；故事卡「练了: …」列出没升级但真练到的几门 |
| `pet/skills/chronicle.md` | 判词口径改写: `skills` = 「这一趟悟到了哪门」+25 熟练度, 并提醒别点已到顶的 |
| `docs/PET_GROWTH.md`（新） | 完整成长机制文档（时间 / 修为 / 等级 / 曲线 / 熟练度 / 上限 / 战斗口径 / 退隐传承 / 参数表 / 验证办法） |
| `tests/test_pet_practice.py`（新 43 项） | 上限单调与"只看等级" / 熟练度升级与封顶 / usage 记账 / 每趟封顶 / 自动内功不越限 / 界面契约 |

**量级校准**（一回合 ≈ 十来分钟阅读）

- 一场十来拍的遭遇 ≈ 60~70 点熟练度, 摊在 2~4 门上 → **主修那门约 2~3 趟升一级**
- 一小时阅读 = 十趟 ≈ 主修涨 3~4 级, 但**等级上限**会先卡住 (12 级 → 14 级)
- 上限随等级抬一格: 1 级 2 / 6 级 8 / 12 级 14 / 24 级 21 / 48 级 30（悟性 18 时）

**没做**：判定的二次影响（掷出来的结果仍不改当晚剧情走向）；熟练度的"退步"（一律不扣）。

## T9 § 进度回退：跨会话闸门 + 未推送时交给读者（2026-11-16）

**症状**：web 往后读几页、放置半小时、刷新又回到早先的在线进度（用户报过两次）。

**两条真因**

1. 跨会话回退闸门是 `REWIND_GUARD = 0.05`（只挡回退 > 5%）。日志实锤 17:45:54 推 15.3% → 17:46:33 被写成 14%（1.3% 从缝里过去）。
2. `flushProgress()`（pagehide / visibilitychange）撞上"补下一章 / 图片解码导致的页表重排"，`pageTopBlock()` 指回上一段 → 把已存位置写回去。

**改法**

| 位置 | 内容 |
|---|---|
| `services/reader_state.py` | 闸门从"回退 > 5%"改成**回退一格都挡**（`_REWIND_EPS`）；放行只有两种：同一会话（sid 相同）、或 `note_open(key, sid)` 记的"这条进度**之后**才打开本书"的会话（`_opened_after`）。`REWIND_GUARD` 保留但不再参与判定 |
| `app/reader.py` | `GET /read/api/book/<key>?sid=` 调 `rs.note_open(key, sid)`；`stale-rewind` 时落一条 `忽略旧会话进度 …` |
| `services/sync.py` | `peek_book` 新增 `choice_kind`：`cloud-ahead`（原有）/ `local-unpushed`（本地更靠前但**没推上去**，差 ≥ `max(1KB, min(8KB, 全书 2%))`，且与边车 `last_push_pct` 也差这么多）。`_choice_gap` / `_unpushed_bytes` |
| `ui/assets/reader.js` | ① `saveAnchor()`：没发生导航（翻页看 page+cur，滚动看 scrollY）却量出比 `S.saved` 更靠前的锚点 → 沿用 `S.saved`；`S.backIntent` 在往回翻 / 上一章 / `jumpTo` 时置位，明确"就是要回去"；② 提示条三种情形（含 `local-remembered`：`localStorage['recto.pos.<key>']` 记得更远 →「回到上次位置」/「从当前开始」）；③ boot 带 `?sid=` |
| `tests/test_progress_revert.py`（新 48 项） | 闸门 / 现场判定 / 门槛 / `peek_book` 三种结局 / 前端契约 |
| `tools/probe_progress_revert.mjs` / `probe_reader_remembered.mjs` | 真浏览器：旧会话回退被挡、同会话放行、两种提示条的文案与按钮、`从当前开始` 之后不再问 |

**边界**：三条提示**都不自动改数据**；不选就按本地继续读，下次打开再问。
跨设备"最后写入者胜"不变：另一个设备**在这条进度之后**打开过书，它回翻就是合法的。
