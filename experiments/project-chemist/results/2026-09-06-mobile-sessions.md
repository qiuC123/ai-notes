# 手机共学会话持久化验收

用户于2026-09-06确认实施 Pi 原生会话保存与恢复。此次仅修改手机入口及其共用 runner 的显式可选参数；独立盲测默认仍使用 `--no-session`。

## 实现

- SQLite 兼容升级增加 `jobs.session_file`，通过原有聊天/任务链绑定会话。
- 每轮新进程复制上一轮成功的 Pi JSONL 文件，使用 `--session` 继续；报告校验通过后与会话路径一起提交数据库。
- 不修改上一轮检查点。失败/崩溃尝试不成为后续会话历史；追问沿当前话题回到最近成功记录。
- 新链接开始新上下文；`新话题` 清除当前绑定并保留历史文件，不取消已排队任务。
- 原生会话路径通过 Pi RPC `get_state` 核对；记录会话 ID、恢复前消息数；缺失/损坏的历史不会静默清空。

## 自动测试

使用项目 `.venv/Scripts/python.exe`、`PYTHONPATH=src`：

- `python -m unittest discover -s tests -p test_mobile_chemist.py`：19项通过。
- `python -m unittest discover -s tests -p test_chemist_runner.py`：12项通过。

覆盖旧数据库迁移、进程重启后绑定恢复、失败任务祖先回退、运行中重置话题、跨聊天/新链接隔离、失败尝试不污染原文件、损坏历史拒绝、RPC 路径核对和盲测隔离。SDK 测试有既有弃用/事件循环 ResourceWarning，测试无失败。

## 真实 Pi 三轮验证

运行目录：`work/mobile-session-smoke-20260906/`；汇总为 `verification.json`。本地测试 inbox/outbox 使用模拟飞书事件，不向真实飞书发送测试消息。每轮重新打开 SQLite，使用正式 `execute_job` 启动独立 Python/Pi 进程。

实时 GitHub 采集先因匿名 API 限流失败。随后使用此前保存的 Requests 外部证据快照（commit `dae7ef63b4df6eded86637f251fc4e3a06c3b479`），显式重新记录当前本地项目指纹和授权登记，仅用于验证会话恢复。来源记在 `snapshot-provenance.json`。测试快照首次漏登记导致校验失败，补齐后重新完整测试；未放宽生产校验。

三轮共享会话 ID `01a07511-6a87-72fb-b148-3bdce1debaf8`：

| 轮次 | 任务 ID | 恢复的历史消息数 | 工具调用/错误 | 结果 |
|---|---|---:|---:|---|
| 1 | `747b3dab4296442c93e79ce288713f22` | 0 | 8 / 1 | sealed；模型修正一次不符合合同的提交后通过 |
| 2 | `ed53566d94b94cf891eda6e71a7feb31` | 18 | 5 / 0 | sealed |
| 3 | `f5cd39445b104e808802b634ecc79546` | 30 | 4 / 0 | sealed |

第一轮给出评估代号 `Cedar-47`，第二轮只问使用场景且要求不重复代号，第三轮不提供代号并询问第一轮设置。第三轮 `project_understanding.problem` 正确回答 `Cedar-47`。每轮检查上一轮文件字节未改变、本轮包含上一轮历史、会话 ID 相同且恢复的消息数递增。三轮最终报告均经过原有引用校验。

这验证了真实 Pi 的多轮会话与进程重启恢复；没有实际重启 Windows，也没有完成升级后的手机真实多轮收发验收。GitHub 匿名采集限流未在本次改动中解决。现有冻结证据在本地仓库变化后仍按原规则拒绝继续分析，可重新发链接采集；测试队列亦会在本次提交后因指纹变化而失效。
