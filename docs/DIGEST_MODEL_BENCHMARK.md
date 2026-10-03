# GLM / Jev 价值筛选对照实验 v1

本轮结果支持先修订“信息增量”的证据要求、未知答案的分流、栏目边界和普通更新的评价对象约束，再判断是否替换模型。当前没有人工金标，不能据此宣布任一模型更准确。按预先冻结的置信度规则模拟，30 卡全部从 Jev 升级到 GLM，没有减少 GLM 调用。

实验日期：2026-10-03。模型为 `glm-5.3-flash`（`reasoning_effort=low`）与 `jev-1.13.0`。实验使用独立目录 `work/digest-model-compare-20261003`，没有写入生产候选审核或榜单。

## 实验范围与冻结条件

先运行 6 张 pilot 卡，再保持这 6 张卡、分组和评分协议不变，扩展到 30 卡。最终为 12 张 dev、18 张 holdout；包含 25 张项目介绍、4 张阅读材料、1 张有明确事件的更新卡。样本是有目的的选择，不代表生产候选的真实比例。

两边使用相同卡片原文，逐卡 `state_hash` 相同。模型输入只含标题、规范 URL、`kind`、`event` 和 `evidence_context`，不包含旧评分、旧推荐、参考栏目或 dev/holdout 分组。旧 Codex 判断不作为金标。

冻结文件中的标识为：

| 项目 | 值 |
|---|---|
| 协议版本 | `digest-model-benchmark.v1` |
| 协议 SHA-256 | `d163cbbcd3e10a809a7547dbbe7c44a09cd5a5e844a731d88fdf8de9f5f616db` |
| 最终 30 卡数据集 SHA-256 | `0b29a8e44d1a303ba54405c23d5cc9ce0dd70954116e9b441ff4443867c8f897` |
| 数据准备时间 | `2026-10-03T23:16:49.039225+08:00` |

每卡原文总预算为 20,000 字符；超限材料按确定性规则裁剪，完整原文与裁剪说明保存在实验目录。材料仅证明文档写了什么，本轮没有安装、运行、游戏体验或实际收益验证。

每张卡回答 11 个问题：栏目、材料预检、价值/增量/证据/易用性/兴趣五维、四个风险标记。五维使用 `0`–`10` 或 `UNKNOWN`。Jev 使用 Choice 保留全部 11 个整数档位，不使用原生 Score。两边的权重、封顶规则与最终分流由同一段程序计算。

总分使用卡片预先固定的 `profile`，而非模型预测栏目对应的权重。本轮可比较栏目答案，但没有测量“模型分类后改变评分权重”的完整链路。

## 实际结果

共发生 60 次真实 API 请求：GLM 30 次、Jev 30 次，没有重试。离线回执检查未新增请求。

| 指标 | GLM | Jev |
|---|---:|---:|
| 严格协议校验成功 | 23 | 30 |
| 严格协议校验失败 | 7 | 0 |
| 严格成功项 select / defer / reject | 14 / 9 / 0 | 5 / 25 / 0 |
| 全部请求输入 tokens | 188,147 | 247,584 |
| 全部请求输出 tokens | 3,503 | 23,552 |
| 全部请求耗时中位数（秒） | 4.29105 | 2.34155 |

严格成功的共同样本为 23 对，最终分流一致 10 对。失败调用的 token 与耗时已计入上表，不能只统计成功项。GLM 最多 2 并发、Jev 1 并发，且两边返回结构不同；这里是本次请求耗时记录，不是统一负载下的性能压测。

### 保留严格失败，再单列无损恢复

GLM 的 7 条失败均有成功的 API 回执，问题是输出没有遵守扁平答案对象格式。它们都有完整的 11 个合法答案，没有缺题或越界选项：

| 候选 | 保存的格式偏差 | 离线转换 |
|---|---|---|
| ResolveHQ、Parrot、Mushrooms、HF Viewer | `{"answer": {…}}` | 解开唯一对象 |
| Effective HTML | `answer` 内为 JSON 字符串 | 解析该字符串 |
| Kindle Comic Converter v11.3.2 | 每题值为 `{"answer": "选项"}` | 逐题解开对象 |
| My HTML Boilerplate | 顶层答案与 `answer` 内答案重复且完全一致 | 验证相同后去除重复层 |

`inspect-receipts` 保留原始回执与原来的 `status=failed`，在 `transport_recovery` 中追加无损转换及二级分析结果。冲突、额外字段或不完整答案不会靠猜测恢复。主结果已有的整数分到字符串枚举转换也只转换同值表示，pilot Pyxel 的该次转换留有 `adapter_history`。

| 无损恢复后的二级结果 | GLM | Jev |
|---|---:|---:|
| 可比较卡数 | 30 | 30 |
| select / defer / reject | 17 / 13 / 0 | 5 / 25 / 0 |

二级分析中分流一致 14/30。该表说明现有答案经过适配后的行为，不抹去主表中的格式失败，也不将后加的适配器描述为模型原生合规输出。

### UNKNOWN 与置信度

Jev 的 30 个原始 `precheck` 都是 PASS。它有 25 卡至少一个评分维度 UNKNOWN，其中 novelty 24 卡、interest 4 卡、usability 2 卡、value 1 卡、evidence 0 卡；这些维度计数有重叠。25 卡全部 defer，其余 5 卡全部 select。GLM 在无损提取全部答案后，只有 HF Viewer 的 novelty 为 UNKNOWN。

既定分流程序遇到任一评分维度 UNKNOWN，会将总分设为空，并将用于分流的 PASS 改为 UNKNOWN。报告同时保留 `raw_choices`、`original_precheck`、`unknown_dimensions`。Jev 的 `confidence` 对应原始选项，不能把原始 PASS 的 0.99 解释成派生 UNKNOWN 或最终 defer 的置信度。

## 同源材料对照审阅

以下是本轮 Codex 对照审阅观点，不是人工标签，不计算准确率。

- **信息增量是主要分歧。** Ledge 的 GLM 为 `7/6/8/8/8`，Jev 为 `7/UNKNOWN/8/8/7`，价值、证据、易用性完全一致。README 已给出就地运行 Markdown 代码、远程 SSH、平台条件和安装入口；它的 74/select 对 defer 主要源于增量未知。该卡只裁剪了许可证尾部，完整 README 保留，不能将分歧解释成主体资料缺失。
- **明确的方法对照能够改变 Jev 的回答。** Chess post-mortem skills 的 Jev novelty 为 8；原文对照传统 Stockfish 数字与长变化线，说明如何结合赛中口述、PGN 时钟和引擎追问解释用户当时的思路。Spinner 一文有设备、测量步骤、HTML/SVG 对照与 measured/expected 区分，Jev 81/select、GLM 64/defer。不能把这些差异简化成 Jev 对所有项目不给分，也不能把文章中的单机收益泛化到所有设备。
- **栏目定义仍有空白。** Pyxel 是游戏开发引擎，GLM 选“开源项目”，Jev 选“游戏”；Pi Codex Connectors 分别被归为“开源项目”和“MCP 服务与连接器”。协议只给栏目名称，没有充分说明工具与成品的边界。
- **普通更新必须绑定本次事件。** KCC v11.3.2 的 `What's Changed` 只有“首页为彩色时不裁剪”；GLM 原始 `routine_update=yes、novelty=3`，Jev 为 `no、3`，且 usability UNKNOWN。按“普通修复或窄支持”的定义，GLM 的标记更贴近该次变化。反之，Laya 卡是 `kind=project、event=null`，双方却被 README 的 0.3.20 修复小节触发 `routine_update`，都偏离了项目介绍的评价对象。Laya 不能作为第二个正确识别普通更新的样本。

`routine_update` 在当前政策中只将 novelty 封顶到 3，不自动 defer/reject。Laya 的 Jev 分数封顶后仍为 77/select，GLM 则为 58/defer；报告需要区分模型标记和政策对该标记的处理。

## 费用与级联模拟

Jev [公开模型价格](https://docs.typesafe.ai/models)为每百万输入 tokens **$0.042**，输出免费。本次估算为 `247584 / 1000000 × 0.042 = $0.010398528`，这是按公开单价计算的请求费用估算，不是账单核对结果。GLM 官网 pricing 内容动态加载，本轮未能读取并核实金额，因此不采用第三方报价计算 GLM 金额。

冻结的级联规则是：任一核心答案置信度低于 0.8、存在未知，或总分距 select/reject 阈值不超过 5 分时，升级到 GLM。离线模拟结果为 **30/30 升级**；即使是 Jev 已选中的卡，也有核心维度低于 0.8。该阈值尚无人类样本校准，不能把每题置信度直接当成最终正确率。目前按该规则叠加 Jev 会增加前置调用，不会节省 GLM 调用。

后续应先明确增量是否必须有原文中的对照、仅某维度未知时是否仍要整卡待定、各栏目归属和更新对象，再用独立的人工判断校准分流阈值。本轮不据此直接切换生产模型。

## 命令与证据入口

实现位于 [digest_benchmark.py](../src/ai_notes/digest_benchmark.py)、[digest_jev.py](../src/ai_notes/digest_jev.py) 与 [digest_runtime.py](../src/ai_notes/digest_runtime.py)。可阅读 [结果报告](../outputs/digest/model-comparison-2026-10-03.md) 和 [同源对照审阅](../outputs/digest/model-review-2026-10-03.md)。原文、冻结卡片、API 回执和机器可读报告保存在本地实验目录，不提交凭据。

以下 PowerShell 命令从仓库根目录执行。`$glmEnv`、`$jevEnv` 应指向分别配置好的私有环境文件；示例不包含密钥。GLM 文件需配置 `DIGEST_MODEL_NAME=glm-5.3-flash` 与 `DIGEST_MODEL_REASONING_EFFORT=low` 等连接参数，Jev 文件需配置 `TYPESAFE_API_KEY`。

```powershell
$experiment = 'work/digest-model-compare-20261003'
$glmEnv = '<GLM 私有环境文件绝对路径>'
$jevEnv = '<Jev 私有环境文件绝对路径>'

# 先冻结并运行 6 张 pilot；此段是实验顺序说明。
.\.venv\Scripts\python.exe -X utf8 -m ai_notes.digest_benchmark freeze --root $experiment --dataset "$experiment/dataset/first6pilot.json" --code-root .
.\.venv\Scripts\python.exe -X utf8 -m ai_notes.digest_benchmark run --root $experiment --glm-env $glmEnv --jev-env $jevEnv --pilot

# 保留 pilot 卡与协议，扩展至 30 卡；已有结果复用，不重发请求。
.\.venv\Scripts\python.exe -X utf8 -m ai_notes.digest_benchmark freeze --root $experiment --dataset "$experiment/dataset/dataset.json" --code-root .
.\.venv\Scripts\python.exe -X utf8 -m ai_notes.digest_benchmark run --root $experiment --glm-env $glmEnv --jev-env $jevEnv

# 以下两步只处理已保存材料，不调用模型 API。
.\.venv\Scripts\python.exe -X utf8 -m ai_notes.digest_benchmark inspect-receipts --root $experiment
.\.venv\Scripts\python.exe -X utf8 -m ai_notes.digest_benchmark report --root $experiment
```

现有目录已冻结 30 卡，不能再用前面的 6 卡命令将其缩回 pilot；复查本轮结果只需执行 `inspect-receipts` 和 `report`。改变已冻结卡片、评分协议或评分语义应另开实验目录，不能覆写本轮依据。

本轮已通过 benchmark 12、Jev 8、runtime 25，共 **45 个 unittest 测试**。环境没有安装 pytest，因此使用现有 unittest 运行器：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_digest_benchmark.py
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_digest_jev.py
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_digest_runtime.py
```

这些测试验证接口边界、冻结保护、回执、无损适配、计量和分流行为；不证明模型判断准确。人工金标数量仍为 **0**，所有一致率均只是两个模型在本协议下的一致程度。
