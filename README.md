# 云阅 Recto

**一个独立的「阅读留痕」服务**：自己的书库 + 内置阅读器 + 与掌阅云端双向同步（进度 / 划线 / 书签 / 阅读时长）+ 墨水屏摘录接入。

- **界面品牌：`云阅 Recto`** —— 一律中英文并写（顶栏 / 浏览器标题 / 页脚 / 登录页），标题形如 `书架 · 云阅 Recto`；`云阅` 只在需要单写中文的句子里用。
- **英文工程名：Recto** —— 只留在仓库名、Python 包 (`src/recto/`)、配置键、日志与命令里。
- 词源：Recto = 书页的正面（Recto Verso：右页 / 左页）。这个项目管的是读者留痕的那一面。

> **不依赖任何外部书目库。** 与本地书库容器相关的能力（注入阅读界面、直写书库数据库、把 epub 重排成「等字节片」）**不在这个仓库里**。Recto 只读写自己的两处目录：`library/`（书）与 `data/`（状态）。

---

## 目录

- [它解决什么问题](#它解决什么问题)
- [能力一览](#能力一览)
- [架构与数据流](#架构与数据流)
- [快速开始（5 分钟）](#快速开始5-分钟)
- [交给 AI 部署：可执行清单](#交给-ai-部署可执行清单)
- [config.toml 全字段](#configtoml-全字段)
- [命令行](#命令行)
- [HTTP 接口](#http-接口)
- [数据与目录](#数据与目录)
- [坐标口径（唯一一套）](#坐标口径唯一一套)
- [掌阅登录（扫码）](#掌阅登录扫码)
- [同步的三个硬规则](#同步的三个硬规则)
- [可选组件](#可选组件)
- [常驻运行](#常驻运行)
- [安全](#安全)
- [排障速查](#排障速查)
- [设计取舍：它不做什么](#设计取舍它不做什么)
- [目录结构](#目录结构)
- [文档](#文档)

---

## 它解决什么问题

同一本书，你可能在三个地方读：

| 场景 | 进度记在 | 划线记在 |
|---|---|---|
| 手机（掌阅 App） | 云端 | 云端 |
| 浏览器（本服务的阅读器） | 本地数据库 | 本地数据库 |
| 墨水屏（摘录设备） | —— | 设备里的一堆摘录 |

三份记录互不相认。根因不是"懒得同步"，而是**两边的最小刻度不同**：

- 云端按「全书字节比例」记（`readPercent` = 已读字节 / 全书字节）；
- 网页阅读器按「页 / XPath」记；
- 换一个阅读器、换一次排版，页号与 XPath 全部作废。

**Recto 的做法：把原书字节位置当成唯一权威。** 所有端只交换两个数 —— 第几个文档（spine 下标）+ 文档内第几个字节。页号、XPath、百分比都只是它的投影，随时可以重算。

副作用：**换排版不会让进度漂**，因为排版从来不进同步通道。

---

## 能力一览

| 分区 | 做什么 |
|---|---|
| **书架** | 书库入口页：卡片式书架（封面 / 进度 / 划线 / 时长）+ 即时搜索 + 排列顺序（**最近阅读** / **书名**，逐设备记忆）+ 统计条 + **卡片上的删除键**（文件直接删除，进度与划线保留）。维护动作（重扫书库、对齐云端映射）收成**屏幕右侧竖排圆钮**，hover 才显示名字；手机上回到页头横排 |
| **内置阅读器** | 直接读 `library/` 里的原书：滚动 / 左右翻页双模式、排版面板（字号 / 行间距 / 段间距 / 边距 / 宽度 / 字体）、目录分层折叠、划线与书签、阅读时长记录、语料关键词高亮 |
| **云端同步** | 掌阅双向：进度 / 划线 / 书签 / 阅读时长。「从掌阅拉取」= 进度 + 逐日时长 + 划线书签**一次拉完**；时长「取较大值」融合，不重复计。扫码登录与个人云书籍（列表 / 下载 / 覆盖下载）也在这页。执行结果与日志**写成人话**（结论 + 计数 + 每本书一行），原始 JSON 收进折叠区 |
| **摘录接入** | 接收墨水屏设备推来的摘录（:8080），入账本，可导出到纸间 |
| **语料** | 内置的 EPUB 关键字精炼：选书 → 分块抽词 → 固定导出（`<书名>-<hash>.json` + `latest.json`），断点续跑、模型库、额度熔断都在里面。同一套外壳与皮肤 |
| **阅读高亮** | 把语料分解 json（每类关键词 + 别名）当渲染层铺在正文上，零 DOM 改动 |
| **外观** | 六套皮肤（Material 3 骨架 + 色板替换）、字号缩放、圆角、疏密、自定义 CSS |
| **设置** | 昵称 / 改口令 / 书库目录 / 阅读高亮开关与配色 |

---

## 架构与数据流

```
                 ┌──────────────── 浏览器 ────────────────┐
                 │  书架 / 阅读器 / 摘录后台 / 语料 / 外观  │
                 └───────────────┬────────────────────────┘
                                 │  HTTP  :5015
┌────────────────────────────────▼─────────────────────────────────┐
│                       云阅 Recto（单进程）                        │
│  app/        server · console · reader · excerpts · corpus        │
│  services/   sync · notes · sessions · acquisition · reader_state │
│  core/       config · paths · geometry · bookfile · sidecar · mark │
│  sources/    cloud（掌阅 API） · cloud_auth（扫码） · device（8080）│
└──────┬──────────────────────────────┬───────────────────────────┘
       │                              │
       │ ① 云端 API                   │ ② 本地状态
       ▼                              ▼
  掌阅云端（进度 / 划线 / 时长）    library/ 原书 + *.recto.json 边车
                                   data/    reader.sqlite + state/
                                        │
                                        │ ③ 墨水屏设备（可选，同一进程的第二个监听）
                                        ▼
                                  :8080 /api/sync
```

**三条原则**

1. **一个方向一次写**：每本书同一时刻只有一条"进度"，谁更靠前谁说话（见[同步的三个硬规则](#同步的三个硬规则)）。
2. **状态全在本地**：关掉云端也能读、也能记；联网后再对齐。
3. **渲染即投影**：阅读器的排版、高亮、皮肤都不进数据库 —— 改样式不产生任何迁移。

---

## 快速开始（5 分钟）

### 前提

| 项 | 要求 | 说明 |
|---|---|---|
| Python | **≥ 3.11** | 用到 `tomllib` |
| 依赖 | `lxml` | 唯一必需的三方包（解析 epub） |
| 端口 | 5015（Web） | 可改 |
| 可选 | Node + Playwright | 只有「藏书票 PNG」需要 |
| 可选 | 语料抽词的 LLM 端点 | 不配也能浏览已抽好的语料，只是不能再跑抽取 |
| 可选 | 墨水屏设备 | 没有就不启 8080 |

### 1. 取代码

```bash
git clone https://github.com/shinemoon/Recto.git
cd Recto
```

### 2. 装依赖

```bash
python3 -V                    # 确认 ≥ 3.11
pip install lxml              # Debian/Ubuntu 也可: sudo apt install python3-lxml
```

不需要 `pip install -e .`：`run.sh` 与本文所有命令都用 `PYTHONPATH=src`。
（想装成命令也可以用：`pip install -e .` 之后直接 `recto serve`。）

### 3. 改配置

`config.toml` 是**入库的默认配置**（默认值全相对项目根，clone 下来就能跑）。
机器专有的值写进同目录的 `config.toml.local`（同格式、逐键覆盖、**不入库**，`.gitignore` 已忽略）。
**最少改这几项**（写在 `config.toml.local` 里就行）：

```toml
[recto]
port = 5015
data_dir = "/你的路径/Recto/data"

[library]
dir = "/你的路径/Recto/library"        # 你的书放这里（递归扫描 epub / txt）

[auth]
user = "root"
password = ""                          # 空 = 首次自动生成 root，登录后立刻改
```

其余段（`[cloud]` `[device]` `[corpus]` `[reader.mark]` `[bookplate]`）可以先原样留着 —— 指向的路径不存在时，对应功能只是显示"未配置"，不会带挂服务。

### 4. 放书、起服务

```bash
mkdir -p library
cp ~/Downloads/某本书.epub library/          # 放一本试试

bash run.sh start                            # 默认 :5015
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5015/healthz   # 期望 200
```

浏览器打开 `http://<主机>:5015`，用 `root` / `root` 登录 → **设置页立刻改口令**。

### 5. 接云端（可选，但这是它的主业）

进「云端同步」→「掌阅认证」→ 扫二维码 → 手机掌阅 App 授权。
之后点「从掌阅拉取」，云端进度 / 划线 / 时长就落到本地；「对齐云端映射」把书库里的书和云端书架按书名对上。

命令行等价：

```bash
PYTHONPATH=src python3 -m recto.cli selftest      # 自检（路径 / 凭据 / 书目 / 端口）
PYTHONPATH=src python3 -m recto.cli library scan  # 扫书库
PYTHONPATH=src python3 -m recto.cli pull          # 云端 → 本地
PYTHONPATH=src python3 -m recto.cli push          # 本地 → 云端
```

---

## 交给 AI 部署：可执行清单

> 这一节写给"让 AI 照着做"的场景。每条都是**幂等**的：重复执行不会重复写数据。
> 每一步都给了**命令 + 判定标准**，跑完对照最后一张验收表即可。

**给执行者的三条约束**

1. 只改 `config.toml`、只写 `data/` 与 `library/`；不要动系统里其它服务。
2. 任何"看起来成功了"都要有输出证据（HTTP 码 / JSON 字段 / 文件存在），不要凭"没报错"。
3. 需要改端口时**先查占用**：`ss -ltn | grep -E ':(5015|8080)\b'`。

### Step 0 · 环境检查

```bash
python3 -V                                     # 判定: ≥ 3.11
python3 -c 'import lxml; print(lxml.__version__)'   # 判定: 打印版本号
ss -ltn | grep ':5015' || echo free            # 判定: free
```

缺 lxml：`pip install lxml`（或 `sudo apt install python3-lxml`）。

### Step 1 · 落地代码与目录

```bash
git clone https://github.com/shinemoon/Recto.git /opt/recto   # 路径自选
cd /opt/recto && mkdir -p library data
mkdir -p config/secrets                     # 云端凭据落这里
```

判定：`ls /opt/recto/src/recto/cli.py` 存在。

### Step 2 · 写配置

只改这些键（其余保持默认）：

| 键 | 改成 |
|---|---|
| `[recto] data_dir` | `/opt/recto/data` |
| `[recto] port` | 5015（或你需要的） |
| `[library] dir` | `/opt/recto/library` |
| `[cloud] auth_file` | `/opt/recto/config/secrets/cloud.json` |
| `[auth] shared_dir` | `/opt/recto/data`（单机部署；多服务共享会话时才用公共目录） |

判定：

```bash
PYTHONPATH=src python3 -m recto.cli config | grep -E 'data_dir|library'
```

> 生效顺序：**环境变量 > `config.toml.local` > `config.toml` > 内置默认**。
> `<data_dir>/settings.json`（设置页里改的项）盖在 `config.toml` 之上、环境变量之下。

### Step 3 · 起服务

```bash
cd /opt/recto && bash run.sh start
bash run.sh status                      # 判定: running (pid …, :5015)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5015/healthz   # 判定: 200
```

失败先看 `data/recto.log`（`run.sh` 已经把 stdout/stderr 都收进去了）。

### Step 4 · 首登改口令

```bash
# 默认 root / root；服务器侧可直接重置（不验旧口令）
PYTHONPATH=src python3 -m recto.cli passwd '你的新口令'
```

判定：`curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5015/` 返回 `303`（未登录跳登录页）。
浏览器登录成功后再看一眼设置页的「登录名」是你要的。

### Step 5 · 放书 + 扫描

```bash
cp /path/to/book.epub /opt/recto/library/
PYTHONPATH=src python3 -m recto.cli library scan
PYTHONPATH=src python3 -m recto.cli library list      # 判定: 每本一行, 有 hash/进度/书名
```

### Step 6 · 接云端（可选）

网络前提：能访问掌阅 PC 接口（`api-pc.zhangyue.com`）。

1. 浏览器进「云端同步 → 掌阅认证 → 扫二维码」，用手机 App 扫码授权。
2. 判定（凭据已落盘）：

```bash
PYTHONPATH=src python3 -m recto.cli selftest | grep 云端凭据   # 判定: ✓
PYTHONPATH=src python3 -m recto.cli status                     # 判定: 每本书两侧进度
```

3. 对映射并首次拉取：

```bash
PYTHONPATH=src python3 -m recto.cli sync-links     # 按书名匹配云端书架
PYTHONPATH=src python3 -m recto.cli links          # 判定: 列出 书名 ↔ 云端 id
PYTHONPATH=src python3 -m recto.cli pull           # 判定: 输出每本 updated/skipped
PYTHONPATH=src python3 -m recto.cli align          # 判定: bad: 0 (位置与百分比自洽)
```

`align` 报 `cloud_bad` 说明**云端那条位置字段被写脏了**，用 `--fix` 按云端自己的百分比重算位置（只改位置，不改进度值）。

### Step 7 · 常驻（可选）

`run.sh` 是前台进程的守护壳，重启机器不会自启。生产建议用 systemd（模板见[常驻运行](#常驻运行)）。

### Step 8 · 可选组件按需接

| 组件 | 需要什么 | 不接会怎样 |
|---|---|---|
| 墨水屏摘录接入 | `[device] port` 可绑 8080 | 「摘录接入」页空账本，其余不受影响 |
| 语料 | `[corpus] dir` + 一个可用的 LLM 端点 | 已抽好的语料照常给阅读高亮用；新抽取需要端点 |
| 阅读高亮 | 语料目录里有 `*.keywords.json` | 阅读器不铺任何底色 |
| 藏书票 | Node + Playwright + `[bookplate] asset_root` | 摘录详情里的「书票」按钮报缺依赖 |

### 验收表

| # | 命令 | 期望 |
|---|---|---|
| 1 | `bash run.sh status` | `running (pid …, :5015)` |
| 2 | `curl -s -o /dev/null -w '%{http_code}' …/healthz` | `200` |
| 3 | `curl -s -o /dev/null -w '%{http_code}' …/` | `303`（未登录跳登录页） |
| 4 | `PYTHONPATH=src python3 -m recto.cli selftest` | 路径行全 `✓`，书目数 > 0 |
| 5 | `PYTHONPATH=src python3 -m recto.cli library list` | 有书，进度列存在 |
| 6 | `PYTHONPATH=src python3 -m recto.cli status` | 每本书一行，含云端百分比 |
| 7 | `PYTHONPATH=src python3 -m recto.cli align` | `bad: 0` |

---

## config.toml 全字段

原则：**所有路径都从这里来**，代码里不出现绝对路径字面量。环境变量可覆盖同名项（`RECTO_PORT` / `RECTO_DATA_DIR` / `RECTO_AUTH_USER` / `RECTO_AUTH_PASSWORD` 等，见 `src/recto/core/config.py` 的 `_apply_env`）。

| 段 | 键 | 默认 | 说明 |
|---|---|---|---|
| `[recto]` | `host` | `0.0.0.0` | 监听地址 |
| | `port` | `5015` | Web 端口 |
| | `data_dir` | `<repo>/data` | 状态库 / 日志 / 设置覆盖项 |
| `[library]` | `dir` | `<repo>/library` | 书库根（递归扫描 epub / txt） |
| | `dirs` | 空 | 多根目录（与 `dir` 并存） |
| | `download_subdir` | 空 | 云端下载落子目录；空 = 直接落根 |
| `[cloud]` | `auth_file` | `config/secrets/cloud.json` | 扫码后写这里（**含凭据，勿入库**） |
| | `state_dir` | 空 = `data/state` | 云端书籍清单 / 任务状态 |
| | `download_subdir` | 空 | 同 `[library] download_subdir` |
| `[device]` | `port` | `8080` | 墨水屏接入端口（**固件写死，不要改**） |
| | `data_dir` | `data/` | 摘录账本目录（`decks/` + `aggregate/` + `events.jsonl`） |
| `[ui]` | `timezone` | 空 = 跟随系统 | **只影响显示换算**（存盘一律本地时间）。写 IANA 名，如 `Asia/Shanghai`；环境变量 `RECTO_TZ` 优先 |
| `[corpus]` | `dir` | 空 | 语料 json 目录（阅读高亮从这里读） |
| `[reader.mark]` | `enabled` | `true` | 阅读器高亮总开关 |
| | `mode` | `highlight` | `highlight`（不改 DOM） / `span`（可加粗圆角） |
| | `dirs` | 空 = `[corpus] dir` | 语料目录 |
| | `min_len` | `2` | 词最小长度 |
| | `max_terms` | `6000` | 下发词上限（**按分类公平分配**，不会让人名吃光） |
| | `skip_digits` | `true` | 纯数字词不高亮 |
| `[bookplate]` | `asset_root` | 空 | 藏书票模板/依赖所在目录（可选） |
| | `output_dir` | 空 | 生成的 PNG 落点 |
| `[auth]` | `mode` | `password` | `password` / `none`（仅本机调试） |
| | `user` | `root` | 登录名 |
| | `password` | 空 | 空 = 环境变量 > `<data_dir>/admin-password`（首次写 `root`，0600） |
| | `shared_dir` | 见注 | **会话密钥目录**（`webauth.json` 落这里）。部署时必须改成**可写**路径（如 `<data_dir>`）；不可写时密钥每次启动重新生成 → 重启后所有登录失效 |
| | `ttl_days` | `7` | 会话有效期 |
| `[sync]` | `auto` | `true` | 自动同步总开关（阅读中推送 + 打开书时拉取；关掉则一切只走手动） |
| | `pull_on_open` | `true` | 打开一本书时先跟云端**比**一次进度（云端更靠前就问你要不要跳） |
| | `push_debounce_sec` | `12` | 两次推送至少隔多久（3–300；**节流到点会自动补推**，不留"永远同步中"） |
| | `push_tol` | `0.000001` | 进度差小于它就算"已同步"。默认几乎**精确**（40MB 的书约 40 字节）；调大 = 少打云端，但云端会停在读者眼前位置的后面 |
| | `pull_gap_sec` | `120` | 同一本书两次自动拉取的间隔 |
| | `flush_on_hide` | `true` | 切后台 / 离开页面时立刻把最后位置推上去（不等节流） |
| | `stamp` | `true` | 阅读页右下角显示「掌阅 15:37 已同步 32.4%」 |
| | `interval_sec` | `60` | **定时同步**（心跳通道）的周期；`0` = 关闭。与翻页推送并行 |
| | `tick_active_min` | `30` | 心跳只碰「最近 N 分钟内动过进度」的书；没活动就不联网 |
| | `tick_notes` / `tick_notes_sec` | `true` / `300` | 心跳顺带同步划线 / 书签，以及多久看一次 |
| | `tick_time` / `tick_time_sec` | `true` / `600` | 心跳顺带推送今天的阅读时长，以及多久推一次 |
| | `tick_settle` / `tick_settle_sec` | `true` / `180` | **收尾补推**：本地还比"上次推上去的"靠前、但已离开 `tick_active_min` 窗口的书，每 180s 补一次（不看活跃窗口） |

这几项都能在 **设置 → 云端同步** 里改，改完立刻生效（落 `settings.json`，下一次翻页 /
下一轮心跳就用新值）。

**生效顺序**：环境变量 > `<data_dir>/settings.json`（设置页写的覆盖项）> `config.toml`。
`settings.json` 只存覆盖项 —— 没设的项完全跟随 `config.toml`，所以部署配置仍然只有一处。

---

## 命令行

`PYTHONPATH=src python3 -m recto.cli <命令>`（或 `pip install -e .` 后直接用 `recto <命令>`）。

| 命令 | 作用 |
|---|---|
| `version` / `config` / `paths` | 版本 / 生效配置 / 关键路径 |
| `tz` | 时间口径（生效时区 / 现在 / 换算约定）—— 「差 8 小时」先看这个 |
| `selftest` | 自检：目录、凭据、书目数、端口 |
| `passwd [新口令]` | 服务器侧重置 Web 口令（不验旧口令） |
| `library scan` / `library list` | 扫书库 / 列书（`--force` 重建几何、`--q` 过滤） |
| `status` | 每本书两侧进度对照 |
| `pull` / `push` | 进度同步（`--dry-run` `--strict` `--overwrite` `--book`） |
| `align [--fix]` | 核对「位置 ↔ 百分比」是否自洽；`--fix` 修云端位置字段 |
| `link` / `links` / `sync-links` | 写/列/自动匹配云端映射（落在书的边车 JSON） |
| `notes sync\|pull\|push` | 划线 / 书签（`--book` `--dry-run`） |
| `time sync\|pull\|push\|backfill\|flush\|queue` | 阅读时长（`--day` `--start` `--end` `--book`） |
| `acquire list\|download\|jobs\|local` | 掌阅个人云书籍（`--name` 按书名） |
| `serve` | 起 Web 服务（`--host` `--port`） |
| `daemon [--interval]` | 轮询拉云端进度（不想用页面按钮时） |

退出码：`0` 成功，非 `0` 有错误（脚本里可直接判）。

---

## HTTP 接口

同源，全部要登录（`/healthz` 与静态资源除外）。供自动化脚本使用的最小子集：

| 方法与路径 | 说明 |
|---|---|
| `GET /healthz` | 存活探针，`200` |
| `GET /` | 书架（未登录 `303 → /login`） |
| `GET /api/state` | 书库 + 映射 + 同步态摘要 |
| `GET /api/library` | 书目列表 |
| `GET /api/log` | 最近日志（已解析成结构化行） |
| `GET /api/auth` / `POST /api/auth/qr` / `POST /api/auth/check` | 云端认证态 / 出二维码 / 轮询扫码结果 |
| `POST /api/sync/run` | body `{"action": "pull"\|"push"\|"links"\|"notes"\|"time"}` |
| `POST /api/cloud/download` / `GET /api/cloud/books` / `GET /api/cloud/jobs` | 云书下载与任务 |
| `GET /api/settings` / `POST /api/settings/password` | 设置 / 改口令 |
| `GET /api/mark` / `POST /api/mark` | 阅读高亮配置（`action=save\|reset\|bind`） |
| `GET /api/appearance` / `POST /api/appearance` | 皮肤与外观 |
| `GET /read/api/books` `GET /read/api/book/<key>` `GET /read/api/book/<key>.toc` `POST /read/api/book/<key>/progress` | 内置阅读器 |
| `GET /admin/` … | 摘录账本（一页多面板：概览 / 书架 / 摘录 / 事件 / 工具） |
| `GET /corpus/*` | 语料服务反代（外部不可达时返回同壳 503 说明页） |

> 同步类接口 **同一时刻只允许一个**：第二个请求返回 `{"busy": true}`，不会排队。

---

## 数据与目录

```
<repo>/
├── config.toml                 默认配置（入库；默认值全相对项目根）
├── config.toml.local           ← 机器专有覆盖项（不入库）
├── config/secrets/cloud.json   云端凭据（0600，勿入库）
├── run.sh                      启停脚本
├── tools/                      测试 / 界面自检脚本（playwright，见下节）
├── library/                    你的书（扫描递归）
│   ├── 某本书.epub
│   ├── 某本书.recto.json       ← 服务写：云端映射 / 同步时间 / 目录章数
│   └── 某本书.keywords.json    ← 可选：自备术语表（写了就能被阅读高亮发现）
├── data/
│   ├── reader.sqlite           进度 / 书签 / 划线 / 会话 / 时长（含 WAL）
│   ├── state/sync.sqlite       云端映射缓存 / 推送时间 / 时长镜像
│   ├── state/cloud_books.json  云书清单
│   ├── settings.json           设置页覆盖项（只存改过的）
│   ├── reading_mark.json       阅读高亮覆盖项（含每本绑定哪份语料）
│   ├── appearance.json         皮肤与外观
│   ├── admin-password          口令（0600）
│   ├── decks/                  墨水屏摘录：每次同步一个目录
│   ├── aggregate/              摘录账本（jsonl，跨 deck 合并去重）
│   ├── events.jsonl            设备事件流水
│   └── recto.log / recto.pid   进程日志与 pid
└── src/recto/                  代码
```

**边车 JSON 的设计意图**：书的"云端身份"跟着书走。搬书库时连 `*.recto.json` 一起搬，映射就还在；不依赖任何全局映射表。

---

## 坐标口径（唯一一套）

```
readPercent = ( Σ bytes(spine[0..chapterId]) + f3 ) / Σ bytes(spine)
```

- 云端位置串：`<chapterId+1>_0_0$##3-0-<chapterId+1>-<章内字节>-0##$`
- 阅读器的章号 = spine 下标 = `chapterId + 1`（**这一条最容易错，错一位就跳到下一章**）
- 反之：位置 → 百分比走同一公式，不需要页号、XPath、切片

两个由此推出的硬约束：

1. **不能用「重排版后的书」算云端坐标** —— 重排改变了 spine，字节全对不上。要用**原书**。
2. **换阅读器排版不影响进度** —— 排版只是投影，不进同步通道。

`recto align` 就是这套公式的自检器：把云端位置反解成百分比，与云端自己报的 `readPercent` 比，差 > 1e-4 就报 `cloud_bad`。

---

## 掌阅登录（扫码）

1. 「云端同步 → 掌阅认证 → 扫二维码」。
2. 手机掌阅 App 扫码授权（二维码内容是 `web_login:<code>`，必须用 App 扫，不能当普通链接点）。
3. 成功后凭据落 `[cloud] auth_file`，页面显示"已认证 · 账号 xxx · 凭据时间"。

实现是纯 HTTP（`sources/cloud_auth.py`），**不依赖 Playwright / 浏览器**。二维码有效期约 8 分钟，过期重点一次即可。

会话失效（拉取报未授权）时重新扫一次就行 —— 不需要重启服务。

---

## 同步的三个硬规则

这三条不是可选优化，是避免"进度被拨回去 / 内容被抹掉"的前提：

1. **最后写入者胜**：读者在阅读页翻页 / 跳转 / 关页面产生的进度按事实覆写云端（含回退，`why=rewind`）；没有读者动作的写入（打开书对齐、批量推送）和所有自动拉取仍旧只前进，不擅自回拨。云端记录比本地新 180 秒以上时先让一让（`why=cloud-newer`）。
2. **取最新那条**：云端同一本书可能有多份记录（不同来源写的），同步时按**更新时间**挑，而不是挑"百分比最大"的那个 —— 后者会把你带到还没读到的位置。
3. **写前端先体检**：不确定时先 `align`，`bad: 0` 再 push。位置字段被写脏时，App 的"继续阅读"会落到错误章节。

### 划线/书签的两处身份坑（2026-11-08 修）

云端把"同一本书"当成**两三个互不相干的抽屉**，而删笔记/切正文都以抽屉里的字节坐标为唯一依据：

1. **一份书最多两个 32hex 身份**：`id64` 前 32 位（云端那份文件的字节 MD5，权威）与边车记的 `id32`；文件被换过时两者会不一致。
2. **同一个身份还区分大小写**：网页推的落大写、掌阅设备自己划的落小写 —— 只按大写去拉，设备上刚划的线永远下不来（真实从"我有笔记但网页没有"复现）。
3. 因此拉取/推送都按「两个身份 × 大小写」**合并**，条目按大写归一去重；删除只认「任何身份都不存在」的墓碑（否则历史合并留下的墓碑会把本地笔记清空）。
4. **先比对文件 MD5 再切正文/写位置**：本地这份与云端那份不是同一个文件时，按字节切出来的正文一定是错的、推上去会让 App 跳到错处 —— 这种书直接跳过并在结果里写 `text-mismatch`。
5. **书库里没有的书也拉下来**：云端有划线/书签、书库里没有原书的那本，会建一条 `kind='cloud'` 的占位书目（`rbook.path=''`）并把条目落到 `rnote`/`rbookmark`（`src='cloud-only'`）。有位置与页/章序号，**没有正文**（正文只能从原书字节切）；摘录里照常出现、写明「原文不在书库」，书架不列它，导出到纸间时自动跳过空正文条目。`kind='cloud'` 的行不会被 `scan()` 清理。

### 划线尾巴为什么要补齐（2026-11-08）

掌阅写的 `positionend` **一律停在最后一个完整句子之前**（实测 30/30 条，相差 2-10 个字，再往后读必是句末标点）。位置是条目身份，不能改；正文必须按 App 里看到的那段补齐 —— `_snap_to_sentence()` 在同一段文本内往前走到句末标点（带上紧跟的收尾引号），遇标签 / 换行 / 空白就放弃，最多补 18 个字。宁可短，不可跨句。

PDF / 图片型书籍的 uniquecheck 是另一套写法（`1-1-<页>-<x>-<y>-<字符>`，两个位置的边界常被粘在一起, 实测 12 / 11 / 10 段与 `176#&#...` 四种形态），`parse_pdf_span()` 只取页号 —— 这类书连原文都没有，位置够用。

### 自动同步（阅读时，不用点按钮）

阅读页会在三处自己跟云端对一次，全部由 `[sync]` 控制，**任何一项都能在设置页关掉**：

| 时机 | 做什么 | 关法 |
|---|---|---|
| 打开一本书 | 只**比**一次：云端更靠前 → 弹选择条「掌阅云端读到 82.3%（第 8 章）／ 这里停在 41.0%」——「跳到云端」或「留在本地」；本地更靠前 → 排一次推 | `pull_on_open = false` |
| 选「留在本地」 | 立刻按本地进度覆盖云端，并在边车记 `local_wins_at`（24h）→ 期间连批量推送也允许回拨，覆盖成功后解除 | 再选一次「跳到云端」即恢复 |
| 阅读中 | 每翻一段存一次进度，**后台线程**推云端，节流 `push_debounce_sec`；**读者自己动的位置会覆写云端，包括比云端更靠后** | `auto = false` |
| 切后台 / 关页面 | 绕过节流立刻补最后那一推（`keepalive` 请求，页面走了也发得出去） | `flush_on_hide = false` |
| **定时心跳** | 每 `interval_sec`（默认 60s）看一眼「最近 `tick_active_min` 分钟内读过的书」：本地进度变了就推（**只前进**）、顺带按各自周期同步划线（`tick_notes_sec`）与今天的阅读时长（`tick_time_sec`）。没活动就完全不联网 | `interval_sec = 0` |
| **收尾补推** | 每 `tick_settle_sec`（默认 180s）把「本地比上次推上去的靠前、但已经离开活跃窗口」的书补一次 —— 合上书走了之后，最后那一小截照样落地 | `tick_settle = false` |

翻页推送解决"读者动了"，心跳解决它天然覆盖不到的三件事：**停在一页上看很久**（进度没变、
时长在涨）、**只加了划线没翻页**、**页面已经关了但时长还在队列里**。
两条通道共用同一个节流窗口（心跳推完，紧跟着的翻页不必再打一次云端），
心跳也永远走"只前进"的闸门 —— 回拨只允许读者自己动出来的位置。

> **口径：云端 = 读者停下时看到的位置。** 判定"要不要写"的容差是 `push_tol`，
> 默认 1e-6（近乎精确）。以前是 0.0005（0.05%，40MB 的书约 20KB），读者最后
> 那一小截永远判成 noop，云端停在半路，下次打开又被拉回半路 —— 症状就是
> 「在网页看过的进度，掌阅没到位」。现在三处都会把尾巴落到底：
> 翻页推送、离开页面（`flush_on_hide`）、以及**收尾补推**（`tick_settle`，
> 已离开活跃窗口也照样补）。同位置被闸门挡过的书会被记住，不会每轮白问云端。

右下角那行小字（`stamp`）是这套机制的观察窗：
`掌阅 15:37 已同步 32.4%` / `掌阅 更靠前 41.0%` / `掌阅 已按本地覆写 31.9%` / `掌阅 同步失败`。
推送进行中只保留上一次同步的信息，不刷「同步中…」。

两条刻意的保守设计：

- **不抢书，也不擅自定去留**：打开书只**比**不写，云端更靠前时问一句（「跳到云端」/「留在本地」），12 秒不选就收起、按本地位置继续读。
  老做法是自动跳 —— 位置被拽走，本地进度还被覆盖了，想留下都留不住。
- **本地所见即云端所记**：读者在这里翻到哪、跳到哪，云端就记到哪 —— 哪怕比云端更靠后（`why=rewind`）。掌阅 App 自己也是这个语义（退出书把本地位置回写云端）。只有**没有读者动作**的写入才只前进。
- **留在本地仍然有效**：选过之后边车记 `local_wins_at`（24h），期间连"无人动"的批量推送也允许回拨；覆盖成功自动解除。
- **旧会话不倒退**：`rprogress` 记着写入者的会话 id（`sid`）。停在 53% 没关的旧标签页滚动一下，
  不会把已经读到 99% 的进度写回去（同一会话内的回翻照常放行）。

---

## 标志（Logo）

界面用 `logo.png`（仓库根，2048×2048 黑字白底）作为标志。原图不能直接用：
白底块在深色皮肤上是个白方块，四周留白也让 22px 高的图徽只剩几个像素。

`tools/make_logo.py` 把它加工成三件**透明派生图**（入库，克隆仓库不需要 `logo.png` 也能跑）：

| 文件 | 用在哪 | 尺寸 |
|---|---|---|
| `assets/logo-mark.png` | 顶栏图徽（`云阅 Recto` 文字左边） | 179×66（显示高 22px/20px/18px，3×） |
| `assets/logo-full.png` | 登录卡片顶部（图徽 + 文字，单独就能认） | 300×192（显示高 64px，3×） |
| `assets/favicon.png` / `apple-touch-icon.png` | 浏览器标签页 / iOS 主屏 | 64×64 / 180×180 |

```bash
python3 tools/make_logo.py            # logo.png → 四张派生图
python3 tools/make_logo.py --check    # 派生图比源图旧就 exit 1（CI 用）
```

三条约定：

* **只按高度缩放**（`height: Npx; width: auto`），永远不拉伸 —— 1.55:1 的完整标志和
  2.7:1 的图徽各自保持比例。
* **白底已经转成 alpha**，前景是纯黑；深色皮肤由 `skin.css` 里一条
  `html img.rc-logo{filter:invert(1)}` 反相成白色 —— 换皮肤不用重出图。
* 派生图**跟着入库**，`tests/test_ui_theme.py` 会检查它们是否比 `logo.png` 旧。

## 推理模型 / 思考

抽取默认**不要思考**（`llm.thinking = "off"`）：一次块抽取的产出只有几百 token 的
JSON，推理模型会把 token 烧在 thinking 上，慢 3-5 倍，还容易撞 `max_tokens` 截断。

但"关不掉思考"的端点（例如 MiniMax 的 anthropic 兼容层对 `thinking.type=disabled`
直接 400）**不会再把整轮拦死** —— 默认放宽：

| 配置 | 关不掉思考时的行为 |
|---|---|
| `thinking = "off"` + `strict_no_reasoning = false`（**默认**） | 照常先发「别思考」参数；端点不接受就**允许它带着思考跑完**，`corpus.log` 记一条 `N 块模型关不掉思考` |
| `thinking = "off"` + `strict_no_reasoning = true` | 一发现就中止本轮（`ReasoningAborted`），不浪费后面的块 |
| `thinking = "on"` | 主动允许思考（最慢、token 最贵；记得调大 `max_tokens`） |

「严格避免推理」在语料页「1 · LLM 配置」里，**默认不勾**。想快就换非推理模型
（`agnes-2.5-flash` / `MiniMax-Text-01` 实测同一本书 `reasoning_tokens = 0`）。
`strict_no_reasoning` 只写在 `data/corpus/config.json` 里（不是 `config.toml`）。

## 可选组件

### 墨水屏摘录接入（:8080）

设备推摘录的被动接收端，**独立进程**（端口由设备固件写死，所以不并入 5015）：

```bash
PYTHONPATH=src python3 -m recto.sources.device start     # 后台起
PYTHONPATH=src python3 -m recto.sources.device status
PYTHONPATH=src python3 -m recto.sources.device tail      # 最近日志 + 最近 deck
PYTHONPATH=src python3 -m recto.sources.device stop
```

数据落 `[device] data_dir`：每次同步一个 `decks/<日期>/<时刻>_<id>/`，同时并入 `aggregate/*.jsonl`（跨 deck 去重）。
浏览与导出在 Web 的「摘录接入」（`/admin/`）。

「工具」面板的**导出到纸间**（`/admin/?pane=tools`）由服务端造 payload、服务端代发到手机
`http://<手机>:8080/send`，两条路都试过才报失败：

0. **摘录来源有两处**（`source` 可分别选）：墨水屏设备推上来的 `aggregate`（历史快照，
   只增不改）+ **阅读器/掌阅的划线 · 笔记 · 书签**（`reader.sqlite` 的 `rnote`/`rbookmark`）。
   后者是掌阅里新加的划线唯一的落点 —— 只读 `aggregate` 就会"明明加了却提示没有可导出的摘录"。
   同一书名下同一条划线只发一份（旧的 aggregate 行正文可能少最后一个字，留阅读器那份）。
1. **服务器代发**（主路径）：浏览器从公网域名打开控制台时不能直连私网 IP，而服务器与手机同在局域网。
2. **浏览器直连**（兜底）：服务器与手机不同网段时用。跨源 POST JSON 会先发 `OPTIONS` 预检，
   纸间 App 不答预检时请求会**挂住**（既不报错也不返回），所以这条只作兜底且**带 8 秒超时**。

第一本就两条路全失败 → 直接停并提示「让手机上的阅读 App 回到前台」。排查口诀：
先在服务器上 `curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' --data '{"title":"t","entries":[]}' http://<手机>:8080/send`，
通了才说明是前端问题；端口能连但无响应 = App 被系统冻结（亮屏再试）。

#### 随书带上的阅读时长

勾上「附带阅读时长」（默认勾选）后，每本书的 payload 会多两样东西：
`timeMinutes`/`timeSessions`（我们自己的汇总）和纸间真正认的
`preciseReadingDurations: [{startTime, endTime}]`（Unix 秒）。

时长来自两条源，都换算成"什么时候在读"的区间：

- **本地 `rsession`**：阅读器自己的会话，精确到秒。心跳会把一次阅读切成很多行，
  所以先按 300 秒间隔合并、丢掉 < 20 秒的碎片、单本最多 400 段（`services/sessions.book_time`）。
- **云端逐日分钟**（`rtime.cloud_minutes`）：别的设备读的，只有每天总数、没有时段，
  于是在那天补一段 `19:00 → 23:59` 往前推。补它是为了让总量跟 Recto 界面对得上 ——
  界面显示的本来就是 `max(本地, 云端)`。

书名是唯一的桥梁：摘录库的 `bookId`（M5Paper 数字 id / `kavita_xxx`）跟书库的 book key
没有任何对应关系，只能按书名归一键匹配（去括号/标点，允许子串）。
**书库里没有这本书就不带时长**，也不会因此让导出失败（出错只记 `time_error`）。


### 语料 + 阅读高亮（内置）

语料（EPUB 关键字精炼）是**本服务的一部分**，没有第二个端口、第二套登录或第二套日志：
`src/recto/corpus/` 管解析与抽词，`app/corpus.py` 提供 `/corpus/*`，页面资产在
`app/ui/assets/corpus.*`。语料根（`[corpus] dir`，默认 `<data_dir>/corpus`）里：

```
config.json        LLM 端点 / 模型库 / 分类     state.json   书目状态
outputs/<bid>/     中间产物 + 断点             <书名>-<hash>.json  固定导出
index.json         导出清单                    latest.json  最近一次成功产物
```

阅读高亮直接读这一层的 `*.json`；`config/state/index` 会被自动跳过。没有配 LLM 端点时
只是不能再跑新抽取，已抽好的语料照常可用。

高亮的语料挑选顺序（`core/reading_mark.py`）：

1. 设置页给这本书**显式绑定**的那份（绑定失效会直说，不偷偷退回模糊匹配）
2. 书库里的 `<书名>.keywords.json` 边车
3. 语料 `meta.source_epub` 指向同一份书
4. 书名模糊匹配：完全相等 / 子串 / 词集合 Jaccard ≥ 0.55 —— **同作者的另一本不会被错配**

两种渲染方式：

- `highlight`（默认）：CSS Custom Highlight API，**一个字符都不改** ⇒ 字节坐标进度、划线选区偏移、翻页列碎片化全不受影响。配色是半透明底色（不设文字色），深浅主题都只是一层淡色。
- `span`：命中词包 `<span class="rv-kw rv-kw-<cat>">`，可以加粗/圆角/内边距，代价是改 DOM（翻页模式会重排一次）。

调试口：`window.__RV__.kw()`（开关 / 模式 / 词数 / 命中数 / 语料文件）。

### 藏书票（可选）

把一条摘录渲染成 1080×1440 的 PNG（`/admin/?pane=excerpts` 的「书票」按钮）。

依赖：Node + Playwright 浏览器 + `[bookplate] asset_root` 指向一套带 `bin/node_modules/playwright` 的素材目录。
`asset_root` 只提供运行依赖和输出目录，渲染代码在 `src/recto/assets/bookplate/` —— 因此渲染器按模块启动
（`python3 -m recto.assets.bookplate.render`），由它自己读 `recto.core.paths` 的配置，不按脚本路径起。

输出落在 `[bookplate] output_dir`，路径以渲染器打印的 `out=` 为准（接口不猜目录、不取"最新文件"）。
缺依赖 / 渲染失败 / 输出不是 PNG 都会把原因带回页面，不静默失败。

摘录超长时文字会按画框自动缩排（34px → 18px，底栏位置不变），不会穿过底栏被画布裁掉；
18px 仍装不下的极端长文按超出量硬裁，优先保住底栏与印章。

---

## 常驻运行

`run.sh` 只管当前用户前台守护。生产用 systemd：

```ini
# /etc/systemd/system/recto.service
[Unit]
Description=Yunyue Recto (:5015)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=recto
WorkingDirectory=/opt/recto
Environment=PYTHONPATH=/opt/recto/src
ExecStart=/usr/bin/python3 -m recto.cli serve
Restart=always
RestartSec=3
# 别把口令写进 unit：用 data/admin-password（0600）

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now recto.service
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5015/healthz   # 200
```

设备接入要常驻时同样给 `recto.sources.device start` 配一个 unit。
**改了 Python 代码必须重启**（模块不热加载）；静态资源与模板改完刷新即可。

---

## 自检脚本（`tools/`）

`tests/` 是纯 Python 断言（`python3 tests/test_reader.py` 这样逐个跑，不需要 pytest）。
`tools/` 是**界面自检**：跑真 Chromium 打开页面，量几何、点按钮、抓 JS 报错。
它们只服务于开发，跑服务不需要它们。

```bash
cd tools && npm i playwright@1.55.0        # 一次就够（或 export PLAYWRIGHT_MODULE=/别处/playwright）
npx playwright install chromium-headless-shell

cd .. && node tools/probe_all_pages.mjs    # 页面/导航/溢出
node tools/probe_logo.mjs                  # 标志: 加载/比例/尺寸/深色反相/favicon
node tools/probe_reader_paged.mjs          # 阅读器左右翻页
node tools/probe_reader_seam.mjs           # 左右翻页: 列距/落点是否与 stride 一致
node tools/probe_flip_rapid.mjs            # 翻卷连翻: 掀页动画不堆叠, 版面不被动画污染
```

脚本里不写死任何本机路径：仓库根、`make_cookie.py` 都由 `tools/_env.mjs` 现算。
需要登录态的探针会用 `tools/make_cookie.py` 现签一个会话 cookie。
`RECTO_BASE=http://127.0.0.1:5015` 可改目标地址（默认 `5015`，个别脚本用 `RV_BASE` / `RV_KEY`）。

**仓库里不带任何现实书名**：要按书名定位的探针把名字交给 env，代码里只放占位名。

```bash
RV_TITLE_LONG=你的长篇书名 RV_TITLE_SHORT=你的短篇书名 node tools/probe_precision.mjs
RV_KEY=<某本书的 key> node tools/probe_reader_paged.mjs
```

`tests/` 里凡要读真实 epub 的用例都从 `RECTO_TEST_BOOKS` 找书，找不到就 `SKIP` ——
样书不进仓库，书名也不写死。

---

## 安全

- **默认口令是 `root` / `root`** —— 装完第一件事就是改（设置页或 `recto.cli passwd`）。
- 口令优先级：环境变量 > `config.toml [auth] password` > `<data_dir>/admin-password`（0600）。`passwd` 在环境变量提供口令时**会拒绝**并说明原因。
- `config/secrets/cloud.json` 是云端凭据，权限 0600，**不要提交到仓库**（`.gitignore` 已含）。
- 默认监听 `0.0.0.0`：只在可信网络里这样做；公网请走反向代理 + TLS，或改成 `127.0.0.1` 后由代理转发。
- `[auth] mode = "none"` 会关闭登录 —— 只在完全可信的本机调试时用。
- 改口令只校验"当前口令"，与登录名无关（会话本身已证明身份）。
- **口令事件全部留痕**：登录成功/失败、改口令成功/被拒，都写一行
  `<data_dir>/logs/webauth.log`（来源 IP + 用户名 + 原因，**不记口令本身**，512KB 轮转）。
  「改了口令但登录还是旧口令」这类问题先看这个文件。
  `RECTO_AUTH_LOG=off` 关闭、或设为具体路径（测试/临时实例用它不污染生产）。
- 改口令写完**立刻读回校验**：若实际生效来源（环境变量 / `config.toml`）把文件盖掉了，
  页面会明确报错并指出是哪一份在生效，而不是"提示成功但登不进去"。
- **登录页只放标志、用户名、密码、按钮** —— 不写配置项名、口令来源、会话时长之类的提示。

---

## 排障速查

| 症状 | 先看什么 | 常见原因 |
|---|---|---|
| 页面打不开 | `data/recto.log`；`ss -ltn \| grep 5015` | 端口被占 / 依赖缺失（`import lxml` 失败） |
| 改了口令还是旧口令能登 | `data/logs/webauth.log` | 实际来源是环境变量 / `config.toml`（页面会写明）；或改的请求根本没到（日志里没有那一行） |
| 一登录就被踢回 | `[auth] shared_dir` 是否可写；`data/admin-password` 权限 | 会话密钥目录不可写 → cookie 签名失败 |
| 重启后所有人都要重新登录 | `[auth] shared_dir` 可写？ | 密钥每次启动重新生成（目录不可写时静默发生） |
| 拉取后"进度没变" | `recto status`；`recto align` | 云端有更新的一条记录没被读到 / 本地更靠前被"只前进"挡住 |
| App 里「继续阅读」跳到下一章 | `recto align` | 云端位置字段被写脏（`chapterId` 差 1），`align --fix` |
| 阅读器进度与云端差半个百分点 | 正常 | 云端是 float32 存百分比，反解回字节有 <1 字节误差 |
| 划线正文末尾少/多一个字 | 打开那条划线看原文 | 区间口径差异（云端 `positionend` 指向最后一个字本身） |
| 掌阅里有笔记，网页里没有 | 拉取结果的每本书那一行；`recto notes status` | ① 设备新划的落在**小写** bookid 下（旧版只读大写）② 本地这份与云端不是同一文件 → `text-mismatch` ③ 书库里没有这本书 → 列在"云端还有 N 本" |
| 摘录页空 | `recto.sources.device status` | 设备接入进程没起，或设备没推到 8080 |
| 语料页抽词失败 | 语料页「1 · LLM 配置」里点测连通；看 `data/state/logs/corpus.log` | 端点/额度/模型不支持（模型强制思考时会变慢，但不是错误 —— 见「推理模型 / 思考」一段） |
| 换了排版后高亮不显示 | 语料目录里有没有这本书的 `keywords.json` | 语料缺失，或书名对不上（在设置页手动绑定） |
| 藏书票报缺依赖 | `node -v`；Playwright 浏览器是否装 | 只有这一块依赖 Node |
| 时间戳差了 8 小时 | `python3 -c "from recto.core import clock; print(clock.summary())"` | 展示层口径问题（旧版有：毫秒时间戳按 UTC 印、裸本地 ISO 当 UTC 换算）；`[ui] timezone` / `RECTO_TZ` 可固定时区 |

**日志位置**：`data/recto.log`（Web），设备接入在自己的 `data_dir` 下，同步审计行格式统一为
`<时间> <级别> <中文一句话>`，页面上是同一个来源。

---

## 设计取舍：它不做什么

- **不做书库管理器的替代品**：不刮削元数据、不做格式转换、不做 OPDS。它读你已经有的书。
- **不改你的书**：不重排、不切片、不写回源文件。原书字节是坐标权威，动了它就等于毁了进度。
- **不碰别人的数据库**：只读写自己的 `library/` 与 `data/`。
- **不做多用户**：单人自托管，一个登录名，没有权限模型。
- **不追求"云端功能全覆盖"**：只同步四件事（进度 / 划线 / 书签 / 时长）。云端的书评、购买、推荐一律不碰。
- **渲染即投影**：皮肤、排版、高亮都不落库 —— 所以"改样式"永远不会变成"数据迁移"。

---

## 目录结构

```
src/recto/
├── app/        Web 层 —— 鉴权 / 路由 / 渲染
│   ├── server.py      单进程入口（阅读器 → 语料 → 摘录 → 控制台 依次派发）
│   ├── console.py     书架 / 云端同步（含同步动作与二维码）
│   ├── reader.py      内置阅读器（页面 + /read/api/*）
│   ├── excerpts.py    摘录账本后台（一页多面板）
│   ├── corpus.py      语料服务反代（同壳同皮肤）
│   ├── appearance.py  外观 / 皮肤
│   ├── settings_page.py 设置
│   ├── webauth.py     登录与会话
│   └── ui/            外壳（导航 / 文档骨架）与皮肤
├── core/       领域层 —— 配置 / 路径 / 边车 / epub 几何 / 书目模型 / 版式 / 高亮
├── sources/    外部适配器 —— 掌阅 API / 扫码登录 / 墨水屏设备
├── services/   用例 —— 进度 / 笔记 / 时长 / 云书获取 / 阅读器状态 / 摘录挑选
├── assets/     藏书票渲染
└── store/      （预留）
```

---

## 文档

- [docs/READER.md](docs/READER.md) —— 内置阅读器：分页、排版、坐标口径、调试口
- [docs/UI.md](docs/UI.md) —— 外壳 / 皮肤 / 分区 / 组件


---

**云阅 Recto** · 让三端读同一本书时，进度只有一条线。
