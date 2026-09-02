# Ai Notes 高质量 AI 信息源优化设计

Status: Implemented and smoke-verified — full 12-source content backtest and 14-day trial not started
Date: 2026-08-31
Owner: 用户 / Codex
Domain language: [`CONTEXT.md`](../CONTEXT.md)
Architecture decision: [`ADR 0001`](adr/0001-release-atom-staged-review-and-source-governance.md)

> 2026-09-02 定位更新：本文继续描述已经实现的信息获取子系统，不再代表 Ai Notes 的完整产品边界。完整目标已扩展为 Codex 技术知识发现与内化，见 [`CODEX_KNOWLEDGE_INTERNALIZATION_DESIGN.md`](CODEX_KNOWLEDGE_INTERNALIZATION_DESIGN.md) 和 [`ADR 0002`](adr/0002-codex-knowledge-internalization-boundary.md)。下文“当前阶段不负责知识融合/记忆同步”仅描述 0.2 实现范围。本文中的 Hermes Review 是现有代码事实，目标架构已由 [`ADR 0007`](adr/0007-replace-hermes-runtime-with-codex-review.md) 取代；迁移完成前不得把“已决定移除”误写成“代码已移除”。

## 1. 目标

Ai Notes 当前只负责建立稳定、可核验的高质量 AI 信息获取能力：注册可信来源、发现正式发布、排除噪声、由 Hermes 判断实质变化，并生成带原文证据的结构化产物。

当前阶段不负责：

- 按自媒体用户画像选题；
- 生成公众号文章、封面或视频；
- 把知识与其他项目建立联系；
- 向 Hermes 或 Codex 长期记忆同步知识；
- 自动发布或外发消息。

## 2. 来源模型

### 2.1 来源等级

- **试运行源**：已通过官方身份和接口核验，正在接受 90 天历史回测与 14 天在线试运行。
- **核心源**：完成试运行并满足晋级门槛的稳定主干来源。
- **观察名单**：重要但当前发布机制不符合正式版本规则，或质量、活跃度、重复度尚不足以进入试运行的项目。
- **来源候选**：AIHOT、搜索或社区发现的新项目提名；不得自动成为正式来源。

### 2.2 第一批试运行来源

第一批只覆盖 Agent/自动化/MCP、AI 编程工具、本地推理与部署工具。12 个来源全部处于 `trial`，没有项目提前成为核心源。

| Source ID | 类别 | 官方仓库 | Release Atom | 接受的 tag |
|---|---|---|---|---|
| `mcp_spec` | Agent / MCP | `modelcontextprotocol/modelcontextprotocol`[1] | `https://github.com/modelcontextprotocol/modelcontextprotocol/releases.atom` | `YYYY-MM-DD` 或 `YYYY-MM-DD-final`，排除 `-RC` |
| `openai_agents_python` | Agent | `openai/openai-agents-python`[2] | `https://github.com/openai/openai-agents-python/releases.atom` | `vX.Y.Z` |
| `langgraph` | Agent | `langchain-ai/langgraph`[3] | `https://github.com/langchain-ai/langgraph/releases.atom` | 主包 `X.Y.Z`；排除 `sdk==`、`checkpoint`、`cli==` |
| `google_adk_python` | Agent | `google/adk-python`[4] | `https://github.com/google/adk-python/releases.atom` | `vX.Y.Z` |
| `nous_hermes_agent` | Agent | `NousResearch/hermes-agent`[5] | `https://github.com/NousResearch/hermes-agent/releases.atom` | `vYYYY.M.D` |
| `anthropic_claude_code` | AI 编程 | `anthropics/claude-code`[6] | `https://github.com/anthropics/claude-code/releases.atom` | `vX.Y.Z` |
| `openai_codex` | AI 编程 | `openai/codex`[7] | `https://github.com/openai/codex/releases.atom` | `rust-vX.Y.Z`；排除 alpha |
| `openhands` | AI 编程 | `OpenHands/OpenHands`[8] | `https://github.com/OpenHands/OpenHands/releases.atom` | `vX.Y.Z` |
| `ollama` | 本地推理 | `ollama/ollama`[9] | `https://github.com/ollama/ollama/releases.atom` | `vX.Y.Z` |
| `vllm` | 推理部署 | `vllm-project/vllm`[10] | `https://github.com/vllm-project/vllm/releases.atom` | `vX.Y.Z` |
| `sglang` | 推理部署 | `sgl-project/sglang`[11] | `https://github.com/sgl-project/sglang/releases.atom` | `vX.Y.Z` |
| `transformers` | 模型工具链 | `huggingface/transformers`[12] | `https://github.com/huggingface/transformers/releases.atom` | `vX.Y.Z` |

### 2.3 第二批候补与观察名单

第二批候补试运行源分为四组：

- PydanticAI、Browser Use、MCP Servers。[13][14][15]
- Semantic Kernel、CrewAI、Gemini CLI。[16][24][17]
- OpenCode、Goose、Cline。[18][19][20]
- Qwen Code、LocalAI、ONNX Runtime GenAI。[21][22][23]

观察名单：AutoGen、MCP Python/TypeScript SDK、Strands Harness、Agno、Aider、Continue、Roo Code、Plandex、llama.cpp、Text Generation Inference、MLX-LM、TensorRT-LLM。

llama.cpp、TensorRT-LLM 等主要依赖连续构建版或 RC 的项目不放宽规则；只有找到能够表达实质变化的专属官方公告来源后才进入试运行。

## 3. 日常采集接口

### 3.1 Release Atom 是每日主入口

每个仓库的 `releases.atom` 是 GitHub 官方版本发布订阅，不是搜索引擎。它提供发布标题、内容、时间、链接和作者，但不提供 GitHub REST API 中明确的 `prerelease` 布尔字段。[25]

每日 Release 发现不依赖 GitHub REST API 或登录凭据。每日运行允许两类 GitHub REST 调用：

- 每天最多一次读取全球安全公告；
- Atom 连续性出现已知或疑似缺口时，按需补抓 Release。

REST API 还用于：

- 90 天历史回测；
- 诊断 Atom 与 GitHub 发布元数据不一致；
- 调查项目发布策略。

全球安全公告失败、Release 缺口补抓失败或匿名配额耗尽时，不把整次运行伪装成完整成功：保留其他可信结果，状态为 `partial`，并在 manifest 中记录缺失范围。除此之外，Release 日常发现不以 REST 成功作为前置条件。

### 3.2 Release tag 必须从稳定字段提取

过滤应使用 Atom `entry.id` 或 Release URL 中的真实 tag，不依赖可能省略前缀的显示标题。每个来源显式配置：

- `repository`；
- `feed_url`；
- `tier`；
- `include_tag_pattern`；
- `exclude_tag_pattern`；
- `policy_version`；
- 安全公告包名/仓库映射。

`mcp_spec` 的接受规则必须覆盖 `YYYY-MM-DD-final`，不能只接受裸日期；fixtures 至少包含一个 `-final` 正例和一个 `-RC` 反例。

### 3.3 Atom 连续性与缺口恢复

GitHub Release Atom 是有界窗口，不能假设它永久保留历史发布。Collect 为每个来源记录本次 Feed 的 entry 数、新旧边界、抓取时间和与 Release 账本的重叠情况。

首次启动通过历史回测建立基线。后续运行按以下规则验证连续性：

1. 若当前 Feed 至少包含一个账本已知的 `release_key`，则处理该重叠点之前的全部新 entry；
2. 若有界 Feed 中找不到任何已知 `release_key`，或 Feed 最旧条目仍晚于该来源最后一次成功抓取时间，则标记疑似缺口；
3. 对疑似缺口使用 GitHub Release REST 分页向后补抓，直到找到已知 `release_key` 或覆盖最后成功抓取时间；
4. 补抓成功后，缺失 Release 按正常硬过滤、Hermes 判断和 Finalize 流程处理；
5. REST 不可用、额度耗尽或仍无法证明连续性时，在 `run-manifest.json` 的 `known_gaps` 中记录来源、时间边界和原因，运行状态为 `partial`；不得生成健康空结果。

tag 不一定连续递增，多包仓库也可能并行发布，因此缺口判断以 `release_key` 重叠和发布时间边界为准，不只比较版本号大小。

## 4. 发布硬过滤

对所有来源统一排除：

- nightly；
- alpha；
- beta；
- RC；
- preview；
- dev；
- 草稿或无法确认正式身份的发布；
- 项目专属排除 tag；
- 缺少官方链接、发布时间或发布说明的条目。

通过 tag 过滤不代表属于高质量信息。正式补丁版仍需接受 Hermes 实质变化判断。

## 5. 实质变化判断

### 5.1 接受范围

实质变化是会改变项目能力、使用方式、兼容范围或实际风险的变化，包括：

- 重要功能或能力；
- 新模型、平台、工具协议或硬件支持；
- 关键 API、默认行为或部署方式变化；
- 性能或资源要求发生足以影响实际选择的变化；
- 弃用、移除或不兼容变化；
- 高影响的安全、权限、数据正确性或稳定性修复。

默认拒绝：

- 文档错别字和链接更新；
- 常规依赖升级；
- CI、测试和发布维护；
- 不改变外部行为的内部重构；
- 只有版本号或模板文本的空洞发布；
- 对实际使用影响很小的普通修复。

### 5.2 Hermes 判断输出

Hermes 对每个通过硬过滤的 Release 输出严格结构：

```json
{
  "release_key": "openai/codex@rust-v0.151.0",
  "decision": "accept",
  "change_types": ["feature", "security"],
  "substantive_changes": [
    {
      "summary_zh": "扩展可在工具结果到达模型前检查或替换结果。",
      "evidence": "Extensions can now inspect or replace MCP tool results before they reach the model."
    }
  ],
  "decision_reason": "改变了扩展处理 MCP 工具结果的能力边界。",
  "pending_verification": []
}
```

每项中文概括必须有官方发布说明中的原文证据。Hermes 不得仅凭项目声量、Star、版本号或模型常识接受条目。

## 6. 一个 daily 命令、三个可验证阶段

对外仍使用一个 `daily` 命令，但内部保留三个阶段及中间产物。

### 6.1 Collect — 确定性采集

职责：

- 拉取 12 个 Atom 和独立安全公告来源；
- 保存原始响应；
- 解析仓库、tag、版本说明和来源字段；
- 执行 tag 硬过滤；
- 查询 Release 账本；
- 验证 Atom 与账本是否存在连续重叠；
- 仅在疑似缺口时使用 REST 补抓，并记录未闭合缺口；
- 输出 `review-queue.json`。

模型不得参与此阶段的来源身份、tag、时间和 URL 判断。

### 6.2 Review — Hermes 判断

职责：

- 只读取通过硬过滤且未处理的队列项；
- 判断是否属于实质变化；
- 输出变化类型、中文概括、判断理由、待核验点和逐项原文证据；
- 写入 `review-decisions.json`。

Hermes 不直接修改来源注册表、Release 账本或最终产物。

### 6.3 Finalize — 确定性校验

职责：

- 校验判断 JSON Schema；
- 确认 `release_key` 属于待处理队列；
- 确认证据原文真实存在于规范化后的官方发布说明；
- 拒绝预发布误入、未知字段和非法状态；
- 原子更新 Release 账本；
- 生成每日 JSON、Markdown 和运行清单。

证据匹配采用同一规范化函数处理官方 Release HTML 和 Hermes 返回的 `evidence`：HTML 实体解码 → 解析可见文本并移除标记 → Unicode NFKC → 统一换行 → 将连续 Unicode 空白折叠为一个空格 → 去除首尾空白。规范化后的非空 `evidence` 必须与规范化发布说明进行区分大小写的精确子串匹配。

任何校验失败都不能被改写为接受或健康空结果。

## 7. Release 账本与幂等

Release 唯一键：

```text
repository + "@" + release_tag
```

安全公告使用 `GHSA ID` 作为唯一键。

账本至少记录：

- 首次发现时间；
- 来源与官方链接；
- tag 和发布时间；
- 硬过滤结果及规则版本；
- Hermes 判断状态及判断版本；
- 接受、拒绝或待重试原因；
- 最后处理时间；
- 原始内容哈希和最近一次 Feed 重叠边界。

同一 Release 默认只判断一次。只有过滤/判断规则版本变化或人工明确要求时才能重评。

安全公告不沿用 Release 的“默认只判断一次”规则。GHSA 账本除 `GHSA ID` 外还保存 `updated_at` 和规范化内容哈希；严重度、受影响包、修复版本、撤回状态或正文变化时必须重新判断。已接受公告后来被撤回时，账本标记为 withdrawn，并生成可审计的撤回记录。

## 8. 运行状态

- `success`：全部必要阶段健康完成；允许零条合格信息。
- `partial`：至少一个来源失败、安全公告失败或存在未闭合 Atom 缺口；保留健康来源结果，但不得声称完整覆盖或健康空结果。
- `review_failed`：Hermes 判断失败；待判断项保留并重试，不得自动拒绝或接受。
- `failed`：无法形成可信运行清单、全部必要来源不可用、校验失败或关键产物无法写入。

只有 `success` 且接受数量为 0 才能称为健康空结果。

## 9. 独立安全公告来源

每天最多一次读取 GitHub 全球安全公告 API。该 API 能提供 GHSA/CVE、严重度、受影响包、首个修复版本和源码仓库。[26]

首期只接受：

- `type = reviewed`；
- `severity` 为 `high` 或 `critical`；
- `withdrawn_at` 为空；
- `source_code_location` 或受影响包能够映射到来源注册表。

Release Feed 不宣称覆盖全部安全事件。安全来源失败时运行状态至少为 `partial`。

全球安全公告是每日运行中唯一固定执行的 GitHub REST 调用。Release REST 只在回测、诊断或 Atom 缺口恢复时执行。安全公告每次读取后按 `updated_at` 和内容哈希检测修订；任何影响判断结论的变化都进入 Review 和 Finalize，不受既有判断状态阻止。

## 10. 90 天历史回测

对 90 天内所有通过硬过滤的 Release 执行与线上相同的 Hermes 判断，不做随机抽样。

已完成的元数据初查观察到 215 条通过 tag 硬过滤的正式发布，约 2.39 条/天；Codex 因查询上限仍是下限。正式实施的回测必须分页到完整 90 天，并缓存 REST 响应、遵守速率限制。

内容回测输出：

- 每个来源的原始发布数；
- 硬过滤通过/拒绝数；
- Hermes 接受/拒绝数；
- 拒绝原因分布；
- 预发布误入数；
- 字段与证据完整率；
- 重复和新增证据情况。

## 11. 14 天在线试运行与晋级

### 11.1 晋升核心源

来源同时满足：

1. 14 天至少 13 天成功抓取和解析；
2. 预发布误入为 0；
3. 合格信息的项目、版本、时间、官方链接、发布说明与证据字段完整率为 100%；
4. 采集延迟不超过 24 小时；
5. 若观察到至少 3 条通过硬过滤的 Release，至少 1 条被 Hermes 接受为实质变化；
6. 低频战略来源样本不足 3 条时继续试运行，不因无发布自动淘汰。

### 11.2 降级到观察名单

- 至少 3 条正式发布全部被 Hermes 拒绝；
- 反复出现抓取、解析或 tag 识别失败；
- 发布说明长期为空或只有模板文本；
- 长期只能提供重复信息，不能增加原始证据。

### 11.3 来源候选治理

AIHOT、搜索和社区只能提出来源候选。候选必须完成：

1. 官方身份与仓库核验；
2. 发布机制与 tag 规则调查；
3. 90 天历史回测；
4. 14 天在线试运行；
5. 书面晋级结论。

不得根据 Star、Trending 或一次热门事件自动加入核心源。

## 12. 产物与保留

建议产物：

```text
data/raw/YYYY-MM-DD/<source-id>.xml
data/releases/release-ledger.jsonl
outputs/YYYY-MM-DD/review-queue.json
outputs/YYYY-MM-DD/review-decisions.json
outputs/YYYY-MM-DD/accepted-information.json
outputs/YYYY-MM-DD/accepted-information.md
outputs/YYYY-MM-DD/run-manifest.json
outputs/trials/<trial-id>/source-evaluation.json
outputs/trials/<trial-id>/source-evaluation.md
```

保留策略：

- 原始 Atom 快照保留 30 天；
- Release 账本长期保留；
- Hermes 接受、拒绝与待重试决定长期保留；
- 来源评估指标和每日最终产物长期保留。

第一批 12 个 Feed 当前约 3.9 MiB/天，30 天约 117 MiB，因此不永久保留逐日原始 XML。

## 13. 现有实现需要改变的行为

实施阶段应明确替换或隔离以下现状：

- `parse_atom()` 目前不提取真实 release tag，也不识别预发布状态；
- GitHub Release 来源目前没有项目专属 tag 规则；
- 现有实现没有验证 Atom 窗口与账本连续性，也没有 REST 缺口补抓和 `known_gaps`；
- 现有 0–100 Amesi 选题评分不用于 Ai Notes 高质量信息判断；
- 现有管道没有跨日期 Release 账本；
- 现有“无候选即失败”必须改为区分健康空结果、部分结果和判断失败；
- AIHOT、官方新闻、Hugging Face 模型和 arXiv 不进入本次 12 源试运行输出；AIHOT 只保留为未来来源发现层。

## 14. 实施验收条件

正式实施必须以测试和真实产物证明：

- 12 个 Atom 均能获取并解析；
- 所有项目专属 tag 规则有 fixtures；
- `mcp_spec` fixtures 覆盖 `YYYY-MM-DD-final` 正例和 `YYYY-MM-DD-RC` 反例；
- nightly、alpha、beta、RC 不会进入判断队列；
- 同一仓库和 tag 不会跨天重复判断；
- Atom 与账本有重叠时只处理新 entry；失去重叠时能 REST 补抓，补抓失败则 manifest 记录 `known_gaps` 且状态为 `partial`；
- Hermes 每项变化均附可在发布说明中匹配的原文证据；
- 证据规范化覆盖 HTML 实体、可见文本、Unicode NFKC、换行和空白折叠；
- 来源失败产生 `partial`，不会抹掉健康结果；
- 全球安全公告是唯一固定每日 REST 调用，其失败产生 `partial`；
- GHSA 内容变化会触发重评，撤回公告会更新账本并产生撤回记录；
- Hermes 失败产生 `review_failed`，队列可重试；
- 全部阶段健康且无合格信息时产生成功空结果；
- 90 天完整内容回测完成；
- 14 天评估报告能给出晋升、继续试运行或降级结论；
- 不读取项目凭据、不自动外发、不自动把来源候选晋升核心源。

## 15. 当前运行边界

用户已授权并完成代码实施。当前仍未授权或执行：创建定时任务、调用外部消息平台、自动晋升来源、开始 14 天在线试运行。完整 12 源 90 天 Hermes 内容回测必须在确认 API 配额与模型成本后单独执行；单源或确定性烟雾测试不能冒充完整基线。

## Sources

[1] https://github.com/modelcontextprotocol/modelcontextprotocol
[2] https://github.com/openai/openai-agents-python
[3] https://github.com/langchain-ai/langgraph
[4] https://github.com/google/adk-python
[5] https://github.com/NousResearch/hermes-agent
[6] https://github.com/anthropics/claude-code
[7] https://github.com/openai/codex
[8] https://github.com/OpenHands/OpenHands
[9] https://github.com/ollama/ollama
[10] https://github.com/vllm-project/vllm
[11] https://github.com/sgl-project/sglang
[12] https://github.com/huggingface/transformers
[13] https://github.com/pydantic/pydantic-ai
[14] https://github.com/browser-use/browser-use
[15] https://github.com/modelcontextprotocol/servers
[16] https://github.com/microsoft/semantic-kernel
[17] https://github.com/google-gemini/gemini-cli
[18] https://github.com/anomalyco/opencode
[19] https://github.com/aaif-goose/goose
[20] https://github.com/cline/cline
[21] https://github.com/QwenLM/qwen-code
[22] https://github.com/mudler/LocalAI
[23] https://github.com/microsoft/onnxruntime-genai
[24] https://github.com/crewAIInc/crewAI
[25] https://docs.github.com/en/rest/releases/releases
[26] https://docs.github.com/en/rest/security-advisories/global-advisories
