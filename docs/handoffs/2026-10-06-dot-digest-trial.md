# Dot 三榜接手与云端试运行

用户已于 2026-10-06 确认执行：同步最新代码与历史候选，先跑一次云端采集和榜单预览，确认保存及恢复，再切换日、周、月任务。2026-10-07 用户截图显示 Dot 已收到任务，后续云端设置回复报告环境配置及 76 项相关测试通过；安装脚本与启动说明仍是未发布草稿，“需要关注 1”的具体项待展开核对。当前本地准备已完成，继续现有环境的检查与发布；不能把配置回报当作已部署或采集生成回执。

## 可以直接交给 Dot 的任务

请接手 `https://github.com/qiuC123/ai-notes` 的三榜信息整理。先完成一次云端隔离试运行，再汇报结果并配置唯一的新定时入口。本项目仍调用智谱普通 API；不要把 Dot 的订阅模型当作现有 Python 客户端已接入的供应商。

先读 `TASK.md`、`docs/DIGEST_DEPLOYMENT.md`、`docs/DIGEST_RANKING_DESIGN.md`、`docs/WEEKLY_PROJECT_DIGEST_WORKFLOW.md`、`docs/WEEKLY_PROJECT_DIGEST.md`、`docs/WEEKLY_PROJECT_DIGEST_OUTPUT_DRAFT.md`、`docs/WEEKLY_PROJECT_DIGEST_SOURCES.md`、`docs/DIGEST_READER_FEEDBACK.md` 与 `docs/DIGEST_REVIEW_SCOPE_2026-10-06.md`。以当前 `config/digest_selection.json` 和 `config/digest_sources.json` 为准，不复用 10 月 3 日旧快照中的配置。

目标是发现读者愿意使用的工具与信息。日报偏实际 AI 动态及易用工具，周月榜偏成熟实用项目与重要变化；热度只帮助发现。读者关注外置 Agent 协作、已有 Codex/WorkBuddy 订阅的复用、文件整理与压缩、项目知识库。不推荐纯编译测试、CI 或树莓派硬件；不设 Star 硬门槛。HelloGitHub 只作选题参考，不采集月刊补数。Hugging Face 按当前来源配置轮换。

介绍采用名称、用途、亮点、已知支持系统和链接，不写成说明书。许可证条款、组件许可归属及商用权限不核对、不扣分，也不主动写这些结论。用途、功能、系统、新闻事件和实际版本继续依据原文。旧冻结任务及回执保留。

日 5–8 条、周 20–25 条且 3 个重点、月至少 20 条；八栏目空栏省略。共用候选库，独立精选；同级全部历史去重，有真实重要更新才重推。月榜不足保留草稿，不凑数；延期月稿保持原月归属。来源失败、实际发现时间、未知发布时间及未完成步骤如实保存。不要把演示写成实测，不安装候选软件或发布公众号。

## 代码、状态与模型分别交接

- 代码：从上述仓库取得此次交接提交，业务基线至少包含 `e2bc2b1` 的核对范围调整。记录实际 Git SHA、Python 版本、云端绝对路径和执行者。
- 状态：使用私密备份目录或其完整压缩包；公开 GitHub 没有生产数据库和完整历史材料。先 `digest_backup verify`，只恢复到新建的空状态目录。代码目录和状态目录分开。
- 模型：当前 endpoint 为 `https://open.bigmodel.cn/api/paas/v4`，model 为 `glm-5.3-flash`，reasoning effort 为 `low`。云端用仓库外配置文件；密钥通过私密配置入口设置，不写入对话、Git 或报告。参见部署说明及 `deploy/digest/model.env.example`。

截至本次备份：701 个文件，47 批次、287 条观察、169 条入选、22 期归档（日 18／周 3／月 1）与 3 份草稿。本次实际只有 `digest.sqlite3`；`selection.sqlite3`、`runtime.sqlite3` 不存在，不能声称迁移了这两个旧库。目标上的新流程可正常初始化它们。本地备份恢复计数及全部文件检查通过，云端必须另行核对。

## 云端单轮验收

当前已有关联 `qiuC123/ai-notes` 的未发布 `ai-notes` 环境，先在其设置聊天底部打开“编辑环境 · 需要关注 1”，检查实际待关注项、安装脚本和启动说明，完成后保存并 Publish；看到 `Environment published` 再继续。准备流程使用下文 Python 版本和安装命令；环境需允许安装包源、候选来源原文及 `open.bigmodel.cn` 的网络访问。以实际设置和测试回执为准，参见[官方云端环境说明](https://learn.chatgpt.com/docs/environments/cloud-environments)。发布环境后返回原 Dot 对话继续，不重复创建环境或接手任务。环境配置不包含历史数据或密钥迁移，也不代表跨任务账本保留已通过验收。

1. 确认 Dot 的执行环境能够运行 Python >=3.11、访问仓库和来源，且有可供下一次任务继续读取的私密状态目录。若只连接本地电脑，标明“本地运行”，不能报“关机后云端继续”。
2. 在代码目录安装项目及阅读依赖：`python -m pip install -e '.[digest-reading]'`。用现有 CLI 验证、恢复私密备份。运行 `python -m ai_notes.digest status --root <state-root>`，核对上述历史；history 的索引刷新只发生在恢复目录。
3. `python -m ai_notes.digest_runtime model-check --model-env-file <private-env>` 只检查本机配置，不能冒充云端 API 连通。配置就绪后运行一次 `model-smoke --root <trial-root> --model-env-file <private-env>`，保留真实回执；重跑复用原回执，不删除账本重付费。
4. 在恢复得到的独立试运行状态上，显式入队当天 `collect`，再执行一个该 ID 的 worker；读回实际来源、候选和批次。正常候选积累保持安静。
5. 随后只为前一个完整北京时间自然日显式入队一个 `generate`，执行该 ID 的 worker；已归档期复用原稿。时间从真实 `Asia/Shanghai` 计算，不传伪造的 `--now`。确认当前新任务采用新配置；有限失败就记录，不循环改材料或抽样重试。
6. 检查文章/草稿、清单、候选库和请求账本，汇报实际入选与缺口。runtime 的 outbox 只是本地待交付记录；向用户实际展示摘要和全文后，才保存真实送达回执。再次读取同一任务确认不重复付费、不重建同一期文章。

离时槽时 `deploy/digest/one_tick.py --execute` 应安静结束；因此不能拿空 tick 当作采集或生成成功。上述显式单轮可使用已实现的 Python 接口，在代码目录执行。先将 `DIGEST_TRIAL_ROOT` 设为刚恢复并核对的独立状态目录，将 `DIGEST_MODEL_ENV_FILE` 设为云端私密模型配置的绝对路径。先执行采集块：

```python
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from ai_notes import digest_runtime

root = Path(os.environ['DIGEST_TRIAL_ROOT'])
today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
job = digest_runtime.enqueue(root, {'action': 'collect', 'period': today.isoformat()})
print(digest_runtime.work_once(root, job_id=job['job_id']))
```

核对采集结果后，单独执行生成块；两个块应在同一个已初始化的 Python 会话中运行，`today` 保留这轮开始时的真实日期：

```python
job = digest_runtime.enqueue(root, {'action': 'generate', 'ranking_type': 'daily',
                                  'period': (today - timedelta(days=1)).isoformat()})
print(digest_runtime.work_once(root, job_id=job['job_id']))
```

## 切换与通知

云端单轮及状态读回通过后，用户已授权继续配置唯一的新定时入口：北京时间每日 09:00 日榜，周一 10:00 周榜，每月 1 日 11:00 月榜，每日 19:00 候选积累。由现有 dispatch 决定分支，不凭执行跨小时改任务。固定重复工作需要保存可读回的 schedule；不能只承诺以后执行。采用 Dot 定时唤醒或单个云端 executor，两者不同时运行。

原本地 heartbeat 继续暂停；不恢复它来代替 Dot，也不修改飞书、Pi、共学和旧 Release 管道。正常采集与未变化缺口安静，仅榜单完成、失败、实质缺口变化或需要用户处理时通知。结果交付到此 Dot 对话；本地原任务作为交接入口。准确报告“材料已准备／Dot 已收到／云端单轮已运行／定时已保存／已观察到定时运行”，未完成的步骤不能合并称为部署成功。

## 本次实际记录

本机模型配置检查通过，未调用模型。代码同步、私密备份及本地恢复证据由原 Codex 主控记录于 `TASK.md` 和忽略目录 `work/digest-dot-handoff-20261006/`。备份、云端密钥与原始状态没有上传公开仓库。2026-10-07 用户提供 Dot 接收回复及云端设置结果截图：设置回复报告 `/workspace/ai-notes`、提交 `91a3117`、Python 3.12.14、`gitingest 0.3.1` 与 76 项相关测试通过，仓库文件无改动；草稿尚未发布，本轮未迁移历史、调用模型、采集生成或切换定时。以上为截图中的执行者回报，主控尚未独立读回原始回执；页面控制仍超时，未代为发布。环境发布、实际采集生成和新 schedule 的证据待补，不另建无关云端任务冒充 Dot。
