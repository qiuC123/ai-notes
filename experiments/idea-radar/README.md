# 开发方向雷达

独立飞书机器人，按需寻找海外应用、SaaS、小游戏线索，并给出讨论、增长、付费证据缺口及最小验证建议。运行在本机，复用现有 Pi 模型和 Exa / Jina 公开网页采集。

## 在飞书中使用

首次在独立机器人私聊中发送本机生成的 `配对 <配对码>`。随后直接发送：

- `找方向，适合个人 Python 开发者，两周能验证的 SaaS`
- `找小游戏，适合手机端的简单解谜玩法`
- `https://example.com/product，看看这个方向是否值得验证`
- `第二个候选有哪些需求证据缺口？`（继续当前话题）

`帮助` 查看说明；`状态` 查看进度；`结果` 重发最近结果；`新话题` 清除当前话题指向，保留历史。新的“找方向”“找小游戏”指令或网页链接自动开启新话题。每轮最多三个候选，无法取得有效资料时明确失败，不生成空泛的成功报告。

## 本机入口

在项目根目录运行（PowerShell 7）：

```powershell
.\experiments\idea-radar\start.ps1 -Action register
.\experiments\idea-radar\start.ps1 -Action check-app
.\experiments\idea-radar\service.ps1 -Action Start
.\experiments\idea-radar\service.ps1 -Action Status
.\experiments\idea-radar\service.ps1 -Action Stop
```

注册用飞书 SDK 的官方应用创建流程，需要用户本人登录并确认。注册链接保存在 `work/idea-radar/registration.json`；凭证通过 Windows DPAPI 加密保存到 `work/idea-radar/feishu-credential.xml`，不显示 App Secret。已经创建但注册中断时，使用 `start.ps1 -Action register -AppId cli_...` 重新连接同一应用；已有凭证时拒绝重复创建。

应用需要机器人能力、私聊消息接收事件及发送机器人消息权限；官方注册流程请求 `im:message.p2p_msg:readonly`、`im:message:send_as_bot` 和 `im.message.receive_v1`。应用是否需要管理员审批、发布或调整可用范围，以飞书实际页面为准。

## 运行与记忆

- `work/idea-radar/inbox.sqlite3`：独立配对、消息去重、排队、话题与重发状态。
- `work/idea-radar/runs/`：每次尝试的网页证据、结构化报告、Markdown 报告和 Pi 会话。
- 追问复制最近成功会话，再加入本轮新证据；失败不会覆盖成功会话。重启恢复未完成任务。
- 凭证仅由飞书适配层使用，不传给搜索或 Pi 子进程。
- 本机须开机、联网且不休眠。未创建定时搜索、自动群发或开机启动任务。
- `service.ps1 -Action Start` 只表示已启动入口进程；需用 Status 核对实际 Python 进程、连接和队列，最后通过飞书私聊验收收发。

## 分析边界

每轮最多三组搜索、每组四条结果、最多三篇正文；不是对所有平台的全面扫描。Exa 搜索摘要与 Jina 网页正文分开标注；返回的页面可能过时或只有作者宣传。平台不可访问或没有结果时列为缺口。

讨论、增长、付费只允许“来源线索”或“未找到证据”。模型须为线索提供能在本轮采集文本逐字找到的引文；代码检查结构和引文，不保证引文足以支持结论。单个 star 数不等于增长，有定价不等于有人购买，作者收入自述不等于独立核实。

Pi 仅负责分析给定资料，禁用内置工具、扩展、Skills、项目上下文发现；不会执行网页中的指令、安装候选项目或修改业务代码。国内适配和验证方案是建议，不自动采纳为项目决策或 Codex 记忆。

## 验证

```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_idea_radar.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_mobile_chemist.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_mobile_registration.py -v
```

实际搜索与模型分析样例、应用接入状态见 `results/2026-09-07-local-check.md`。
