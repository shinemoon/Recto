# 内置阅读器（书库 → 阅读）

> 服务名：**云阅 Recto**（工程名 Recto）

> 2026-11-03 · 目标：**本地书直接在原书文件上读，不再需要切片重排**

## 1. 为什么不需要切片

云端阅读服务的 `readPercent` 是**字节比例**：

```
readPercent = (Σ bytes(spine[0..chapterId]) + f3) / Σ bytes(spine)
```

有些阅读器只认"页"，于是必须把 epub 切成"每页字节数相等"，让 `页号/页数`
近似这个比例。Recto 自己的阅读器没有"页"这个中间量：它直接按**原书字节**
记位置，所以切片这一步可以完全省掉，书库里的文件永远就是原书。

验收标准：读同一本书，阅读器算出的比例必须与 `core/geometry.py` 的算法
**逐位吻合**（`tests/test_reader.py` 有断言，实测差 < 1e-7，等价于 float32 存储误差）。

## 2. 坐标模型

```
全书字节流
├── 章 0 (spine[0])   0 .. size0            ← 封面/前置文档，catalog_id = 0
├── 章 1 (spine[1])   size0 .. size0+size1  ← 云端 chapterId = 0
└── ...
```

- 章节顺序 = epub 的 spine 顺序（txt = 按标题行切出来的章节）
- `chapterId` = 章下标 − 1（epub；与云端 `catalogCount` 口径一致），txt 直接取章下标
- `f3`（章内偏移）用**块起始字节**：正文被切成块级元素（`p` / `h1..h6` / `blockquote` / `li`…），
  每块带 `off`，写进度时就是"当前视口最上面那个块的 `off`"
- `readPosition` = `build_position(chapterId, f3)` → `<ch+1>_0_0$##3-0-<ch+1>-<f3>-0##$`
  - `pre` 那一段**就是 spine 下标**；`rprogress.chapter` 也是 spine 下标
  - 所以"阅读器 → 云端"必须传 `chapterId = chapter - 1`（`services/sync.position_for()`）
- `readPercent` = `(Σ bytes(章 0..章-1) + f3) / 全书字节`，落库前按 float32 对齐

**两端口径的自检命令**（位置 ↔ 百分比必须互相反解回同一个字节）：

```bash
python3 -m recto.cli align            # 逐本列出两侧 pct 与"按位置反解出来的 pct"
python3 -m recto.cli align --fix      # 按云端自己的 readPercent 重写 readPosition
python3 tools/audit_progress.py 剑来  # 逐条列出云端每份记录 (32/64/笔记本) 并核差几字节
```

> **2026-11-10 复核**：用 `tools/audit_progress.py` 对 4 本书 × 我们写的 / App 写的 /
> App 笔记本三类记录逐条验过，`readPosition` 反算出的比例与记录里的 `readPercent`
> 差 **0.0–0.8 字节** —— 两边的**计算与定位口径完全一致**。
> 那次的"跳到别处"不是公式问题，是**云端同一本书有两份进度记录**：
> 掌阅 App 只读写 `32 hex`（文件内容 MD5）那份，而且它一读书就把那份**回写成自己的
> 位置**；我们推的双写里 `64 hex` 还留着我们的值，旧闸门看 max 就判 noop，于是永不补推。
> 现在按 key 判定（`why=key-drift`）并由 `tick_drift` 定期核对补回；详见 README
> 「云端同一本书有两份进度记录」一节。

历史事故（2026-11-05 修）：push 时把 spine 下标直接当 `chapterId` 交给
`build_position()`，写出的 `pre` **超前一整章** —— `readPercent` 是 34.0034%
（chapterId 194 = spine 195），`readPosition` 却是 `196_0_0`。App「继续阅读」按
`readPosition` 跳，于是落在下一章，看着就是"云端进度到了本地不是真正阅读地点"。
现在 `align` 会把这类记录判成 `cloud_bad`，`--fix` 只改位置字段、不动进度值。

**精度标尺**（2026-11-05）：一个显示刻度 ≈ 2KB 正文（一屏 ≈ 600 字 ≈ 1.8KB）。

| 书的大小 | 刻度（0.001% / 0.01% / 0.1%） | 显示 |
|---|---|---|
| ≤ 2MB（如 320KB 的《示例短篇》） | 0.1% = 327 字节 | 1 位小数：`99.7%` |
| 2~20MB | 0.01% = 400 字节 | 2 位小数：`31.82%` |
| ≥ 20MB（41MB 的示例长篇） | 0.001% = 419 字节 | 3 位小数：`31.821%` |

固定的 1 位小数在长篇上 0.1% 就是 41KB（一万多字），界面上百分比会长时间
"不动"。位数由 `geometry.pct_decimals(total_bytes)` 算，前端
（`console.py` 的 `pct()`、`reader.js` 的 `pctText()`）用同一套规则，
接口里每本书都带 `bytes`，界面不猜。**向下取整**：宁可少说，不把没读到的
地方说成读过了。

**拉取优先用 `readPosition` 的 (chapterId, f3)**，而不是先把 `readPercent`
反解回字节：比例是 float32，反解会差个位数到几十字节（实测示例长篇差 1 字节）。
位置缺失、或位置反解出的比例与 `readPercent` 相差超过 4KB（说明不是同一份
文件）时才退回按比例反解 —— 拉取结果里的 `basis` 字段会写 `position` /
`percent`，界面上"按云端精确位置"就是走了前者。

块粒度是段落（比"页"细 1~2 个数量级），因此：

- 续读能回到**同一段**，不是页首；
- 划线的起止能写成 `positionstart` / `positionend`（云端同构）；
- 换设备/换阅读器只要拿到同一个字节偏移就能落到同一段文字。

## 3. 数据模型（`<data_dir>/reader.sqlite`）

| 表 | 字段要点 | 对齐云端的点 |
|---|---|---|
| `rbook` | key / path / kind / title / author / bytes / chapters | 书目 |
| `rprogress` | chapter / offset / percent / **position** / complete | `readPercent` + `readPosition` + `readComplete`（≥99.9% 判读完） |
| `rbookmark` | chapter / offset / summary / title | `uniquecheck` + `marksummary`（按位置唯一） |
| `rnote` | chapter / start_off / end_off / summary / remark / color / **anchors** | `positionstart`/`positionend`/`summary`/`remark` + 给 DOM 用的字符锚点 |
| `rsession` | start_ts / end_ts / minutes / day | 逐次会话 |
| `rtime` | book / day / **bitmap** / minutes / cloud_minutes | APK 的 `ReadTimeInfo`：每 书×天 一行，1440 位分钟位图（180 字节 base64）；`cloud_minutes` 是云端同一天的分钟数，展示取 `max` |

`anchors` 是 `{sb, sc, eb, ec}`：块字节偏移 + 块内字符序号。字节给云端，
字符给 DOM（精确还原高亮，不靠文本搜索）。浏览器支持 CSS Custom Highlight API 时
零 DOM 改动上色；不支持则退化成 `<mark>`。

## 4. 路由

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/read` | **302 → `/#library`**（书架已并入控制台的「书架」分区） |
| GET | `/read/b/<key>` | 阅读页 |
| GET | `/read/api/books` | 书目 JSON（`?q=` 搜索，`?refresh=1` 重扫） |
| GET | `/read/api/book/<key>` | 元信息 + 章列表 + 进度 + 书签 + 划线 + 时长 |
| GET | `/read/api/book/<key>/chapter/<i>` | 章节块（每块 `off`/`tag`/`html`/`text`） |
| GET | `/read/api/book/<key>/asset?p=` | 书内图片/字体 |
| POST | `/read/api/book/<key>/progress` | 写进度 `{chapter, offset}` |
| POST | `/read/api/book/<key>/bookmark` | 加/删书签 |
| POST | `/read/api/book/<key>/note` | 加/改/删划线笔记 |
| POST | `/read/api/book/<key>/session` | 阅读时长 `start|beat|end` |

阅读页走统一登录（`webauth`），跟其它分区同一套会话。

## 5. 书目扫描

根目录（按顺序）：

1. `cloud.books_archive_dir` —— **原书归档**（掌阅那几本的原件在这里）
2. `library.books_dir` —— 本地书库目录
3. `cloud.epub_dir` —— 云端下载目录

去重与过滤：

- 切片版（zip 里有 `*/kp/pNNNN.xhtml`）**跳过**；sidecar 登记过的切片版路径会解析回原书
- 同一份文件经不同挂载点出现时，按「大小 + 头 256KB/尾 64KB 的 md5」判重
  （bind mount / FUSE 下 inode 可能不同，光比 inode 不够）
- 书架里的 `bytes/chapters` 在首次打开时补齐（读整本书的 spine 才能知道字节数）

## 5.5 阅读时长: 拉取即融合

`pull` 现在一次做完: 进度 → 逐日阅读时长 → (控制台里还带) 划线 / 书签。

融合口径 (`services/sessions.py` + `reader_state.set_cloud_minutes`):

```
rtime.minutes        = 本地位图里点亮了多少分钟 (Recto 阅读器读的)
rtime.cloud_minutes  = 云端 read_report/book 给的当天分钟 (App / 其它设备读的)
展示 / 统计          = MAX(minutes, cloud_minutes)
```

取较大值而不是相加 —— 两边很可能是**同一段阅读** (我们推上去又拉回来)。云端只给
"当天读了多少分钟", 没有"哪一分钟", 所以本地位图不被回灌, 融合只写 `cloud_minutes`。
重复拉取幂等 (`days` / `merged` 不变, 数值不变)。

`sync.pull_once(with_time=True)` 默认带时长, 10 分钟节流; 关掉用
`pull_once(with_time=False)` 或 `sessions.pull(merge=False)` (只做镜像)。

## 6. 阅读时长

- 打开书 → `session start`；每 30 秒 `beat`；隐藏/关闭 → `end`（`sendBeacon`）
- 每次心跳只把「上次心跳 → 现在」的分钟打上位，**空闲保护 2 分钟** ——
  挂着不读不会刷时长（等价于 APK「翻页/切前台才计时」）

### 只有页面在前台才计时（`document.visibilityState`）

"网页没被激活"（切到别的标签页 / 别的网页 / 最小化窗口）**不累计**，三层都在挡：

| 层 | 机制 |
|---|---|
| 前端心跳 | `setInterval(beat, 30s)` 里先判 `document.visibilityState === 'hidden'` → 直接 return，**不发请求** |
| 前端事件 | `visibilitychange → hidden`：`flushProgress()` + `sendBeacon('session end')` 立刻收尾；`→ visible`：`session start` 重新开锚点 |
| 服务端 | `session_beat` 只算「上次心跳 → 现在」且 `cap_min=2`；即使某次心跳迟到很久，也只补 ≤2 分钟，不按墙钟补 |

`pagehide`（关页 / 切走 / 进后台被杀）与 `visibilitychange` 走同一套收尾。

- **边界滑点**：切走那一瞬间的 `end` 属于真实阅读，分钟粒度下会给"切走所在的那一分钟"
  记 1 分钟 —— 最多 1 分钟，不是后台在刷。同一分钟内来回切不重复（位图是幂等并集）。
- **未被覆盖的情形**：窗口只是被别的应用**遮挡**（没最小化、标签页仍是激活标签）时
  浏览器仍报 `visible`，会正常计时。若要连"遮挡也算不读"，得再加一层"无交互 N 分钟就停"
  的空闲判定（当前没做：`beat` 的 2 分钟上限只防"一次心跳跨太久"，不防"一直开着不动"）。
- 验收：`node tools/probe_reader_time_hidden.mjs`（`RV_TIME_BOOK=<key>` 指定书）——
  前台 70s 涨、切后台 90s 不涨、切回来继续涨；前后自动做时长快照 / 还原（`tools/rtime_snapshot.py`）。
- 落库按 **天** 聚合，分钟位图与 APK 的 `ReadTimeInfo` 同格式，
  以后要推到云端阅读服务时直接用（`rtime.bitmap` 就是 `d` 字段）

## 6.1 进度互通（云端 ⇄ 阅读器）

阅读器的进度在 `reader.sqlite.rprogress`，云端在 `readProcess`，两边用**同一个量**
对齐：字节比例。换算直接是 `chapter = chapterId + 1`、`offset = f3`，没有任何
页号 / XPath 中间层。

| 方向 | 触发 | 行为 |
|---|---|---|
| 云端 → 阅读器 | 手动 `pull` / API「从掌阅拉取」 | 云端 **`readPosition`（chapterId + f3）优先**，缺失才按 `readPercent` 反解；云端为 0 时不写（不抹本地进度） |
| 云端 → 阅读器 | **打开一本书时自动**（`pull_on_open`） | 节流 `pull_gap_sec`；**只比不写**：云端更靠前 → 弹选择条问读者；本地更靠前 → 不动，排一次推 |
| 云端 → 阅读器 | 读者选「跳到云端」 | 这时才写本地并跳到那个位置 |
| 阅读器 → 云端 | 读者选「留在本地」 | 边车记 `local_wins_at`（24 小时），本书记录允许回拨 → 立刻按本地覆盖云端，之后照常按节流推 |
| 云端 → 阅读器 | 自动 pull（`only_forward`） | 只在云端更靠前时覆盖 |
| 阅读器 → 云端 | 阅读中每翻一段（后台线程，节流 `push_debounce_sec`，容差 `push_tol` 默认 1e-6 近乎精确） | 写掌阅 `uploadProcess`（双 key：32 hex 文件 MD5 + 64 hex 云书架 id）；**读者自己动的位置就是准，包括比云端更靠后**（`why=rewind`）；节流到点自动补推 |
| 阅读器 → 云端 | 切后台 / 离开页面（`flush_on_hide`） | 绕过节流补最后一推（`keepalive` fetch，页面走了也发得出去） |
| 阅读器 → 云端 | **定时心跳**（`interval_sec`，默认 60s） | 只碰「最近 `tick_active_min` 分钟读过」的书；本地进度变了才推，且**只前进**；顺带按 `tick_notes_sec` / `tick_time_sec` 同步划线与今天的时长。没活动不联网 |
| 阅读器 → 云端 | **收尾补推**（`tick_settle_sec`，默认 180s） | 不看活跃窗口：本地还比「上次推上去的」靠前就补一次。读者合上书走了之后，最后那一小截照样落地；同一位置被闸门挡过的书不重复问云端 |
| 手动 push | API「推到掌阅」/ `recto push` | 批量推送不认读者意图，默认挡回退（`--allow-rewind` / `force` 可强推） |
| 手动修复 | `recto align --fix` | 按云端自己的百分比重算 `readPosition`（不动进度值） |

三处「自动」全是 `[sync]` 的开关，**设置 → 云端同步**里能逐项关，改完立刻生效。
定时心跳（`interval_sec`）是第四条通道，与翻页触发**并行**：它补的正是翻页覆盖不到的
三种情形 —— 停在一页上看很久（进度没变但时长在涨）、只加了划线没翻页、页面关了但时长
还在队列里。两条通道共用同一个节流窗口，心跳也永远走"只前进"的闸门。
推送是否允许回退由服务端判定，读者侧不用关心：写进度时带 `user=1`（读者动作）/
`user=0`（系统补写），推送线程消费一次即清。
右下角那行小字（`stamp`）是观察窗：`掌阅 15:37 已同步 32.4%` / `掌阅 更靠前 41.0%` /
`掌阅 同步失败`。推送进行中**不刷「同步中…」**，保持上一次同步的信息不动
（节流窗口可持续十几秒，反复刷只是噪声）。

两条保守设计：

- **不抢书，也不擅自定去留**：打开书时只**比**一次（`peek_book` + `dry_run`），云端更靠前就弹一条选择条：
  `掌阅云端读到 82.3%（第 8 章）／ 这里停在 41.0%` —— 「跳到云端」或「留在本地」。
  12 秒不选就自己收起，按本地位置继续读，下次打开再问。
  老做法是「拉进来 + 自动跳」：位置被拽走，而且本地进度已被覆盖，想留在原地都留不住。
- **本地所见即云端所记**（最后写入者胜）：读者在这里翻页、点目录、笔记跳转、关页面时，
  进度按事实覆写云端 —— 哪怕比云端更靠后（`why=rewind`）。掌阅 App 自己也是这样：
  退出书时把本地位置回写云端。老做法一律"只前进"，结果"云端 32.8%、本地 31.9%"会永远卡在
  `regress`：本地怎么读云端都不动，下次打开又被拽回云端那个位置。
  只有**没有读者动作**的写入（打开书后的对齐、CLI 批量推送）仍旧只前进。
- **云端刚读过就先让一让**：云端那条记录的 `updateTime` 比本地这条新 180 秒以上时
  （手机那边刚读完），这次不覆盖（`why=cloud-newer`）；本地再读一页时间就翻过来。
- **选「留在本地」仍然有效**：边车记 `local_wins_at`（24 小时），期间连"无人动"的推送
  也允许回拨（手动/批量场景用），覆盖成功后自动解除。
- **小字不撒谎**：本地落后云端且这一推还没轮到（节流窗口内）时状态是 `cloud-ahead`
  （`掌阅 更靠前 82.3%`）；刚按本地覆写过云端则显示 `掌阅 已按本地覆写 31.9%`
  （`rewound`）。进行中（`pushing` / `dirty`）不再写「同步中…」，而是保持上一次的
  「已同步 x%」不动 —— 一行提示只在有结论时更新。
- **旧会话不倒退**：`rprogress.sid` 记写入者会话。停在 53% 没关的旧标签页再滚动，
  不会把已读到 99% 的进度写回去（`REWIND_GUARD = 0.05`，同一会话内的回翻照常放行）。

**云端同一本书有三份记录**：`getProgress` 的 32 hex key、64 hex key，以及
`getUserNotebooks` 里 App 自己写的那份（带 `updatetime`）。三份互不同步。

取哪一份：**最新写过的那一份**（`updateTime` 最大），不是百分比最大的那一份。
取最大值曾经把"手机读到 31.8%、桥接此前推过 34.0%"混成 34%，拉回来就跳到
读者还没读到的位置。`max_pct` 只留给"别往回拨"的闸门用。

落库前还会用本地几何按 `(章, 偏移)` 重算一次百分比，保证 `rprogress` 的
`percent` 与 `(chapter, offset)` 永远自洽；比较时给 `1e-6` 容差（float32 反解误差）。

划线 / 书签 / 阅读时长的双向同步见 `services/notes.py` 与 `services/sessions.py`，
坐标同样都是原书字节；书与云端的对应关系来自书库边车 `<书名>.recto.json`。

## 6.2 阅读界面（沉浸模式）

两种阅读模式，**在排版面板（顶栏 `Aa`）第一行「阅读模式」里切**，逐设备记在
`localStorage` 的 `recto.mode`（`scroll` / `page`），刷新即还原：

| 行为 | 实现 | 说明 |
|---|---|---|
| 顶栏自动隐藏 | `.rv-bar.is-hidden` + `translateY(-105%)` | 下滑 6px 起收起；上滑 / 回到顶部 / 点正文中间唤出；恢复上次位置后落地时保持可见 |
| 隐藏时的进度 | `.rv-prog` 2px 细线 | 只有沉浸态才显示，宽度 = 阅读进度（翻页模式按页号算） |
| 左右页边翻页 | `.rv-turn-l` / `.rv-turn-r` 固定热区 | 点击 = 上/下一页（滚动模式 90% 屏高；翻页模式整页）；拖动 >10px 或正在选字则不翻页 |
| 点正文关浮层 | `dismissPopups()` | 目录 / 书签 / 笔记抽屉、选区工具条、笔记编辑框一并收起；`Esc` 同效 |
| 书签与笔记 | `#rv-marks` / `#rv-notes` 两个抽屉 | 书签列表只列书签（抽屉头部有"＋ 加书签"），笔记列表只列划线/笔记 |
| 排版 | 顶栏 `Aa` 图标 → `#rv-typo` 面板 | 阅读模式 / 字号 / 行间距 / 段间距 / 上下页边距 / 阅读宽度 / 字体；逐设备记在 `localStorage`（`recto.fs` 等），刷新即还原 |
| 顶栏按钮 | 全部图标化（32px 方块） | 目录 / 书签 / 笔记 / 排版 / 配色，名称走 `title` + `aria-label`；按钮组与右边框留 0.55rem |
| 滚动条 | **隐藏**（`scrollbar-width: none` + `::-webkit-scrollbar` 宽高 0） | 沉浸阅读里那条竖条只在打断排版，索性藏条不藏滚动：不占布局、不留 gutter，顶栏吃满整幅宽，正文居中与页边热区都不用再为它让位。代价是看不到“还能滚多远”，改用右下角进度百分比 + 顶部 2px 进度条表达 |

### 6.2.0 目录（分层折叠）

`BookGeometry.toc_nodes` 保留 NCX / nav 的层级：`[{spine, text, label, depth}]`
（`label` = 拼上卷名的完整名，`text` = 自己那一层）。阅读器按它渲染：

- **单层目录**（示例短篇 8 条全是 `depth 0`）→ 平铺，跟以前一样；
- **多层目录**（示例长篇 1-54 册，55 卷 × 章）→ `depth 0` 是卷，用 `<details>` 折叠；
  `depth ≥ 1` 按层缩进挂在卷下面（`.rv-toc-row.d1..d4` 的 `padding-left`）；
- 默认**只展开当前章所在的卷**，并把那一行滚进可视区（590 条全展开没人找得到）；
- 点**箭头** = 折叠/展开，点**卷名** = 跳到该卷首页（`preventDefault` + `stopPropagation`，
  不会顺手把这卷折起来）；
- 抽屉头有「展开 / 收起」（只在多层目录时出现）—— 收起等于只留当前卷。

### 6.2.1 左右翻页（多栏分页）

不是"把整章排版后按屏截图"，而是让浏览器自己分栏：

```
.rv-view   ← 唯一定高裁切窗口（居中，高 = 整个视口高）
  .rv-page ← 多栏容器：column-width = 可视区宽, column-fill: auto, height: 100%
```

放不下的内容自动流进"下一列"（横向溢出 `scrollWidth`），翻页就是把 `.rv-page`
横向平移一列（`translate3d(-页号 × (列宽+列间距))`）。所以：

- **零 DOM 改动**：选区、划线高亮（CSS Highlight API）、XPath 都不受影响；
- 列宽恒等于可视区宽 → 一屏永远只显示一列，不会并排出现两列；
- 每章 `break-before: column` 另起一页，标题不落页尾，图片不跨页；
- 顶栏收起/展开时 `.rv-view` **原地不动**（不再 `margin-top: -顶栏高` 上移一档）：
  页高本来就与顶栏无关，何必让人读到一半整页往上跳。收起后顶上留出那条空白，
  视觉上等价于"顶栏浮在上面又淡出"，正文一个字都不动。
- **顶栏是浮层**（`position: fixed`），不占版面：页高 = 整个视口高，
  `.rv-view` 的上下内边距取同一个值 `max(--rv-pad, 安全区上下)` —— 上边距 == 下边距，
  正文在视口里**真正垂直居中**。顶栏显示时会盖住正文顶部一两行（点正文中间即可收起），
  这是"不把顶栏算进边距"的必然代价，也是沉浸式阅读的常规做法。

三个实现要点（都是踩过的坑）：

1. **页号表只在排版稳定时重建**（`rebuildFlow()`）。翻页有 0.26s 过渡动画，
   动画途中 `getBoundingClientRect()` 拿到的是中间位置 —— 现量算出来的"当前页第一段"会飘。
   重建时先把平移钉到当前页（临时 `rv-no-anim`）再量，得到一张"块 → 页号"的静态表。
2. **分栏是异步碎片化的**：首屏 `scrollWidth` 可能还是上一次页高算出来的。
   `scheduleSettle()` 在 90ms 后按锚点再排一次，页数变了就再来一轮（最多三轮）。
3. **列宽必须用可视区的真实宽度（小数），不能用 `clientWidth`**：
   390 屏上可视区是 364.4px，`clientWidth` 取整成 364，而浏览器实际按 364.4 排 ——
   每翻一页 `stride` 就少 0.4px，翻到第 70 页整块正文**偏右约 28px、右边被裁掉一截**。
   桌面（704px 整数）看不出问题，只有窄屏会累积。现在取
   `viewEl.getBoundingClientRect().width - 左右内边距`，保留三位小数。
4. **页号提示**：块级游标落在跨页段上时会差一页，所以逐设备记
   `recto.pg.<bookKey> = {ch, off, page, rel, sig}`（`sig` = 列宽/页高/字号/行距/宽度/字体指纹）。
   同一设备同一排版时用页号精确复原；换设备或改了排版就退回块级口径（±1 页）。
   **`rel` 是"本章内第几页"，不是绝对页号**（2026-11-06 修复，见 6.2.2）。

#### 6.2.2 两条"页号"口径坑（2026-11-06 定位并修）

**症状**：左右翻页时刷新（或从书架重进）会落到比实际更后的位置（用户报过"跑到 96.1%"）；
补章那一刻按一次右翻有时会跳过好几页，甚至把进度存到后面某一章的段上。

**根因 A：提示存的是整篇文档的绝对页号。**
刷新后 `boot()` 只载入进度所在**那一章**，前面几章不在 DOM 里 ——
同一个 `page` 数字现在是"章内页号"，于是往里多跳了 `page(本章前那些章)` 那么多页。
修法：写提示时存 `rel = page − chapterFirstPage(ch)`；读时 `p = chapterFirstPage(ch) + rel`，
并要求 `pageTopBlockAt(p)` 真的是 `hint.ch/hint.off` 那一段，对不上就退回块级定位。

**根因 B：`rebuildFlow()` 的页号换算把"前面翻过的页"全压成 0。**
原来是 `(left − (vr.left + page·stride)) / stride` 再 `Math.max(0, …)` ——
当前平移也算进了零点，于是**当前页之前的所有块都得到负数 → 被钳成第 0 页**。
这张表只在第 0 页是对的；补章/重排时若在别的页上量，`pageTopBlock()` 会把后面某一章的段
当成"本页第一段"，锚点、存进度、settle 全跟着跑偏。
修法：`page = (left − vr.left) / stride + S.page`（先回到"相对当前页的页差"，再加回当前页）。

**配套：瞬态页表不许当真。**
分栏碎片化期间量到的表会把不同章节挤到同一页 —— 用一条硬不变量识别：
章首带 `break-before: column`，**后一章的首块必定落在更后的页**。
`flowLooksSane()` 不满足就把这张表标成不可信，此时
① 不更新游标/百分比 ② 不写页号提示 ③ `currentAnchor()` 退回最后一次可信位置
④ `scheduleSettle()` 只重量不重锚点（尾部补章不会改动前面已有的页界），
并且一直稳到页表可信为止（最多三轮）。

回归：`tools/probe_reader_pagehint.mjs`（12 项：单次翻页只走一页 / 提示是章内相对页 /
rel 与页表自洽 / 刷新前后落在同一段 / 章内往后几页刷新同样一致）。

翻页入口：左右热区点击、键盘 `→ / ← / PageDown / PageUp / 空格`、
手机上左右滑动（>44px 且横向为主）。翻到末页自动补下一章，翻到首页回上一章末页。

#### 翻页效果（排版面板里可换，也能关掉）

`.rv-fx-*` 五个：`slide` 滑动（默认，= 上面那套 CSS transition）、`none` 无、
`fade` 淡入、`ripple` 水波纹、`flip` 翻卷。逐设备记 `localStorage['recto.fx']`。

关键约束：**位移永远瞬时切到位，效果只是"入场"动画**（Web Animations API 播在
整页舞台上）。所以任何效果都**不参与分页测量** ——
`rebuildFlow()` 的块→页号表、`pageStride`、续读页号都不会因为换了效果而飘。
非滑动效果翻页时临时加 `rv-no-anim`（关掉 CSS transition），量完位移再解锁。

**但"动画不参与测量"要靠两件事守住**（2026-11-07 踩过，见 6.2.3）：
量列宽只读布局值（`getComputedStyle`），量 rect 前先 `flipStop()`。

效果**盖满整个视口**，不是只作用在正文那一列：所有视觉层都放进 `#rv-fxstage`
（`position: fixed; inset: 0; z-index: 32` —— 在顶栏 30 / 进度条 31 之上、抽屉 40 之下，
`pointer-events: none` + `contain: paint`）。正文列宽 `--rv-w` 比屏幕窄时，页边留白和顶栏
一起参与动画。

- `fade` = 一层满屏底色（`var(--rv-bg)`）盖住再揭开（230ms），新页面从整屏里显影；
- `ripple` = 满屏一层径向光晕，圆心贴在转页那一侧的屏幕边缘（直径 220vmax），
  一路漫过整屏再淡出（520ms），播完自动移除，不挡选字/点击；
- `flip` = **整页**（`#rv`，含顶栏与页边）绕转页那一侧的边缘掀起 `±12deg` 再落回，配一道
  掠过整屏的纸页高光（320ms）。`perspective(2200px)` 写在 transform 里，
  不给 `body` 挂 `perspective` —— 那会让 `position: fixed` 的层改以 body 为容器；
- 系统开了「减少动态效果」且用户没自己选过时，按 `none` 处理；
- 面板里的「恢复默认」会把效果一起回到 `slide`。

#### 6.2.3 掀页动画与版面测量（2026-11-07：连翻时"露出前后页边沿"）

报障：**翻卷模式下翻页很快时，有时候阅读区域错位 —— 会露出前后页边沿。**

两条独立根因，都在"动画期间去量版面"这条缝里：

1. **`getBoundingClientRect()` 在 3D 动画期间给的是投影宽。**
   `flip` 把 `#rv` 绕一侧掀起 `±12deg` + `perspective(2200px)`，整页被放大：
   桌面实测 `.rv-view` 从 **704 → 781px（+11%）**，手机 364.4 → 370。
   这期间只要来一次重排（**补章、图片加载、resize** 都会走 `scheduleSettle` →
   `layoutPaged`），`pageGeom()` 就把放大值当列宽 → `columnWidth` / `pageStride`
   全错 → 正文按错的列距平移，**相邻列从页边露出来**。
   `settle` 最多重试三轮（每 90ms），有时能自愈，有时三轮全落在动画里 —— 于是"有时候"。
   修法：`pageGeom()` 读 `getComputedStyle(viewEl).width`（布局值，与 transform 无关），
   `layoutPaged()` / `rebuildFlow()` 开头先 `flipStop()`（rect 测量必须在无变换空间里）。
2. **掀页动画会堆叠。** 连翻时上一次还在跑，`root.animate()` 直接再加一个；
   先结束的那个在自己 `onfinish` 里执行 `transformOrigin = ''`，
   于是**还在转的那几个改成绕画布中心转** —— 整页左右各缩一截，两个边都能看见。
   修法：`flipAnim` / `flipSweep` 单飞（开始新的一次前 `flipStop()`），
   `onfinish` / `oncancel` 里先判 `if (flipAnim !== a) return;`（origin 只由当前那次清），
   并且连翻时**接着当前角度继续往回收**（`flipDone()`），不弹回 `±12°`。

规则（以后改这条路径必须守）：
- **布局测量不读 rect，如果祖先可能带 transform。** 宽度读 `getComputedStyle`，
  位置读 rect 前先把动画停掉。
- **动画的 `onfinish` 只许动自己那份状态。** 单飞句柄 + 身份判定，
  别用"清全局默认值"收尾。
- 探针：`tools/probe_flip_rapid.mjs`（连翻时"同刻最多 1 个掀页动画 / origin 不被抢 /
  掀页途中重排后列宽与 stride 不变"）、`tools/probe_reader_seam.mjs`
  （落点 = `-page × stride`、实际列距 = stride、窗外无内容）。

#### 章末续读为什么不会再"卡死"

补章链路（`appendNextChapter`）以前有三处会把状态永久锁死，症状都是
「每读到一个章节末尾就再也翻不动，只能刷新」：

1. `api()` 的 `fetch` **没有超时** —— 网络半死时请求永不返回；
2. `loadChapter()` 失败时**不回调** —— 等它的人（补章）永远停在 loading；
3. `appendNextChapter` 的 `appendBusy` 只在成功回调里清 —— 一旦 (2) 发生就永远 `true`，
   之后每次补章直接 `cb(false)`，页数不再增长。

现在：`api()` 有 25s 硬超时（`AbortController`）；`loadChapter` 同章并发去重且
**成功 / 失败都回调**（失败带 `null`）；`appendBusy` 无论成败都解锁；
加载中再点的章末翻页会**排队**（`appendWait` / `turnWait`）而不是被吞掉；
失败时页脚变成可点的「下一章没载入, 点这里重试」。
滚动模式下章末点热区，补到内容后会**接着往下滚一屏**，不再是"点一下不动"。

热区宽度：窄屏（<780px）`clamp(64px, 25vw, 160px)` —— 手机上 390 屏约 98px，
左右各四分之一，中间一半留给"点正文切顶栏"。原来的 `clamp(44px, 10vw, 220px)`
在手机上只有 44px，几乎点不中。≥780px 视口改成
`max(44px, calc(50vw - var(--rv-w) / 2 - 1.4rem))` —— **只在正文列之外**，不压字，
而且跟着排版面板里的"阅读宽度"自动让位。页边热区**始终不可见**：hover 不变色、不出现箭头，
只把鼠标指针换成手型。


正文字体是**输入框 + 自绘下拉**（不再逐个度量枚举，也不用原生 `datalist`）：

| 途径 | 说明 |
| --- | --- |
| 输入框（`#rv-font`） | 唯一真源。敲字体名 → 回车或失焦即生效；**清空 = 跟随界面**；写 `serif` / `sans-serif` / `monospace` 走通用族 |
| 自绘下拉（`#rv-font-menu`） | 内置四档（跟随界面 / 衬线 / 无衬线 / 等宽）+ 约 50 个常用中英文字体名（中文在前）+ 读到的本机字体；聚焦或点箭头展开，打字即筛，↑↓ 选、回车落 |
| ⟳ 读取本机字体 | `window.queryLocalFonts()`（要安全上下文：https / localhost），读到后并入清单，按拼音排序 |

原生 `<datalist>` 的弹层是浏览器自己画的（灰底 / 系统字体 / 宽度和行高都不受控），
跟排版面板割裂 —— 已换成 `<div class="rv-combo">`：输入框无边框填充式（跟按钮同密度，
高度对齐「阅读模式」那一行），候选清单借同一套 `--rv-*` 变量（面板底 + 一条描边 +
强调色高亮 + 当前项打勾），行高 30px、一屏约 6 条、比输入框略宽（长字体名不早折）。

早前的做法是 canvas 逐个"量宽度验证"约 190 个候选 × 5 基族 × 4 字重 × 2 串
（几千次 `measureText`），打开排版面板有明显延迟，而且照样列不全 —— 已废弃。
现在还留着的兜底只有一条：**手输**。

**面板与滚动的关系（别好心反噬）**：面板外的滚轮 / 手指滑 = 开始阅读，收起面板；
**面板内的滚轮 / 滑动不收起** —— 指在面板上就是在自己面板里滚（清单一屏 6 条，
56 条候选要靠滚轮翻）。实现见 `scrollInsidePanel(ev)`。另一条同类坑：清单在
`mousedown` 时就收起，于是 `mouseup` 落在别的元素上，浏览器把 `click` 派发给两者的
共同祖先（正文）—— 会把"选个字体"变成"点正文 → 顺手关面板"。所以收起面板 / 切顶栏
这两个"点正文"判断一律看 **按下点**（`downInTypo`，`pointerdown` 捕获阶段记），
不看 `click.target`。

> 正文段落在 HTML 里是真的 `<p>`（`renderChapter` 包一层），段间距才落得上；
> 之前裸 `div.rv-blk` 让 `.rv-page p` 的规则一直没生效。
手机上两侧各 44px 窄边（`touch-action: pan-y`，纵向滚动照常）。

笔记编辑框被点正文关掉时会把草稿按选区起点暂存（`DRAFT`），同一处再打开还在；
保存成功后草稿清空。

## 6.3 页内排版: 图文混排 + 中文版式

阅读器**不照搬原书样式表**（那是给"一整页纸"写的：`body` 宽度、整套配色、
字体族、行高，照搬会把读者的字号/行距/主题设置全部顶掉），但也**不能完全不读** ——
很多版面信息只写在 class 上。折中做法见 `core/bookstyle.py`：

| 取（这本书的版面） | 不取（读者的版式） |
|---|---|
| `img` 的 `width` / `height` / `max(-width/-height)` / `vertical-align` | `color` / `background` / `border` |
| 块的 `text-indent` / `text-align` / `font-size` / `font-style` / `font-weight` | `font-family` / `line-height` / 左右 `margin` / `display` / `position` |

- 选择器只认**主体复合选择器**（`p` / `.po` / `p.po` / `.a.b`），
  `.section p` 取最后一个 token；带 `:hover` 这类伪类、属性选择器的规则跳过（不是静态版面）。
- `padding` / `margin` 简写会展开成四边，注文的悬挂缩进（`padding-left:1em` + `text-indent:-1em`）才有意义。
- 块级样式通过 `Block.style` 下发，前端写到**真正的那个元素**（`p` / `h*` / `blockquote` / `li`）上 ——
  放在外层 wrapper 会被 `.rv-page p` 自己的规则盖过。
- 裸标签 `p { text-indent: 2em }` 这类**不采纳**：首行缩进是读者的排版选择（见下）。

### 图文混排

| 类 | 判定 | 样式 |
|---|---|---|
| `.rfig` | 块内没有文字（独占一段的插图、封面页） | 块级居中、`max-width:100%`、滚动模式 `max-height:90svh`、翻页模式限高到一页 |
| `.rfig-inl` | 段内还有文字（脚注标记、装饰小图标） | 行内混排：`max-height:1.35em`，尺寸优先用书内 CSS |

事故与根因（2026-11-05）：`示例短篇` 的脚注标记是
`<a class="duokan-footnote"><img class="w10" src="zhu.png"/></a>`，
`.w10 { width: .85em }`，而图片本身是 **500×500**。旧代码丢掉 `class` 属性、
只给 `max-width:100%`，于是那个本该 0.85em 的小图标按原始像素铺满整行 ——
"注释图片几乎全宽"。现在该图标实测 16.1×16.1（19 个字号的 0.85 倍）。

另外三处同类修正：

- epub 的封面常写成内联 `<svg><image xlink:href="cover.jpg"/></svg>`，只认 `<img>` 会整张漏掉；
- 字节切片可能把结尾标签切成半截（`</body`），别把它当正文显示；
- **章首题图**（`<div><img class="top"><h2>…</h2><p>…</p></div>`）：图片元素是 `div` 的
  直接子节点、既不是块标签也没有文字，旧代码把它整块跳过 —— "每章开头的题图"永远不显示。
  现在 `<img>` / `<image>` / 内联 `<svg>` 自己就是块（`Block(tag="img")`），排在 `h2` 之前。

图片一律 `loading="eager"`：翻页模式按列高量分页，图片是异步解码的，
量列位那一刻还没固有尺寸就会把页数算少（另配 `load` 捕获兜底，图到位再重排一次）。

### 中文版式（参考 W3C clreq）

`.rv-page` 上这组声明都是**渐进增强**，不支持的浏览器按普通规则排：

| 目标 | 声明 | clreq |
|---|---|---|
| 两端对齐、末行不拉伸 | `text-align: justify; text-justify: inter-ideograph; text-align-last: left` | 3.1.2 |
| 禁则（行首/行尾禁排标点） | `line-break: strict` | 3.2.2 |
| 西文整词不拆、中文可逐字断 | `word-break: normal; overflow-wrap: break-word; hyphens: none` | 3.2.2 |
| 中西文自动间距 | `text-autospace: normal` | 3.2.4 |
| 行首标点压缩（保留首行缩进） | `text-spacing-trim: space-first` | 3.2.5 |
| 行末标点悬挂 | `hanging-punctuation: allow-end last` | 3.1.3 |
| 首行缩进 2 字符 | `.rv-ch.rv-cjk p { text-indent: var(--rv-indent, 2em) }` | 3.1.6 |
| 标题另起、不缩进、断行均衡 | `text-indent:0; text-align:left; text-wrap: balance` | 3.1.4 |
| 图注居中、小一号、不缩进 | `figcaption/.caption` | 3.3 |

- **首行缩进只给中文段落**：`/chapter/<i>` 返回 `cjk`（汉字占比 ≥ 30%），
  前端给该 `<article class="rv-ch rv-cjk">` 打标记。西文书不加缩进 —— 西文用段间距分段。
- **缩进可关**：排版面板「首行缩进 无 / 两字」，逐设备记 `localStorage['recto.indent']`；
  关闭后 `--rv-indent: 0`，注文的悬挂缩进（来自书内 class）不受影响。
- 只有图没有字的段落不参与首行缩进（`p.rv-figblk`），否则整幅图被推右两个字符。

#### 装饰分隔符（`◎` / `**` / `——`）

独占一段的符号是**版面停顿**，不是标题。书内 CSS 常把它们挂在与标题同一个
class 上（示例短篇 `.biaoti` = `1.7em + bold`），照抄字号的结果是 19px 正文
旁边一个 32px 实心粗圆 —— 看着"过大"。

规则：`core/bookfile.py` 认出这类块后打 `role`，**丢弃**书内的
`font-size / font-weight / font-style / letter-spacing / text-align`，
视觉规格全部由 `reader.css` 的 `.rv-sep` 定：

| 档 | role | 规格 |
|---|---|---|
| 单字符 `◎` | `sep` | `0.9em` / `400` / 字距 0 / 居中 / 60% 前景色 |
| 多字符 `**` `※※` | `sep-multi` | 同上 + 字距 `.4em`（负右外边距抵掉末字后那份） |
| 破折号串 `——` | `sep-dash` | 同上 + 字距 0（加了会把一条线拆成两截） |

两个坑：

1. **`text-align-last` 必须一起写 `center`** —— 正文那条规则是
   `text-align-last: left`（末行左对齐），单字符分隔符整段就是"最后一行"，
   只写 `text-align: center` 会原地左对齐。
2. **单字符不要加字距** —— 字距跟在末字之后，居中会偏半个字距（实测 4px）。

自检：`python3 tests/test_bookstyle.py`（49 项）+ `python3 tests/test_reader_sep.py`（25 项）+
`node tools/probe_reader_layout.mjs`（37 项）+ `node tools/probe_sep.mjs`（14 项，真开阅读器量
字号/字重/居中/留白，滚动与翻页两种模式）。

## 6.4 关键词高亮（语料滤镜）

语料分解 json（`<书名>.keywords.json` 或 `[corpus] dir` 里 epub-refiner 导出的
`keywords.json`）→ `GET /read/api/book/<key>/mark` → 前端在章节 DOM 上铺一层高亮。

| 关心的事 | 做法 |
|---|---|
| 不改数据 | 纯渲染层：不写文件、不写 `reader.sqlite`、不动章节块 |
| 不改 DOM（默认） | CSS Custom Highlight API + `Range` + `::highlight(rv-kw-<cat>)`；`span` 模式才包 `<span>` |
| 进度/划线不受影响 | highlight 模式下字符偏移完全不变（划线靠 `charToNode` 算文本偏移） |
| 翻页不受影响 | 分栏是按元素碎片化的；高亮不改盒模型 ⇒ 页数/页号不变 |
| 大书性能 | **词典树**逐位置最长匹配（不是几千分支的正则）；示例长篇 5876 词 / 一章 3398 处命中，首屏无感 |
| 可读性 | 默认只给半透明底色、不设文字色 ⇒ 深色主题不会出现"白斑"；自己的划线/笔记 `priority=1` 永远盖在上面 |

挑选语料（`core/reading_mark.py: resolve`）：显式绑定 > 书库边车 `<书名>.keywords.json` >
语料 `meta.source_epub` 同源 > 书名模糊匹配（相等 / 子串 / Jaccard ≥ 0.55）。
**同作者的另一本不硬配** —— 宁可漏，让人在设置页手动绑定。

预算（`max_terms`）按分类**注水均分**（`_allocate`）：大书里人名动辄几千个词，
按顺序装到上限就 break 会让后面 7 类一个词都没有（表现为"只有人名高亮"）。

配置分两层：`config.toml` 的 `[reader.mark]`（部署默认）+ `<data_dir>/reading_mark.json`
（设置页改的覆盖项，缺的键跟随 config）。

自检：
```bash
python3 tests/test_reading_mark.py          # 38 项：归一化 / 语料 / 挑语料 / 预算 / 概览
node tools/probe_reader_paged_narrow.mjs     # 48 项：窄屏左右翻页 (列宽不取整 / 深处不漂 / 热区宽度 / 手感 / 进度写回)
node tools/probe_reader_kw.mjs              # 21 项：真开阅读器 (命中 / 零 DOM 改动 / 开关 / 翻页)
node tools/probe_settings_mark.mjs          # 15 项：设置页保存 / 绑定 / 恢复默认
node tools/probe_settings_layout.mjs        # 40 项：两张宽卡排版 / 输入框 / 色板 / 手机
```

## 7. 已知边界

- **画面级定位**：位置粒度 = 段落。同一段很长时重新打开会回到段首（可接受；
  需要更细可加"块内字符偏移"）。
- **翻页模式的重开位置**：同设备同排版精确到页（页号提示），换设备/改排版后
  按段落口径复原，可能差 ±1 页（跨页段落在块级游标上表达不出来）。
- **大书首屏**：章节按需加载（当前章 + 下一章）。
- **书 key = 原书路径哈希**：换文件副本（重新下载）会变成新书，旧进度不跟随。
  需要跨副本沿用进度时可按内容 MD5 建 key（云端那边就是文件 MD5）。
- **不支持 PDF**（书库里的 .pdf 不参与扫描）。
- 掌阅 `downloadDataV2` 长期只回 status 无 `res`，所以云端划线的正文是**从原书字节切**出来的；
  批注（remark）云端拿不到，本地写的照推。

## 7.1 实测（2026-11-03，生产 5015）

| 检查 | 结果 |
|---|---|
| 行内坐标一致性（合成样本） | `BookDoc.percent` 与 `geometry.percent` 差 < 1e-7（float32 量级） |
| 示例短篇（327KB / 9 篇 / 目录 8） | 云端记录 `ch=5 f3=11752` → 29.326825%，阅读器算 29.326825% |
| 示例长篇（40MB / 590 篇 / 目录 588） | 中段 `ch=294 f3=342` → 两边都是 0.52075964；`totalCount=588` 与云端一致 |
| 打开速度 | 示例长篇 40MB 首屏 2.7s（按需只取当前章 + 下一章） |
| 目录跳转 | 590 章目录，跳到中段 → 52.0%，落点章名正确 |
| 划线 + 高亮 | 选区 → 划线 → `CSS.highlights` 1 段；刷新后仍在（anchors 还原） |
| 连续阅读 | 滚到底自动接下一章；进度 0.1% → 0.8% → 1.6% → 2.0% |
| 阅读时长 | 书架显示累计分钟；`rtime` 逐天一行，位图与 APK 同格式 |
| pull → 阅读器 | 示例长篇云端 31.72% → `rprogress` ch=183 off=12954 pct=31.72%，打开落在「示例长篇·卷十七 第五章」 |
| 阅读器 → 云端 | 阅读器读到云端更靠前的位置 → `push` 写掌阅（只前进），边车记 `last_push_at` |
| 移动端 390px | 横向溢出 0，0 console error |

`读到底 = 读完`：最后一章末尾进入视口时，位置记到章末（`offset = size-1`），
服务端按 `percent >= 0.999` 写 `complete=1`（对应云端 `readComplete`）。

## 8. 运维

```bash
python3 tests/test_reader.py        # 147 项：坐标一致性 / 块切分 / 状态库 / 时长位图 / 翻页页号口径
python3 tests/test_reading_mark.py  # 38 项：阅读高亮 (语料 -> 词表 -> 配置)
python3 tests/test_sync_notes.py    # 边车 / 进度判定 / 笔记位置 / 时长队列

node tools/probe_reader_pagehint.mjs   # 12 项：翻页页号提示 + 刷新落点 (真浏览器)
```

清空阅读状态（进度/书签/划线/时长）只需删 `data/reader.sqlite`；书目录会在下次
访问书架时重建。

上游回归：`tests/` 下原有 20 个测试文件全绿（本模块不改动它们的路径）。
