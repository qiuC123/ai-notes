# TASK — 三榜筛选优化与独立运行

更新时间：2026-10-10；状态：Dot 原云端任务真实单轮已执行，采集成功、日榜初筛失败，尚不能正式自动运营。三个轮换来源成功，新增20候选／20观察并入库读回；2026-10-09日榜输入19项、模型返回17项逐项决策，漏掉两个入选候选，被完整覆盖校验拒绝。1次API、5,951 tokens、零重试，未进入深核、评分或成文，无新文章／草稿。后续新Python进程重复执行检查通过，返回原job且worker idle，不重复付费或入库。原历史业务记录、文章和索引保留；当前48批／307观察／169入选／22归档／3草稿。新容器持久化、持续运行与定时尚未验收；原自动化实读PAUSED，未新建schedule。许可范围排除已实现，但本轮初筛仍有许可理由偏离；不以564项旧离线通过数或模型HTTP成功冒充自动刊用通过。见[云端单轮实测](docs/DIGEST_DOT_CLOUD_TRIAL_2026-10-10.md)；主控Codex。

本次用户调整已落实：三榜重点是发现项目，正文显示名称、用途、代表性亮点、已知支持系统和链接，配图按需；不要求讲全使用条件或限制。固定的读者／泛化使用条件行已移除；新复核只因实际写出的实质事实错误暂缓，不因省略无关细节拒绝。原有错例和测试结果保持。

用户进一步确认：条件信息只保留“支持系统”，如 Windows、macOS、Linux；网页版及移动应用按实际入口／系统标注。系统字段单独绑定原文证据并贯通评分、复核与正文，未知／不适用留空，不从安装说明猜测。

当前范围以 [核对范围调整](docs/DIGEST_REVIEW_SCOPE_2026-10-06.md) 为准；此前 [单理由复核](docs/DIGEST_REASON_SOURCE_REVIEW_2026-10-06.md) 保留原实测数据，许可范围漏判不再作为后续阻断项。

## 目标与验收

完成已授权的《优化三榜流程》方案，优先借鉴 AIHOT 的筛选与打分。保留 Python＋SQLite，日榜看有实际影响的 AI 动态和易用工具，周月榜看值得保留的项目、用法与重要变化；改善筛选质量后，再验收独立后台与云端运行。旧 `CODEX_MVP_TASK.md` 是历史 Release MVP，不是当前入口。

| 验收目标 | 状态与证据 |
| --- | --- |
| 分类评分、理由与证据、候选查询、导出恢复 | 已实现；原文段落绑定和冻结恢复见 [v6 验收](docs/DIGEST_PASSAGE_BINDING_2026-10-04.md)。分数不等于内容正确。 |
| 日报动态＋易用工具，周月成熟内容，真实错例校准 | 规则已落实；7 条选择标签（6 select、1 reject），另有 6 条 B／一般阅读偏好，不能混为同口径金标。未完成代表性校准，不报告整体准确率。 |
| 新材料能经过原文→评分→内容复核，结果可靠 | [v9 编辑定位](docs/DIGEST_EDITORIAL_FIRST_2026-10-05.md)完成：6 核验／5 评分／5 复核，16 请求结构有效。公共价值理由成立，但五次 accept 仍遗漏条件与读取范围错误，自动刊用未通过。 |
| 项目阅读方法与来源范围进入后台 | [v10 项目阅读](docs/DIGEST_PROJECT_READING_2026-10-05.md)已接入：同版本原文补读、用途／输入／操作／输出／条件事实卡、逐资料范围及评分复用；378 测试通过，四旧例实际 10 API。FFmpeg 兼容修复和转语音方向改善，促销范围等仍待解决。 |
| 发现式正文与相应复核标准 | [v11 介绍验收](docs/DIGEST_DISCOVERY_INTRODUCTION_2026-10-05.md)：409 项离线测试通过，6 条格式预览，3 条真实旧冻结评分输入相同。[新材料实测](docs/DIGEST_DISCOVERY_LIVE_TRIAL_2026-10-05.md)：15 API、2 程序选中，独立检查后 1 项直接展示；未通过整体自动刊用或代表性校准。 |
| 产品原文与公开复核范围修正 | [v12 修正](docs/DIGEST_SELECTION_FIX_2026-10-05.md)：模板／开发说明转向用户原文，metadata 单独绑定出处；公开内容及评分理由复核、周榜重点范围检查、旧冻结回执保持。443 测试通过，3 项原文／4 份旧响应离线回放完成，2,673 既有文件保持；没有新模型结论。 |
| v12 真实模型质量 | [10/6 实测](docs/DIGEST_V12_LIVE_TRIAL_2026-10-06.md)：Zotero 取得官网资料且不再被初筛误挡，TagSpaces 引用 ID 合法；四次模型 accept 仍漏掉商业授权范围和评分出处问题。1 完整通过＋3 候选短介绍见[预览](outputs/digest/v12-live-preview-2026-10-06.md)，不是正式榜单。Mindustry 输出漏字段，Ditto 旧帮助页编码失败；新闻只验旧事件周期排除。 |
| v13 新兴趣候选与逐理由核对 | [七项实测](docs/DIGEST_V13_FEEDBACK_PILOT_2026-10-06.md)：7 核验字段完整、2 完整推荐独立通过；4 本地输入阻断、1 评分坏结构／出处错误保持。13 API、零重试。[发现表格](outputs/digest/v13-reader-preview-2026-10-06.md)按原文人工整理，不把七项全部计作自动推荐通过。 |
| v14 输入预算、输出结构与恢复稳定性 | [评分回归](docs/DIGEST_SCORE_INPUT_FIX_2026-10-06.md)：489 项测试通过；5 评分＋5 复核、零重试，全部输入小于 60,000。五条[原模型短介绍](outputs/digest/v14-reader-preview-2026-10-06.md)可展示；四项 select 的完整评分引用仍需暂缓，机器复核漏掉五处问题。 |
| 独立 worker 从初筛运行到本地文章，恢复不重付费 | 真实隔离单期 14 初筛→4 深核尝试→2 原文成功→1 条归档；8 API、0 付费重试，重复 work 为 idle。日榜缺 4 条，尚未验证连续采集、周月和持续调度。 |
| 默认轮换来源的真实采集与幂等恢复 | 已完成一次隔离 worker：三个入口、33 份 fetch 回执、16 个 discovered 候选入库读回；同任务 idle、同批重放 unchanged，零模型调用。见[来源验收](docs/DIGEST_SOURCES_RUNTIME.md)。 |
| 历史迁移与云端持续运行 | 10/9恢复701文件及22期归档通过；10/10采集后48批／307观察／169入选／22归档／3草稿，同任务新Python进程读回与重复idle通过，原历史保护通过。现有ingest自动v2→v3且迁移备份保留，旧3份草稿清单仍legacy_untracked。新容器持久化、长期运行及通知送达尚未验收。 |
| Dot 接手与最新状态准备 | 已发布GLM配置、唯一smoke与GitHub历史恢复通过；[10/10单轮](docs/DIGEST_DOT_CLOUD_TRIAL_2026-10-10.md)采集通过、日榜初筛覆盖失败，1次API／零重试，未启用schedule。修正初筛后继续有界验收，见 [Dot 交接](docs/handoffs/2026-10-06-dot-digest-trial.md)。 |

## 约束与决定

- 遵循 [三榜设计](docs/DIGEST_RANKING_DESIGN.md)、[编辑规则](docs/WEEKLY_PROJECT_DIGEST.md)、[执行契约](docs/WEEKLY_PROJECT_DIGEST_WORKFLOW.md)及[读者反馈](docs/DIGEST_READER_FEEDBACK.md)：八栏目、自然周期、日 5–8／周 20–25／月至少 20、共享池独立筛选、Q12、同级全部历史、24 小时核验与不可覆写归档保持。
- HelloGitHub 只作编辑参考。没有 Star 硬门槛；不推荐纯编译测试和树莓派硬件；CLI/MCP/Skills 不按名字一刀切。原文、作者自述、独立反馈和软件实测明确区分。
- 2026-10-05 按用户截图与直接反馈，采用发现式短介绍：名称、用途、两三个亮点、官方链接，配图按需。取代此前要求逐项讲清条件／限制的正文标准；后台事实卡不用全部展示。核对实际写出的主张，保留明确误述的检查，不把资料未穷尽等同描述错误。
- 原 heartbeat 按用户要求暂停。当前授权允许本地隔离验收；没有撤销暂停，不启动生产定时器、正式榜单、对外发布或消息发送，不改飞书／Pi／共学／旧 Release 管道。
- 模型已配置为智谱官方普通 API `glm-5.3-flash` / `low`，仓库外文件 `C:/Users/Mayn/.config/ai-secrets/glm.env`。请求账本保存实际用量，账单金额未知；不把 API 当作免费或免额度执行。
- 当前权重、65 分推荐及 45 分以下淘汰均为待校准编辑标准，不因样本失败调门槛凑数。人工标签只能来自用户真实反馈，模型审阅不是人工 gold。
- 保留全部旧材料、原始坏回执、人工反馈和生产历史；有界一次请求，不循环抽样。源于操作中断的未知请求明确记录，不误报为供应商故障、不自动重发。
- 每组完成并验证的代码改动提交 Git；实验材料及私密数据保留于忽略目录。
- 2026-10-09 用户直接要求将已有历史 ZIP 上传自己的 GitHub 仓库供 Dot 下载；单份 `digest-dot-20261006-234016.zip` 已作为公开 Release 附件发布，保留原哈希，不加入代码 Git 历史。该明确授权仅适用于此快照，后续备份和密钥仍保持私密。

## 当前工作

- 2026-10-10，用户要求完整项目同步GitHub、让Dot下载并解释本机与云端差异：已确认main为`ad59fe0`、281跟踪文件、树`e2a183b896a6c002e49c40897079538475dc815f`，运行必需源码／配置／依赖声明／部署脚本／项目Skill均已跟踪，Dot原`a587897`与当前版本无业务差异。使用原post-readback完整冻结19项输入与原17项output，本机实际`_run_screen`也重现同一coverage错误，0API／0数据库写。11:47实际fetch、11:48仅fast-forward成功；Dot核对281文件／Git树、checkout导入、Python3.12.14、CLI及pip check通过。主控下载1,773,773-byte完整回执，SHA-256 `cd396f6c72ed6551e5588d637a1efef3c5dd65b92b10c290c7d45b4944e22254`一致，逐项对比281个Git对象及前后runtime、808状态文件／89历史文件／21旧回执，全部保持；原48／307／169／22／3和123候选不变，本轮0API／retry／restore／schedule。检查器字段路径或表示差异错误均另行保留、修正后检查通过，不改变业务失败。证据`work/dot-code-sync-20261010/`及[报告](docs/DIGEST_DOT_CLOUD_TRIAL_2026-10-10.md)。完整代码同步完成，暂停决定保持；新容器持久化仍未验证。本机没有pip模块仅影响本机pip check，实际模块加载和离线回放已成功。
- 2026-10-10，北京时间：用户“你去测试一下”后，复用原云端任务、代码`a587897`及状态`/workspace/ai-notes-digest-state-trial-20261008`，冻结真实当天2026-10-10及日榜期2026-10-09。预检字节差异经本机与云端分别确认为仅CRLF/LF，完整内容相同，保留原字节继续。三个轮换入口成功，20新候选／20观察入库读回；初筛19输入／17决策、两个入选漏decision，程序以`shortlist must retain one reason for every screened candidate`拒绝。1模型请求／0重试，3,729输入＋2,222输出＝5,951 tokens，未深核／评分／复核／成文，无新文章或草稿；不是合格内容零条。原解析后output完整保存，实际输出达到预算但无finish_reason，不认定截断；另有许可理由偏离excluded.v1，不放宽规则。旧归档、入选和草稿业务字段及文件保持，现48／307／169／22／3；ingest现有代码自动schema2→3且先备份。主控已下载并核对完整回执SHA，证据`work/dot-cloud-trial-20261010/`，详见[报告](docs/DIGEST_DOT_CLOUD_TRIAL_2026-10-10.md)。11:34新Python进程免费重复检查通过：同payload原job、worker idle、attempts1，计数／请求／usage／原error/result／outbox及808状态文件／89历史文件hash保持，主控已下载完整回执并直接比较前后快照；首次只读诊断查错表发生在动作前，原错误保留，改正确查询后完成。新容器持久化未验证；outbox为pending，不伪造送达。原自动化PAUSED，scheduler调用0。
- 2026-10-09 17:38，北京时间：已发布 [历史 Release](https://github.com/qiuC123/ai-notes/releases/tag/digest-backup-20261006)，原 ZIP 7,722,087 bytes、SHA-256 `b1c91cc78427d40492152fdb8b88befbc63e7dd773d14c77ab9ee23394fedba6`。静态检查全部成员及SQLite文本，未发现真实用户凭据或明确无关业务资料；701载荷逐项哈希、CRC、SQLite及47／287／169／22／3一致。本机无认证下载HTTP200并核对大小／哈希，临时副本已移除；17:40发原Dot，17:42经当前公开资源证据核对后继续原任务。17:48:30云端回执为 `restored_with_legacy_draft_limitations`：下载HTTP200、零重试、无Authorization；verify/restore exit0，701文件逐项匹配，目标 `/workspace/ai-notes-digest-state-trial-20261008`，47批／287观察／169入选／22归档（日18/周3/月1）／3日草稿，SQLite integrity与外键检查通过。22期文章／清单及4个索引通过，3份旧草稿JSON缺原库预期hash，标记legacy_untracked并原样保留，strict_export_audit_ok=false，不迁移格式。主控网页打开并下载原JSON读回，保存为 `work/digest-github-transfer-20261009/cloud-history-restore-receipt.json`；云端回执目录 `/workspace/digest-backup-import/github-public-h41gxu69/receipts`，Library `libfile_3ab3640de974819197b833333cec33c9`。smoke账本及全部旧失败回执哈希保持；本轮模型、采集生成及scheduler均0。原任务状态读回通过，新容器持久化未验证；本地原自动化再次实读PAUSED。
- 2026-10-08 21:43，北京时间：原 Dot 附件下载 HTTP 403 后，主控将同一个已核验 ZIP 直接附到“检查智谱最小模型调用”现有任务，使用新文件 ID 再执行一次合法下载，仍 HTTP 403。主控在网页打开 `/workspace/digest-backup-import/receipts/direct-attachment-blocked.json`：`status=blocked`、`new_attachment_download_attempts=1`、`old_attachment_download_attempts_this_round=0`、`download_exit_code=1`，错误为 `library file transfer failed: download failed with HTTP status 403`；`local_zip_exists=false`、`target_exists=false`、`counts_verified=false`，模型及采集生成调用均为0，smoke 账本保持。解压、verify、restore、历史读回均未执行；47／287／169／22／3只是预期计数。原 Dot 已自动收到任务失败摘要。失败截图保留在 `work/dot-cloud-config-20261008/`；未改分享权限或上传私有数据到 Git。本地自动化文件再次实读为 PAUSED。下一步需解决附件合法下载或取得现有私密传输渠道；仅指定同名 `/workspace` 路径不能证明新任务共享状态。
- 2026-10-08 21:21:41 北京时间，Dot 的“检查智谱最小模型调用”任务实际完成唯一 smoke：`status=succeeded`、`output={"ok":true}`、`reused=false`，输入81／输出32／合计113 tokens（reasoning21、cached0），零重试。主控打开 `/workspace/ai-notes-glm-smoke-20261008/model-smoke.stdout.json` 与 `model-smoke.execution.json`，读回以上字段和命令 exit_code=0；直接二进制账本不支持网页预览，其成功请求数、error=null、无 jobs/outbox 为执行者的 runtime status 回报。代码 SHA `a587897b934013e333f299b4d7db4a8febea631d`、Python 3.12.14、解释器 `/workspace/ai-notes/.venv/bin/python`；云端使用普通环境变量与网络密钥，未使用模型 env 文件。原生成进度文本链接打开曾显示无权访问；原生任务卡片与“打开聊天”可正常读结果，没有改共享权限。历史 ZIP 7,722,087 bytes，SHA-256 `b1c91cc78427d40492152fdb8b88befbc63e7dd773d14c77ab9ee23394fedba6`，本地逐成员与解压 verify 均通过，21:30 已上传原 Dot 私密对话并下发恢复；后续两次下载403的结果见上一条，云端历史尚未恢复。
- 2026-10-08 主控按用户授权操作网页：内置浏览器的环境列表重试仍显示“无法加载已保存的环境”，随后切换用户已有 Chrome 配置会话。管理环境变量中实际核对 endpoint `https://open.bigmodel.cn/api/paas/v4`、model `glm-5.3-flash`、reasoning `low`；三项均为环境作用域。现有 `DIGEST_MODEL_API_KEY` 网络密钥绑定可见但值保持隐藏，环境变量列表没有 `DIGEST_MODEL_ENV_FILE`。保存草稿后显示“所有更改已保存”，点击发布后读回“环境已发布”与面板“已发布”；截图保留在忽略目录 `work/dot-cloud-config-20261008/published.png`。联网范围及私有权限保持现值。本地旧自动化文件实读为 PAUSED；本步骤没有发模型 HTTP、迁移历史或启动生产定时。
- 2026-10-07 用户后续截图显示联网范围为“全部（不受限制）”、所有更改已保存，发布按钮可用；转述北京时间 22:20 的实际来源连通检查：OpenAI、Google、Show HN、Hugging Face Spaces／Models／Blog 共 6 成功、0 失败，正文结构有效。Google 301 跳转后的 RSS 可用，配置未改。转述回执位于云端 `/workspace/ai-notes-connectivity-20261007-222040-y9zl3xrb/results.json`，报告称响应哈希检查通过；主控未独立读取该文件。仅证明入口当时可用，未采集入库、生成、调用模型或迁移历史。下一步发布现有环境，原自动化继续暂停。
- 2026-10-06 用户确认 Dot 交接及云端单轮试运行，验收通过后配置唯一的新定时入口。已制作可执行交接说明；私密备份 `E:/private-backups/digest-dot-20261006-234016`，本地恢复 `E:/private-digest-state-dot-20261006-234016`，701 文件／38,236,289 bytes，22 期（日18／周3／月1）、169 入选、3 草稿、47 批次、287 观察读回一致。生产仅有 digest 库，selection/runtime 缺失已保留，不伪造旧账本。证据 `work/digest-dot-handoff-20261006/state-preparation.json` 和 `preparation-verification.json`。本机模型检查通过，零付费 HTTP。2026-10-07 用户截图确认 Dot 已接收；后续环境设置回复报告仓库 `/workspace/ai-notes`、提交 `91a3117`、Python 3.12.14、`gitingest 0.3.1` 与 76 项相关测试通过，仓库文件无改动。安装脚本／启动说明已存草稿，后续联网范围已保存、来源入口检查通过；环境尚未发布，原始测试回执未独立核对。主控页面绑定仍超时，未代为发布。下一步发布现有环境，不重复创建环境或投递接手任务。原 heartbeat 不恢复，私密备份及密钥不上传公开 Git。
- 2026-10-06 用户明确许可范围“这个不需要核对”：新增冻结 `license_review_scope=excluded.v1`，新提取／评分／复核沿用，介绍与评分理由不主动写许可证适用或商用结论。用途、功能、系统和实际版本／事件继续核对，混合句不整句免检。旧冻结请求、原评分与审计保留，默认逐理由 gate 不自动开启，自动化继续暂停；本轮零真实 API，564 项三榜离线测试通过，见[核对范围调整](docs/DIGEST_REVIEW_SCOPE_2026-10-06.md)。
- 2026-10-06 逐理由复核完成：[结果与证据](docs/DIGEST_REASON_SOURCE_REVIEW_2026-10-06.md)。`own-refs.v1` 独立隔离原引用；七次实际检查抓到主要旧问题 3/5，但四份因文字复制无效、其中一份为正例误拦。`own-refs.v2` 改由程序绑定完整原句 ID，代码提交 `63a2357`，549 测试通过。三个新合同实际检查结构全有效：rclone 范围问题正确暂缓、Basic Memory 正例通过；Czkawka 总理由仍误认整体 MIT 范围，稳定性表述也缺据。两阶段十次 HTTP、60,341 tokens、零重试，金额未知；初始 recorder 本地错误零 HTTP 另存。原评分、介绍、原回执与生产／暂停状态保持，默认配置未开启门禁，不报整体质量通过。
- 2026-10-06 用户确认的评分输入与 Joplin 回归已完成：[v14 结果](docs/DIGEST_SCORE_INPUT_FIX_2026-10-06.md)。代码提交 `32ba824`、`d34e7fb`；显式 compact-schema.v1 合并全等节点，固定新合同保存／恢复顺序，保持旧请求、原文及 60,000 字符上限。489 项测试通过；新隔离实测复用五项原资料，5 评分＋5 复核、零重试，164,405 tokens。五项结构有效、Joplin 额外字段消失；五项短介绍独立通过，但 Basic Memory／rclone／Czkawka／Joplin 四项程序推荐仍有五处评分出处或范围问题，机器全部 accept 未拦住。161 项运行记录检查通过，4,323 既有文件、原核验时间、183 来源 hash 与冻结代码保持，账单金额未知。首份零 API 冻结材料、旧错例和原响应均保留，无正式榜单、标签或生产修改，自动化继续 PAUSED。
- 2026-10-06 [协作偏好补充](docs/DIGEST_READER_FEEDBACK.md)：用户明确跨 Agent 主要用外部 Agent，优先复用官方订阅与登录；`reader_context` 已细化，新任务冻结载入。七项清单整体“还可以”另存[原话反馈](outputs/digest/v13-reader-feedback-2026-10-06.json)，立即实践意愿未知，不转 A/B/C 或 select。原预览、请求、分数、审核和文件整理／知识库兴趣保持，无模型调用。5 项背景冻结／隔离／旧请求恢复测试通过，配置仅 `reader_context` 改动，3,978 项既有文件哈希保持。
- 2026-10-06 用户确认执行：[v13 原文与证据指导](docs/DIGEST_EVIDENCE_FOCUS_2026-10-06.md)已提交 `5c1ada2`，467 项相关测试通过。随后[七项新候选实测](docs/DIGEST_V13_FEEDBACK_PILOT_2026-10-06.md)完成：13 实际 HTTP、零付费重试、215,311 tokens；7 核验必填字段完整。AionUi 68、PeaZip 65 均 select／accept 且实际重点和逐理由引用独立通过；Basic Memory／PAL／Czkawka／rclone 在评分发送前超过 60,000 字符上限，Joplin 多余字段及评分出处错误保留。七项[用途／亮点／系统／链接表格](outputs/digest/v13-reader-preview-2026-10-06.md)按原文人工整理，45 份引用读回通过；AionUi 供文使用同 commit 人工定向摘录，另读系统资料未送模型。未改变原响应、分数、权重、来源等级或生产暂停决定，未新增真实偏好标签，不报整体校准通过。
- 2026-10-06 [新增读者兴趣](docs/DIGEST_READER_FEEDBACK.md)：用户确认已有 WorkBuddy / Codex 订阅，关注多 Agent 协作、文件整理与压缩，以及整理已做项目供后续 AI 复用的知识库；Syncthing、TagSpaces、Kopia 获正向兴趣评价。反馈与现有 `reader_context` 配置同步，后续新任务冻结载入，旧请求与原审计保持；不自动填正式选择标签，不调权重或门槛。本组只记录偏好和验证加载，未做新模型实验、软件安装或知识库实施，自动化仍按原决定暂停。
- [v12 真实实测](docs/DIGEST_V12_LIVE_TRIAL_2026-10-06.md)：本轮 5 个已见开发回归项目＋1 个新游戏，真实重读 21 份原生供文；历史 Claude 新闻在调用模型前按原始日期排除。6 深核、4 核验、4 评分及公开复核；Zotero 75、Syncthing 72、TagSpaces 70、Kopia 65 均原生 select/accept。独立核对只有 Zotero 完整通过；TagSpaces 的实际重点商业授权范围错误，Syncthing 实质评分加入供文未支持的架构判断，Kopia 部分评分只引 README 而相关事实在 API metadata。三项短 summary 与系统有据，但不计完整推荐验收通过。Mindustry 漏 understanding.output，Ditto 两页 Help 解码失败，原响应/缺口不补写。15 真实 HTTP、零重试、222,490 tokens；原文/回执/配置读回一致，3,683 既有文件及生产保持，自动化 PAUSED。未改业务代码、重跑历史 443 测试、安装软件、生成正式榜单或新增人工标签。
- [v12 筛选修正](docs/DIGEST_SELECTION_FIX_2026-10-05.md)：用户确认后采用显式新合同，初筛不要求发现资料已穷尽；模板／开发 README 定向补用户文档，当前仓库简介有独立 API 引文，不倒灌历史版本。公开字段及实质评分理由单独复核，内部阅读范围问题保存；周榜按分数安排重点，在一次复核前确定实际展开文字。引用 ID 指导加强而原 binder 不放宽。3 项旧原文回放包含 8 份供文及 4 个缓存缺口，4 份旧评分／复核输入逐值相同；v11 原始错例、分数、结论保持。没有新 API、生产写入或自动化恢复。
- [v11 新材料实测](docs/DIGEST_DISCOVERY_LIVE_TRIAL_2026-10-05.md)：用户认可发现式介绍样式并授权小批实际模型验证；样式反馈不是选择金标。隔离读取此前未测试的 7 项、20 份供文，6 深核、4 评分／复核，Flow Launcher 71/select、Kopia 72/select、Vane 64/defer；Syncthing 内部许可范围冲突导致 editorial defer，未记录 selection。TagSpaces 引用 ID 结构失败，Ditto 取到旧 fork 模板而缺根本用途证据，Zotero 未深核。15 实际 HTTP、零重试、207,169 tokens；独立检查公开／内部字段分别记录，Kopia 增量备份有官方 metadata，但绑定 README 引文有缺口，不报功能证伪。[预览](outputs/digest/v11-live-preview-2026-10-05.md)展示 1 条完整绑定的原模型介绍及全部去向，没有手改回执补成功。冻结代码／来源、2,352 个既有文件及生产库保持；未生成正式榜单、补采或恢复自动化，无新人工标签。
- [v11 发现式介绍](docs/DIGEST_DISCOVERY_INTRODUCTION_2026-10-05.md)：显式 `introduction_contract=discovery.v1` 只用于新任务；系统信息从原文核验直接传递，未知不补造。正式日周月沿用原周期、数量、证据、24 小时核验和历史去重规则；旧任务保持冻结合同及原始回执。六条[格式预览](outputs/digest/v11-discovery-preview-2026-10-05.md)仅复用 10/4–10/5 保存资料，不是新筛选或正式榜单。该离线实现组核对 7 份原文、20 段引用，2,343 项既有生产／历史文件保持；零 API、无新分数或人工标签，与后续实测分开记录。
- [v10 项目阅读](docs/DIGEST_PROJECT_READING_2026-10-05.md)：项目 Skill 保存定向阅读方法，Python 后台落实；Gitingest 0.3.1 只整理已取得文件。真实读取 Requests 四份同 commit 文档，完整／摘录与时间／哈希读回一致。不同候选的原文隔离；明确 release／commit 事件不混入默认分支资料；旧冻结任务不升级或重付费。
- 四个已知旧错例开发回归：ScreenToGif 63/defer＋gate accept，SumatraPDF 68/select＋gate accept，Firefly 60/defer＋gate defer，ElevenReader 原时间被降精度，程序拒绝后未评分。4 核验／3 评分／3 复核，10 实际 API、零重试、90,723 tokens，账单未知。FFmpeg 兼容性修复、许可证摘录和文本转语音方向改善；促销模型范围仍遗漏，不能把结构有效当内容可靠。保留原回执、不补标签、不调整门槛；2,240 个既有文件与生产库保持，自动化 PAUSED。

- v7 配置区分日榜介绍价值、周月保留价值和普通非 AI 工具价值；一次内容复核只接受或暂缓，不能改事实或分数。超额条目保留材料继续其他候选；全部输入、响应、策略与来源可追溯，旧合同不自动升级。
- 真实证据统一在 `work/digest-v7-check-20261004/`：`known-gate` 为旧四例开发回归；`fresh` 为六项新原文；`fresh-run` 为新材料阶段结果；`worker-sandbox` 为保留全部历史的数据库备份沙箱；不写生产。
- 新实验共 14 次请求、13 返回、1 次 Claude 核验因操作中断结果未知（保留 pending，不重发）。新的 source-refs 投影已无损去重，仅推进从未发出的阶段；Calibre 69/select＋gate accept，Audacity 67/select＋gate defer，James Clear 64/defer＋gate accept；两份评分坏结构保留拒绝。方法文章的实验条件错误被 gate 漏报；Calibre 的仓库许可证全文与模型所见摘录需区分，不据此认定许可事实错误。已知 111,836 tokens，未知请求及账单另待核实。
- [6 项读者偏好清单](outputs/digest/v7-reader-review-2026-10-04.md)保留原展示；用户在 2026-10-05 明确全部 B／一般：“看了感觉还行，但不会想马上动手去实践”。[反馈](outputs/digest/v7-reader-feedback-2026-10-05.json)已保存读回，不自动映射正式选择标签，原队列／分组保持。云端目标／已配置连接仍未收到。
- [计划完成度审计](work/digest-v7-check-20261004/plan-audit.md)已与完整规划回答核对。原方案的“实现、真实质量、本地独立执行、云端运行、通知送达”分别验收，不合并报完成。
- 补齐真实默认采集验收，材料位于 `work/digest-collection-acceptance-20261004/`。来源配置保持；Google 因 20 条均超出 48 小时窗口而零新增，不能误报失败。隔离库增加 1 批／16 观察，生产和旧材料未变；没有恢复定时器或进行新一轮模型试评。
- 采集独立审计已完成：16 条来源日期、原始字节哈希、规范身份、发现状态及库内载荷逐项一致，报告为上述目录的 `independent-audit.md`。当前无运行中的采集或模型实验需要等待；六条新偏好已经另存反馈，部署目标／连接仍未收到。
- v8 加入 `reader_context`，新初筛在请求前冻结 policy/prompt 与候选，评分及复核继承；旧 preparation 保持，旧 screen 无个人背景不补入，旧初筛回执无可恢复输入时在联网前停止。120 项相关测试通过，30 条旧冻结投影和指纹相同；权重／门槛／caps 不变。
- 四项 `weekly/project` 共用核验事实对照 v7/v8，OpenShot 70→68、Node-RED 65→64、FreshRSS 55→56、Stellarium 72→66；四次 gate 全 accept，独立核对仍见实质错误，未通过质量验收。16 请求／零重试／144,183 tokens，账单未知。证据在 `work/digest-v8-check-20261005/`，1,722 项旧／生产文件保持。
- 六项 B 后续改用开发参考，原反馈与分组保留，[使用记录](outputs/digest/v8-calibration-use-2026-10-05.json)说明用途，没有把偏好改成选择金标。已准备[新四项阅读对照](outputs/digest/v8-reader-review-2026-10-05.md)并询问真实兴趣，尚未取得新人工标签。
- 用户明确确认“我的确没有树莓派这个硬件”，从本次起加入新任务背景，旧冻结请求和当时依据不足的审计保持。此前停止在单轮验收是执行停顿，不是该硬件事实造成的阻塞；本地修正和隔离验证继续，不等待新的喜好标签或云端连接。
- [v8.1 修正与开发回归](docs/DIGEST_CONFIRMED_HARDWARE_2026-10-05.md)：65 selection/projection＋16 editorial 测试通过；4 新请求／零重试／23,429 tokens，两个修正介绍未误拦，但两个旧错误仍 accept。不重复评分或自动补标签；1882 项旧／生产文件保持、自动化现场状态 PAUSED。单靠补充文字尚未解决复核漏报，继续改进核对路径，不再用待补偏好解释本地停顿。
- v9 将公共编辑定位冻结到新初筛、评分和复核；`value`/`interest` 先看栏目读者价值，个人背景仅用于明确排除、必需条件与条件式关联。五维、权重、65／45 和 caps 不变。旧规则有权威快照才恢复；无法恢复的旧 screen 联网前停止，不能用删新字段后的最新规则替换旧规则。另支持无歧义原文 publication-date 数字日期，保留日精度。
- [六项新预览](outputs/digest/v9-reader-preview-2026-10-05.md)：ScreenToGif 67/select、SumatraPDF 69/select、Firefly 64/defer、ElevenReader 61/defer、Forte 64/defer；Flameshot 缺许可证证据，未评分，不算验证了维护压噪声。16 API／零重试／118,558 tokens，五次 gate accept 仍漏报。原文、冻结请求和独立审计保存在 `work/digest-v9-editorial-20261005/`，1,910 项旧／生产文件不变，无新偏好或人工选择标签。

## 验证与历史证据

- 10/6 v13 实测：独立运行核对 13 份 HTTP 原始字节／回执／usage／账本一致，4 次本地超预算为零评分 HTTP；167 份来源、16 份业务文件与 runner 冻结保持，3,978 项既有生产／实验／自动化文件未变。独立内容核对 7 原 summary 及两项完整推荐；Joplin 每理由出处缺口保持，不用整个包的别处事实补当前引用。`work/digest-v13-feedback-pilot-20261006/` 保存原 summary 与后续独立审核；自动化 PAUSED，未安装软件或形成正式榜单。
- 10/6 v12 实测：15 份 raw HTTP 状态/字节哈希/供应商 usage 与本轮账本一致，实际 endpoint/model/reasoning 与冻结一致；151 来源文件、16 业务/策略/提示词文件、runner 与 sandbox 副本保持。独立 source/公开内容/评分依据审阅完成，3,683 项旧／生产文件未变；本组只提交报告、预览清单与任务状态，不把历史 443 离线通过数算作本轮模型质量验证。证据在 `work/digest-v12-live-20261006/`，三项短介绍待推荐依据核对，原始错误和四次 accept 保留。
- v12 本地修正：443 项三榜离线测试通过（含准确周榜重点范围、缺省旧请求、坏 ID 不修、恢复不重发），Skill 与差异检查通过；3 项来源／4 份旧评分和复核回放，权重／门槛不变。2,673 个既有文件及生产库哈希保持，自动化 PAUSED；本组零真实 HTTP／模型 API。细节在 [v12 报告](docs/DIGEST_SELECTION_FIX_2026-10-05.md)，原文缺口真实保留，不能据模拟结果认定新模型筛选质量已通过。
- v11 新材料实测：15 份实际请求 endpoint／model／reasoning 与冻结一致、HTTP 回应与账本 usage 一致；20 份供文原始字节和范围独立核对，原模型文件及公开展示字段读回一致。保护检查 2,352 旧／生产文件无变化、自动化 PAUSED。报告／预览清单变化，未重跑前一组 409 离线测试。证据在 `work/digest-v11-live-20261005/`；不报告盲测准确率或软件亲测。
- v11 三榜相关 409 项离线测试通过，Skill 结构校验及 `git diff --check` 通过。3 条 v10 真实旧冻结评分投影逐值相同；新系统字段证据绑定、为空不猜测、归档正文、恢复不重发及私有预览隔离已检查。证据在 `work/digest-v11-discovery-20261005/`。不据模拟模型或手工预览声称真实模型理解质量通过。
- v10 三榜相关 378 项测试通过，Skill 校验通过；独立代码复核关闭抓取时间和更新版本路由两项问题。真实模型回归与原文审阅分别记录于 `work/digest-v10-reading-20261005/`，不据已知四例声称盲测准确率、校准或自动刊用通过。
- v9 相关 113 测试通过（47 selection、7 projection、42 pipeline、17 editorial），8 个真实旧冻结评分输入逐值与哈希一致；没有重跑全仓或新旧策略 A/B。代码集成审阅与新材料独立原文审阅已完成，结果与边界见 v9 报告。
- v7 历史验证：全套 300 用例中 299 通过，一个旧投影断言修订为无损还原后单独重跑通过；runtime 含真实 `-m` 子进程。旧 24 条投影指纹及当轮 4 张旧合同评分输入不变，30 条补充引用可逐字还原。独立代码与内容审阅已完成，具体适用范围和失败见 v7 报告。
- v7 旧例：4 次 API、0 重试、4/4 格式合法，只有 Kdenlive 延期，另外三项存在漏报；26,091 tokens，账单未知。这是已见开发样本，不是盲测准确率。
- 生产基线为 47 批次、287 观察、22 期归档；本轮保护清单含 488 个旧／生产文件，核对生产 SHA-256 为 `966af2a3119893f719a6c220c0825fe03cf1f98781eb19d25d63d5caa4c69748`。收尾读回结果保存在 v7 实验目录。
- 历史结果保持：GLM/Jev 30 卡对照及[人工方法](docs/DIGEST_MODEL_BENCHMARK.md)；[试刊](docs/DIGEST_READER_PILOT_2026-10-04.md)；[v3](docs/DIGEST_V3_TRIAL_2026-10-04.md)、[v4](docs/DIGEST_EVIDENCE_SCOPE_FIX_2026-10-04.md)、[v5](docs/DIGEST_SCOPED_SOURCE_FIX_2026-10-04.md)、[v6](docs/DIGEST_PASSAGE_BINDING_2026-10-04.md)。此前 TASK 的逐轮记录保留于 Git `ceff990:TASK.md`，没有追溯改写。
- 旧测试临时目录 `C:/Users/Mayn/AppData/Local/Temp/tmpb4yilkde` 和本次 `E:/private-backups/ai-notes-digest-restore-check-20261004-225952` 的清理被自动审批策略拒绝，均保留，未绕过。私密备份位于相邻 `ai-notes-digest-20261004-225952`。

## 下一步

GLM配置、唯一smoke、历史恢复及10/10云端采集已通过，不重复smoke、下载或覆盖状态。实际日榜失败停在初筛；下一步依据保留的原响应修正输出预算／理由长度和已排除许可范围的执行，保留完整覆盖校验，不人工补漏或直接retry旧请求。随后有界验证新合同的真实完整输出及生成结果。继续复用 `/workspace/ai-notes-digest-state-trial-20261008`，分别验收新容器状态持续保存和真实通知，全部通过后才保存唯一新定时入口，详见[实测报告](docs/DIGEST_DOT_CLOUD_TRIAL_2026-10-10.md)和[云端交接](docs/handoffs/2026-10-06-dot-digest-trial.md)。原自动化继续暂停，既有失败账本、候选和历史保留。采用云端注入变量时不设置 `DIGEST_MODEL_ENV_FILE`，不传 `--model-env-file`。不重复创建环境、索取对话链接或重新确认已授权的试运行；原Codex保留交接入口。

在新试运行中，用未见过的资料判断用途、亮点、系统及读者价值，不继续许可证组件范围专项修复；其余实质事实、新闻／游戏与代表性筛选校准保持。外部 Agent、订阅复用、文件整理及知识库偏好沿用。旧生产调度继续暂停；不要求在聊天发送密钥。未知 Claude 请求保持原账本，不能重发。仍不能宣称整体筛选质量、持续运行、云端和通知送达已完成。
