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

## T2 · 精排书籍的注释/旁注排版 `todo`

**目标**：对带复杂注释、旁注、多类型边注的精排书
（样本：`/read/b/da7e470fa0c2c4b8`），做一套灵活、可复用的排版支持，
换一本书不用重新写一遍。

**已知难点**
- 注释类型不止脚注：旁注、边注、批注、题解混排
- 锚点 id 在本样本里部分对不上 → 尾注正文必须按**内容匹配**兜底
- 分页模式（多栏）下新增注释区不能破坏列高/页数计算
- 原型期踩过：嵌套容器重复计数（`<aside>` 里的 `<ol>` 被算两遍），个别 mark 类型漏检

**待办**
- 通用的注释识别/归类层（按语义而非按 class 名硬编码）
- 分页与滚动两种模式都能用的注释呈现（弹出 / 侧栏 / 章末三种形态可选）
- 复用性验收：再挑一本结构不同的书，零改代码跑通

---

## T3 · 书宠故事：成长为主，关联阅读证据 `todo`

**问题**（用户原话归拢）
1. 每回推进**不一定**非要有战斗，重点是成长；故事要合理
2. 看不出每回进展与**这次读的内容**（章节、语料遭遇）的关联与启发

**方向**
- 遭遇从「必然一战」改为**按剧情需要的选项池**：斗、谈、观、学、救、避…
  由语料与当回阅读内容决定该出哪一类
- 故事必须显式引用**这一趟读了什么**：具体章节 / 母题 / 语料词 ——
  读者要能认出「这是我刚读的那段」
- 成长理由要能回溯到具体读数（已有 `reason`），再加一层「读了哪一段」
- 战斗保留（有语料/剧情支撑时），但不是默认项

---

## T4 · 历史回合的战斗记录可查看 `todo`

**目标**：往期游历里，每回若打过，提供折叠入口查看完整战报
（逐拍、掷骰、气血变化），与最新一回一致。

**现状**：战报只挂在最新一回（`adv-hero`）。

---

## 约定

- 分支：日常在 `pet` 分支工作，功能完成后 cherry-pick 回 `master` 双推
- 提交前 `git status -sb` 确认分支
- 每轮收工更新本表状态
