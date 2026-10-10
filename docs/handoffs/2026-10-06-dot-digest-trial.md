# Dot 三榜接手与云端试运行

用户已于2026-10-06确认执行：同步最新代码与历史候选，先跑一次云端采集和榜单预览，确认保存及恢复，再切换日、周、月任务。10/8 GLM配置发布与唯一真实模型smoke通过，10/9 GitHub单份历史传输与恢复通过。10/10复用原云端任务的[真实单轮实测](../DIGEST_DOT_CLOUD_TRIAL_2026-10-10.md)：三个轮换来源成功，20候选／20观察入库读回；2026-10-09日榜初筛19输入／17逐项决策，漏掉两个入选候选，覆盖校验失败。1次API／5,951 tokens／零重试，没有进入深核、评分或成文，无新文章／草稿。当前48批／307观察／169入选／22归档／3草稿，原历史业务记录及文件保持。需修正初筛后继续验收，不重新下载历史、重做smoke或直接retry旧请求。原自动化PAUSED，新schedule未创建；新容器持久化和真实通知未验收。此前云端76项测试与六个来源入口检查成功为执行者回报，不混入此次三来源实测。

## 可以直接交给 Dot 的任务

请接手 `https://github.com/qiuC123/ai-notes` 的三榜信息整理。先完成一次云端隔离试运行，再汇报结果并配置唯一的新定时入口。本项目仍调用智谱普通 API；不要把 Dot 的订阅模型当作现有 Python 客户端已接入的供应商。

先读 `TASK.md`、`docs/DIGEST_DEPLOYMENT.md`、`docs/DIGEST_RANKING_DESIGN.md`、`docs/WEEKLY_PROJECT_DIGEST_WORKFLOW.md`、`docs/WEEKLY_PROJECT_DIGEST.md`、`docs/WEEKLY_PROJECT_DIGEST_OUTPUT_DRAFT.md`、`docs/WEEKLY_PROJECT_DIGEST_SOURCES.md`、`docs/DIGEST_READER_FEEDBACK.md` 与 `docs/DIGEST_REVIEW_SCOPE_2026-10-06.md`。以当前 `config/digest_selection.json` 和 `config/digest_sources.json` 为准，不复用 10 月 3 日旧快照中的配置。

目标是发现读者愿意使用的工具与信息。日报偏实际 AI 动态及易用工具，周月榜偏成熟实用项目与重要变化；热度只帮助发现。读者关注外置 Agent 协作、已有 Codex/WorkBuddy 订阅的复用、文件整理与压缩、项目知识库。不推荐纯编译测试、CI 或树莓派硬件；不设 Star 硬门槛。HelloGitHub 只作选题参考，不采集月刊补数。Hugging Face 按当前来源配置轮换。

介绍采用名称、用途、亮点、已知支持系统和链接，不写成说明书。许可证条款、组件许可归属及商用权限不核对、不扣分，也不主动写这些结论。用途、功能、系统、新闻事件和实际版本继续依据原文。旧冻结任务及回执保留。

日 5–8 条、周 20–25 条且 3 个重点、月至少 20 条；八栏目空栏省略。共用候选库，独立精选；同级全部历史去重，有真实重要更新才重推。月榜不足保留草稿，不凑数；延期月稿保持原月归属。来源失败、实际发现时间、未知发布时间及未完成步骤如实保存。不要把演示写成实测，不安装候选软件或发布公众号。

## 代码、状态与模型分别交接

- 代码：从上述仓库取得此次交接提交，业务基线至少包含 `e2bc2b1` 的核对范围调整。记录实际 Git SHA、Python 版本、云端绝对路径和执行者。
- 状态：默认使用私密备份目录或其完整压缩包。2026-10-09 用户明确授权的单份快照已有公开 [GitHub Release 下载入口](https://github.com/qiuC123/ai-notes/releases/tag/digest-backup-20261006)，不意味着代码 checkout 自带历史或后续备份自动公开。先核对 ZIP，再 `digest_backup verify`，只恢复到新建的空状态目录。代码目录和状态目录分开。
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

网络密钥按官方约定向程序提供占位符，代理在指定域名的 HTTPS 443 请求中替换；当前 `ModelClient` 只检查密钥非空并写入 Bearer 请求头。2026-10-08 已按此配置完成唯一真实 smoke，主控在网页打开结果与执行文件读回成功，见下方记录。采用此方式时不设置 `DIGEST_MODEL_ENV_FILE`，也不传 `--model-env-file`：选定文件时程序会完全使用文件配置，不会合并云端注入变量。若选择完整私密文件，则显式提供其云端绝对路径。配置入口和交付方式见[官方环境变量与网络密钥说明](https://learn.chatgpt.com/docs/environments/cloud-environments#configure-environment-variables-and-network-secrets)。

### 执行检查与单轮试运行

1. 确认 Dot 的执行环境能够运行 Python >=3.11、访问仓库和来源，且有可供下一次任务继续读取的私密状态目录。若只连接本地电脑，标明“本地运行”，不能报“关机后云端继续”。
2. 在代码目录安装项目及阅读依赖：`python -m pip install -e '.[digest-reading]'`。用现有 CLI 验证、恢复私密备份。运行 `python -m ai_notes.digest status --root <state-root>`，核对上述历史；history 的索引刷新只发生在恢复目录。
3. 使用云端注入变量时运行 `python -m ai_notes.digest_runtime model-check --root <trial-root>`；它只检查当前执行环境配置，不能冒充 API 连通。配置就绪后运行一次 `python -m ai_notes.digest_runtime model-smoke --root <trial-root>`，保留真实回执；完整私密文件模式才给两条命令添加 `--model-env-file <private-env>`。重跑复用原回执，不删除账本重付费。
4. 在恢复得到的独立试运行状态上，显式入队当天 `collect`，再执行一个该 ID 的 worker；读回实际来源、候选和批次。正常候选积累保持安静。
5. 随后只为前一个完整北京时间自然日显式入队一个 `generate`，执行该 ID 的 worker；已归档期复用原稿。时间从真实 `Asia/Shanghai` 计算，不传伪造的 `--now`。确认当前新任务采用新配置；有限失败就记录，不循环改材料或抽样重试。
6. 检查文章/草稿、清单、候选库和请求账本，汇报实际入选与缺口。runtime 的 outbox 只是本地待交付记录；向用户实际展示摘要和全文后，才保存真实送达回执。再次读取同一任务确认不重复付费、不重建同一期文章。

截至10/10，这一单轮已经实际执行到初筛失败，原请求及采集结果保留。允许原日期、同payload的幂等检查，预期返回原job且worker为idle；保留原job和冻结输入，不调用retry或改期重发。先依据原响应修正新初筛合同，随后另行有界验收。来源配置、状态根路径、失败任务及旧合同不覆盖。初始字节差异经两端检查仅CRLF/LF，完整内容相同，可保留原字节；有实质内容差异则仍需记录并核对，不能只凭JSON部分字段相同放行。

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

2026-10-10，北京时间：主控按用户“你去测试一下”继续原云端任务。代码`a587897b934013e333f299b4d7db4a8febea631d`，Python3.12.14，状态`/workspace/ai-notes-digest-state-trial-20261008`；真实开始日期2026-10-10、目标日榜2026-10-09。状态配置和提示词与Linux checkout仅CRLF/LF不同，完整内容经两端分别检查相同，原字节保持。当天轮换OpenAI／Google／Show HN成功3失败0，新增20候选／20观察并入库读回；Google窗口外零新增。生成初筛19输入、17有效decision，7个入选ID中两个没有decision，应用以`shortlist must retain one reason for every screened candidate`拒绝。1模型请求／0重试，3,729输入＋2,222输出＝5,951 tokens；不把HTTP/JSON成功算生成成功，不把未完成筛选算零条合格。原output及错误保留，未深核／评分／复核／成文，无新文章或草稿；输出达到预算但没有finish_reason，不断言截断。另有初筛许可理由偏离excluded.v1，后续应纠正执行而不是恢复许可专项核验。

主控下载原完整JSON并核对SHA-256`48d9e593994210f266acf1f2d408f6baf1a6b37442faf56b053151993560efc9`，本地`work/dot-cloud-trial-20261010/cloud-single-trial-receipt.json`，云端Library`libfile_8f354591a6d081919c759fd9fdf7ec4c`。运行后48批／307观察／169入选／22归档／3草稿；云端审计原归档／入选／草稿业务字段及原文章／清单／索引保持。现有ingest自动schema2→3，先备份再写入，未手工迁移；原三个legacy_untracked草稿JSON、smoke账本及旧失败回执保持。11:34同任务新Python进程重复验收通过，同payload复用原job、worker idle、attempts1，计数／请求／usage／原error/result／outbox及808状态文件／89历史文件hash保持。主控下载完整post-readback JSON并直接比较前后快照，Library`libfile_df52523991d08191bd7d483f541a0ee1`，本地同目录`post-readback-final.json`；首次只读诊断查错表发生在再入队前，零动作，原错误保持，改正确查询后完成。不冒充新容器持久化；outbox失败通知pending，不伪造ack。原本地自动化PAUSED，scheduler_calls=0，未恢复或新建定时。详情及证据边界见[实测报告](../DIGEST_DOT_CLOUD_TRIAL_2026-10-10.md)。

2026-10-09 17:38，北京时间：按用户“上传到 GitHub 仓库，然后让 Dot 下载”的直接要求，在公开仓库发布 `digest-backup-20261006` Release（prerelease，非 latest），附件为原 `digest-dot-20261006-234016.zip`，7,722,087 bytes，SHA-256 `b1c91cc78427d40492152fdb8b88befbc63e7dd773d14c77ab9ee23394fedba6`。下载地址：[原 ZIP](https://github.com/qiuC123/ai-notes/releases/download/digest-backup-20261006/digest-dot-20261006-234016.zip)。701 个载荷和 manifest 完整匹配，ZIP CRC、SQLite 均通过，47／287／169／22／3一致；静态检查未发现真实用户凭据或明确无关业务资料，未读取引用的私密文件。GitHub 元数据大小和 digest 一致；主控不带账号凭据实际下载 HTTP 200，逐字节大小及 SHA 相同，检查后移除临时下载副本。原 ZIP 未改。审计、Release 元数据与下载回执保留于忽略目录 `work/digest-github-transfer-20261009/`。

17:40已把当前公开下载地址和核验／恢复要求发给原Dot；Dot起初把旧附件403概括为不应换渠道读取，未执行下载。主控补充用户所有权、直接发布授权及本机无认证HTTP200的实际证据；17:42 Dot核对当前Release上传者、元数据与访问依据后明确修正判断，继续原云端任务。未要求修改旧附件权限或读取其URL。17:45:05实际下载完成，HTTP200、7722087 bytes、SHA匹配，零重试且无Authorization头；verify/restore均exit0，目标先前不存在。

17:48:30最终回执状态 `restored_with_legacy_draft_limitations`：状态目录 `/workspace/ai-notes-digest-state-trial-20261008`，恢复701文件全部与备份匹配，无多余／缺失／修改；实际47 batches、287 observations、169 ranked_selections、22 ranked_issues（日18/周3/月1）、3 daily drafts，旧issues/selections表均0。仅迁移digest.sqlite3，selection/runtime原本缺失且仍未伪造。SQLite integrity／外键检查通过；status、due、history读回成功；22期文章／清单及4个索引通过，history刷新后701文件仍未变化。recover只读检查25期，44个归档导出为ok；3份旧草稿正文为ok，2026-09-10／09-14／09-23三份JSON清单因原库没有manifest文本／预期hash而标为legacy_untracked。它们存在且与备份manifest匹配，未修复或迁移格式，`strict_export_audit_ok=false`不能省略。

主控在网页打开并下载原JSON回执读回上述字段，Library `libfile_3ab3640de974819197b833333cec33c9`，本地保存为 `work/digest-github-transfer-20261009/cloud-history-restore-receipt.json`；云端回执目录 `/workspace/digest-backup-import/github-public-h41gxu69/receipts`。原ZIP与解压备份位于 `/workspace/digest-backup-import/github-public-h41gxu69/`。原smoke账本、三份附件403资料、诊断及初始不下载回执hash保持；本轮model_calls、collection_generation_calls和scheduler_calls均0，automation_changed=false。原任务读取状态通过，`new_container_persistence_verified=false`；未来新容器必须先实读状态，不能因路径相同断言共享。原本地自动化再次实读PAUSED。历史传输与本轮恢复已完成，后续采集生成、状态持续保存与schedule仍待验收。

2026-10-08 21:43，北京时间：原 Dot 附件403后，主控将同一个 ZIP 直接附到现有执行任务，新附件 ID 为 `file_00000000278c81fd849163532a73e4ed`。执行者使用新ID只下载一次，未重试旧链接，仍返回 HTTP403。主控打开 `/workspace/digest-backup-import/receipts/direct-attachment-blocked.json`，读回 `status=blocked`、`download_exit_code=1`、`download_error="library file transfer failed: download failed with HTTP status 403"`、`local_zip_exists=false`、`target_exists=false`、`counts_verified=false`。附件元数据大小与预期相同，但实际 bytes／SHA均为空，解压、verify、restore、status/history及索引检查全部未运行。47／287／169／22／3保持预期值，不能称已迁移。原403回执与模型账本保留；本轮模型、采集生成调用均为0。原 Dot 已自动收到失败摘要，主控没有更改分享权限、公开上传备份或恢复自动化。截图保留于忽略目录 `work/dot-cloud-config-20261008/direct-attachment-403.png` 与 `direct-attachment-403-details.png`。

10/8 尚无已核验的替代私密传输渠道；10/9 用户另行明确授权公开发布同一 ZIP，当前公开入口的本机下载已通过，见上方新记录。不要循环重传附件、猜链接或用空历史代替。取得文件后，`digest_backup verify` 对解压后的备份目录执行，成功才恢复到空目标；restore不会将manifest复制进状态目录。后续先续跑现有任务读回同一root和账本；另开任务不因路径同名就共享状态，持续保存与跨任务读取还需实际验证。原本地自动化保持 PAUSED，尚未保存新schedule。

2026-10-08 21:21:41 北京时间，Dot 新任务“检查智谱最小模型调用”完成唯一模型请求，零重试。实际代码 SHA 为 `a587897b934013e333f299b4d7db4a8febea631d`，Python 3.12.14，使用 `/workspace/ai-notes/.venv/bin/python`，独立 root 为 `/workspace/ai-notes-glm-smoke-20261008`。`model-check` 回报表中参数正确、key_configured=true，无模型 env 文件；主控打开该 root 的 `model-smoke.stdout.json` 与 `model-smoke.execution.json`，读回 `succeeded`、`output={"ok":true}`、`reused=false`、exit_code=0、输入81／输出32／合计113 tokens（reasoning21、cached0）。执行者另回报 runtime status 为1成功请求、error=null、无 jobs/outbox；二进制账本无法直接网页预览，未声称主控独立查过 SQLite。原生任务卡片和“打开聊天”可读；生成的进度文本链接曾显示无权访问，未改变共享权限。

同日21:30，已私密上传历史 ZIP `digest-dot-20261006-234016.zip` 到原 Dot 对话：7,722,087 bytes，SHA-256 `b1c91cc78427d40492152fdb8b88befbc63e7dd773d14c77ab9ee23394fedba6`。单一同名根目录下为701内容文件＋manifest；本地逐 ZIP 成员哈希及实际解压 verify 通过，无 .env／密钥配置。Dot 已将恢复要求交给现有云端任务继续：先核对原 ZIP、verify，再 restore 到 `/workspace/ai-notes-digest-state-trial-20261008`；保留模型账本且不重做 smoke。历史与跨任务状态仍待云端读回，尚未采集生成或保存新定时，原自动化保持暂停。

2026-10-08 21:16 北京时间，主控按用户授权使用 nuphus 操作已有 Chrome 的 `ai-notes` 配置会话。管理环境变量中实际核对表中三个值及环境作用域，原有 `DIGEST_MODEL_API_KEY` 网络密钥绑定保持且值未读取；列表没有 `DIGEST_MODEL_ENV_FILE`。保存草稿后页面显示“所有更改已保存”，随后点击发布，读回“环境已发布”与“已发布”。截图保留在忽略目录 `work/dot-cloud-config-20261008/published.png`，没有上传凭据。内置浏览器的环境列表仍加载失败，但 Chrome 这次保存发布成功；不再沿用此前 `draft_not_editable` 作为当前配置阻塞。原本地自动化现场实读为 PAUSED；本步骤没有模型请求、历史迁移、采集生成或定时切换。云端配置注入及代理替换还须以下一步实际执行回执验收。

2026-10-07 用户后续截图同时显示“环境已发布”和面板底部“已发布”，网络密钥与环境变量仍为空。主控据此更新发布状态，尚未独立读取环境或配置凭据。当前客户端与官方代理密钥机制的匹配仅做源码检查，未执行真实 GLM 请求；历史迁移与定时切换仍未开始。

2026-10-07 用户转述北京时间 22:20 的云端来源连通检查：全部 6 个配置入口返回有效正文，成功 6、失败 0；Google AI News 为 301 → 200，跳转到 `/innovation-and-ai/technology/ai/rss/`，配置未改。转述回执为 `/workspace/ai-notes-connectivity-20261007-222040-y9zl3xrb/results.json`，报告称响应哈希检查通过；主控尚未读取原文件。这只证明入口当时可用，本轮未采集、生成或调用模型，原自动化继续暂停。

本机模型配置检查通过，未调用模型。代码同步、私密备份及本地恢复证据由原 Codex 主控记录于 `TASK.md` 和忽略目录 `work/digest-dot-handoff-20261006/`。备份、云端密钥与原始状态没有上传公开仓库。2026-10-07 用户提供 Dot 接收回复及云端设置阶段截图：设置回复报告 `/workspace/ai-notes`、提交 `91a3117`、Python 3.12.14、`gitingest 0.3.1` 与 76 项相关测试通过，仓库文件无改动；当时草稿尚未发布，该轮未迁移历史、调用模型、采集生成或切换定时。以上为截图中的执行者回报，主控尚未独立读回原始回执；页面控制仍超时，未代为发布。发布状态已由后续截图更新，实际采集生成和新 schedule 的证据仍待补，不另建无关云端任务冒充 Dot。
