# 初筛完整输出修正：同批云端验收

用户在原任务确认执行：修正初筛输出完整性，再验证日榜生成。原自动化继续暂停。本轮复用已有 Dot 环境与智谱普通 API 配置，保留原失败账本；不重做 smoke、采集或历史恢复。

## 代码与输入

从主控给定的精确 Git 提交 fast-forward 更新 `/workspace/ai-notes`，工作区有本地改动则先报告，不能 reset 或覆盖。依赖已安装，不安装候选软件。新建初筛请求使用 `digest_pipeline._screen_request()`；旧冻结 `screen-input` 不自动升级。

原状态根为 `/workspace/ai-notes-digest-state-trial-20261008`，原生成 job 为 `ff114c88126f75512d92d29b546592a931a0d8950d7640cf2e4196cc761276e4`。只读取得其 `checkpoints['screen-input']`：19个 records 与19个 compact cards，期号 `daily / 2026-10-09`、深核上限12。验证两个ID集合一致，原模型17条决策及原失败记录保持。

新合同新增精确数量、闭合字段、最多80字符的短理由结构；预算为 `min(16384, 1024 + n * 256)`，19项为5,888。初筛明确继承 `excluded.v1`：不核对许可条款，不因许可未核实暂缓。读者兴趣、评分权重、阈值、来源等级、19项原始资料和统计期不变。

主控从原完整回执重建后，`digest_runtime._hash` 的预期值如下，云端在付费前必须逐项匹配：records `d0749cc0c8d9fd2d95659e92d59b0407aa290bba91f01cc0e49f77fbeabbd6ee`；原cards `62dd71c2a1bce1a200ace6ac5666e7eb6ed99ca00a887afcbe4abf8fcccfcd79`；原policy `b71adcc45e7df2e323e9d3caeab62165b1604af435c7f51dce74de9218db2fa6`；新request `598515a9986a79b639af4a7c499d346011a09e1d482a86260286d74d3ce27e28`。不匹配先保存实际差异，不付费继续。

## 一次有界运行

1. 保存原状态根的业务计数、全部 runtime 行与全部文件哈希，核对48批／307观察／169入选／22归档／3草稿、123候选及原失败job存在。缺少原状态或输入则停止。
2. 创建此前不存在的私密验收目录 `/workspace/ai-notes-screen-v18-trial-20261010`。复制原候选与历史、配置及提示词；SQLite 用只读源连接的 `backup` 取得一致快照。新目录不复制原 runtime 库及其 WAL/SHM，初始化独立的新请求／job账本。保留原目录全部数据，不导入或删除其失败请求。复制后确认目标期 `daily / 2026-10-09` 尚未归档；若已归档则停止，不能将 `reused_archive` 计作新生成验收。
3. 在新目录仅入队原期号的 generate。用 `work_once(..., job_id=..., handlers={'generate': ...})` 的受控 handler，在有租约的任务中保存新 `screen-input`：其 records、offset、eligible_count、source_review_contract、policy_snapshot、prompt_snapshot 均来自原冻结输入；仅 request 调用 `_screen_request()` 重新构造。紧接着调用真实 `digest_pipeline.generate()`。
4. 只执行这个指定 job 一次。初筛请求最多1次；全日榜沿用现有预算 `1 + 12 * 3 = 37` 请求，不循环改提示词、补理由、抽样、retry或重排队。新合同不同指纹允许这次明确授权的验证，不能把新运行称作旧请求免费恢复。只读取原文，不安装项目。程序对单候选正常暂缓、排除并继续处理属于本轮有界流程；整体任务失败后保留真实响应及错误，停止额外请求或修改，不手工跳过失败继续。
5. 保存真实初筛输出、逐ID覆盖与理由长度检查、后续原文／评分／复核实际结果、文章或草稿、缺口、请求数和实际usage。若附加 HTTP 观察，仅保留供应商的 finish_reason、usage和request ID，不打印请求头、密钥或带凭据URL。没有这些字段就保留未知，不断言旧响应被截断。
6. 在同一新目录再次只读核对任务终态，并执行一次同ID `work_once`，应为idle且请求／usage不增加。比较原状态根前后全部数据与哈希保持；新验收目录的文章是试运行结果，不合并到原历史。

## 回执与交付

完整 JSON 回执包含精确 Git SHA、解释器与导入位置、原冻结输入和新冻结请求、实际逐项输出、runtime 前后快照、原目录保护比较、真实失败及新目录文章／草稿路径。通过 Library 提供可下载链接与SHA-256。不能仅报告HTTP成功或初筛结构通过就称全日榜质量通过；列出确实执行到的阶段和实际数量。

原 API 配置直接沿用，不读取密钥值或重填 key，不设置 `DIGEST_MODEL_ENV_FILE`。本轮不运行 dispatch/schedule、不恢复原 heartbeat、不设置新定时、不改其他管道、不向他人发消息。不伪造 outbox 已送达。新容器持久化及长期运营仍需后续独立验收。

## 实际完成记录

2026-10-10已按`f565e4f`完成一次有界验证：19条初筛完整返回，选中7项；6次verify、2次score、2次editorial，隔离日榜生成2条、缺3条。11次API／165,130 tokens／零付费重试，另1次核验输入超限在发送前暂缓。同ID终态idle、原目录808文件及全部数据库记录保持，主控完整回执读回通过。筛选许可范围仍偏离、部分评分缺出处被模型复核接受，整体质量与运营未通过；原自动化保持暂停。详见[实际验收报告](../DIGEST_SCREEN_OUTPUT_FIX_2026-10-10.md)。该执行合同已经使用，不重复付费重跑同批。
