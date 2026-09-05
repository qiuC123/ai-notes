# 单项目共学实验

复用 Ai Notes 的 `prepare-learning` 采集和 `learning-decisions.v1` 校验，让独立 Pi 分析一个外部项目与 Ai Notes 的关联。不是此前五案变更影响测试，也不会执行实验建议。

候选发现由主对话完成；Pi 不自行上网搜索。Pi 可以分页读取被冻结的外部证据，以及显式限定的 Ai Notes `storage.py`、`learning.py`、`review.py`，最多提出一个关联。文件选择范围由人设定，因此不能宣称已验证全自主项目探索。

实现仅增加独立学习 reader/extension 和启动脚本，共享已有有界 RPC、SSE、错误/重试日志。默认 impact 模式不变；没有替换正式共学的 Codex Review，也不增加新学习协议。

在独立 LearningRoot 中复制现有 `config/ai_notes_learning.yaml`，调用现有 `prepare-learning ... --root <LearningRoot> --project E:\devlop\ai-notes`。主动发现需提供现有 provenance JSON。这样采集、登记、缓存只落在实验目录，不计入正式手动试运行次数。运行前先提交代码，再准备队列；准备后到复核完成前不要修改 Ai Notes，否则现有指纹校验会拒绝。

```powershell
E:\devlop\ai-notes\experiments\project-chemist\learning-start.ps1 -QueueFile <learning-queue.json绝对路径> -LearningRoot <实验根目录绝对路径>
```

加 `-Check` 只做本地预检，不调用模型。真实启动使用现有 Pi 模型，15分钟/120次工具上限；三个工具分别列出证据、分页读取、提交分析。模型提交不含 run/queue 元数据，由 worker 绑定；具体问题必须标为 inferred。提交复用原 JSON schema、原文 quote、文件 hash 和当前项目指纹校验，另行检查自有证据路径范围；不证明语义或性能收益。

运行产物直接保存于 `<LearningRoot>/outputs/pi/<run-id>/`，包括 decisions、manifest、调用和重试日志，不生成需要再次复制的临时运行目录。不会调用 Finalize、写入正式账本、创建 Skill、写记忆、安装外部项目或执行自有项目修改。

本轮候选：Aider 与 Repomix；选择 Aider 的上下文选取方法作为学习对象。候选不是采用建议，具体结论由 Pi 读源码后提出、主对话核验并交付。结果只作为这一次有人工限定证据范围的可行性实验。
