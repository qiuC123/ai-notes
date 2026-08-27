# Codex 实施任务：AIHOT 每日多来源候选榜 MVP

## 用户目标

把当前“AIHOT 单一来源周报”向可持续的 Amesi 每日内容生产上游迈出第一步：每天从多个公开来源收集 AI 事件，统一结构、去重聚类、可解释评分，输出 Top 3 候选及证据。当前只生成本地候选榜，不修改现有飞书自动化，不发送任何消息，也不接微信公众号。

## 当前仓库

仓库根目录：`D:\devlop\Ai定时任务\Ai 热点`

根级 Git 已初始化。历史 `.agents/`、`work/`、`outputs/`、`data/` 已忽略。现有 `docs/` 是背景资料，不要改写或删除。

Python：3.11。可用依赖：`httpx 0.28.1`、`PyYAML 6.0.3`。没有 pytest；测试必须使用标准库 `unittest`。

## 必须遵循 TDD

对每个纵向行为切片执行：

1. 先写一个最小失败测试；
2. 运行它并确认因功能缺失而失败；
3. 写最小实现；
4. 运行并通过；
5. 再继续下一条行为。

在 `docs/MVP_IMPLEMENTATION_REPORT.md` 记录真实执行过的 RED/GREEN 命令与失败/成功摘要。不得先写完生产代码再补测试。

## 已核实的公开来源

### AIHOT（发现层，不作为唯一出版证据）

OpenAPI：`https://aihot.virxact.com/openapi-v1.json`

端点：

- `https://aihot.virxact.com/api/v1/items?mode=selected&window=24h&limit=50`
  - 顶层：`schemaVersion`, `query`, `items`, `page`
  - item：`id`, `title`, `originalTitle`, `summary`, `source`, `links`, `publishedAt`, `discoveredAt`, `category`, `score`, `selected`, `reason`, `attribution`
- `https://aihot.virxact.com/api/v1/hot-topics`
  - 顶层：`schemaVersion`, `count`, `items`
  - item：`rank`, `id`, `title`, `source`, `links`, `sourceCount`, `signalCount`, `sourceNames`, `latestAt`

### 官方来源

- OpenAI News RSS：`https://openai.com/news/rss.xml`
- Hugging Face Blog RSS：`https://huggingface.co/blog/feed.xml`

### 开源生态

GitHub REST API 在当前匿名共享出口可能返回 403；必须优先使用公开 Atom Feed：

- `https://github.com/NousResearch/hermes-agent/releases.atom`
- `https://github.com/modelcontextprotocol/servers/releases.atom`
- `https://github.com/openai/openai-python/releases.atom`
- `https://github.com/huggingface/transformers/releases.atom`

### Hugging Face 模型信号

- `https://huggingface.co/api/models?sort=trendingScore&limit=30`
- 返回 JSON 数组，常见字段：`id`, `modelId`, `likes`, `trendingScore`, `downloads`, `tags`, `pipeline_tag`, `library_name`, `createdAt`

### 研究来源

- `https://export.arxiv.org/api/query?search_query=cat:cs.AI&start=0&max_results=30&sortBy=submittedDate&sortOrder=descending`
- `https://export.arxiv.org/api/query?search_query=cat:cs.CL&start=0&max_results=30&sortBy=submittedDate&sortOrder=descending`

所有请求使用明确 User-Agent、30 秒超时、最多 3 次有界重试和指数退避。单个来源失败必须进入 run manifest，不得伪装成功；只要至少 3 个来源成功且存在候选，整次运行可以继续成功。

## 目录与文件

创建：

```text
README.md
pyproject.toml
config/sources.yaml
config/scoring.yaml
src/aihot/__init__.py
src/aihot/__main__.py
src/aihot/models.py
src/aihot/http.py
src/aihot/collectors/
src/aihot/normalize.py
src/aihot/cluster.py
src/aihot/score.py
src/aihot/report.py
src/aihot/pipeline.py
tests/
tests/fixtures/
docs/MVP_ARCHITECTURE.md
docs/MVP_IMPLEMENTATION_REPORT.md
```

可以根据清晰职责增加小文件，但不要创建框架式空壳。

## 统一事件结构

每个归一化 item 至少包含：

```json
{
  "item_id": "稳定哈希",
  "title": "",
  "summary": "",
  "url": "",
  "source_id": "",
  "source_class": "aggregator|primary_official|open_source|model_ecosystem|research",
  "owner": "",
  "published_at": "UTC ISO-8601 或 null",
  "discovered_at": "UTC ISO-8601",
  "categories": [],
  "entities": [],
  "evidence_allowed": true,
  "raw_ref": "相对路径"
}
```

事件聚类结果至少包含：

```json
{
  "event_id": "稳定哈希",
  "canonical_title": "",
  "items": [],
  "source_ids": [],
  "source_classes": [],
  "primary_source_url": null,
  "evidence_status": "primary|multi_source|discovery_only|insufficient"
}
```

优先选择 `primary_official`、`open_source` 或 `research` 作为主证据；AIHOT 链接只能作为发现来源，除非它的 item 中包含可解析的原始来源链接，原始链接应保留为事件来源但仍明确 provenance。

## 去重和聚类

MVP 使用确定性算法：

1. URL 规范化：去 fragment、常见跟踪参数、统一 host 大小写、处理尾部斜线；
2. 标题规范化：Unicode NFKC、小写、去标点和多余空白；
3. 中文字符 bigram + ASCII token Jaccard；
4. 相同 canonical URL 必须合并；
5. 标题相似度达到配置阈值且发布时间在允许窗口内时合并；
6. 算法、阈值和局限写进文档。

不能只按标题完全一致去重。

## 可解释评分

总分 0–100，必须输出每个分项和惩罚原因。配置放在 `config/scoring.yaml`。

建议分项：

- audience_value：25
- evidence：20
- novelty：15
- timeliness：15
- amesi_fit：10
- explainability：10
- production_cost_fit：5

硬惩罚：

- discovery_only：-20
- 与最近 14 天 decision 相似：-20
- rumor/传闻/据称等弱证据词：-15
- 纯融资、股价、名人争议且无工作流价值：-15

最终分数必须 clamp 到 0–100。每个候选写一段确定性生成的中文推荐理由，不调用 LLM。

## 运行产物

命令接口至少支持：

```bash
python -m aihot daily --date 2026-08-27 --top 3
```

如未安装 package，README 给出 Windows Git Bash 下可执行方式，例如设置 `PYTHONPATH=src`。也可提供 `python -m pip install -e .`，但测试不能要求联网安装。

输出：

```text
data/raw/YYYY-MM-DD/<source-id>.<json|xml>
data/events/YYYY-MM-DD/events.json
outputs/YYYY-MM-DD/top-candidates.json
outputs/YYYY-MM-DD/top-candidates.md
outputs/YYYY-MM-DD/run-manifest.json
```

`top-candidates.md` 每条至少包含：

- 排名、标题、总分；
- 分项分数与惩罚；
- 为什么适合 Amesi；
- 证据状态；
- 主证据链接；
- 所有来源链接；
- 尚待核验事项。

`run-manifest.json` 至少包含：run_id、开始/结束时间、配置哈希、成功来源、失败来源及真实错误、原始 item 数、归一化数、事件数、Top N、退出状态。

## 测试要求

使用本地 fixtures，不让单元测试依赖公网。至少覆盖：

1. AIHOT selected JSON 解析；
2. AIHOT hot-topics JSON 解析；
3. RSS 解析；
4. Atom/GitHub Release 解析；
5. Hugging Face 模型 JSON 解析；
6. arXiv Atom 解析；
7. URL 规范化；
8. 中英文标题近似聚类；
9. 不相关标题不合并；
10. discovery-only 惩罚；
11. primary evidence 得分高于 aggregator-only；
12. 单来源失败不阻断其他来源；
13. 少于 3 个成功来源时失败关闭；
14. 端到端 fixture 运行生成全部 3 个输出文件；
15. 相同日期重复运行不会追加重复事件，输出稳定可覆盖。

验收命令：

```bash
python -m unittest discover -s tests -v
```

## 最终真实联网验收

单元测试全绿后，实际执行当天运行。若某个外部源失败，保留失败记录并判断是否满足至少 3 个成功源。不得修改测试来适配网络故障。

验证：

- JSON 可解析；
- Top 3 数量与声明一致；
- 每个候选有来源链接和评分分解；
- 原始快照存在；
- 重复运行幂等；
- 无任何飞书、微信或其他外部写入。

## 边界

- 不修改 `C:\Users\Administrator\.codex\automations\ai\automation.toml`；
- 不读取或写入凭据；
- 不发送消息；
- 不调用 Kimi；
- 不修改历史 `work/`；
- 不提交 Git；由 Hermes 检查后提交；
- 只做候选发现与排序，不生成公众号正文和封面。

完成后在 `docs/MVP_IMPLEMENTATION_REPORT.md` 写清：实现文件、RED/GREEN 证据、测试数量、真实联网运行结果、失败来源和已知局限。不要只在终端自述。
