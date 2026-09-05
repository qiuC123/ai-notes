# 第二组跨项目影响案例

这是**案例集 v2**，不是新的协议或评分器。沿用 `impact-suite.v1`、`impact-blind-input.v1`、`impact-baseline.v1` 和原评分器；第一组标准答案、原始报告与分数全部保留。

首次运行已完成，见[结果复核](../project-chemist/results/2026-09-05-new-cases.md)：运行时元数据通过，结构化影响判定因两个对照误报未通过。原始题目和评分不回改。

## 预先冻结的设计

- 同一对已授权项目、同一历史提交；五个新场景 `impact-06` 至 `impact-10`。这属于同领域新案例验证，不是新项目泛化测试，也不是同案例 Codex/Pi 对照。
- 3 个必须同步修改的契约变化、2 个无需同步修改的对照。标准答案仅存于 `suite.json`；Pi 只收到剥离答案后的 `blind-input.json`。
- 统一测试名为 `ClassName.test_method` 或顶层函数名，严格匹配，不启用模糊匹配、别名或事后补分。10 项测试要求都是已有测试的定位锚点，**不代表当前测试已覆盖新场景**；部分需要补充新的边界断言。
- 正例检查身份字段嵌套迁移、成功状态改名、允许上限收紧；反例检查新增可选输出字段、仅调整被显式参数覆盖的默认值。判定边界是“是否必须同步修改”，不是“能否增加兼容性测试”。
- 既有协议用 `semantic_similarity` 编码无跨项目修改的对照，不表示两个项目从来没有其他依赖。原评分器不对反例证据计分，因此仍须人工复核其推理。
- `source_learning` 复用原实验的方法来源，不代表新增了学习账本事件或生产接入批准。

冻结前，使用只读 worker 检查全部 11 条证据引用和 10 项精确测试名，均存在于固定源码；未执行两个样本项目的代码或测试。标准答案及输入先提交 Git，随后才启动独立 Pi；中途不提示答案或调整评分。

## 运行与评分

```powershell
E:\devlop\ai-notes\experiments\project-chemist\start.ps1 -Run -InputFile E:\devlop\ai-notes\experiments\cross-project-impact-v2\blind-input.json
```

新报告由运行器生成执行时间、任务 ID、哈希及固定仓库事实；真实读取方法在 manifest，模型只提交分析和盲测声明。使用现有 Pi 配置与 SSE，15 分钟、120 次工具调用上限不变。

封存后，在 Ai Notes 根目录使用原评分器：

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe -m ai_notes score-impact-baseline --suite experiments/cross-project-impact-v2/suite.json --baseline <本次绝对路径>\baseline.json
```

检查运行日志、反例误报、精确测试漏项及元数据；不因得分好看而自动进入生产或写入学习账本。结果另行记录于 `../project-chemist/results/`。
