# 三榜公开来源采集与原文缓存

这条管道与 `src/aihot` 的旧 Release / 聚合采集独立。它只把公开来源保存为共享候选，不给项目打分、不提升为 `verified`、不自动选榜，也不发送或发布内容。筛选和人工／模型核验在后续环节进行。

## 已实现的范围

| 入口 | 读取方式 | 默认轮换 | 记录性质 |
| --- | --- | --- | --- |
| OpenAI News / Google AI | 官方 RSS | 每日，与其他来源共享预算 | `news` 发现线索；订阅发布时间不冒充原始事件发生时间 |
| Show HN | 官方 Firebase API 的 `showstories` 与有限条目详情 | 每日 | 作者发布线索；帖子时间不是项目首发时间 |
| Hugging Face Spaces | 公开 API，最近修改排序 | 周三 | 应用入口；修改时间不等于重要更新 |
| Hugging Face Models | 公开 API，最近修改排序 | 周四 | 模型入口；不根据目录猜用途、许可或运行效果 |
| Hugging Face Blog | RSS / Atom | 周五 | 文章；缺失发布日期保留未知 |
| 其他 RSS / Atom | 配置同一 reader | 显式配置 | 可设置八栏中的主栏目和 project / reading / news |
| GitHub 原文 | 官方 API 解析 commit，再按 commit 读 README | 按需 `github` 命令 | 原始响应、固定版本 README 与 SHA-256 |
| 通用公开原文 | 有界 HTTP GET | 按需 `fetch` 命令 | 原始字节和请求凭据（receipt），不运行页面脚本 |

配置在 `config/digest_sources.json`，周一为 `0`、周日为 `6`。OpenAI News、Google AI 和 Show HN 每日保留，HF 入口继续轮换。配置只是目前接通的有限采集范围；原有来源规划中 GitHub Trending、MCP Registry、游戏等仍需独立 reader 或按需人工读取。本轮不宣称八个栏目均已自动覆盖。HelloGitHub 不接受作为采集源。

2026-10-04 00:34 北京时间，本机使用同一有界 HTTP reader 只读验证：OpenAI News RSS HTTP 200，解析 1,245 个条目；Google AI RSS HTTP 200，解析 20 个条目。配置只各取最多 20 条、按 48 小时窗口处理，并和所有来源共用最多 20 个新候选预算；上述解析总数不等于实际采集数。临时缓存已清理，无生产入库、模型调用或自动化恢复。

新闻 RSS 保留可解析的文章发布时间；只有 updated/刷新日期时 `published_at` 保持未知，不创建 event、不自动 verified。正文核验阶段须独立核对原始事件和日期；新闻不因为是官方源就自动入选。不默认接入 AIHOT 实际 API 或扩大研究/融资类配额。

## 调用

在项目根目录运行；Windows 使用 `.venv/Scripts/python.exe`，其他平台使用相应虚拟环境的 Python：

```powershell
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_sources collect --root .
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_sources collect --root . --source hf-blog --limit 3 --run-id manual-hf-check
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_sources fetch --root . --url https://example.org/article
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_sources github --root . --url https://github.com/HackerNews/API
```

`--source` 可重复，用于明确选择入口；不传时按真实北京时间的星期轮换。`--config` 可指定另一个配置文件。每个入口默认最多解析 30 条，可配置 1～80；每批新身份上限为 20，`--limit` 只能降低到 1～20。各入口轮流贡献新候选，避免 Show HN 先占满预算、轮换源无法进入候选库。已经在库中的项目再出现时保存来源观察，不占新项目名额；同批同一规范身份合并来源。总观察数可能超过 20，但受入口数量和各入口解析上限约束。

自动收集不会凭 `lastModified`、版本号或 README 文字把记录标成重要更新。真正重要更新必须在后续核验中明确事件及原文日期后进入 `digest-batch.v2`。尚未确定的栏目为初步分类，`source_observation.category_is_provisional` 会明示这一点。

Python 集成接口：

```python
from ai_notes.digest_sources import collect, fetch, read_github

receipt = collect(root, source_ids=None, limit=20, run_id="worker-unique-run")
# receipt: schema_version=digest-source-run.v1, status, run_id,
# collected_at, new_candidates, known_observations, source_results,
# batch_path, receipt_path, ingest.read_back_confirmed

evidence = fetch(root, "https://example.org/article")
# evidence: body_path, sha256, byte_count, fetched_at, checked_at,
# final_url, http_status, cache_status, receipt_path

readme = read_github(root, "https://github.com/HackerNews/API")
# 额外提供 commit_sha、original_url、document_path、document_sha256。
```

`root` 为 `pathlib.Path`。测试可传入使用 `httpx.MockTransport` 的 `client`，该注入点绕过真实 DNS 查询。生产默认自行创建只读公开 HTTP 客户端，不传入浏览器 Cookie 或私有 token。

## 保存与恢复

数据全部保存在已忽略的 `data/weekly_digest/source_runs/`：

- `cache/<URL hash>/`：不可变内容字节，以内容 SHA-256 命名；`latest.json` 保存 ETag、Last-Modified 和最后请求元数据。
- `fetches/`：每次成功或失败请求的记录，包括真实时间和失败原因。
- `documents/`：从 GitHub 原始 API 响应解码的 README，另有内容 hash。
- `runs/<run_id>/batch.json`：可直接交给 `digest.ingest` 的 `digest-batch.v2`。
- `runs/<run_id>/receipt.json`：实际入口、覆盖窗口、条目数、截断、失败和入库读回结果。
- `source-state/<id>.json`：上次入口结果，供失败后的有限补查使用。

先原子落盘批次，再入库，随后以只读 SQL 对比完整批次和观察条数。相同 `run_id` 再执行会重放已保存批次，不重新联网，也不刷新旧时间；即使上次入库失败，已保存候选仍可恢复。要执行新一轮实际请求，应使用新 `run_id`。空候选和全部失败也保存合法空批次及真实来源结果。

单个 run 使用 Windows `msvcrt` / POSIX `flock` 的非阻塞进程锁，防止并行执行。`.lock` 文件保留以避免删文件造成的竞争；进程硬退出时操作系统会释放锁，之后可直接重放，不需要删除残留文件，也不会抢走仍在运行的进程的锁。这里是采集级防重复，不是后台任务队列的租约系统。

通常回看 48 小时；上次同入口失败且失败在最近 7 天内，下一次读取最多回看 7 天。Reader 仍受条数上限约束，这不意味着完整补齐 7 天。receipt 分别记录计划窗口、实际读到的最早／最晚来源日期、日期未知、超窗口、未来日期、初筛预算排除和索引截断。未来发布日期不录入；缺失或无法解析的日期保留未知，不据此推断“今天新发布”。

HTTP 最多跟随 5 次重定向，每次都检查公开 HTTP(S) 地址；不允许凭据 URL、localhost 和非公网 IP。直接连接时检查本机 DNS；已有 HTTP 代理负责目标域名解析时，不把不参与路由的本机 DNS 当作代理端解析结果。每次请求读超时 15 秒，读取和重定向期间检查 30 秒预算（当前阻塞读最多再等一次读超时），响应体最多 3 MiB。ETag / Last-Modified 支持条件请求；`304` 会核对已存字节的 hash，保留原 `fetched_at`，只更新 `checked_at`。**读取缓存与 HTTP 304 均不会制造新的核验时间。**新候选首次被识别的 `discovered_at` 使用实际识别时间，不因响应缓存更早就倒填发现日期；已知项目保留最早发现时间。通用 reader 不支持登录网页、付费接口或 JavaScript 渲染。

## 验证

```powershell
.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests -p test_digest_sources.py
```

离线测试覆盖：RSS / Atom、Show HN、HF Models / Spaces、未知及未来日期、失败后 7 天上限、HTTP 304、缓存篡改、跳转内网、超大响应、空／失败批次、规范 URL 去重与旧项目不占新名额、保存后幂等重放、固定 commit 的 GitHub 原文以及真实入库读回。可通过 `--fixture tests/fixtures/digest_sources/hf-blog.json` 使用离线响应；演练应指定临时 `--root` 与明确的 `--config`，防止把示例写入生产候选库。

2026-10-03 18:25:47（北京时间）公开网络 smoke test：Show HN、HF Spaces、HF Models、HF Blog 四入口各读到 3 条，无入口失败；临时库保存 3 个新候选并读回一致。另于 18:24:13 读到 `HackerNews/API` commit `8a0528f538bca407c2ceeeefc9bee48bdb99c1c8` 的 README（12,878 字节，SHA-256 `d05432c450dbf604b62611ec012cdf18ca15e31005a3c2ab071d6bb8de82e61b`）。此前 HF 因本机异常 AAAA 解析而失败，修正已有 HTTP 代理的域名解析边界后重新读取成功。

本次 smoke test 使用临时 root、每入口最多 3 条、全批最多 3 个新候选；临时数据库与正文缓存验证后清理，未修改生产账本。来源轮流取候选的预算策略另由离线测试验证；不把一次读取成功当作持续无人值守运行的证明。
