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

## T4 · 历史回合的战斗记录可查看 `done` (2026-11-20)

**目标**：往期游历里，每回若打过，提供折叠入口查看完整战报
（逐拍、掷骰、气血变化），与最新一回一致。

**落点**：由 **T16 的故事档案页**（`/pet/story/`）一并解决 —— 前台那条路
不分章节，按回查阅就去档案页，每回一个折叠块，展开即见交手战报 /
路上的坎 / 这一趟的来处 / 本回所显。

* 后端：`pet/story.py` 的 `archive()` 走 `chronicle.adventures()`（那里面
  已经把老记录缺失的「交手有没有写进正文」两面旗补算过）
* 前端：`app/ui/assets/story.js` + `pet-shared.js`（战报/判定/来处/徽标
  与主页共用一份零件）
* 验收：探针 `probe_pet_story_flow.mjs` 24 项（含「展开的那回带战报 / 判定 /
  来处 / 所显」）

---

## T5 · 书架支持上传书籍 `done` (2026-11-20)

**目标**：书架页提供一个「上传书籍」按钮，用户可从本机选文件（epub / txt 优先）
直接传进书库，落盘后立刻出现在书架，可读、可同步。

**要点**
- 上传走 `POST /api/shelf/upload`（multipart），落 `[library] dir`（或其 `download_subdir`）
- 文件名清洗：去路径、去非法字符、重名自动加序号；**绝不覆盖已有书**
- 类型白名单按扩展名（epub / txt …）+ 内容嗅探双确认（防改后缀）
- 上限可配（默认按单文件大小），超限/类型不符返回人话原因
- 落盘后自动补边车（`.recto.json`）与云端身份（可选「上传完顺手传到掌阅」）
- 上传进度与结果复用现有任务槽（`job_begin` / `job_update`），书架即时追加卡片

**落点**

| 位置 | 内容 |
|---|---|
| `services/library_upload.py`（新） | `clean_name` / `unique_path` / `sniff_file` / `save_stream` / `ingest_uploaded` / `copy_in` |
| `core/multipart.py`（新） | **流式** multipart 解析（滑窗找 boundary + 按偏移读 part）；`spool` / `part_spans` / `read_span` / `text_fields` |
| `app/console.py` | `POST /api/shelf/upload`（`_handle_shelf_upload`）；书架页「上传书籍」按钮 + 隐藏 file input；XHR 传（要上传进度） |
| `services/reader_state.py` | **内容去重改成只在不同书库根之间**（见下） |
| `tests/test_library_upload.py`（新） | 33 项：文件名清洗 / 类型双确认 / 不覆盖 / 只落书库 / 大小上限 / multipart 切分 / 流式落盘 |
| `tools/probe_shelf_upload.mjs`（新） | 11 项真浏览器：按钮 / accept / 传完上架 / 重名不覆盖 / 非白名单被拒 / 手机不溢出 / 收尾清理 |

**验收**

* 手机上也能选文件（`accept=".epub,.txt"` 生效）
* 上传一本 epub → 书架出现、可打开、进度从 0 开始
* 重名上传不覆盖原书
* 非白名单文件被拒且提示得是人话

**三个踩出来的真问题**

1. **内容去重把「同一本书的两份副本」合并了** —— `reader_state.scan()` 原来按
   内容指纹全局去重（本是给「同一本书经多个挂载点出现」用的），结果**同一书库
   目录里**内容相同的两份文件也被合并: 上传同一本书两次，第二份成了「文件在
   磁盘上、书架上却看不见」的幽灵文件。改成**只在不同书库根之间去重** ——
   跨根才是"同一个文件的两条路径"。
2. **multipart 滑窗的 `skip` 在裁剪后没重算** —— 窗口裁掉前缀后相对起点跟着
   挪，否则 `skip` 指着裁掉的地方，越界后 part 结束位置被算到文件尾之后
   （实测内容多出 21 字节）。
3. **滑窗与 part 头读取共用一个句柄** —— 读 part 头时 `seek` 走了，窗口的读
   位置就跟不上。滑窗自己开句柄。

**已知边界**

* 传输层受限于 `Content-Length`（浏览器 `fetch`/XHR 都带），没做 chunked 编码
* 大文件上传期间**占着连接**（没有分片断点续传）
* 上传**不自动传掌阅云** —— 两件事分开，免得"传一本书"悄悄变成"把整个书库推上公网"
* 单文件上限默认 200MB（`[library] upload_max_mb`，0 = 不限）

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

- 分支：日常在 `pet` 分支工作。`master` 上**有** pet 模块，但停在合并点 `7c01b7c`
  （T16 之后的书宠改动都只在 `pet` 上），所以书宠相关任务只推 `pet`；不要把单个 commit
  cherry-pick 到 `master` —— 那会把 README 弄冲突、把 pet 文件做成 DU（deleted by us）。
  要让 `master` 追上，就等一次整体合并（像 `7c01b7c` 那次），别逐个搬。
- 非书宠的功能（阅读器 / 云端同步 / 摘录 / 语料）照旧在 `master` 上做，需要时合并进 `pet`。
- 提交前 `git status -sb` 确认分支。**切换分支前先确认生产服务能不能跟着换**
  （`checkout master` 会把 `src/recto/pet/` 退回 `7c01b7c` 那份旧代码 —— T16 之后的
  故事读本 / 排版 / 判定嵌入全没了，5015 一重启就退化）。
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

## T10 § 行囊与银钱：受伤之后能自己活下来（2026-11-16）

**症状**：书宠的气血**永远停在 20/97**（实测两天），故事里每回都带伤 —— 出场一次伤一次，
永远不会好。旧代码只在回合末尾回 18%，追不上"一刀砍掉半条命"。

**做法**（`src/recto/pet/gear.py` 新模块 + `charsheet` / `chronicle` / `checks` / 页面接线）

| 位置 | 内容 |
|---|---|
| `pet/gear.py`（新） | 物件表 28 样（药 / 吃食 / 兵器 / 护具 / 佩物 / 杂用）+ 银钱账；`selfcare`（先吃药 → 伤重就医馆/客栈 → 没钱打坐）+ `aftermath`（赏钱 / 脚钱 / 谢礼 / 纸墨钱 / 战利品）+ 买卖装卸 + `mods()` / `check_bonus()` |
| `pet/charsheet.py` | 存档加 `money` / `bag` / `equip`（老存档补起步那一份）；`gear_mods()` 是装具加成唯一入口；`d20_stats()` 的护体 / 命中 / 兵刃骰 / 先攻 / 暴击各多一行 `src="gear"`；`derive()` 的 `hp_max` / `neili_max` 吃佩物百分比 |
| `pet/chronicle.py` | 出门前 `selfcare` + 战后 `selfcare` + 路上 `aftermath`；`_gear_brief` 进 payload；**核正文** `_gear_need` / `_gear_in_body`（用了药没写 → 重写一次 → 仍漏记 `gear_omitted`） |
| `pet/checks.py` | 行囊里的杂用（火折子 / 麻绳 / 名帖）给对应的坎 +1（封顶 +2）；**顺带修**：`plan()` 漏了 `skill` 一栏，导致"过坎长功夫"从来没记过账 |
| `app/pet.py` | `GET /api/gear` + `POST /api/gear/{buy,use,sell,wear,unwear}` |
| `ui/assets/pet.*` | 人物面板第二张卡「行囊与银钱」：钱袋徽标 / 三个装具槽 / 行囊（用·带上·卖）/ 药铺铁匠铺（买） |
| `pet/skills/adventure.md` | 新增「照实写这一趟的钱与药」整节 + 输入里加 `gear` 示例 |
| `docs/PET_ITEMS.md` | 新文档：恢复路径 / 钱从哪来 / 物件表 / 装具口径 / 故事侧闸门 / 边界 |

**实测**（生产 5015，2026-11-16 18:59 第十九回）

- 出门前「用了金创药, 气血 +34；用了金创药, 气血 +34」→ 气血 20 → 97；行囊里的两份药真扣掉
- 那一趟是「走了一趟远路」：脚钱 +10 文，钱袋 60 → 70 文
- 页面上钱袋 / 三个装具槽 / 行囊 2 行 / 铺子 28 样都在

**测试**：`tests/test_pet_gear.py`（104 项）+ `tests/test_pet_gear_narr.py`（19 项，含重写与漏项记账）
+ `tools/probe_pet_gear.mjs`（23 项真浏览器）；全套 66 个测试文件通过。

## T11 § 点划线看笔记：按章匹配 + 划线不再被别章抹掉 — `done` (2026-11-17)

**症状**：在「建安二十二年的瘟疫」（第 10 篇）**第一页点普通正文**，弹出的却是
第 8 篇「人背负着世界的恶意生活」那条笔记（1555~1945）—— 看起来像"点击位置算错了"。

**两个真根因**

1. **章内字节偏移脱章比较**：`start_off / end_off` 是章内偏移，各章都从 0 起算。
   `noteAtPoint` 在没传章时扫全书，第 10 篇第一页的偏移正好落在第 8 篇那条的区间里。
2. **划线被别章抹掉**（顺带查出）：`CSS.highlights` 是全局注册表，旧 `paintHighlights`
   每次先删掉全部 `rv-hl-*` 再只装当前章 → 预取的下一章一画，前一章划线全消失。
   所以"点划线看笔记"在很多章上根本点不中（高亮也被抹了）。

**改法**

| 位置 | 内容 |
|---|---|
| `ui/assets/reader.js` | 新增 `pointAt(x,y)` → `{ch, byte}`（取 `.rv-blk[data-ch]`）；`noteAtPoint` 缺章时用点到那块的章，认不出章就**不弹**，逐条 `+n.chapter !== +ch` 过滤 |
| `ui/assets/reader.js` | 划线高亮加**按章登记表** `HLREG` + `hlCommit()` / `hlForget(ch)`：只摘本章旧区间、按全量重建；换章清空 DOM 时连登记表一起清 |
| `tests/test_reader_note_click.py`（新 20 项） | 源码契约：带出章 / 缺章不猜 / 按章过滤 / 无旧写法 / 登记表与 rebuild |
| `tools/probe_reader_note_click.mjs`（新 11 项） | 真浏览器：第 10 篇点正文不弹 / 第 8 篇点中划线弹出且章名对 / 点空白不弹 / 0 JS 报错（带进度快照还原） |
| `docs/READER.md` §6.7 | 点划线口径补"按章匹配"与两条坑 |

**验证**：旧代码复现失败（第 10 篇弹出「屈原：时间的焦虑」那条）；修后探针 11/11、
全套 67 个测试文件通过、`probe_reader_notes` 28/28。

## T12 § 交手里的「剑招」: 三处一起修 — `done` (2026-11-17)

**问题**: 22 回游历里只有两回动了手, 战报里招式栏几乎总是空的 —— 读者看到的是
"老是平平地砍"。

**三处真因 (都实测过)**

| 处 | 旧行为 | 现在 |
|---|---|---|
| 交手长度 | 对手气血/伤害各自独立归一, 一拳三成半气血 → 四拍收场 | 按"几拍见血"配对 (双方约五拍倒), 中位数 14~17 拍; 分母夹在同量级 0.75~1.28× |
| 对手拆账 | 只改 `attack` 数值, 掷骰仍读 pseudo 的 `attack_parts` (面板 +8、实际 +14), 八十场只赢 5 场 | 归一后重写拆账; 护体/命中反向浮动; 兵刃骰文本同步重算 |
| 守势类绝招 | 使完直接 return = 白扔一拍 (少 14 点伤害换 4 点减伤) | 出招后照旧递一手 (守中带攻); 按战况选招; 同场使过的招降权 |

**新增**: `growth.drill` —— 「学」类那一趟磨招 (绝招 +26 / 主修兵器 +20, 幂等、
不点没学过的招); `chronicle._moves_in_body` —— `moves` 非空而正文没点招式名则重写一次。

**验收**: `tests/test_pet_duel_balance.py` 20 项 + 全套 67 个测试文件通过;
生产 dry-run: 11 拍 / 对手 1d10+14 vs 我方 1d8+14; 真跑第 23 回 (observe) 无误。

## T13 § 阅读 → 化身 (`spark`): 让故事真的从读到的东西里长出来 — `done` (2026-11-17)

**问题**: 读者反馈「故事跟当前的阅读经历关联比较淡」。原意是: 模型该据"书的大致
情况 + 这段时间撞见的语料"做**发散联想**, 据此推进情节。

**真因 (两层, 都在输入侧)**

| 层 | 旧做法 | 后果 |
|---|---|---|
| 语料 | 只发**类别计数** (`person 1663`) | 模型只知道"这一路人多", 不知道撞见的究竟是什么样的东西 |
| 书 | 只发整本书的抽象种子 (`seeds`), 同一本书一百回同一份 | 时间维度的"这一段读到哪儿"完全缺失 |

**改法**: 新 `pet/spark.py` 一道**化身**工序 (story 之前, 一次模型调用, 六小时缓存):

```
别人书里的名字 → [一次联想] → 它世界里的人和物
某个人名 1663 次 →           → "一个把旧誓背在身上走了很久的人"
```

- 材料: 书卡 (定位/设定/概要/种子) + 读到的章 (标题 + 开头 400 字) + **真语料词** (带类别与次数)
- 产出: `echo` (3~5 条: `trace` 是什么 / `image` 能看见的碎片 / `keys` 2~3 个普通词) +
  `twists` (3 个可走的方向) + `scene` (天色/地貌/质地) + `motif`
- **去专名**: 每条过 `proper` 的禁用词表, 命中专名的**整条丢弃** (一条不剩就当没有化身)
- 写故事: `spark` 进 payload; `seeds` 把这一段的母题排在最前
- **闸门** `_spark_in_body`: 正文至少落地两条 echo (拿 `keys` 字面核对), 不够重写一次,
  两稿都不够 → `spark_omitted` 记账 (不装作写了)
- 页面: 「这一趟的来处」下多一块「**化成了**」(类别 + 一句人话 + 一个画面)

**验收**: `tests/test_pet_spark.py` 35 项; 全套测试通过; `tools/probe_pet_spark.mjs` 12/12
(桌面 + 390px); 生产第 24 回三条来处全部落地 (流放者的信 / 糊补丁的窗纸 / 晕开墨的简牍)。


## T14 § 笔记身份: 「一本书只一个抽屉」变成常驻机制 — `done` (2026-11-18)

**起因**：11-18 上午把笔记身份统一成小写（依据是 APK 里 `getFileMD5()` 裸值），
当日下午又改回大写（依据是手机自己写的条目落在大写抽屉）。两次都是"全局切一个常量"，
用户指出：**这不是一次性迁移，机制本身要在实现里站住**，并按同一标准把已有笔记/划线规整。

**机制（现在在代码里，不靠记忆）**

| 层 | 规则 | 位置 |
|---|---|---|
| 内部坐标系 | `zy_key` / `zy_seen` / 墓碑差集 / 幂等判断一律**大写** | `notes.norm_key` / `_upper_local_keys` |
| 写入身份 | **按书**选：`wire_id(ids, sources)` = 已经有内容的那个抽屉，两个都有才大写 | `notes.wire_id` |
| 发送边界 | key 前 32 位换成 `wire` 的大小写；删除用云端返回的**原始大小写** | `notes.wire_key(key, wire)` |
| "推过没有" | 只看**目标抽屉**（`cloud_versions_multi()["per_case"]`），不看合并视图 | `notes.push` |
| 漂移可见 | `status()["identity"]` + `pull` 结果 `identity.dual` + 控制台结果行 | `notes.status` / `console.notesBody` |
| 漂移可修 | `recto notes identity [--fix] [--book]` | `notes.identity_report` |

**`--fix` 的边界**：先 pull 再 push，然后**只删**"另一个抽屉里、现在大写抽屉也有的"条目；
正文拿不到的（老设备独占 / 文件不是同一份）一律不动 —— 宁可留两份，也不删别人的笔记。

**规整结果（2026-11-18 生产）**：7 本映射书逐本核对 —— 本地 key 全大写、`local_bad_prefix` 全空、
云端无一本分裂（大写抽屉 17 / 44 / 1 条，小写抽屉 0 条）。
`notes pull` 幂等（`unchanged` 不动），`notes push --dry-run` 0 待推。

**验收**：`tests/test_notes_wire.py` 扩到 40 项（新增 `wire_id` 四种情形 / `wire_key` 前缀换算 /
双抽屉合并端到端 / 小写独占书写回小写 / 本地 key 漂移归一）；全套测试通过。

---

## T15 § 故事要能一段一段读 + 人物收进抽屉 + 划线线色统一（2026-11-19）

**起因**：用户「用量恢复，请继续」后收尾工作树里积压的一批活。四件事，各自独立。

### ① 读故事：一段一段往下读

故事原本是**整篇铺在卡里**的 —— 一屏塞满，想接着上次那句读做不到。
现在故事正文收进**读本**：从"没读过的那段"开始，空格推进，读完自动收起。

| 层 | 做法 |
|---|---|
| 位置存哪 | **独立文件** `readpos.json`（不塞 `chronicle.json` —— 推演一回要跑 10~60 秒，期间它持有整份 state，最后一次性写回；前端在这段时间记的位置会被那份旧快照覆盖掉。真踩过：读着故事，推演一回，位置归零） |
| 写者 | HTTP 处理线程 + 可能的后台线程 → `fcntl.flock` 圈住"读 → 改 → 写" |
| 防手滑 | 一次跳超过 `MAX_JUMP=400` 段就夹住（手滑或脚本不该把位置一次推到天边） |
| 往回看 | 允许回退读，但**不改位置** —— 回头看一眼不该把进度抹了 |
| 复位/转生 | 位置一起清（故事都清了，位置没有意义） |

**接口**：`GET/POST /pet/api/reading`（`{no, para}`）；页面一次拿全 → `chronicle.state_view()["readpos"]`。

### ② 人物收进右侧抽屉（页面顶签 4 → 3）

原来「人物」是一个整页子页签，把故事挤到第二位。现在收成一行图标
（人物 / 行囊 / 本事 / 陪读），点一下浮出抽屉，里面再分四段。故事页始终是主角。

* 老书签 `#hero` → 自动打开人物抽屉并把 hash 改回 `#story`（不让人撞哑链接）。
* Esc 关抽屉；抽屉开着时页签点不到（先关再切）—— 探针踩过这个次序。

### ③ 划线线色统一（一个主题变量）

划线原本分 yellow/green/blue/pink/underline **五档各一个色**，满页五颜六色。
现在五档只用来区分"哪条"（抽屉里那颗小圆点与列表顺序），线本身统一。

* `--rv-hl`：淡色主题深蓝 `#1d4e89`，深色主题浅灰 `#c9ccd4`。
* 两个浏览器限制（实测，别再踩）：
  * `::highlight()` **不支持逗号多选择器** —— `::highlight(a, b)` 整条规则失效，
    所以五档必须各写一条规则（值都取同一个变量）；
  * `::highlight()` **支持 var() 从元素继承**，但 CSS 的 `prefers-color-scheme`
    **不跟随**页面上的 `color-scheme` —— 所以"auto 主题跟随外壳皮肤"只能由
    服务端把皮肤模式标到 DOM（`data-shell`），不能靠 media 查询。
* 抽屉里那颗小圆点也不再内联色，改用 CSS 变量（`reader.js` 删掉 `colorHex`）。

### ④ 原子写的临时名带线程 id

`config / llmconfig / reading_mark / secret_io / settings / charsheet / config /
life / reborn / world / chronicle` 的临时文件名原本只有 pid —— **同进程两个线程
同时写会用同一个临时名**，一个 `os.replace` 走之后另一个找不到文件，抛
`FileNotFoundError`（真踩过：陪读心跳写状态的同时，前端 POST 读位置也在写）。
现在临时名带 `threading.get_ident()`。

### ⑤ 万古江河的笔记重新对齐

本地文件覆盖后 md5 与云端一致（`3749B344…`），`text-mismatch` 闸门解除；
`notes pull --book 4f9cf8f5bf318028` → `unchanged 1`（本地正文与云端切出来的逐字相同，
200 字，一字不差）。此前它卡在 `text-mismatch`（本地与云端不是同一份文件）。

**验收**：`tests/test_pet_reading.py` 32 项 / `tests/test_reader_wavy.py` 40 项；
探针 `probe_pet_read.mjs` 37 项（读本逐段 + 抽屉 + 手机 + 0 报错）、
`probe_reader_hlcolor.mjs`（像素级线色）、`probe_reader_hlshell.mjs`；
`probe_pet_char` 59 / `probe_pet_story` 35 / `probe_pet_ui` 36 / `probe_pet_gear` 23 全绿；
全套测试只有 `test_excerpt_persist` 那 2 项**既有**失败（stash 对照基线同样红）。

### ⑥ 分支合并

`master` 与 `pet` 分叉了 4 个 commit（T2 注释层 / 云端上传 / cloudlink 字段）。
今天把 master 并进 pet（冲突 11 处：文档取 pet 侧新稿，console/cloudlink 取
"单按钮 + attached 口径"新写法），再把 master 快进到同一 commit。
两个分支现已同步在 `1fe7dd5`，都推了 origin。

**教训**：
1. 「整篇铺开」和「能接着读」是两种东西 —— 前者是一屏字，后者是进度。
2. 位置这类"频繁写的小状态"不能跟"偶尔整体覆盖的大状态"共用一个文件。
3. 原子写的临时名只带 pid 不够 —— 同进程多线程会撞。
4. `prefers-color-scheme` 不跟随 `color-scheme`；`::highlight()` 不支持逗号选择器
   但支持 var() 继承 —— 这三条是实测出来的，不是查文档。


---

## T16 § 故事不分章节：一条连续的路 + 档案页（2026-11-20）

**起因**：用户「书宠经历不要显式的分'章节'，你就参考 mud 的逻辑，滚动故事即可
（后台可以按照章节组织，但是需要打开单独页面来看），空格不是一行一个，而是段落。」

### 三个要求怎么落的

1. **前台不分章节** —— 故事页不再有「第 N 回」的卡片与往期列表，正文是**一条连续的路**。
2. **后台按回组织，但要单独页面看** —— 新增档案页 `/pet/story/`：按回摆，每回一个
   折叠块，带交手战报 / 路上的坎 / 这一趟的来处 / 本回所显。读本工具条上有个
   「按回查阅」入口。
3. **空格推一段** —— 段落 = 空行分隔的一段（不是一行）。

### 关键决定

| 决定 | 为什么 |
|---|---|
| **分段在后台做** | 位置（读到第几段）是跨端共用的索引；两边各切一次迟早错位 |
| 位置改记**全局段序号** | 一条连续的路只有一个游标；按回记要另开一套映射 |
| 位置**只前进** | 位置是"已经读过的最远处"；回头重读不该抹掉已读的地方 |
| 老档**自动迁移** | 老格式按回记段数 → `legacy_global_index` 换算成全局序号（一次性） |
| DOM **只往后追加** | 600+ 段时每按一次空格就重建上千个节点 → 明显卡 |
| 零件搬进 `pet-shared.js` | 主页与档案页共用战报/判定/来处/徽标；复制一份必然各自漂移 |
| 新标题**不带「第N回」** | 回数是后台的事；提示词 `skills/adventure.md` 同步改了 |

### 落地

- 新 `src/recto/pet/story.py`（`chapters` / `archive` / `flatten` / `split_paras` / `clean_title`）
- `src/recto/pet/reading.py` 重写（全局段序号 + 高水位 + 老档迁移）
- 新接口：`GET /pet/api/story`、`GET /pet/api/story/archive`、`GET/POST /pet/api/reading`（`idx`）
- 新页面：`/pet/story/`（`story.html` / `story.css` / `story.js`）
- 前端：`pet.js` 换成连续读本；`pet-shared.js` 收走共用零件

### 验收

- `tests/test_pet_reading.py` 55 项（全局序号 / 只前进 / 老档迁移 / 页面契约）
- 新探针 `tools/probe_pet_story_flow.mjs` 24 项（连续的路 / 空格推整段 / 档案页 / 手机）
- 改 `tools/probe_pet_read.mjs`（28）、`probe_pet_kind.mjs`（19）、`probe_pet_spark.mjs`（12）、
  `probe_pet_watch.mjs`（13）指到档案页
- 全套 python 测试绿（只有 `test_excerpt_persist` 那 2 项**既有**失败，基线同样红）


---

## T17 § 读本改成 MUD 式阅读窗口（2026-11-20）

**起因**：用户「现在前台阅读体验很差，1. 每次空格继续就出现 scroll bar 滚动，
我希望不要改变阅读网页本身内容高度，而是把文字在固定高度的阅读窗口内滚动，
即还是参考 mud 的显示方式，整个页面结构保持稳定；2. 不要一次敲击只有一行字，
应当是一页更好」

### 两个要求怎么落的

1. **固定高度的阅读窗口，页面结构不变**
   - 正文区从 `max-height` 改成 **`height`（固定）** —— 之前每追加一段容器就
     长高一截 → 整个页面被撑高 → 页面自己冒出滚动条，版面跟着晃
   - 加 `overflow-anchor: none`（浏览器会自作主张调整滚动位置）与
     `overscroll-behavior: contain`（窗口内滚到底不带动页面）
2. **一次敲击一页**
   - 空格从「推一段」（平均 39 字 ≈ 两行）改成 **翻一屏**（滚一个视口高）
   - 翻页是**瞬时**的，不做平滑动画 —— 动画期间 `scrollTop` 在动，连按空格
     每次只走半屏

### 关键决定

| 决定 | 为什么 |
|---|---|
| 滚动**只在窗口内** | 原来用 `scrollIntoView` —— 它会连带把**页面**卷走，整个版面跟着晃（要治的正是这个） |
| 位置 = **窗口底压着的那一段** | 眼睛看到屏幕底就等于读到那儿了；比"推一段"更贴近眼睛 |
| 位置**只前进** | 位置是"已经读过的最远处"；往回看不抹掉已读的 |
| 程序化滚动加**时间戳锁** | `scrollTop = x` 的 scroll 事件是**异步**的 —— 同步置位/复位布尔开关盖不住（真撞过：「从头读」复位后位置跳到 9 段） |
| 打开页面**不推进**位置 | 刚打开时窗口里摊着的那几段是"刚好装得下"的量，不是读者读过的量（不设闸 → 每开一次页面凭空多读几段） |
| `clamp=false` 存盘 | 后端那个"一次跳太远就夹住"的闸是给手滑/脚本用的；读者拖滚动条滑到底是合法的 |
| 存盘**防抖 600ms** | 滚动是连续的，一次翻屏会连着滚很多事件 |
| 段落**边界**保留 | 位置仍是全局段序号（跨端共用的索引），只是口径从"推了几段"变成"底压着第几段" |

### 落地

- `src/recto/app/ui/assets/pet.css`：正文区固定高度 + `overflow-anchor` +
  `overscroll-behavior`；窄屏高度跟着视口但仍固定
- `src/recto/app/ui/assets/pet.js`：读本重写（`readPageDown` / `readPageUp` /
  `readSync` / `readEnsure` / `readMeasureAll` / `readBottomIdx` / `readLock`）
- `src/recto/pet/reading.py`：`set_idx` 加 `clamp` 参数
- `src/recto/app/pet.py`：`POST /api/reading` 认 `clamp`
- `src/recto/app/ui/assets/pet.html`：提示改「按空格翻一屏 · PageUp 回看」

### 验收

- 新探针 `tools/probe_pet_mudread.mjs` 20 项（窗口固定 / 翻的正好一屏 /
  页面没被卷走 / 位置只前进 / 刷新接着 / 手机）
- 新探针 `tools/probe_pet_read_reset.mjs` 6 项（「从头读」复位归零）
- `tests/test_pet_reading.py` 55 → **76** 项（新增 MUD 窗口契约 21 项）
- 改 `tools/probe_pet_story_flow.mjs`（27）、`tools/probe_pet_read.mjs`（29）
- 全套 python 测试绿（只有 `test_excerpt_persist` 那 2 项**既有**失败，基线同样红）
---

## T18 § 书宠阅读界面「一屏装下 + 可见滚动条」— `done` (2026-11-21)

### 用户口径

> 「书宠的阅读界面我希望一个屏幕可以装下，即尽量 compact，
> 然后页面要有 scrollbar」

### 改之前的样子（1280×900 实测）

| 东西 | 高度 | 说明 |
|---|---|---|
| 页面总高 | **1535px** | 视口 900 —— 得滚掉 635px 才够看正文 |
| HUD | 123px | 头像/名/量条/chips 挤成两行 |
| 页签 + 图标栏 | 43 + 40px | 各占一行 |
| 卡头 + 概览 + 行动行 | 59 + 32 + 42px | 正文窗口上方堆着 325px 的「壳」 |
| 正文窗口 | 522px | `height: 58vh`（猜的） |
| 读本下面 | 200+px | 状态 / 语料 / 说明 |
| 滚动条 | **看不见** | Chromium 默认 overlay 滚动条（不占位） |

### 关键决定

| 决定 | 为什么 |
|---|---|
| **页面变成 flex 列 + `height: 100vh`** | 页面自己不滚，滚的是正文窗口 —— 这才是「一个屏幕装下」的真义 |
| 滚动条用 **占位式**（`scrollbar-gutter: stable` + `overflow-y: scroll`） | 用户要「页面要有 scrollbar」= 要**看得见**、能拖着走；overlay 滚动条既看不见又不占位 |
| 正文窗口高度由 **flex** 给，不用 `vh` 猜 | 壳的高度会随内容变（chip 换行、按钮换行），`vh` 是猜的、flex 是真的 |
| 诊断信息收进**折叠块**（默认收起），展开时自己滚（限高 200px） | 一屏装下时它们是负担，但**不该消失**（要查模型名、失败原因、语料遭遇）；限高 = 展开时不挤扁正文窗口 |
| 页签与图标栏并成一行（新增 `.pg-toprow`） | 少占一行是一行 |
| 窄屏 (≤860px) **退回页面可滚** | 一屏装不下那么多；硬塞会把正文挤成一条缝 |
| 档案页用 `:not(.rc-pet-story-page)` 排除 | 档案页内容长（37 回），不受一屏布局影响 |

### 落地

- `src/recto/app/ui/assets/pet.css`：新增「一屏装下」整段（页面骨架 / 壳压扁 /
  正文窗口 flex / 占位式滚动条 / 诊断折叠块 / 窄屏退回）
- `src/recto/app/ui/assets/pet.html`：页签与图标栏包进 `.pg-toprow`；
  状态 / 语料 / 说明包进 `#pet-diag` 折叠块（新增 `.pg-diag-body` 限高滚动容器）
- `tests/test_pet_onescreen.py`（新，43 项）：钉一屏与滚动条的契约
- `tools/probe_pet_onescreen.mjs`（新，21 项）：真浏览器量（一屏 / 滚动条占位 /
  翻一屏 / 档案页不受影响 / 窄屏 / 0 JS 报错）
- `tests/test_probe_hygiene.py`：加一条 —— 探针写 `/pet/api/reading` 必须
  `readPos()` 快照 + `writePos()` 写回（位置是只前进的高水位，探针翻了不还原
  会把用户顶到半路）

### 验收

- 实测 1280×900：页面 `scrollHeight == clientHeight == 900`（**不滚**），
  正文窗口 476px，滚动条占位 10px，空格翻一屏 Δ476px = 视口高
- 窄屏 390px：页面可滚、无横向溢出、滚动条仍占位 10px
- 深色皮肤：滚动条滑块用 `color-mix(var(--rc-fg) …)` → 深浅都看得见
- 既有 pet 探针无回归（`probe_pet_read` / `mudread` / `read_reset` / `read_sticky` /
  `story_flow` 的失败**基线同样红** —— 故事已读完 idx=695/695 的数据状态）
- 全套 python 测试绿（只有 `test_excerpt_persist` 那 2 项**既有**失败）
---

## T19 § 前台只显示最新一回 + 判定/启发嵌在正文对应位置 — `done` (2026-11-21)

### 用户口径

> 「书宠经历不要显式地分『章节』，参考 mud 的逻辑，滚动故事即可（后台可以按照章节组织，
> 但是需要打开单独页面来看），空格不是一行一个，而是段落」

上一轮（T16/T17/T18）做的是「全书摊平成一条连续的路」。这一轮改口径：
**前台只显示最新的一回** —— 因为「掷出来的事」本来就有它发生的那一段，
摊平全书时无法把判定/启发放回它该在的位置。

### 关键决定

| 决定 | 为什么 |
|---|---|
| 前台 = **最新一回** | 判定/启发/交手都要**回到正文里对应的那一段**；摊平全书时做不到 |
| 位置改记 **(回号, 段号)** | 全局段序号在「新回一出来」时会跳 —— 读者还没读新回，位置却显示「读完了」 |
| **锚不上就不锚** | 锚错比不锚更坏：模型自报「已写进正文」不可信（实测三回全留空），只能靠文本核实 |
| 锚点要**分得出高低** | 一段 3 字公共子串就命中地名 ≠ 那件事写在那儿；要比第二名高 ≥2 分 |
| 档案页加**正序/倒序** | 后台按回查时，"最新在前"和"最早在前"各有用途；记在 URL (`?ord=desc`) |

### 锚点算法（`story._anchor`）

```
score = 关键词命中数 × 4 + 最长公共子串长度
接受 = score ≥ 4  且  score − 第二名 ≥ 2
```

- 化身自带 **keys**（几个普通词，人名地名已过滤）→ 权重最高
- 否则按最长公共子串（≥3 字）
- 锚不上 → 收进正文末尾的「补记」区，明说「正文里没写这几件」

### 落地

- `src/recto/pet/story.py`：新增 `latest()` / `_inline_blocks()` / `_anchor()` /
  `_anchor_keys()` / `_spark_keys_map()` / `migrate_position()`
- `src/recto/pet/reading.py`：存档格式 **v3 = `{v, round, idx}`**；`set_pos()` 取代
  `set_idx()`（后者留成显式废弃的兼容层）；v1/v2 老档一次性迁移
- `src/recto/pet/chronicle.py`：化身的 `keys` 落进 `growth.echo.spark`
- `src/recto/app/pet.py`：新接口 `GET /api/story/latest`；`/api/reading` 收 `(round, idx)`
- `src/recto/app/ui/assets/pet-shared.js`：`checkInline` / `sparkInline` / `duelInline`
  （轻量一行，掷骰明细塞 title，完整战报留档案页）
- `src/recto/app/ui/assets/pet.{html,js,css}`：标题 + 类型徽标 + 「这一趟长出来的」+
  内嵌块渲染 + 「这一趟的账」折叠块
- `src/recto/app/ui/assets/story.{html,js,css}`：档案页正序/倒序切换
- `tests/test_pet_latest.py`（新，58 项）/ `tests/test_pet_reading.py`（110 项）
- 探针：`probe_pet_story_flow`（45 项）/ `onescreen`（21）/ `mudread`（20）/ `read`（29）/
  `read_reset`（6）/ `read_sticky`（6）

### 验收

- 全套 python 测试绿（`test_pet_latest` 58 / `test_pet_reading` 110 /
  `test_pet_onescreen` 43）—— 只有 `test_excerpt_persist` 那 2 项**既有**失败
  （stash 对照基线同样红）
- 探针全绿：story_flow 45 / onescreen 21 / mudread 20 / read 29 / read_reset 6 /
  read_sticky 6 / story 34 / char 59 / kind 19 / spark 12 / ui 36 / style 9
- 实测（生产 5015）：第 34 回 15 段 · 6 组内嵌块 · 位置记 `round=34 idx=15` ·
  刷新后落回同一段 · 空格翻一屏 Δ374px · 页面自己没被卷走

### 顺手修的既有失败

- `tools/probe_pet_style.mjs` **基线就红**：它直接点 `#llm-base` / 量 `#pet-state`，
  但那两块都在**默认收起的「设置」页签**里 → 祖先 `.pg-pane` 是 `display:none`
  → 量出 0×0、click 等到超时。改成先切页签 → 9/9 全绿

## T20 § 书宠正文排版（中文长文 / clreq）— `done` (2026-11-21)

### 用户口径

> 「正文排版要美观，字体可稍紧凑」

T19 把判定 / 启发嵌回了正文对应那一段，但正文本身还是"日志"的样子：
桌面 **一行 78 字**（版心 1083px ÷ 字号 14.7px）、左对齐、无衬线。
中文长文一行超过 45 字，右边缘全是参差的短头 —— 眼睛找不到下一行的起点。

### 七条规则（全部渐进增强，与阅读器正文同一套口径）

| 规则 | 为什么 |
|---|---|
| 版心上限 **36em** 并居中 | 一行 ~36 字；超过 45 字读起来会串行 |
| 两端对齐 + 末行左对齐 | 汉字等宽，伸缩只发生在标点处（clreq 3.1.2） |
| `line-break: strict` | 行首不排句读、行末不排开引号 |
| `text-autospace: normal` | 中西文之间不再贴在一起（clreq 3.2.4） |
| `text-spacing-trim: space-first` | 行首标点压缩，但保留首行缩进（clreq 3.2.5） |
| `hanging-punctuation: allow-end last` | 行末标点悬挂，右边缘齐（clreq 3.1.3） |
| 衬线 `--rc-serif` + `text-wrap: pretty` | 中文长文比无衬线耐读；避免末段孤行 |

一屏版（`body.rc-pet-page:not(.rc-pet-story-page)`）另给 **34em**，跟着 `.92rem`
一起收 —— 桌面一行 ~33 字，手机上 18~20 字（版心本来就窄，max-width 不起作用）。

### 内嵌块（判定 / 启发 / 交手）

原先 `margin-left: 1.6em` 是"跟着正文左缘缩进"。版心居中了以后这个写法会
**漂到版心外面**去 → 改成同样的 `max-width + auto margin`，再 `padding-left: 1.6em`：
左缘与正文同一条竖线，内容缩进一格，仍是"上面那段的事"的旁注感。
旁注用 `--rc-sans`，与正文衬线分开 —— 一眼分得出"这是正文，那是旁注"。

### 档案页

`.arch-text p` 原先在 story.css 里**写了三处**（后写的覆盖先写的），改版式时极易漏掉其中一段
—— 2026-11-21 就是这么漏的。合并成末尾一处，与前台同一组 clreq 属性；
字号 `.96rem` / 行高 1.8，比前台读本（`.92rem` / 1.72）略松（前台是"读"，这里是"查"）。

### 落地

- `src/recto/app/ui/assets/pet.css`：新「正文排版 (中文长文)」段 + 一屏版 34em + 内嵌块改版心对齐
- `src/recto/app/ui/assets/story.css`：三处 `.arch-text p` 合并为一处，补 clreq 属性
- `tests/test_pet_story_type.py`（新，28 项）—— 渐进增强属性拿不到 computed，只能验 CSS 声明
- `tools/probe_story_type.mjs`（36 项，真浏览器双视口 × 前台/档案页）：
  一行字数（18~45 之间）、justify、衬线落地、首行缩进 2 字符、`line-break: strict`、0 JS 报错
- `tools/measure_story_type.mjs`：只量不判的测量工具（调版心/字号时用）

### 验收

- `test_pet_story_type` 28/28 · 全套 python 测试绿（仅 `test_excerpt_persist` 那 2 项既有失败，
  stash 对照基线同样红）
- 探针 `probe_story_type` **36/36**：前台 1280 一行 37 字、390 一行 ~19 字；
  档案页 1280 一行 37 字、390 一行 18~22 字；两处 `font-family` 都落到 `Source Serif 4`
- CSS 是静态资产（每请求读盘 + `no-store`）→ **刷新即生效，不用重启**

### 顺手修的真 bug：「这一趟长出来的」整条空白

验收探针 `probe_pet_story_flow` 报 `FAIL 「这一趟长出来的」有内容 ()`。
查下来不是 CSS，是 T19 留下的一个洞：这一条只吃**模型自报**的 `growth_shown`
（`skills/adventure.md` 里让模型"照 growth 里的名字抄 1~2 条"），
而这一回实测它**一条都没给** —— 于是账本里有 `修为 +28`、`医术 熟练 +9`、
`追踪 熟练 +9`，界面上却什么也不显示。

模型自报不可信这件事在 T19 就已经记过一次（`duel_shown` 三回全留空），
`growth_shown` 是同一个病。修法是**只在内报为空时**按账本折算
（`RCP.growthChips(latest.growth)`，与档案页同一份实现），模型挑了就用它挑的 ——
自报的用处是"挑出写进故事的那两三条"，不是"决定有没有成长"。

`tests/test_pet_latest.py` 加两条钉住这个顺序（自报优先 + 只在自己为空时折算），
免得以后有人把它改成"一律按账本"（那样就把"写进故事"与"实际长进"混成一回事了）。

## T21 § 版面呼吸: 相邻块不许贴死 — `done` (2026-11-22)

**报障**：书架上那一格格子（`#shelf`）与它下面的「上传到掌阅云」折叠区（`#sec-ident`）
之间是 **0px** —— 两块贴在一起，看着像同一块。要求顺带扫一遍同类。

**扫法**：写了个一次性探针，遍历 `body *` 的相邻**块级兄弟**（同栏、都可见、都 ≥24px 高），
量 `next.top - prev.bottom`，打印 ≤7px 的对。桌面 1400 与手机 390 各跑一遍。

**扫出来的（真问题）**
1. `#shelf` → `#sec-ident` **0px** —— 报障点。`.shelf-grid` 有 `gap` 有网格，**唯独没有下边距**，
   而 `details.sec` 只写了下边距。
2. 摘录面板 `.pager` → `.timeline` **0px** —— `.pager` 只有 `margin-top`；而它在列表**上方**
   也出现一次（上条 + 列表 + 下条）。
3. 摘录卡 `.tl-meta`（来源/章节/位置）→ `.tl-content`（正文）**2px** —— 桌面与手机都一样。
4. 「导出到纸间」表单：`<label>` 与 `<input>` 是**纯 inline 流**，两者**并排挤在同一行**
   （卡片 1132px 宽，实际只用了左边 ~400px），字段之间 5px。
5. 设置页「节奏与状态」：`.st-row` 只有 `min-height`，没有内边距也没有分隔线 ——
   夹在开关行（8px 内边距 + 一根发丝线）之间时上下一齐贴死，0px。
6. 概览小标题 `.kicker` → 下面的图表/列表 **0px**；统计卡 `.kicker-sm` → 大数字 **0px**。
7. 手机专属：操作轨落回页头后与搜索栏只有 4px；摘录卡折成一列后时段/元信息/正文只有 2~4px。

**修法（只动边距，不动结构）**
- `.shelf-grid` 显式 `margin: 0 0 calc(var(--rc-gap) + 4px)`（桌面/手机都是 18px）
- `.pager + .timeline` / `.pager + .muted` 补 `margin-top`
- `.tl-item .tl-content` 上边距 2px → 8px
- `.kicker` 补下边距 8px、`.kicker-sm` 补 3px
- 设置页 `.st-block > .st-row` 与开关行同一节奏（8px 内边距 + 分隔线）；
  `.st-two` 行距 6→14、`.mk-opts` 行距 6→10、分类配色卡标题与色板 2→10
- 「导出到纸间」表单改单列网格：标签在上、控件占满一列、字段之间 12px
- 手机：`.rc-head-row:has(> .rc-rail:only-child)` 留 12px；`.tl-item` 折列后 `gap: 8px 0`

**约束（写进探针）**：相邻块之间**最小间距**钉死；0px 只允许出现在
「自带内边距 / 自带分隔线」的元件之间（`.st-sw` / `.st-row` / `.mk-row` / `.epub-row` /
`card-head` 对 `card-body` / `details.sec > summary` 对 `.sec-body`）。
间距用 `var(--rc-gap)` 表达，疏密档（compact / roomy）改了不该红。

**落在哪**
- `src/recto/app/ui/assets/recto.css`（网格下边距 / 分页条 / 小标题 / 摘录正文 / 手机两条）
- `src/recto/app/settings_page.py`（单列行节奏 / 两栏行距 / 配色卡）
- `src/recto/app/excerpts.py`（`#zhijian-form` 单列表单）
- 验收：`tests/test_spacing.py` **17 项**（查声明在不在）+ `tools/probe_spacing.mjs`
  **14 项**（真浏览器量几何，1400 与 390 双视口）

**别误记成回归**（都 stash 对照过基线，一样红）：`test_excerpt_persist` 2 项、
`probe_material_shelf` 2 项（搜索即时过滤 / 水波纹）、`probe_all_pages` 3 项（阅读页 404）、
`probe_btn_groups` 12 项（深色皮肤下 `/corpus/` 的 `.row` 按钮对比度）。

## T22 § 读本正文居中 + 设置页「书宠开关」— `done` (2026-11-23)

### 用户口径

> 「Recto 的 webui 的书宠，它自己走的路显示页面，正文需要和'按回阅读'一样居中，
> 然后 /settings 中增加一个'书宠开关'，可以一键 disable 掉所有书宠相关的内容」

### ① 正文居中：一条 `margin` 简写清掉了 `auto`

`#adv-read-body > p` 的基础规则里是 `margin-left/right: auto`（居中），
但一屏版后面写了 `margin: 0 0 .68em` —— **简写**，把左右 `auto` 一起清成 0。
于是正文贴着窗口左缘（实测 1400px 下左边 14px、右边 514px），
而旁注 `.adv-inline` 仍是 `auto` 居中，两者左缘错开一格。

修法（`ui/assets/pet.css` 一屏版那一段）：

```css
body.rc-pet-page:not(.rc-pet-story-page) .adv-read-body > p {
  max-width: 34em;
  margin-left: auto; margin-right: auto;   /* 补回来 */
}
/* 旁注同版心 —— 而且 font-size 也得跟正文一样: 34em 是拿字号算的宽度 */
body.rc-pet-page:not(.rc-pet-story-page) .adv-read-body .adv-inline {
  max-width: 34em; font-size: .92rem;
}
```

实测（1400×900）：段落宽 500，左右余白 **291 / 301**（差 10px = 滚动条占位），
中线偏 5px；旁注左缘与正文 **差 0px**；档案页本来就居中（左右差 0px）。
390px 窄屏版心比窗口还宽，退化成"铺满 + 滚动条 10px"，仍无横向溢出。

### ② 书宠开关：一个开关管五处

`settings.json` 新增覆盖项 `pet_enabled`（`on`/`off`；空 = 回到默认"开"），
优先级 **环境变量 `RECTO_PET_ENABLED` > `settings.json` > 默认开**
（读 `core/settings.py` 的 `pet_enabled()` / `pet_source()`）。

| 关掉之后 | 落点 |
|---|---|
| 顶栏「书宠」入口不出现 | `ui/shell.py` 的 `nav_html()` 过滤 `pet` 那一行 |
| `/pet` `/pet/story` 回书架；`/pet/api/*` 返 404 `pet_disabled` | `app/pet.py` 的 `dispatch()` 开头一道闸 |
| 阅读器右下角小图标不出现 | `app/reader.py` 的 `/read/api/pet-brief` 先问开关，关着直接 `ok:false` |
| 语料遭遇不记 | `app/reader.py` 的 `_track_chapter()` 早退 |
| 陪读心跳 / 后台推演空转 | `pet/companion.py` / `pet/chronicle.py` 的 `_loop()` 每轮现读开关 |

**关掉 ≠ 删数据**：存档 `char.json`、游历、语料遭遇原样留着，再打开接着走
（设置页文案明写这一条，免得用户以为会清空）。

设置页那张卡（`/settings`，默认展开）除了开关还报现状：
「已陪你读 N 分钟 · 走过 M 回 · 出处（环境变量 / 设置页 / 默认）」。

### 落在哪

| 位置 | 内容 |
|---|---|
| `core/settings.py` | `KEYS` 加 `pet_enabled`；`pet_enabled()` / `pet_source()` / `PET_SOURCE_LABEL` |
| `app/ui/shell.py` | `pet_on()` + `nav_html()` 过滤 |
| `app/pet.py` | `enabled()` + `dispatch()` 开头闸门 |
| `app/reader.py` | `pet-brief` 开关 / `_track_chapter` 早退 |
| `pet/companion.py` / `pet/chronicle.py` | `_enabled()` + `_loop()` 里挡 |
| `app/settings_page.py` | `pet_state()` / 卡片 / `api_post` 收 `pet_enabled` / JS 开关 |
| 验收 | `tests/test_pet_switch.py` **63 项**（开关三档来源 / 顶栏 / 分区闸门 / 阅读器 / 心跳 / 设置页 / CSS 契约）<br>`tools/probe_pet_center.mjs` **16 项**（真浏览器量左右余白与中线，1400 + 390 + 档案页）<br>`tools/probe_settings_pet.mjs` **20 项**（真浏览器: 关 → 入口消失 / 404 / 落书架 / 小图标不画 → 再打开 → 还原） |

**探针卫生**：`probe_settings_pet` 会改 `settings.json` → 收尾按开头读到的值还原；
`probe_pet_center` 纯只读（不翻页、不写进度）。

**别误记成回归**：`test_excerpt_persist` 那 2 项（正文补上 / `_noText`）stash 对照基线一样红。

## T23 § 内功与运功 (exert): 门派内功不再是装饰 — `done` (2026-11-23)

### 用户口径

> 「书宠目前有些技能一直没有触碰，类似剑法我可以理解是没有相应场景，
> 但是为什么门派内功也从来没有使用？这和侠客行 mud 逻辑不吻合」

### 查下来是**三处一起**把门派内功做成了死数

1. **内力上限只认 `type == "neigong"`**（取最大）。而基本内功的 type 是 `basic` ——
   读书自动长到 11 级，对内力上限 **一点贡献都没有**；门派内功又停在入门 1 级，
   于是书宠 18 级时内力上限还冻在 53（实测 `derive()["neili_max"]`）。
2. **门派内功没有任何熟练度来路**：`practice_from` 只记出手 / 绝招 / 拆招 / 过坎，
   判词每回最多点 1 门且倾向挑"明显的"（兵刃 / 绝招）。
3. **`SKILLS` 里每门内功的 `use` 写着"剑上更快一分 / 越练手越沉 / 落雪不陷…"，
   代码里一条都没落地**；`charsheet` 开头那句"内功 (exert) 与绝招 (pfm) 分开"
   也只是注释 —— 一处 exert 都没有。

### 改了四处

| 改什么 | 位置 | 口径 |
|---|---|---|
| 启用一台内功 | `charsheet._neigong_level` | 有门派 → **本门内功**；没有 → 等级最高的那门（照 `enable force`） |
| 内力上限 | `charsheet.derive` | 启用那门 ×10 + 根骨×2 + 基本内功底子（每级 2，封顶 +40）+ 本门 +3/级 |
| 本门内功各管一路 | `charsheet.SECT_NEIGONG` / `neigong_effects` | 观海诀(剑·命中) 磨墨功(刀·伤害) 养气诀(豁免) 踏雪功(闪避+先手) 通明诀(命中) 卧云功(气血) |
| **运功** | `duel._try_exert` | 加力（伤害%，只撑三拍，每场 2 试）/ 回气（内力换气血，占一拍，每场 2 次） |

熟练度：运成一式 **+6**，运功走岔 **+3**，打坐（`gear.selfcare`）**+4**。
`chronicle.md` 明写**判词不许再点内功**（重复记账）。

故事侧：`duel.exert` 进 payload；正文没有"内息 / 真气 / 丹田 / 调息"痕迹就重写一次，
仍没有记 `exert_omitted`（走岔的不强制写）。

### 数值校准（都实测过，不是估的）

- **回气要"够得着"才准用**（加值+10 ≥ DC）：1 级内功成功率不足一半，
  那一拍赌不起 —— 白挨一击比回几点气血更亏。于是它是**练出来的能力**。
- **加力只撑三拍**：一加到底直接把胜率从 65% 拉到 75%（600 场实测），
  那就不是"运一口气"了。
- 现态：600 场 66% 胜 → **71%**（加力与内力上限各一半贡献），
  交手中位拍数 16.8 → 18.1，绝招 4.25 → 4.12 每场（运功与绝招共用内力池）。

### 落点

| 位置 | 内容 |
|---|---|
| `pet/charsheet.py` | `SECT_NEIGONG` / `sect_neigong()` / `neigong_effects()` / `exert_spec()`；`_neigong_level` 改口径 + `_basic_neigong()` |
| `pet/duel.py` | `_try_exert()` / `_exert_mods()`；`_fighter` 带 `neigong`/`exert_used`/`dmg_buff`；`usage.exert` / `usage.exert_fail` |
| `pet/growth.py` | `PRACTICE["exert"|"exert_fail"|"rest"]`；`practice_from` 读 `usage.exert` |
| `pet/gear.py` | 打坐那一笔记一笔内功熟练度（`REST_PRACTICE`） |
| `pet/flavor.py` | 运功四路文辞（加力成 / 加力散 / 回气 / 走岔） |
| `pet/chronicle.py` | `_exert_need` / `_exert_in_body` + 闸门与 `exert_shown/omitted` |
| `pet/skills/*.md` | adventure 写运功 / chronicle 不许重复点内功 |
| `ui/assets/pet.js` | 交手数据卡多「运功」「本门内功」两行 |
| `docs/PET_GROWTH.md` | 新增 §12 整节 |
| 验收 | `tests/test_pet_neigong.py` **42 项**；`test_pet_duel_gate.py` 42（假模型那稿补了"内息"一句） |

### 留给下一轮

- 对手这一版**不运功**（pseudo 人物没有内功）。要加就先给 `_foe_fighter` 挂一份 `neigong` spec。
- 胜率已到 71%，再给任何一方加力都要先抬对手份量，否则会变成"必赢"。
