# 单条评分理由的来源隔离复核（2026-10-06）

已实现逐理由原引用隔离与程序绑定句子 ID，549 项完整三榜回归通过。七次 v1 实测后，三个 v2 新合同检查修复了云盘覆盖范围漏报和正例格式误拦，但整体许可范围仍漏报，另有稳定性表述缺据。因此新门禁尚不适合默认开启，自动化保持 PAUSED。正文仍采用用途、亮点、已知系统和链接的发现式介绍。

## 可选合同与恢复

- `own-refs.v1` 保持独立：一条原始理由及其原始引用对应的全部已捕获段落；原文片段复制必须完整覆盖。旧提示词、形状和已冻结请求保持。
- `own-refs.v2` 复用同一来源隔离，只由程序预先分句并给出稳定 `statement_id`。完整句子串接等于原理由，标点、空白、引用不变；模型按 ID 返回，每句必须且仅核对一次，不要求复制原文。
- 每条只收到自身来源；其他字段的分数、简介、理解卡、审计期望均不作为产品证据。缺少自身引用的资料时零调用暂缓。precheck 共享引用的原句作为一组，五个维度与各 flag 分开。
- 总理由原来没有自己的 refs，明确标为组件原引用的派生支撑，最后核对；任一组件失败先阻断，不能由总理由反向补证。
- `supported` 与 `scope_conflict` 需要本条段落 ID；`accept` 只能含支持或合理编辑判断，`defer` 必须有具体不支持／范围冲突。错 ID、遗漏、额外字段或矛盾判决均保存无效响应，不修补、不重新抽样。结构通过仍不证明理解正确。
- 新门禁只在原公开复核通过、程序原生判为 select 后、正式记录前执行；第一条失败即停止本候选。卡片、原文、原评分回执、合同和单条 unit 绑定请求指纹。输入、原响应、结果分开保存；已付成功请求恢复复用，未知请求不重发。
- 两个合同均显式可选，并要求现有 v14 完整依赖。没有 marker 的流程保留原预算与评分输入；门禁 marker 不进入模型评分材料。新合同提示按来源的主体、量词和许可文件名限定范围，不加入真实项目名或审计答案。

## 请求预算

每项最多十三条：precheck 一组、五维、最多六个 flag、总理由，遇首个失败停止。仅显式 marker 使用 `1 + deep_limit × (3 + 13)` 作业硬上限，日／周／月 193／561／961；旧合同 37／106／181 保持。数字为最坏上限，来自冻结政策，非每期实际调用量。60,000 字符输入限制保持。

本轮实验各自冻结上限：v1 七次，v2 三次。均复用保存的 v14 原理由、分数及原文，不重新评分、重新抓取或运行正式榜单。期望保存在模型输入之外。

## 第一阶段：七次实际模型检查

模型：智谱官方普通 API，`glm-5.3-flash`，reasoning `low`。

| 检查 | 原模型判断／程序结果 | 独立内容结论 |
| --- | --- | --- |
| Basic Memory interest | defer／合法 defer | 识别自身引用缺用途支撑；部分附带判断仍不够准确 |
| rclone usability | defer／合法 defer | 识别 MIT 许可不在自己的引用资料中 |
| rclone interest | accept／漏覆盖导致无效 defer | 云盘覆盖范围仍误判，不能将格式拦截算理解正确 |
| Joplin usability | defer／片段改写导致无效 defer | 识别同步／云端内容不由该安装引用支持 |
| Czkawka 总理由 | defer／用了组件文字导致无效 defer | 整体 MIT 范围仍未正确判断，负项混入另一组件文字 |
| Basic Memory value（正例） | accept／漏覆盖导致无效 defer | 内容可接受，被复制格式误拦 |
| Joplin interest（正例） | accept／合法 accept | 内容与原文相符 |

七次真实 HTTP，零重试；prompt 36,081、completion 6,615，共 42,696 tokens，实际账单金额未知。机械运行审计确认原始 HTTP、receipt、SQLite、checkpoint 一致；4,481 个保护文件及冻结资料未改变。输出结构合法 3/7；旧问题实质识别 3/5，两个正例内容均可接受但只有一个原生通过。这些是已见错例的开发回归，不是盲测准确率、用户金标或整体质量验收。

最初 recorder 在保存请求路径时触发本地断言，发生在底层 HTTP 前，真实请求为零；原失败目录及七条 pending 行完整保留。它的原 summary 中 `actual_requests=7` 是 ledger 数量，不能作为真实网络数。修复后的 recorder 在新目录先以 MockTransport 验证，再进行上述七次实际调用。

## 第二阶段：句子 ID 修正

v2 实现已完成；549 项完整三榜回归通过，新增 17 项句子绑定与 6 项 pipeline 接入检查。独立代码复核另跑 60 项相关测试通过，未发现阻断问题；这些检查证明执行边界，不证明模型理解。代码已提交 `63a2357` 后冻结，完成恰好三个新合同检查，全部结构有效，零重试。

| 新合同检查 | 原模型／程序结果 | 独立内容结论 |
| --- | --- | --- |
| rclone interest | 合法 defer | 正确区分“支持的存储系统均可用这些命令”与“几乎所有常见云盘”，原范围夸大被识别 |
| Czkawka 总理由 | 合法 accept | 仍将 MIT 正文完整误当整体应用的许可支持；文件名明确为 Cargo app/library 以外内容，实际供文还有各前端许可导航，但适用归属未核实；另遗漏“稳定性仅为作者自述”缺证据 |
| Basic Memory value（正例） | 合法 accept | 全部句子 ID 正常覆盖；原文支持 Markdown 人机共同读写、跨会话保留知识、语义搜索与知识图谱，不再因漏复制开头误拦 |

独立运行审计确认 HTTP 原始字节、receipt、SQLite、九个 checkpoint 与汇总一致；独立内容审计逐句核对八条 statement，三个输出结构均合法，但两个目标问题只实质识别一个，正例有据并原生通过。

v2 三次真实 HTTP：prompt 16,065、completion 1,580，共 17,645 tokens；金额未知。4,547 个保护文件保持，七份既有响应和原审计不改写。Czkawka 结论仅表示这条总理由的整体 MIT 归属缺据，不断言 Krokiet 的实际许可证。模型误用 `editorial_judgment` 掩盖其中的许可事实，结构合法无法代替范围核对。

两阶段合计十次真实 HTTP、60,341 tokens，初始 recorder 本地失败另记为零 HTTP。三个新合同检查属于已见资料的开发验证，不合并为“总体准确率”，不构成新材料质量或代表性评分校准。没有正式榜单、生产通知、重新评分、来源补采或恢复自动化。

## 剩余问题与下一步

范围与事实判断仍有已证实的漏报，新门禁继续保持显式实验选项。下一项应把带范围限定的产品事实单独核对，避免与编辑评价混在一起获得放行；随后用未见过的材料验证漏报及误拦，再决定是否默认启用。不通过手改原响应、提高预算或反复抽样凑成功。

## 可审查证据

- 实现：`src/ai_notes/digest_reason_review.py`（v1）、`src/ai_notes/digest_reason_statements.py`（v2）、pipeline／selection 可选接入。
- 测试：`tests/test_digest_reason_review.py`、`tests/test_digest_reason_statements.py`、`tests/test_digest_reason_pipeline.py`。
- v1 本地及 recorder 原失败：`work/digest-v15-own-refs-20261006/`；526 项完整三榜回归通过。
- v1 七次真实调用：`work/digest-v15-own-refs-live-20261006/`，含原始字节、summary、runtime-audit、content-audit。
- v2 三例：`work/digest-v16-statement-ids-20261006/`，含独立冻结 manifest、runner、MockTransport preflight、原始 HTTP、receipt、SQLite、summary 和独立审计。
- 旧原评分和短介绍见 [v14 实测报告](DIGEST_SCORE_INPUT_FIX_2026-10-06.md)。

独立审计摘要校验值：v2 runtime `2bb87d31ddb1748e1740c66b32fca497957c54695ccc0fc17e70931dfffe461b`；v2 content `0ef3a66cd0ff3ba9fce5c3a845c46b497663631f5993b4cfcf951ab03d40627b`。
