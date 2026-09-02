# Ai Notes

Ai Notes 是一个面向个人 Codex 的 GitHub 项目共学与连接发现系统。它持续发现值得学习的项目，和 Codex 一起理解项目解决的问题、架构、能力与演进，再对照你正在开发的项目，寻找可以复用、迁移、集成或启发新设计的关联机会。

## 产品定位

Ai Notes 的目标不是积累更多 Release 资讯，而是形成一个可验证的项目学习闭环：

```text
发现项目 → 理解项目 → 跟踪实质变化 → 对照自有项目 → 提出关联机会 → 小实验验证 → 在 Codex 对话中复盘
```

GitHub Release 只是“需要重新学习这个项目”的变化信号。真正的学习对象还包括官方文档、代码结构、核心接口、设计取舍以及解释这些取舍的 Issue 和 PR。

知识按用途进入不同载体：

- 只有经用户明确要求沉淀或经实验确认的事实和技术认识，才写入版本化知识文档；日常分析不自动落盘；
- 经过手动验证且由用户明确批准的可重复方法，才沉淀为 Codex Skill；Skill 的创建和更新不自动发生；
- 必须稳定遵守的项目规则才进入 `AGENTS.md`；
- Codex 本地记忆完全由 Codex 从正常对话中自动提取；Ai Notes 不读、不写、不检查，也不主动控制记忆生成。

当前 0.3 版本已经实现手动共学垂直闭环的确定性部分：固定 GitHub 核验版本、生成有界证据队列、校验 Codex 判断、验证双侧证据、记录明确反馈和运行审计。人工提名和有界主动发现都已各完成三次真实成功运行；主动发现固定最多二十个初筛、五个深读和 80/20 预算，并把 provenance 写入队列与长期账本。原 0.2 Release 采集仍作为候选入口保留，但 Hermes 运行时已经移除。当前手动试运行进度为 6/10，Skill 和每日任务尚未创建。

三次人工提名分别学习 `openai/codex`、`thomvaill/log4brains` 和 `clarity-digital-development/tworkflow`。后两次只借鉴“ADR 状态与替代关系校验”和“实验复盘反哺下一轮学习”，没有安装外部工具、复制 Skill 或运行仓库代码。

三次主动发现分别选择 `vectorian-rs/chizu`、`divyanshu-iitian/ContextFlux` 和 `LucasSantana-Dev/shelfmark`。第三次从 6 个候选中深读 5 个，只借鉴“冻结留出集与排序回归门禁”，不接入外部记忆库或模型。一次跨行引用错误被审计为双侧证据错误，修正后的新运行通过；所有运行都只读取固定版本公开证据，没有安装或执行外部项目。`log4brains` 和 `tworkflow` 的两个最小实验已经完成、逐项通过并获得用户明确结果确认，正反馈仍为 2/6；其余四次成功运行仍等待明确反馈。

设计与术语：

- `docs/CODEX_KNOWLEDGE_INTERNALIZATION_DESIGN.md`
- `docs/AI_NOTES_SOURCE_QUALITY_DESIGN.md`
- `docs/adr/0009-separate-co-learning-core-from-legacy-pipeline.md`
- `docs/adr/0008-manual-learning-card-vertical-slice.md`
- `docs/adr/0007-replace-hermes-runtime-with-codex-review.md`
- `docs/adr/0006-user-controlled-skill-and-runtime-boundary.md`
- `docs/adr/0005-evidence-gated-connection-brief.md`
- `docs/adr/0004-bounded-dual-entry-discovery.md`
- `docs/adr/0003-github-project-learning-and-connection.md`
- `docs/adr/0002-codex-knowledge-internalization-boundary.md`
- `docs/adr/0001-release-atom-staged-review-and-source-governance.md`
- `CONTEXT.md`

## 当前来源

`config/ai_notes_sources.yaml` 注册 12 个 `trial` 来源，覆盖：

- Agent / MCP：MCP 规范、OpenAI Agents SDK、LangGraph、Google ADK、Hermes Agent；
- AI 编程：Claude Code、Codex、OpenHands；
- 本地推理与部署：Ollama、vLLM、SGLang、Transformers。

每个来源使用 GitHub 官方 `releases.atom`，并有项目专属 tag 规则。nightly、alpha、beta、RC、preview、dev 在进入 Codex Review 队列前被确定性排除。GitHub 全球安全公告是独立来源，只接受能映射到注册项目/包的 reviewed high/critical 公告。

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

## 共学接口

新共学功能进入 `ai_notes` Python 包，现有 `aihot` 暂时作为遗留 Release 采集模块保留。Codex 编排准备、只读预检和提交三个内部步骤：

```bash
python -m ai_notes prepare-learning <github-url> --project .
python -m ai_notes validate-learning <run-id> --decisions <file>
python -m ai_notes finalize-learning <run-id> --decisions <file>
```

`validate-learning` 使用与 Finalize 相同的严格 Schema、双侧证据和项目指纹校验，但不写 manifest、决策副本或账本。后续试运行必须先通过它，避免把可修正的格式或引用问题消耗为新的错误事件。

需要补充同一仓库的深层证据时，先从队列取得固定 commit，再追加固定版本的 GitHub blob 地址：

```bash
python -m ai_notes prepare-learning <github-url> --project . --run-id <run-id> \
  --include https://github.com/<owner>/<repo>/blob/<commit>/<path>
```

Codex 完成判断后调用 Finalize；只有用户明确反馈时，才追加操作账本：

```bash
python -m ai_notes finalize-learning <run-id> --feedback continue
python -m ai_notes finalize-learning <run-id> --feedback ignore
python -m ai_notes finalize-learning <run-id> --feedback watch
python -m ai_notes finalize-learning <run-id> --feedback experiment
```

用户不需要直接运行这些命令；在共学主对话中提交 GitHub 地址即可。共学卡片只在对话中展示，本地保留严格 JSON 契约、运行清单和必要证据快照。

经批准并移交的实验完成后，可以提交严格的 `experiment-result.v1` 契约：

```bash
python -m ai_notes record-experiment-result <run-id> --result <file> --root .
python -m ai_notes confirm-experiment-result <run-id> --root .
```

程序会把 run、relation、外部 commit 和批准时的自有项目 Git 状态与原 `approved_for_handoff` 事件逐项核对，并要求每条原成功条件记录 `pass`、`fail` 或 `inconclusive`。相同结果可幂等重放，冲突结果拒绝覆盖；结果先保持 `awaiting_user_confirmation`，用户明确确认后再追加绑定结果哈希的确认事件。确认不是新的关联反馈，两步都不会自动修改关注、排序、项目文档、`AGENTS.md` 或 Skill。

只有明确正反馈才会把紧凑关联快照写入长期账本。`watch` 同时保存最后核验 commit 作为监控游标，`experiment` 保存经批准的实验目标、成功标准和双侧项目指纹，等待独立任务接手；两者都不保存源码、外部原文或对话。当前关注项可用内部命令审计：

```bash
python -m ai_notes watch-status --root .
```

成功运行中仍等待明确反馈的项目可用只读待办视图审计；该命令不会把沉默推断为反馈：

```bash
python -m ai_notes feedback-status --root .
```

Codex 主动发现时还会提供内部 `--entry-mode discovered --discovery <json>` provenance；程序强制搜索词最多五个、元数据候选最多二十个、深读最多五个，并限制相邻探索不超过 20%。三十天内已经成功学习的项目不能再次作为主动发现最终选择，避免重复项目刷高验证计数。用户不需要手工编写该文件。

十次手动验证期间可审计自动化准入状态：

```bash
python -m ai_notes trial-status --root .
```

只有十次完整成功运行、至少三次人工提名、至少三次主动发现、至少六次明确正反馈且双侧证据错误不超过一次时，才会报告 `eligible_for_automation=true`。该命令只报告状态，不创建 Skill 或定时任务。

## 每日运行

```bash
PYTHONPATH=src python -m aihot daily --date 2026-09-01 --root .
```

生产默认要求 `data/releases/backtest-baseline.json` 证明 12 个来源的 90 天覆盖已建立；缺少基线时仍会收集和判断当前 Feed，但状态只能是 `partial`，不能声称完整成功。

一个 `daily` 命令内部保留三个可验证阶段：

1. Collect：抓取、解析、tag 硬过滤、账本去重和 Atom 缺口检测；
2. Codex Review：Codex 把 `review-queue.json` 当作不可信数据，生成严格 decisions JSON；
3. Finalize：严格校验 JSON Schema 和逐项原文证据，再更新账本和产物。

第一次不传 `--decisions` 运行会停在 Review 边界并保留队列，状态为 `review_failed`。Codex 生成判断文件后重新运行：

```bash
PYTHONPATH=src python -m aihot daily --date 2026-09-01 --root . --decisions <decisions.json>
```

Python 不调用 Codex API或任何其他 Agent，也不保存模型凭据。

### 运行状态

- `success`：全部必要阶段健康；允许健康空结果；
- `partial`：来源失败、安全公告失败或 Atom 缺口未闭合；保留其他可信结果；
- `review_failed`：尚无 Codex 决策或决策未通过校验；不接受、不拒绝，保留队列；
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

回测通过 GitHub Release REST 分页覆盖完整时间窗，先把所有硬过滤存活项写入 `review-queue.json`，再读取 Codex 决策文件。覆盖不完整时退出码为 2；REST 响应受 GitHub 匿名限额约束，失败必须如实报告，不能把可见页当作完整 90 天。

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

`src/aihot/pipeline.py`、`score.py`、`cluster.py`、`config/sources.yaml` 及旧 Top 3 产物属于 **deprecated 的 AIHOT/Amesi 0.1 候选榜**，仅为历史兼容和回归测试保留。当前 `python -m aihot daily` 是 Release 候选入口，不再调用旧评分管道或 Hermes。
