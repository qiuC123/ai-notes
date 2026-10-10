# Dot 云端采集与日榜单轮实测

北京时间 2026-10-10，在现有 Dot 云端任务“检查智谱最小模型调用”执行一次采集与一次日榜生成。**采集成功，日榜在初筛校验处失败，单轮验收未通过，未启用生产定时。** 本轮不是新的模型连通测试，也没有重新下载或恢复历史。

## 环境与输入

- 代码：`/workspace/ai-notes`，提交 `a587897b934013e333f299b4d7db4a8febea631d`；Python 3.12.14，解释器 `/workspace/ai-notes/.venv/bin/python`。
- 状态：复用 `/workspace/ai-notes-digest-state-trial-20261008`。开始时按真实 `Asia/Shanghai` 计算一次日期：当天为 2026-10-10，生成期为前一完整自然日 2026-10-09。
- 模型：沿用已发布的智谱普通 API 配置，`glm-5.3-flash`、`low`；原网络密钥保持隐藏，未重做 smoke。
- 策略：`v17-discovery-scope-uncalibrated`，`license_review_scope=excluded.v1`；读者兴趣、来源配置及筛选预算保持。

最初预检发现状态目录的策略与提示词字节哈希不同，暂停在付费调用之前。本机及云端分别确认完整内容仅有 CRLF/LF 换行差异，规范换行后逐字相同；随后保留状态目录原字节继续授权的单轮。没有复制新配置来掩盖差异。

## 采集与历史

当天轮换实际执行 OpenAI News、Google AI News、Show HN 三个入口，成功 3、失败 0，通常回看 48 小时。OpenAI 新增 5、Show HN 新增 15；Google 读取的 20 条均在窗口外，零新增，不计为失败。达到每日最多 20 个新候选后停止。三个入口的读取均有回执；本轮没有宣称覆盖六个来源或整个候选空间。

新增 20 个候选、20 条观察，ingest 后读回确认。以下计数来自云端 SQLite 回执：

| 状态 | 运行前 | 运行后 |
| --- | ---: | ---: |
| 批次 | 47 | 48 |
| 观察 | 287 | 307 |
| 正式入选 | 169 | 169 |
| 归档期数 | 22 | 22 |
| 草稿 | 3 | 3 |

云端逐行检查原归档、入选及草稿业务字段保持；原文章、清单和索引文件保持。既有 ingest 自动把 digest 库从 schema v2 升到 v3，先保存迁移备份，再增加草稿恢复字段及新观察。因此数据库字节变化属于实际迁移与采集，不能写成“数据库完全未变”。旧三份草稿 JSON 仍为 `legacy_untracked`，未追溯修复。原 smoke 账本和全部旧失败回执哈希保持。

## 日榜失败与用量

初筛输入 19 个候选，模型返回 17 条有效、无重复的逐项 decision 和 7 个入选 ID。其中两个入选 ID 没有对应 decision：

- `a43bbd6eb6fc20e0151e42ea`
- `b58251a95774b346606590f9`

程序按覆盖契约拒绝输出，错误为 `shortlist must retain one reason for every screened candidate`。这次不是 reason 字段为空；是模型漏回两项决策。HTTP/JSON 解析成功不代表业务校验或生成成功。

| 指标 | 实际结果 |
| --- | --- |
| 模型请求／付费重试 | 1／0 |
| 输入 tokens | 3,729 |
| 输出 tokens | 2,222，含 reasoning 558 |
| 总 tokens | 5,951 |
| 账单金额 | 未知 |
| 深核、评分、内容复核、成文 | 均未执行 |
| 本期新文章／草稿 | 无 |

19 项对应的输出预算为 `512 + 19 × 90 = 2222`，实际输出恰好达到此值。预算耗尽是可调查线索；账本没有原始 HTTP body 或 `finish_reason`，不能认定发生了截断。原解析后 JSON 输出与错误完整保留，不人工补理由或循环重发。

独立读取原 output 还发现五条理由提及许可或商用，其中多条以许可未核实作为暂缓理由，与本次 `excluded.v1` 范围偏离。后续应纠正初筛对已排除范围的执行，不能修好输出覆盖后就宣布筛选质量全部通过。这不要求重新开展许可证范围专项核验。

## 恢复与运营边界

11:34，新 Python 进程 PID 14082 在原环境、原状态根路径进行免费重复检查，结果通过：同日期、同 payload 再入队分别返回原 collect／generate ID，指定 ID worker 均为 idle，attempts 均仍为1。原采集成功与初筛失败状态不变。

主控下载完整 post-readback JSON，直接比较其中前后快照：48批／307观察／169入选／22归档／3草稿、123个候选、请求1／重试0／5,951 tokens、原error/result及outbox均相同；808个状态文件、89个历史文件与三份配置哈希相同。runtime库存在，selection库未初始化，符合未进入评分阶段的结果；不能把“不存在”说成丢失历史库。免费验收进程记录零网络尝试，未执行retry。

首次只读诊断脚本误查不存在的candidates表，发生在再入队和worker之前，动作0；执行者改用现有status的`COUNT(DISTINCT canonical_url) FROM observations`查询后完成检查，原错误回执保留，未改项目代码。这是检查脚本错误，不是本次日榜生成的失败原因。

此结果仅证明现有任务、现有容器中新的Python进程能继续读取且重复执行不付费；新容器或机器重启后的持久化尚未验证。

失败通知写入 outbox，状态为 pending，没有伪造送达回执。原本地自动化现场实读为 PAUSED；本轮 scheduler 调用为零，没有启用新定时入口。

下一步先修正初筛输出预算、理由长度及排除范围执行，保留完整覆盖校验和原失败账本，再做有界新合同验证。生成、状态保存及真实通知验收通过后，才切换唯一云端定时入口；不能用此轮采集成功代替这些验收。

## 原始证据

主控从原云端任务打开并下载完整 JSON 回执，本机 SHA-256 与云端一致。初次独立审阅直接从解析后 output 重算 17 条 decision、7 个入选及两个漏项。随后 post-readback 完整回执包含原 generate job 的 checkpoints，解码后取得完整 screen-input：19个 records 与19个 cards 的ID集合一致，两个缺失ID均被原模型选中。输入与输出集合现在可以在本机逐项独立重算；数据库逐行保护检查仍依据云端审计，原数据库未另行下载。

- 本地完整回执：`work/dot-cloud-trial-20261010/cloud-single-trial-receipt.json`（忽略目录）。
- 回执时间：2026-10-10 11:23:50，北京时间。
- SHA-256：`48d9e593994210f266acf1f2d408f6baf1a6b37442faf56b053151993560efc9`。
- 云端 Library：`libfile_8f354591a6d081919c759fd9fdf7ec4c`；原回执目录 `/workspace/ai-notes-single-trial-20261010-6ot9arps/receipts`。
- 采集 job：`cd4b52b376e6124000291049305774b7ff0189d15927394790d4c9b6004ffd0b`。
- 生成 job：`ff114c88126f75512d92d29b546592a931a0d8950d7640cf2e4196cc761276e4`。
- 模型 request：`23ef524f5997077f82294f47c486141549abcfd1ee189728e5dbbbe20db102d9`。
- 后续完整回执：`work/dot-cloud-trial-20261010/post-readback-final.json`，808,106 bytes；本机SHA-256 `f45dd37d9d9421da22ce579c33be63d1662172b1f98d1de3878b8fcb6c351dfb`，Library `libfile_df52523991d08191bd7d483f541a0ee1`，云端 `/workspace/ai-notes-post-readback-0sttmkza/receipts/`。
- 本机直接重算输出及快照比较：同目录 `local-output-audit.json`、`local-post-readback-audit.json`；没有另外下载数据库来重做SQL审计。

本组只记录真实云端测试与状态，不改业务代码、评分门槛、来源等级、其他自动化或凭据；没有安装候选软件。

## 完整代码同步与同响应回放

用户随后要求完整项目上传GitHub并让Dot下载，追问为何本机能跑而Dot失败。已实读GitHub main为`ad59fe040b07f4bda2c09cb23ae791e435b19eb1`，Git树`e2a183b896a6c002e49c40897079538475dc815f`，281个跟踪文件。源码、配置、提示词、依赖声明、部署脚本及项目阅读Skill均已跟踪；没有未提交的运行必需源码。Dot实测版本`a587897`到此提交只有记录文档变化，业务代码及配置没有差异。运行历史已在原状态根恢复，模型密钥沿用云端配置；不把本机虚拟环境或凭据加入代码仓库。

主控在本机Python3.12.11用上述原冻结输入与原解析后output调用实际`digest_pipeline._run_screen`，仅将付费请求替换成返回原回执的对象，冻结请求参数逐值匹配；数据库renew与save禁止写入。得到相同错误`shortlist must retain one reason for every screened candidate`，19输入／17决策／同两个漏项，0 API、0数据库写。两次附加统计器的字段路径KeyError单独保存，修正统计路径后读回，不改变输入或原响应。因此此份输出在本机也失败；此前其他候选的本地成功不证明这一轮会通过。重新上传相同业务代码不能修复模型漏决策。

11:47:51，现有Dot任务实际从GitHub fetch main成功；11:48:07仅fast-forward至上述精确提交，均exit0，工作区干净。随后核对全部281个文件存在、字节与Git blob一致、Git树匹配；Python3.12.14、实际模块均来自该checkout，六项CLI帮助与`pip check`通过。主控下载完整回执，将281项文件记录与本地对应提交的Git对象逐项比较，全部一致。

回执前后快照直接比较通过：原48批／307观察／169入选／22归档／3草稿与123候选、全部runtime行、808个状态文件、89个历史文件及21份旧回执保持。原采集completed、生成failed状态不变，本轮模型、采集生成、retry、restore和scheduler调用均0，未读取密钥值或重配凭据。仍是原环境中新Python进程，未验证新容器持久化。云端组装回执时误把records映射当列表的TypeError、主控将前后哈希不同表示直接比较的AssertionError均是只读检查错误，修正读取方式后完成；不改原业务失败账本。

完整下载回执：`work/dot-code-sync-20261010/cloud-code-sync-complete.json`，1,773,773 bytes，SHA-256 `cd396f6c72ed6551e5588d637a1efef3c5dd65b92b10c290c7d45b4944e22254`，与网页交付值一致；本地核对记录`local-code-sync-audit.json`。云端回执工作目录`/workspace/ai-notes-code-sync-m4iuupbm`，状态`code_sync_and_read_only_verification_passed`。已完成完整代码同步；当前运营阻塞仍是初筛模型输出不完整，而非漏传源码。

本机证据保留于`work/dot-code-sync-20261010/`：`local-code-manifest.json`、`local-same-response-replay.json`、下载要求及截图。本机依赖metadata可读，`pip check`因虚拟环境没有pip模块不可执行；未为此安装pip。实际模块加载及离线原函数回放成功，不把该诊断工具缺失写成业务生成故障。
