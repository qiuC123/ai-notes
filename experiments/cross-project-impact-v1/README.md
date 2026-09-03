# 跨项目影响基线实验 v1

状态：五个案例已冻结，独立盲测与确定性评分已完成。

本实验验证 Codex 加本地搜索在 `official-campus-radar` 与 canonical `wechat-oa` 这组真实耦合项目上，能否找全关键跨项目影响。方法只借鉴学习运行 `20260903T055625Z-2556d12b`（关系 `rel-8d3b4f2a7c91`）提出的“先用冻结变更集测漏报”原则；没有克隆、安装、构建或执行 `kesari/ecommerce-poc`。

## 冻结边界

- 标准答案：`suite.json`，包含五个案例、固定 Git HEAD、双侧证据和必要测试。
- 盲测输入：`blind-input.json`，只包含变更描述、只读目录、固定 HEAD 和响应字段约定；不包含受影响项目答案、关键程度、证据或测试答案。其 SHA-256 由 `prepare-impact-blind` 输出并由评分器复算。
- 样本只读：`E:\devlop\official-campus-radar` 与 `E:\devlop\qiuC-tools\CLI\wechat-oa`。
- `official-campus-radar` 冻结于 `4ca4fa4a478e82af446fbd16659d39d92867b52f`；冻结时存在一个与实验无关的未跟踪截图，实验不得触碰。
- canonical `wechat-oa` 冻结于 monorepo HEAD `767827c2f10bd8cd80875275beec5e7cc928dabd`，不是历史 standalone checkout。
- 冻结之后样本工作区可能继续变化；盲测必须用只读 `git show <git_head>:<path>` 分析固定提交，忽略工作区漂移，不切换 checkout。
- 案例 01–04 是真实运行时依赖；案例 05 是语义相似但无跨项目影响的方向性对照。

## 独立盲测流程

有效基线已经由独立 Codex 上下文产生。复现或创建新版本时必须继续遵守：

1. 使用已冻结的 `blind-input.json`。只有套件发生新版本变更时，才由知晓标准答案的任务重新生成输入：

   ```powershell
   python -m ai_notes prepare-impact-blind `
     --suite experiments/cross-project-impact-v1/suite.json `
     --output <Ai-Notes-仓库之外的临时目录>/blind-input.json
   ```

2. 只把 `blind-input.json` 和两个只读样本目录交给独立任务。该输入自带 `impact-baseline.v1` 的响应字段约定。独立任务不得浏览 Ai Notes 仓库的其他内容、`suite.json`、本文件或先前分析；不得修改样本项目。
3. 独立任务核对两个 HEAD，使用 Codex 与本地文本搜索完成五案分析，输出 `impact-baseline.v1`。每案必须评价另一个项目为 `direct_dependency`、`semantic_similarity` 或 `unrelated`，并给出证据路径、行号或 symbol 以及必要测试。
4. 独立任务完成并封存输出后，才把结果交回知晓标准答案的任务评分：

   ```powershell
   python -m ai_notes score-impact-baseline `
     --suite experiments/cross-project-impact-v1/suite.json `
     --baseline <baseline.json>
   ```

`blind_protocol` 必须记录独立任务 ID、盲测输入 SHA-256，并明确在完成前没有访问标准答案或 Ai Notes 仓库。这是可审计声明，不是密码学隔离；真正隔离仍依赖独立任务权限和交接纪律。

## 指标与决策门

- 项目召回：正确找到的真实受影响项目 / 全部真实受影响项目。
- 关键漏报：`critical` 的真实项目影响没有报为 `direct_dependency`。
- 精确率：正确的直接依赖项目影响 / 全部报出的直接依赖项目影响；案例 05 专门捕捉语义误报。
- 证据完整性：标准答案要求的双侧证据中，被路径加行号重叠或准确 symbol 命中的比例。每个真阳性同时命中变更侧和受影响侧时，证据等级为 `two_sided`；只命中一侧为 `one_sided`；否则为 `none`。
- 必要测试完整性作为附加指标，按项目、路径和测试 selector 精确匹配。

只有至少两个不同的关键项目影响漏报，才算“重复关键漏报”。即使出现重复漏报，结果也只允许进入依赖图根因复核，不能直接批准建设。没有重复关键漏报时，评分器固定输出 `do_not_add_dependency_graph_to_mvp`；MVP 继续使用 Codex 加本地搜索，不增加知识图谱、索引、外部服务或本地模型。

## 基线结果

独立 baseline 位于 Ai Notes 仓库之外，SHA-256 为 `0cfd0fc23b4ff6448ae809278dc90c33ea99c859a2eb6295c792a62221b25a08`；契约和冻结 HEAD 校验通过。确定性结果保存在 `score-result.json`：

- 项目召回 `1.0`，精确率 `1.0`；
- `0` 个 critical miss，没有重复关键漏报；
- 证据完整性 `0.5`，四个真实依赖案例均为评分器推导的 `one_sided`；
- 必要测试完整性 `0.0`，因为 baseline 提出了新的测试名，没有精确命中冻结标准中的既有 selector；
- 最终门禁：`do_not_add_dependency_graph_to_mvp`。

结论不是“分析完美”，而是“本样本没有证明依赖图必要”。MVP 继续使用 Codex 加固定提交上的本地搜索；下一轮如果要提升质量，先改进双侧证据和测试定位提示，不增加知识图谱、索引、外部服务或本地模型。
