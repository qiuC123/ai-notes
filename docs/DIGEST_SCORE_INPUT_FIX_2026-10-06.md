# 评分输入精简与 Joplin 回归（2026-10-06）

本轮已解决评分请求超预算和 Joplin 输出结构错误；逐理由引用仍有漏引，自动内容复核未拦住。五项发现式短介绍有原文支持，可以查看；不能据此宣称完整评分推荐已通过。不是正式日、周、月榜，不恢复自动化，不更改分数权重、65／45 门槛或来源等级。

## 改动与离线结果

原文此前已只保存一份；主要重复来自 JSON Schema 多次展开相同的网址枚举和五维字段规则。新任务显式使用 `score_input_contract=compact-schema.v1`，把完全相等的节点放入 `$defs` 并用 `$ref` 引用。原文、引用、段落 ID、范围与严格字段校验保持，60,000 字符预算不提高。

离线恢复检查又实际发现：同一策略字典保存、读回后，flag `oneOf` 排列会变化，进而改变请求指纹。新合同固定 flag 顺序及导航顺序；展开比较仅规范这一无语义影响的 flag 顺序，其余节点逐值相同，接受的输出集合不变。旧合同不追溯排序。首份零 API 冻结记录保留，正式本轮回归使用新的 `work/digest-v14-score-input-live-20261006/`。

新请求附上 PASS／UNKNOWN 的结构指导和来源导航，示例不表示候选应得零分或应 PASS。每个理由的引用仍须支持其实际事实；复核导航只列该理由原有 URL 对应的段落，不能自动加入包内其他出处。`reason_note:null` 等额外字段仍拒绝，旧坏响应不删键、不补分或补引用。

完整请求字符数包含新提示词、材料及 schema：

| 项目 | v13 原请求 | 本轮真实评分请求 |
| --- | ---: | ---: |
| Basic Memory | 65,474 | 58,021 |
| PAL MCP | 60,641 | 53,246 |
| Czkawka | 61,980 | 55,076 |
| rclone | 60,677 | 54,901 |
| Joplin | 58,516 | 52,433 |

这些请求复用保存的原文卡，并非重新联网采集；真实 wire request 尺寸与离线构造一致。原读取与核验时间保持，最长输入 58,021 字符，小于原 60,000 上限，没有截断原文。

验证：最终 489 项三榜测试通过。真实 v13 七份评分 input／prompt／schema 及两份复核 material 与保存请求逐值一致。新 marker 必须依赖现有原文、发现式介绍、公开复核及评分合同，不能只改阶段名而漏掉 schema。五项真实评分及复核输入，保存前与 sorted JSON 恢复后逐值一致，请求指纹稳定。

代码提交：`32ba824`（输入与指导）、`d34e7fb`（保存恢复稳定性）。

## 真实回归

已完成：复用同一天保存的五项材料，在新隔离库中评分和复核，实际 10 次 HTTP 请求（5 评分＋5 内容复核），全部 HTTP 200，零重试。原文核验阶段不重复付费，原有原始响应和实验记录保留。

| 项目 | 原始分数／程序决定 | 模型内容复核 | 独立核对结果 |
| --- | --- | --- | --- |
| Basic Memory | 68／select | accept | 短介绍可展示；interest 的共同读写等事实漏引 README。 |
| PAL MCP | 55／defer | accept | 短介绍可展示；证据理由提到 metadata，但引用未列该来源；程序没有推荐。 |
| rclone | 73／select | accept | 短介绍可展示；许可理由漏引 COPYING，云存储覆盖广度只引 FAQ。 |
| Czkawka / Krokiet | 67／select | accept | 短介绍可展示；总理由把 Cargo 应用范围之外的 MIT 许可扩大到 Krokiet 应用。 |
| Joplin | 67／select | accept | 短介绍可展示；额外字段已消失，但同步条件仍只引安装页，未引支持该事实的 README。 |

五项评分结构全部合法，`reason_note` 均未出现。独立核对确认五项实际公开介绍有原文支持；四项程序 select 的完整评分推荐仍应暂缓，涉及五处引用或范围问题，模型复核漏报全部五处。多数是出处绑定错误，不是项目功能被证伪；不能用另一字段或总体供文中的正确引用替本条理由补出处。未展示的内部 detail 不按说明书完整性要求拒绝，不要求发现式正文列出这些审核细节。

161 项运行记录检查通过：原始 HTTP bytes／hash、供应商 usage、回执、隔离请求账本和 summary 一致；183 项来源 hash、16 项代码／配置／提示词 hash、runner hash 及旧采集时间保持，4,323 个既有受保护文件在完成后仍无变化。运行记录检查由 runner 作者执行，独立语义审核由另一 Agent 执行；运行通过不等于语义通过。

实际用量：prompt 156,083＋completion 8,322＝164,405 tokens；实际账单金额未知。没有初筛／核验付费调用、outbox、正式归档、生产历史修改或新偏好标签。原 summary 的 `independent_audit_pending:true` 是生成当时的记录，保持原样，后续结论另存独立审核文件。

新请求采用用户最新确认的外部 Agent／订阅复用偏好；这是已见错例的开发回归，不是严格 A/B 或盲测，不能把分数变化全归因于 schema 精简。

## 证据入口

- `work/digest-v14-score-input-20261006/compact-schema-size-report.json`：schema 无损展开与旧输入尺寸。
- `work/digest-v14-score-input-20261006/integration-audit.json`：首个代码提交 `32ba824` 的旧冻结请求兼容检查；该目录零 API，冻结后不覆写。
- `work/digest-v14-tests-stable.log`：最终 489 项测试。
- `work/digest-v14-score-input-live-20261006/frozen.json`、`requests/`、`summary.json`：基于 `d34e7fb` 的真实请求与原回执。
- `work/digest-v14-score-input-live-20261006/runtime-audit.json`：161 项运行记录检查，含恢复指纹与保护文件核对。
- `work/digest-v14-score-input-live-20261006/content-audit.json`：独立逐理由、范围与实际公开文字审核。
- [五项原模型短介绍预览](../outputs/digest/v14-reader-preview-2026-10-06.md)：保留已核对的实际公开字段，评分实验状态另记，不改成正式榜单。
- 原错例保留于 `work/digest-v13-feedback-pilot-20261006/`；没有把本轮结果写回旧分数或旧审计。

自动化维持 PAUSED。本轮预算与结构修复验收完成，引用正确性未通过；下一项需要针对每条理由自身出处进行隔离核对，而不是继续增加整体供文或补全使用说明。筛选质量尚未代表性校准；完整三榜、新闻／游戏和云端持续运行仍按 [TASK](../TASK.md) 分别验收。
