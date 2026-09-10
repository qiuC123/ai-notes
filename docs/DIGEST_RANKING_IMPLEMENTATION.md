# 日榜、周榜、月榜实施验收

日期：2026-09-10。设计依据：[已确认 Q1–Q12](DIGEST_RANKING_DESIGN.md)。执行入口：[运行说明](WEEKLY_PROJECT_DIGEST_WORKFLOW.md)。

## 范围与数据保护

本次接续同目录中的未提交原型，日周月榜共用候选库、各自选题；榜单逻辑保留在 `ai_notes.digest`，不接入飞书、Pi、项目共学或旧 Release 管道。

迁移前只读核对：1 个批次、16 个候选、16 个观察、0 期正式归档，唯一实际采集日为 2026-09-10。已使用 SQLite backup API 保存 `data/weekly_digest/backups/digest-pre-ranking-20260910T152331.sqlite3`，副本 `integrity_check=ok`。数据库及运行输入、网页资料均在 Git 忽略目录内。

## 真实内容核验

本次从既有 16 项中选择 5 项回读原始资料，没有新增项目，也没有安装候选软件。GitHub 通过 agent-reach 的 `gh api` 路由读取；5 个仓库 README、许可证和仓库状态均可读，Effective HTML 另读 `skills/html-prototype/SKILL.md`。

| 候选 | 实际核对 | 主要边界 |
| --- | --- | --- |
| [Dbmate](https://github.com/amacneil/dbmate) | README 的用途、安装、命令和连接说明；MIT 文件 | 未连接数据库、执行迁移或测试回滚 |
| [Hister](https://github.com/asciimoo/hister) | README 全文；AGPL 文件 | 未建立索引；可选语义搜索会发送文本到配置的端点 |
| [花笺](https://github.com/Achilng/floral-notepaper) | README 全文；MIT 文件 | 未安装；MSIX 更新限制按原文说明 |
| [fooyin](https://github.com/fooyin/fooyin) | README 全文；GPL 文件 | 未播放测试；macOS 官方支持尚在推进 |
| [Effective HTML](https://github.com/plannotator/effective-html) | README、许可证、html-prototype 技能正文 | 未安装或运行技能；技能要求检查产物不等于产物已经通过检查 |

README 快照与元数据保存在 `data/weekly_digest/verification/2026-09-10/`，本次核验批次为 `data/weekly_digest/incoming/2026-09-10-ranking-verification.json`。首次发现保留原批次的实际日期；真实首次发布日期仍为未知，不用仓库创建时间代替。其余 11 个候选仍为发现状态。

三篇真实内容预览均为当前周期的 **5 条草稿**：日榜短介绍，周榜展开 3 个重点，月榜写出保留价值。自然周期尚未结束，月榜也未达到 20 条，不生成正式文章来冒充已运行一周或一个月。

## 正文与历史入口

- [日榜草稿：2026-09-10](../outputs/digest/daily/drafts/2026-09-10.md)
- [周榜草稿：2026-09-07 至 2026-09-13](../outputs/digest/weekly/drafts/2026-09-07.md)
- [月榜草稿：2026-09](../outputs/digest/monthly/drafts/2026-09.md)
- [三榜历史入口](../outputs/digest/index.md)

上述是本机运行产物，未加入 Git。对应 `.json` 保留秒级时间、核验观察与覆盖记录；正文用北京时间日期展示，避免将批次编号等实现细节放入读者介绍。

实际迁移使用命令 `migrate --root .`，保存第二份自动备份 `digest-v0-before-v2-20260910T153627975656.sqlite3`。写入新核验批次前，原四表所有行与迁移前副本逐行比对一致；加入核验后为 **2 批次、16 候选、21 观察、3 草稿、0 正式归档**，仍只有 2026-09-10 一个实际采集日。

## 验证

测试采用隔离临时目录；合成项目没有进入实际候选库或正式榜单。

- 既有功能回归：排除正在改造的 `test_digest` 模块后，**224 项通过**，耗时 97.643 秒。覆盖现存共学、来源、移动入口及遗留功能；日志中的故障信息来自预期失败场景，最终结果为 `OK`。
- 三榜专项：**27 项通过**，耗时 1.786 秒；与上项合计 **251 项**。覆盖同日三榜独立归档、跨级复用、全部同级历史、同一事件改写与换 ID、真实后续更新、Q12 北京时间边界与晚核验、月榜 19／20 条、较新月榜先交付、草稿不占位、正文与清单一致、导出失败回滚及重放。新增 `dispatch` 用例验证四个时槽、非目标时槽退出、每次仅重试最早一期延期月榜和已归档抑制。
- CLI 副本检查：从迁移前数据库副本依次执行迁移、入库、三种草稿保存及重放、提前归档拒绝、历史和到期查询，全部符合预期；SQLite 完整性检查为 `ok`。
- 真实草稿检查：三篇正文各 5 条，与结构化清单和 `render` 输出一致；6 条本地历史链接可解析；仍为 0 期正式归档。

实际执行的专项命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -p test_digest.py -v
```

既有回归单独排除了 `test_digest`，避免在该模块编写测试时加载未完成内容。复现当前全部测试可运行 `.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v`。不需要访问真实来源，测试时钟和合成数据不代表实际运行日期。

本次记录在本地 `data/weekly_digest/verification/2026-09-10/`：`digest-tests.txt`、`independent-regression.txt`、`cli-smoke.json`、`live-migration.json`、`preview-check.json`。副本检查结束后临时目录已清除；原始资料、输入与可恢复备份继续保留。

## 实际调度

`automation_update` 实测返回：一个任务只能附加一个 heartbeat。新增独立日榜被拒绝，未产生重复记录。因此复用原 ID **`automation`**，由一个 heartbeat 承载四项流程；没有新建 cron 或迁移交付任务。

2026-09-10 工具更新成功后，逐字段读回本机自动化配置，确认全目录只有一个自动化，名称 **实用项目日周月榜与候选积累**、状态 **ACTIVE**、目标仍为 **`01a0747b-b12e-74f2-a22a-a4b24d5dea92`**，提示词与提交给工具的内容逐字一致。系统时区为 China Standard Time（UTC+08:00）。

| 北京时间触发点 | `dispatch` 实际工作 |
| --- | --- |
| 每日 09:00 | 前一天日榜，已经归档则跳过 |
| 每日 10:00 | 仅周一生成上周周榜，其余日期空操作 |
| 每日 11:00 | 仅每月 1 日生成上月月榜，其余日期空操作 |
| 每日 19:00 | 积累候选，并最多重试最早一期已到期的月榜草稿 |

每次先执行 `dispatch --root .`，空操作直接退出，不联网。只在任务开始时分流一次，跨整点不改变本轮工作。非目标日期仍会发生一次本地唤醒，这源于单 heartbeat 的平台限制；榜单实际生成日期与已确认方案一致。正常积累及没有实质变化的草稿缺口保持安静。

调度写入前后快照保存在本地核验目录的 `automation-before.toml`、`automation-after.json`。**本次只验证配置与本地分流，没有等待真实定时触发，不能宣称已完成定时周榜或月榜。** 漏过的日／周期不会被 `dispatch` 自动扩写成历史文章；`due` 和实际采集记录会显示范围，可显式指定期号处理。

## 架构图验收

[交互架构图](diagrams/codex-weekly-digest.html) 已用 archify 更新为单 heartbeat、四个时槽、本地 `dispatch`、共享候选与独立三榜，并注明首次定时运行待验证。

- `deliver`：showcase **9/9** 检查通过，0 错误、0 警告；[交付收据](diagrams/codex-weekly-digest.delivery.json)。
- `visual-check`：1440×900、1600×1000、1920×1080、2048×1320 四个视口的明暗主题无页面溢出；[浏览器测量](diagrams/codex-weekly-digest.visual-check.json)。
- 图像复查：实际查看 1440 和 2048 两个尺寸的明暗截图，主链、分流、文字与状态卡无遮挡；[截图入口](diagrams/codex-weekly-digest.visual-check.html)。浏览器自动收据保留 `visualReview: pending`，独立图像审查结果记录在交付收据中，不混淆两种证据。

冻结的规范 SHA256 为 `b6f5761b2f6fe49880d82f6aae707b1fdbf76a394c121ad007c2f0466dae2a5a`；HTML SHA256 为 `e93c6ff2acc0175737f1775bad017b962ac1613a698620248febc69023be7cd9`。验证后未修改规范或 HTML。

## 剩余边界

程序核验结构、已记录证据、事件身份、时间与历史；它不会自行读取网站，也不能证明原文主张真实、事件重要或项目实用。这些仍由 Codex 实际阅读并作编辑判断。未访问的来源、软件实际效果和跨设备同步均不在本次验证结果内。

新归档要求逐项核验距离真实归档时刻不超过 24 小时；旧的 `prepared_at` 不能延长时效。修改时间字段不能代替重新核验。已正式归档的同输入重放可从账本恢复原文与索引，允许跨日恢复，但不接受改写原刊。

本地定时需要电脑开机、项目可用且桌面应用运行；这是本地任务的运行条件，见 [OpenAI 定时任务说明](https://learn.chatgpt.com/docs/automations?surface=app)。本次配置成功与未来真实定时执行分别验收。
