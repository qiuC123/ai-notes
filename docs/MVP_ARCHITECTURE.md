# Design Handoff — AIHOT 每日多来源候选榜 MVP

Status: Implemented and verified
Owner: Codex implementation task
Phase: MVP（由 \`CODEX_MVP_TASK.md\` 明确授权实施）

## 目标与边界

每天从公开的 AIHOT、官方 RSS、GitHub Release Atom、Hugging Face 模型 API 和 arXiv Atom 收集候选，生成本地 Top 3 候选榜。它只做发现、聚类、排序和证据整理；不会发送飞书或微信、不会读取凭据、不会修改既有自动化或历史 \`work/\`。

## 数据流

\`sources.yaml\` → 受校验 TLS 的 HTTP 拉取和原始快照 → 各来源解析器 → 统一 \`NormalizedItem\` → 确定性聚类 \`Event\` → 可解释评分 → JSON、Markdown 和运行清单。

- 每个来源都有独立解析器；HTTP 200 但内容不是预期 JSON/XML 结构也会作为该来源失败写入运行清单。
- 每个统一条目至少包含任务约定的 ID、标题、摘要、URL、来源、发布时间、分类、实体、原始响应引用和发现时间；额外保留带类型的 \`source_links\`。
- \`source_links\` 的每项为 \`url\`、\`source_id\`、\`source_class\`、\`provenance\`。其中 \`discovery_link\`、\`original_link_from_aggregator\`、\`direct_source\`、\`related_source\` 明确区分发现和原始来源。
- AIHOT 是发现来源；即使多个聚合来源指向同一主题，也仍是 \`discovery_only\`。主证据只能来自允许用于证据的独立来源。

## 确定性聚类与评分

- URL：移除 fragment、常见追踪参数，host 小写，移除非根路径尾斜线。
- 标题：Unicode NFKC、小写、去标点、压缩空白。
- 相似度：中文采用相邻双字 bigram，英文采用 ASCII token，计算 Jaccard。标题相似度达到 **0.48** 且发布时间相距不超过 **72 小时** 才合并；完全相同的规范 URL 必定合并。这两个数值在 \`config/sources.yaml\` 的 \`clustering\` 中配置。
- 打分：七个分项、硬惩罚和阈值在 \`config/scoring.yaml\`；总分始终限制在 0–100，推荐理由由固定模板生成，不调用 LLM。JSON 和 Markdown 均输出每个分项、惩罚值和惩罚原因。

局限：这不是语义模型，无法理解所有同义改写；短标题或混合语言可能误判。上游 RSS、模型热度和 API 返回会随时间变化；同日重放使用保存的完整原始快照，避免把同一次日报的重跑变成新的抓取。

## 失败关闭、快照与人工输入

- 只要至少 3 个来源成功且存在带来源链接的事件，运行才成功。
- 任何失败运行都会覆写该日期的 \`events.json\`、候选 JSON 和 Markdown 为明确的失败空产物，并写 manifest，避免旧候选在失败后继续被消费。
- 成功运行会保存每个成功来源的原始响应。配置未变的同日期后续运行重放完整快照，并在 manifest 的 \`reused_raw_sources\` 记录来源；事件、候选 JSON 和 Markdown 可逐字节复现。manifest 中的开始/结束时间故意随运行更新。
- 最近决策惩罚只读取可选的本地 \`data/decisions/YYYY-MM-DD/decisions.json\`，格式为 \`{"decisions":[{"title":"..."}]}\`，向前查 14 天。没有该人工输入时，不施加该惩罚，也不会把旧候选误判为“已决定”。

## 验证与安全边界

- 标准库 \`unittest\` 的每一行为切片均先写 RED 测试，再写最小生产实现；完整命令与实际结果见 \`docs/MVP_IMPLEMENTATION_REPORT.md\`。
- 当前真实验收使用 11 个公开来源，所有原始 JSON/XML、事件 JSON、Top JSON、Markdown 和 manifest 均已解析检查。
- 该 MVP 只有公开 HTTP GET 和本地文件写入；没有飞书、微信、Kimi、浏览器会话、凭据读取或任何外部写入路径。
