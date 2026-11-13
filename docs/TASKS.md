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
| `tools/probe_cloud_upload.mjs` | 14 项真浏览器（列 / 按钮文案 / 数量 / 隐藏 / 窄屏不溢出） |

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

## 约定

- 分支：日常在 `pet` 分支工作，功能完成后 cherry-pick 回 `master` 双推
- 提交前 `git status -sb` 确认分支
- 每轮收工更新本表状态
