# Dot 三榜接手与云端试运行

用户已于 2026-10-06 确认执行：同步最新代码与历史候选，先跑一次云端采集和榜单预览，确认保存及恢复，再切换日、周、月任务。2026-10-07 用户截图确认 `ai-notes` 环境已发布；此前截图／转述回报 Dot 已收到任务、云端配置及 76 项相关测试通过，联网范围已保存为全部，22:20 的 6 个来源入口检查全部成功。下一步配置云端 GLM 私密参数与迁移历史；不能把环境发布当作已完成采集生成回执。

## 可以直接交给 Dot 的任务

请接手 `https://github.com/qiuC123/ai-notes` 的三榜信息整理。先完成一次云端隔离试运行，再汇报结果并配置唯一的新定时入口。本项目仍调用智谱普通 API；不要把 Dot 的订阅模型当作现有 Python 客户端已接入的供应商。

先读 `TASK.md`、`docs/DIGEST_DEPLOYMENT.md`、`docs/DIGEST_RANKING_DESIGN.md`、`docs/WEEKLY_PROJECT_DIGEST_WORKFLOW.md`、`docs/WEEKLY_PROJECT_DIGEST.md`、`docs/WEEKLY_PROJECT_DIGEST_OUTPUT_DRAFT.md`、`docs/WEEKLY_PROJECT_DIGEST_SOURCES.md`、`docs/DIGEST_READER_FEEDBACK.md` 与 `docs/DIGEST_REVIEW_SCOPE_2026-10-06.md`。以当前 `config/digest_selection.json` 和 `config/digest_sources.json` 为准，不复用 10 月 3 日旧快照中的配置。

目标是发现读者愿意使用的工具与信息。日报偏实际 AI 动态及易用工具，周月榜偏成熟实用项目与重要变化；热度只帮助发现。读者关注外置 Agent 协作、已有 Codex/WorkBuddy 订阅的复用、文件整理与压缩、项目知识库。不推荐纯编译测试、CI 或树莓派硬件；不设 Star 硬门槛。HelloGitHub 只作选题参考，不采集月刊补数。Hugging Face 按当前来源配置轮换。

介绍采用名称、用途、亮点、已知支持系统和链接，不写成说明书。许可证条款、组件许可归属及商用权限不核对、不扣分，也不主动写这些结论。用途、功能、系统、新闻事件和实际版本继续依据原文。旧冻结任务及回执保留。

日 5–8 条、周 20–25 条且 3 个重点、月至少 20 条；八栏目空栏省略。共用候选库，独立精选；同级全部历史去重，有真实重要更新才重推。月榜不足保留草稿，不凑数；延期月稿保持原月归属。来源失败、实际发现时间、未知发布时间及未完成步骤如实保存。不要把演示写成实测，不安装候选软件或发布公众号。

## 代码、状态与模型分别交接

- 代码：从上述仓库取得此次交接提交，业务基线至少包含 `e2bc2b1` 的核对范围调整。记录实际 Git SHA、Python 版本、云端绝对路径和执行者。
- 状态：使用私密备份目录或其完整压缩包；公开 GitHub 没有生产数据库和完整历史材料。先 `digest_backup verify`，只恢复到新建的空状态目录。代码目录和状态目录分开。
- 模型：当前 endpoint 为 `https://open.bigmodel.cn/api/paas/v4`，model 为 `glm-5.3-flash`，reasoning effort 为 `low`。云端可通过私密面板注入网络密钥与普通环境变量，或显式读取完整的仓库外配置文件；两种配置来源不混用，密钥不写入对话、Git 或报告。参见下文、部署说明及 `deploy/digest/model.env.example`。

截至本次备份：701 个文件，47 批次、287 条观察、169 条入选、22 期归档（日 18／周 3／月 1）与 3 份草稿。本次实际只有 `digest.sqlite3`；`selection.sqlite3`、`runtime.sqlite3` 不存在，不能声称迁移了这两个旧库。目标上的新流程可正常初始化它们。本地备份恢复计数及全部文件检查通过，云端必须另行核对。

## 云端单轮验收

用户截图已确认关联 `qiuC123/ai-notes` 的 `ai-notes` 环境发布成功；安装脚本／启动说明及全部联网范围已保存，转述的 6 个来源入口检查通过。发布后的面板目前为只读；补充配置从设置 → Codex Cloud → `ai-notes` 的 … → Edit 进入，保存并 Republish，在新任务中验证更新。准备流程使用下文 Python 版本和安装命令；环境需允许安装包源、候选来源原文及 `open.bigmodel.cn` 的网络访问。参见[官方云端环境说明](https://learn.chatgpt.com/docs/environments/cloud-environments)。在原 Dot 对话继续，不重复创建环境或接手任务。环境配置与入口连通不包含历史数据或密钥迁移，也不代表模型 API 或跨任务账本保留已通过验收。

### 云端 GLM 私密配置

在现有环境的编辑面板添加以下配置：

| 位置 | 名称 | 值或范围 |
| --- | --- | --- |
| 网络密钥 | `DIGEST_MODEL_API_KEY` | 用户自己的智谱 API key；Allowed domains 仅填 `open.bigmodel.cn` |
| 环境变量 | `DIGEST_MODEL_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` |
| 环境变量 | `DIGEST_MODEL_NAME` | `glm-5.3-flash` |
| 环境变量 | `DIGEST_MODEL_REASONING_EFFORT` | `low` |

网络密钥按官方约定向程序提供占位符，代理在指定域名的 HTTPS 443 请求中替换；当前 `ModelClient` 只检查密钥非空并写入 Bearer 请求头，代码层面可采用该方式，代理替换仍需真实 smoke 回执验证。采用此方式时不设置 `DIGEST_MODEL_ENV_FILE`，也不传 `--model-env-file`：选定文件时程序会完全使用文件配置，不会合并云端注入变量。若选择完整私密文件，则显式提供其云端绝对路径。配置入口和交付方式见[官方环境变量与网络密钥说明](https://learn.chatgpt.com/docs/environments/cloud-environments#configure-environment-variables-and-network-secrets)。

### 执行检查与单轮试运行

1. 确认 Dot 的执行环境能够运行 Python >=3.11、访问仓库和来源，且有可供下一次任务继续读取的私密状态目录。若只连接本地电脑，标明“本地运行”，不能报“关机后云端继续”。
2. 在代码目录安装项目及阅读依赖：`python -m pip install -e '.[digest-reading]'`。用现有 CLI 验证、恢复私密备份。运行 `python -m ai_notes.digest status --root <state-root>`，核对上述历史；history 的索引刷新只发生在恢复目录。
3. 使用云端注入变量时运行 `python -m ai_notes.digest_runtime model-check --root <trial-root>`；它只检查当前执行环境配置，不能冒充 API 连通。配置就绪后运行一次 `python -m ai_notes.digest_runtime model-smoke --root <trial-root>`，保留真实回执；完整私密文件模式才给两条命令添加 `--model-env-file <private-env>`。重跑复用原回执，不删除账本重付费。
4. 在恢复得到的独立试运行状态上，显式入队当天 `collect`，再执行一个该 ID 的 worker；读回实际来源、候选和批次。正常候选积累保持安静。
5. 随后只为前一个完整北京时间自然日显式入队一个 `generate`，执行该 ID 的 worker；已归档期复用原稿。时间从真实 `Asia/Shanghai` 计算，不传伪造的 `--now`。确认当前新任务采用新配置；有限失败就记录，不循环改材料或抽样重试。
6. 检查文章/草稿、清单、候选库和请求账本，汇报实际入选与缺口。runtime 的 outbox 只是本地待交付记录；向用户实际展示摘要和全文后，才保存真实送达回执。再次读取同一任务确认不重复付费、不重建同一期文章。

离时槽时 `deploy/digest/one_tick.py --execute` 应安静结束；因此不能拿空 tick 当作采集或生成成功。上述显式单轮可使用已实现的 Python 接口，在代码目录执行。先将 `DIGEST_TRIAL_ROOT` 设为刚恢复并核对的独立状态目录。模型采用云端注入变量时直接继承其配置；完整私密文件模式才将 `DIGEST_MODEL_ENV_FILE` 设为该文件的云端绝对路径。先执行采集块：

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

2026-10-07 用户后续截图同时显示“环境已发布”和面板底部“已发布”，网络密钥与环境变量仍为空。主控据此更新发布状态，尚未独立读取环境或配置凭据。当前客户端与官方代理密钥机制的匹配仅做源码检查，未执行真实 GLM 请求；历史迁移与定时切换仍未开始。

2026-10-07 用户转述北京时间 22:20 的云端来源连通检查：全部 6 个配置入口返回有效正文，成功 6、失败 0；Google AI News 为 301 → 200，跳转到 `/innovation-and-ai/technology/ai/rss/`，配置未改。转述回执为 `/workspace/ai-notes-connectivity-20261007-222040-y9zl3xrb/results.json`，报告称响应哈希检查通过；主控尚未读取原文件。这只证明入口当时可用，本轮未采集、生成或调用模型，原自动化继续暂停。

本机模型配置检查通过，未调用模型。代码同步、私密备份及本地恢复证据由原 Codex 主控记录于 `TASK.md` 和忽略目录 `work/digest-dot-handoff-20261006/`。备份、云端密钥与原始状态没有上传公开仓库。2026-10-07 用户提供 Dot 接收回复及云端设置阶段截图：设置回复报告 `/workspace/ai-notes`、提交 `91a3117`、Python 3.12.14、`gitingest 0.3.1` 与 76 项相关测试通过，仓库文件无改动；当时草稿尚未发布，该轮未迁移历史、调用模型、采集生成或切换定时。以上为截图中的执行者回报，主控尚未独立读回原始回执；页面控制仍超时，未代为发布。发布状态已由后续截图更新，实际采集生成和新 schedule 的证据仍待补，不另建无关云端任务冒充 Dot。
