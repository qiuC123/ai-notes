# 关系与本次影响分离：同题协议回归

结果：修复目标通过真实回归，详见[运行与评分复核](../project-chemist/results/2026-09-05-impact-v2-regression.md)。三个正例、两个兼容对照均正确，测试定位9/10；包含一次网络失败及一次重试恢复，不代表生产就绪。

这是第三个案例集，使用新 `impact-suite.v2`、`impact-blind-input.v2`、`impact-baseline.v2` 协议；不是第三次发现新案例。复用第二组的五案、两个历史固定提交、证据、测试要求和受影响项目集合，仅更新版本、套件 ID、冻结时间及两个兼容案例的真实关系标签。所有五案都存在直接依赖，但只有 06、07、08 必须同步修改。

## 明确约定

- `relationship`：项目之间已存在的关系，不回答本次是否需要修改。
- `requires_change`：必填 JSON 布尔值，仅在本次变化要求另一个项目同步修改以保持正确行为时为 true。建议增加回归测试不等于 true。
- `direct_dependency + false` 是有效组合，仍需双方证据和既有测试；false 不能成为逃避证据检查的捷径。
- `semantic_similarity/unrelated + true` 是矛盾组合，会被拒绝。不确定就报告未完成，不用缺省 false 掩盖缺口。
- v2 影响评分只读取 `requires_change`，不从关系或文字理由猜测；不单独给关系分类准确率打分。结构合法并不保证文字与布尔判断语义一致，仍需人工复核。
- 缺失字段、null、字符串和整数均拒绝；v1/v2 输入、报告不可混用，不自动升级旧报告或重新解释旧分数。
- 测试名仍严格匹配原有十项要求，没有添加别名或修改漏项标准。反例证据仍不由现有指标计分，人工检查保留。

新 Pi 进程只接收无答案输入和只读工具；主对话已知这些题的答案，所以本次定位为**同题协议回归**，不是新样本泛化，也不是给旧报告补字段后重算。先提交实现和输入，后运行；原来的两个误报及 0.6 精确率记录永久保留。

## 入口

```powershell
E:\devlop\ai-notes\experiments\project-chemist\start.ps1 -Run -InputFile E:\devlop\ai-notes\experiments\cross-project-impact-v3\blind-input.json
```

仅检查时把 `-Run` 换成 `-Check`。默认不带 InputFile 的入口仍是第一组，避免悄悄改变旧用法。

评分仍用原 CLI，按套件协议版本明确分派：

```powershell
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe -m ai_notes score-impact-baseline --suite experiments/cross-project-impact-v3/suite.json --baseline <新运行目录>\baseline.json
```

这轮不执行样本项目测试，不写账本、记忆、Skill，不批准自动派发修改任务、依赖图或生产接入。
