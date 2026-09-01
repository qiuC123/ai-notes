# AIHOT 每日多来源候选榜 MVP 实施报告

Status: Complete — unit tests, real public-network run, same-date replay, and artifact inspection passed
日期：2026-08-27
范围：仅本仓库 MVP；未修改历史文档、\`work/\`、\`.agents/\`、外部 Codex 自动化或任何凭据；未提交 Git。

本报告只记录实际执行过的命令、返回结果和产物检查，不把计划写成成功。

## 实现内容

- 新增公开来源采集、六类格式解析、统一事件模型、URL/标题规范化、确定性聚类、可解释评分、JSON/Markdown 报告、失败关闭 manifest 和 CLI。
- 新增来源溯源字段：\`source_links[].url/source_id/source_class/provenance\`；聚合来源的发现链接与其提供的原始链接明确区分。
- 新增受校验 TLS 的 Windows 证书库兜底：没有关闭证书验证，也没有读取或打印凭据。
- 新增完整原始快照重放：同日期、同配置的成功重跑不再受实时热度指标波动影响。

主要实现文件：

- \`src/aihot/http.py\`、\`src/aihot/collectors/parsers.py\`、\`src/aihot/normalize.py\`
- \`src/aihot/cluster.py\`、\`src/aihot/score.py\`、\`src/aihot/pipeline.py\`、\`src/aihot/report.py\`、\`src/aihot/__main__.py\`
- \`config/sources.yaml\`、\`config/scoring.yaml\`
- \`tests/test_collectors.py\`、\`tests/test_normalize_cluster.py\`、\`tests/test_score.py\`、\`tests/test_pipeline.py\`、\`tests/test_cli.py\`、\`tests/test_safety_contracts.py\`、\`tests/test_http.py\`

## 严格垂直切片 TDD 证据

每个切片都先创建并运行失败的 \`unittest\`，再增加该行为的最小生产实现，随后运行 GREEN。测试 fixtures 全部在本地，不访问公网。

### 切片 1：本地来源格式解析

- RED：\`python -m unittest discover -s tests -v\`
- RED 结果：退出码 1；真实错误为 \`ModuleNotFoundError: No module named 'aihot'\`。当时只有测试和 fixtures，尚未创建生产源码。
- 中间反馈：初版解析器运行后有 1 条断言失败，AIHOT 原始链接仍含 \`utm_source\`；测试未改动，修复的是 URL 清理实现。
- GREEN：同一全量命令退出码 0，**6** 个测试通过。实现 \`RawRecord\`、六个解析器和仅清理跟踪参数的 URL 函数。

### 切片 2：URL/标题规范化与确定性聚类

- RED：\`python -m unittest tests.test_normalize_cluster -v\`
- RED 结果：退出码 1；真实错误为 \`ModuleNotFoundError: No module named 'aihot.cluster'\`。
- GREEN：\`python -m unittest discover -s tests -v\` 退出码 0，**10** 个测试通过。覆盖 URL/标题规范化、中英文近似标题合并、相同规范 URL 强制合并和不相关标题不合并。

### 切片 3：可解释评分与硬惩罚

- RED：\`python -m unittest tests.test_score -v\`
- RED 结果：退出码 1；真实错误为 \`ModuleNotFoundError: No module named 'aihot.score'\`。
- GREEN：全量命令退出码 0，**14** 个测试通过。覆盖七个分项、discovery-only、最近人工决策相似、弱证据词、融资/股价/名人噪声惩罚，以及一手证据优先。

### 切片 4：离线端到端每日流水线

- RED：\`python -m unittest tests.test_pipeline -v\`
- RED 结果：退出码 1；真实错误为 \`ModuleNotFoundError: No module named 'aihot.pipeline'\`。
- 中间反馈：首次全量运行有 1 个测试在 \`TemporaryDirectory\` 清理后才检查原始快照；只移动断言到临时目录生命周期内，没有放宽断言或改动生产行为。
- GREEN：全量命令退出码 0，**18** 个测试通过。覆盖单源失败继续、少于三成功源失败关闭、JSON/Markdown/manifest、原始快照和离线同日期覆盖。

### 切片 5：CLI

- RED：\`python -m unittest tests.test_cli -v\`
- RED 结果：退出码 1；真实错误为 \`ModuleNotFoundError: No module named 'aihot.__main__'\`。
- 中间反馈：CLI mock 缺少真实 \`RunResult\` 的输出路径属性；只补齐 mock，不修改生产行为。
- GREEN：全量命令退出码 0，**20** 个测试通过。覆盖日期/Top 参数和失败关闭时的非零退出码。

### 切片 6：证据安全、来源结构和失败产物失效

- RED：\`python -m unittest tests.test_safety_contracts -v\`
- RED 结果：退出码 1；真实错误为无法导入 \`load_pipeline_config\`，因为来源聚类配置与安全输出契约尚未实现。
- GREEN：全量命令退出码 0，**29** 个测试通过。新增并覆盖：聚合来源不能凭数量变成 primary、\`evidence_allowed: false\` 不能成为主证据、HTTP 200 错误形 JSON/HTML 仍记为失败、失败重跑会失效旧产物、Top 请求数与实际数一致、无链接候选失败关闭、完整来源溯源、Markdown 惩罚原因和配置化 0.48/72h。

### 切片 7：TLS、AIHOT 链接关系与稳定排序

- RED：\`python -m unittest tests.test_http tests.test_safety_contracts -v\`
- RED 结果：退出码 1；分别暴露缺少 \`build_verified_ssl_context\`、聚合来源唯一链接被误标为原始链接、同 URL 的不同来源链接排序不完整。
- GREEN：同一命令退出码 0，**33** 个测试通过。实现保持 TLS 证书验证的 Windows ROOT 证书库兜底；AIHOT 仅在解析到指定原始链接时标记为 \`original_link_from_aggregator\`；链接按 URL、来源 ID、来源类别、溯源类型完整排序。

### 切片 8：同日真实快照重放

- RED：\`python -m unittest tests.test_safety_contracts.SafetyContractTests.test_successful_same_date_rerun_replays_the_real_raw_snapshot_without_fetching -v\`
- RED 结果：退出码 1；真实错误为 \`TypeError\`，\`DailyPipeline.run()\` 尚不接受 \`refresh\` 参数。
- GREEN：全量命令退出码 0，**34** 个测试通过。实现 \`refresh=True\` 的明确重新抓取，以及默认同日期成功快照重放；测试验证第二次不调用 fetcher 且稳定产物逐字节一致。

### 最终全量 GREEN

实际命令：

\`\`\`powershell
python -m unittest discover -s tests -v
\`\`\`

最后一次确认结果：退出码 **0**，\`Ran 34 tests in 0.883s\`，\`OK\`。其中 CLI 测试打印的 fixture 输出和失败关闭提示均来自 mock 测试，不是外网运行。

## 真实公开网络运行

### 先前失败与修复证据

第一次真实 HTTPS 尝试确实失败：11 个来源全部报告 \`FetchError ... Permission denied\`，没有事件，管道按设计失败关闭。原因是该 Windows 运行环境的默认 Python CA 路径不可访问，并非把 HTTP 错误伪装为成功。

修复前先写 TLS RED 测试，再实现 Windows ROOT 证书库兜底；验证仍为开启状态，没有使用 \`verify=False\`，没有导出任何证书、凭据或会话。后续所有公开来源均通过受校验 HTTPS 成功读取。

### 最终新鲜抓取（真实网络）

在 \`src/\` 目录实际执行的等价每日管道调用：

\`\`\`powershell
python -c "from pathlib import Path; from aihot.pipeline import build_default_pipeline; build_default_pipeline(Path('D:/devlop/Ai定时任务/Ai 热点')).run('2026-08-27', top=3, refresh=True)"
\`\`\`

实际结果：退出码 **0**。清单 \`daily-2026-08-27-df8333caaafd\`：

- UTC 时间：\`2026-08-27T10:40:51Z\` 至 \`2026-08-27T10:41:51Z\`
- 配置哈希：\`df8333caaafd17060a92f901b695a7674bc941db02842936d87ded2e39f65b2b\`
- 成功来源：**11/11**；失败来源：**0**
- 原始/归一化条目：**2,162 / 2,162**
- 聚类事件：**2,095**
- 输出候选：请求 3、实际 3
- 写入 11 份原始快照（3 JSON、8 XML）及事件、Top JSON、Markdown、manifest。

该次 Top 3 为：

1. \`The Hugging Face incident and the road ahead\`，100 分，主链接为 OpenAI 官方页面。
2. \`One Symptom, Three Levers: A Critical Review of On-Policy Self-Distillation\`，97 分，主链接为 arXiv。
3. \`Training and Finetuning Multi-Vector Embedding Models with Sentence Transformers\`，97 分，主链接为 Hugging Face 博客。

### 同日期 CLI 重跑（快照幂等验证）

随后实际执行：

\`\`\`powershell
python -m aihot daily --date 2026-08-27 --top 3 --root D:/devlop/Ai定时任务/Ai 热点
\`\`\`

实际结果：退出码 **0**。新 manifest 的 UTC 时间为 \`2026-08-27T10:42:59Z\` 至 \`2026-08-27T10:43:36Z\`，仍是 11/11 成功、0 失败、2,162 条目、2,095 事件、Top 3；\`reused_raw_sources\` 恰为全部 **11** 个来源，证明这次没有重新抓取而是重放刚才的新鲜原始快照。

逐字节 SHA-256 比较：

| 产物 | 新鲜抓取 | 同日重跑 | 结果 |
| --- | --- | --- | --- |
| \`events.json\` | \`91dc52f692624b5e21583da4a63af25325923151a5263fe2f89ada9f4838f00f\` | 相同 | 相同 |
| \`top-candidates.json\` | \`a57640d4af856bbc2ce7d54e74051fb9db63f42729d90955dab78d71b6506bc4\` | 相同 | 相同 |
| \`top-candidates.md\` | \`ac90c016bd3899010c0f4d2d4840c887e83f882cbfeac69228f75b9d19faab30\` | 相同 | 相同 |
| \`run-manifest.json\` | \`30e12554b4cf124745bcb0a178530b8480cc9690cf592b8f8ae9624d360f014a\` | \`c613def8863d2858ce264db2b00118dc9594c3a06936f64e400268459fdcb1d3\` | 预期不同 |

\`run-manifest.json\` 不要求逐字节相同：其开始/结束时间每次运行都会更新，且新鲜抓取的 \`reused_raw_sources\` 为 \`[]\`，重放运行记录 11 个来源。这是审计信息，不是重复事件。

早期两次“都重新抓取”的真实检查曾观察到两个非 Top 的 Hugging Face 模型事件热度/点赞摘要随上游 API 改变；事件 ID 与数量、Top JSON 和 Markdown 保持一致，但 \`events.json\` 不能保证跨两个不同实时输入逐字节相同。这是上游动态数据，不是追加或重复。最终的快照重放验证正是用来保证相同日报输入的可复现性。

## 产物逐项审计

以下文件均已实际读取和解析：

- \`data/raw/2026-08-27/\`：11 个快照。所有 JSON 均可解析；所有 XML 根节点符合 RSS 或 Atom feed。
- \`data/events/2026-08-27/events.json\`：可解析、状态成功、2,095 个唯一事件 ID。每个条目包含约定字段和非空的带类型来源链接。
- \`outputs/2026-08-27/top-candidates.json\`：可解析、请求数/实际数/候选数均为 3；每位候选的总分在 0–100，七个评分分项齐全，主链接及完整来源链非空且排序确定。
- \`outputs/2026-08-27/top-candidates.md\`：已完整读取。三位候选均显示总分、分项分数、惩罚、惩罚原因、适合理由、证据状态、主证据、全部来源链接及尚待人工核验事项。
- \`outputs/2026-08-27/run-manifest.json\`：可解析，明确记录成功/失败来源、计数、配置哈希和快照重放状态。

## 验收结论

- [x] 公开来源数量不少于 3（实际 11）。
- [x] 单来源失败、内容结构错误、少源、无候选、无链接候选均失败关闭，并失效旧候选产物。
- [x] 所有来源响应、事件、候选和 Markdown 产物实际存在且已检查。
- [x] 每个候选包含可追溯来源链接、主证据、0–100 分数、七个分项与惩罚原因。
- [x] 同日期重跑不追加事件；使用快照后 \`events.json\`、Top JSON、Markdown 逐字节一致。
- [x] 全部 34 个本地 \`unittest\` 通过。
- [x] 未发送外部消息、未读取凭据、未改动历史工作区或外部自动化，未执行 Git 提交。

---

# Ai Notes 0.2 实施补充报告

Status: Code implemented and smoke-verified; full 12-source 90-day Hermes content baseline and 14-day online trial not started
日期：2026-09-01
授权：用户明确发送“开始实施 Ai Notes，按已批准设计执行”
边界：未创建 cron，未外发，未读取项目凭据，未提交 Git。

## 实现范围

- 新增 `config/ai_notes_sources.yaml`：12 个 trial Release Atom、分类、tag 规则、包映射和 policy version。
- 新增 `src/aihot/release_sources.py`：真实 tag 提取、Atom/REST 解析、预发布过滤、仓库 URL 归属校验、路径安全、REST 分页与内容寻址缓存。
- 新增 `src/aihot/release_ledger.py`：长期 JSONL 账本、规则版本、待重试状态、安全公告内容变更、首次发现/原始引用/处理历史、原子批量提交。
- 新增 `src/aihot/release_review.py`：零工具 Hermes 探针、5 条一批的 one-shot review、严格 JSON Schema、HTML/纯文本规范化和逐项证据子串校验。
- 新增 `src/aihot/security_advisories.py`：GitHub reviewed high/critical 映射、updated 游标数据、内容哈希、降级/映射变化/撤回处理。
- 新增 `src/aihot/ai_notes.py`：一个 daily 命令内部的 Collect → Review → Finalize、source state、baseline gate、Atom 缺口补抓、partial 合并、review_failed 重试、同日 success 重放、30 天 raw 清理和进程锁。
- 新增 `src/aihot/source_evaluation.py`：完整历史 backtest、REST 缓存、baseline marker、固定 14 天来源评估 JSON/Markdown。
- `src/aihot/__main__.py` 已切换到 Ai Notes，并新增 `daily`、`backtest`、`evaluate`。
- `pyproject.toml` 与 `src/aihot/__init__.py` 版本统一为 0.2.0；README 已改为 Ai Notes 实际行为。

## 严格 TDD 证据

每个行为切片均先运行目标测试并观察因功能缺失或错误行为产生的 RED，再写最小实现并运行 GREEN。关键 RED 包括：

- `ModuleNotFoundError: No module named 'aihot.release_sources'`：Release Atom/tag 切片；
- `ImportError: cannot import name 'filter_release'`：正式版与 prerelease 硬过滤；
- `ImportError: cannot import name 'load_release_sources'`：12 源注册表；
- `ModuleNotFoundError: No module named 'aihot.release_ledger'`：跨天账本；
- `ImportError: cannot import name 'detect_atom_gap'`：Atom overlap 缺口；
- `ImportError: cannot import name 'GitHubReleaseRestBackfiller'`：REST 分页补抓；
- `ModuleNotFoundError: No module named 'aihot.release_review'`：Finalize 证据校验；
- `ImportError: cannot import name 'HermesCliReviewer'`：真实零工具 reviewer；
- `ModuleNotFoundError: No module named 'aihot.ai_notes'`：三阶段端到端；
- `RuntimeError: model unavailable` 直接穿透：review_failed 隔离；
- same-date rerun 把已接受信息覆盖为空：成功快照重放；
- partial 重跑不重试补漏且覆盖健康结果：持久 gap + 按 release_key 合并；
- `ModuleNotFoundError: No module named 'aihot.security_advisories'`：安全来源；
- 安全公告内容变化未触发重评：security content hash；
- 匿名安全端点失败仍被误标完整：partial；
- GHSA 降级/映射变化被静默过滤：known advisory revision；
- 未来日期先清理 raw：日期写入前校验；
- 1 天试运行可错误晋级：固定 14 天；
- gap 当天仍计成功：评估排除 known_gaps；
- Finalize 接受非严格 reject/pending 形态：Schema 收紧；
- HTML 规范化包含隐藏节点并删除纯文本尖括号：HTML/纯文本分流；
- unsafe source id `../escape` 未被拒绝：ASCII source id/repository 白名单；
- 并发锁不存在：O_EXCL run lock；
- REST 回测不缓存：URL hash cache；
- 账本更新覆盖审计历史：first_seen/raw_ref/history；
- 已知 GHSA 降为 medium 或失去映射后不再出现：修订记录；
- Feed 有 overlap 但最旧时间晚于 last success 时漏报：时间边界缺口。

最终实际命令：

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

最终结果：退出码 **0**，`Ran 76 tests`，`OK`；compileall 退出码 **0**。

## 真实公开网络采集验收

在临时根目录运行真实 `PublicHttpFetcher`、12 个已批准 Atom 和 GitHub 全球安全公告；使用确定性拒绝 reviewer，仅验证采集/解析/过滤/产物，不冒充 Hermes 内容结论。

实际结果：

- 12/12 Release Atom 成功并解析；
- 全球安全公告在第一次烟雾测试中因匿名共享配额返回 403，运行正确降级为 `partial`；配额恢复后的第二次测试成功读取；
- 原始 Atom XML：12 份；
- 硬过滤拒绝：35 条；
- 进入 review queue：84 条；
- 84 个唯一 `release_key`，缺失必填字段 0；
- 生产 baseline gate 未建立时，12 个来源均写 `baseline_not_established`，状态为 `partial`，没有伪装完整成功；
- manifest、queue、decisions、accepted JSON 均已解析。

真实烟雾 manifest：

`C:\Users\Administrator\AppData\Local\Temp\ai-notes-real-smoke\outputs\2026-09-01\run-manifest.json`

## 真实 Hermes reviewer 验收

先实测 `hermes chat --toolsets __no_tools__ --safe-mode --quiet --max-turns 1`，模型报告可用工具数组为空。生产 reviewer 现会在首次审阅前自动执行同样的零工具探针，失败即关闭。

随后从真实队列各取一条执行 Hermes one-shot 并通过 `finalize_decisions()`：

1. `modelcontextprotocol/modelcontextprotocol@2024-11-05-final`：Hermes 以“说明缺少具体实质变化”为由拒绝；严格 Schema 校验通过。
2. `openai/codex@rust-v0.152.0`：Hermes 接受 3 项变化（MCP 工具能力、安全后端 URL/重定向保护、planning tool 默认禁用）；3 项英文 evidence 均在真实 Release 说明中精确匹配，Finalize 通过。

真实 reviewer 使用了零工具 probe + review 两次调用；没有调用终端、文件、浏览器或其他工具。

## 真实回测路径烟雾验收

对低频 `mcp_spec` 单一来源执行真实 GitHub Release REST 90 天回测路径，使用确定性拒绝 reviewer，仅验证分页、缓存、覆盖和 marker：

- observed：1；
- eligible：1；
- coverage_complete：true；
- pages_fetched：1；
- 成功写入 backtest JSON/Markdown；
- 成功写入 `backtest-baseline.json`，complete source 为 `mcp_spec`。

产物：

`C:\Users\Administrator\AppData\Local\Temp\ai-notes-backtest-smoke\outputs\backtests\2026-09-01-90d\backtest.json`

该单源确定性烟雾测试不等于完整 12 源 Hermes 内容回测。

## 独立审查与修复

- Codex CLI `workspace-write` 首次因 Windows `pwsh.exe` CreateProcessAsUserW error 5 阻塞，未改文件；残留进程已结束。
- 按 Codex Skill 改用 `danger-full-access` 做只读审查。它运行测试并提出事务顺序、基线、安全游标、GHSA 降级、试运行指标、缓存/指标、账本审计、零工具边界、严格 Schema、日期/CLI 错误等问题；进程后来偏离到无关个人知识库，已主动终止，偏离内容未作为证据。
- 独立 Hermes reviewer 第一轮给出 REQUEST_CHANGES，额外发现 source_id 路径逃逸和 Atom 时间边界；有效问题均以失败测试复现后修复。
- 第二轮 reviewer 因超过 10 分钟仍未完成，被停止；其已完成的读取和测试未给出新的已证实阻断项。

## 尚未执行与残余边界

- [ ] 尚未执行完整 12 源、90 天、所有硬过滤存活项的 Hermes 内容回测；当前实现和命令已存在，但完整运行会产生大量 GitHub REST 请求和模型调用，必须单独确认配额与成本。
- [ ] 尚未开始 14 天在线试运行，因此没有来源晋升结论。
- [ ] 未创建 cron，未自动晋升来源，未外发消息。
- [ ] GitHub 匿名安全 API 仍可能因共享出口限额返回 403；系统保留游标并标 `partial`，不会伪装成功。
- [ ] 语义判断仍由模型完成；零工具探针、严格 Schema 和原文证据降低风险，但不能数学证明中文概括完全等价于证据，仍需试运行抽检。
- [ ] 账本使用进程级排他锁防止正常并发；进程被强制终止可能遗留 lock 文件，需要人工核查后删除，系统不会自动猜测为 stale。

## 2026-09-01 进度复审补充

- 本机现场核验：`python --version` 为 3.11.15；Windows `py` 默认解释器为 3.13.12；`py -3.11` 不可用，因为 Python Launcher 不识别 uv 管理的 3.11。README 已改为要求 Python >=3.11，并建议 cron/计划任务固定 `.venv/Scripts/python.exe`。
- 复审声称测试创建 `outputs/fixture/`，当前现场无法复现：目录不存在，原测试只打印 fake path，并未写文件。为消除误导，fake path 已迁移到系统临时目录；重新运行 76 tests 后确认仓库内仍无 `outputs/fixture/`。
- README 已明确 `pipeline.py`、`score.py`、`cluster.py` 和 `config/sources.yaml` 是 deprecated 的 0.1 历史管道，当前 CLI 不再调用。
- 来源注册表现已为 12 个来源逐项显式声明 `feed_url`、`policy_version` 和 `excluded_tag_patterns`；新增 `tests/fixtures/ai_notes_tag_cases.json`，覆盖每个来源的一个正式正例和一个预发布/多包反例。
- 当前最大操作风险仍是整个 0.2 工作树未进入 Git 历史；是否提交必须由用户明确授权。

## Ai Notes 当前验收结论

- [x] 12 个批准来源、tag 规则、`-final`/RC、预发布过滤已实现并有离线测试；
- [x] Release 账本、同日幂等、review_failed 重试、Atom 缺口补抓、REST 缓存、partial 合并已实现；
- [x] 三阶段产物、严格 evidence 校验、零工具 reviewer 探针已实现并真实验证；
- [x] 安全公告 high/critical 映射、游标、满页缺口、内容变化、降级、映射变化和撤回已实现；
- [x] 90 天回测命令、baseline marker、14 天评估报告能力已实现；
- [x] 76 个 unittest 与 compileall 全绿；
- [x] 12/12 真实 Atom 采集和两条真实 Hermes review 验收通过；
- [ ] 完整 12 源内容基线与 14 天试运行尚未执行，不能宣称来源已晋升 core。
