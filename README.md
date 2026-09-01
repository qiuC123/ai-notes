# Ai Notes

Ai Notes 是一个本地、高质量、可审计的 AI 信息获取系统。当前阶段只监控经过批准的开源 AI 项目官方发布，执行确定性过滤、Hermes 实质变化判断和确定性证据校验；它不做自媒体选题、知识融合、长期记忆同步或自动发布。

设计与术语：

- `docs/AI_NOTES_SOURCE_QUALITY_DESIGN.md`
- `docs/adr/0001-release-atom-staged-review-and-source-governance.md`
- `CONTEXT.md`

## 当前来源

`config/ai_notes_sources.yaml` 注册 12 个 `trial` 来源，覆盖：

- Agent / MCP：MCP 规范、OpenAI Agents SDK、LangGraph、Google ADK、Hermes Agent；
- AI 编程：Claude Code、Codex、OpenHands；
- 本地推理与部署：Ollama、vLLM、SGLang、Transformers。

每个来源使用 GitHub 官方 `releases.atom`，并有项目专属 tag 规则。nightly、alpha、beta、RC、preview、dev 在进入 Hermes 前被确定性排除。GitHub 全球安全公告是独立来源，只接受能映射到注册项目/包的 reviewed high/critical 公告。

## 安装

项目要求 Python **3.11 或更高版本**。不要让定时任务依赖 Windows `py` 启动器的默认版本；创建项目虚拟环境，并在 cron/计划任务中固定该解释器路径：

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
```

本机核验时，`python` 为 3.11.15、`py` 默认为 3.13.12，两者均能满足版本要求；`py -3.11` 不可用，因为 Python Launcher 不识别 uv 管理的 3.11。以 `.venv/Scripts/python.exe` 为准可避免交互终端与计划任务解析到不同 Python。

不安装包时，在 Windows Git Bash 中可使用：

```bash
PYTHONPATH=src python -m aihot --help
```

## 每日运行

```bash
PYTHONPATH=src python -m aihot daily --date 2026-09-01 --root .
```

生产默认要求 `data/releases/backtest-baseline.json` 证明 12 个来源的 90 天覆盖已建立；缺少基线时仍会收集和判断当前 Feed，但状态只能是 `partial`，不能声称完整成功。

一个 `daily` 命令内部保留三个可验证阶段：

1. Collect：抓取、解析、tag 硬过滤、账本去重和 Atom 缺口检测；
2. Hermes Review：使用 `--safe-mode --toolsets __no_tools__` 的零工具 one-shot，按 5 条一批判断实质变化；
3. Finalize：严格校验 JSON Schema 和逐项原文证据，再更新账本和产物。

Hermes 输出中的 Release 内容始终被标记为不可信数据。若零工具 reviewer 无法运行，状态为 `review_failed`，队列保留供重试。

### 运行状态

- `success`：全部必要阶段健康；允许健康空结果；
- `partial`：来源失败、安全公告失败或 Atom 缺口未闭合；保留其他可信结果；
- `review_failed`：Hermes 判断失败；不接受、不拒绝，保留队列；
- `failed`：关键校验或产物写入失败。

CLI 退出码：`0=success`、`2=partial`、`1=review_failed/failed`。

## 每日产物

```text
data/raw/YYYY-MM-DD/<source-id>.xml
data/raw/YYYY-MM-DD/github_advisories.json
data/releases/release-ledger.jsonl
data/releases/source-state.json
outputs/YYYY-MM-DD/review-queue.json
outputs/YYYY-MM-DD/review-decisions.json
outputs/YYYY-MM-DD/accepted-information.json
outputs/YYYY-MM-DD/accepted-information.md
outputs/YYYY-MM-DD/run-manifest.json
```

Release 账本以 `repository@tag` 为键。已完成的 Release 默认只判断一次；`pending_review` 会重试；安全公告在内容哈希变化或撤回时重新判断。

原始快照保留 30 天；账本、判断、指标和最终产物长期保留。同日期 `success` 默认重放已有产物；`partial` 和 `review_failed` 会重试失败阶段，并按 `release_key` 合并而不是覆盖已接受信息。

## 90 天回测

```bash
PYTHONPATH=src python -m aihot backtest --date 2026-09-01 --days 90 --root .
```

回测通过 GitHub Release REST 分页覆盖完整时间窗，对所有硬过滤存活项执行与线上相同的分批 Hermes 判断；覆盖不完整时退出码为 2。REST 响应受 GitHub 匿名限额约束，失败必须如实报告，不能把可见页当作完整 90 天。

产物：

```text
outputs/backtests/YYYY-MM-DD-90d/backtest.json
outputs/backtests/YYYY-MM-DD-90d/backtest.md
```

## 14 天来源评估

完成在线试运行后：

```bash
PYTHONPATH=src python -m aihot evaluate --start-date 2026-09-01 --days 14 --root .
```

产物：

```text
outputs/trials/2026-09-01-14d/source-evaluation.json
outputs/trials/2026-09-01-14d/source-evaluation.md
```

评估区分 `promote_core`、`continue_trial` 和 `demote_watchlist`，依据抓取天数、样本量、接受数、预发布泄漏、证据完整率和最大采集延迟。

## Atom 缺口恢复

GitHub Release Atom 是有界窗口。Ai Notes 保存每个来源的最近成功时间和 Feed 边界：

- 当前 Feed 与账本有已知 `release_key` 重叠：只处理新条目；
- 没有重叠：使用 Release REST 向后补抓；
- REST 无法闭合：写入 `manifest.known_gaps`，状态为 `partial`，禁止健康空结果。

## 测试

单元测试全部离线，不访问公网：

```bash
python -m unittest discover -s tests -v
```

公开网络验收必须另行执行并审计 12 个 Feed、全球安全公告、JSON 产物和 manifest。仓库不会自动创建 cron、读取凭据、外发消息或提交 Git。

## 旧 0.1 管道

`src/aihot/pipeline.py`、`score.py`、`cluster.py`、`config/sources.yaml` 及旧 Top 3 产物属于 **deprecated 的 AIHOT/Amesi 0.1 候选榜**，仅为历史兼容和回归测试保留。当前 `python -m aihot daily` 已切换为 Ai Notes 0.2，不再调用旧评分管道。
