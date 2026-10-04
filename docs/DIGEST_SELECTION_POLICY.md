# 三榜筛选与评分：读者适配与新闻

状态：已实现可审计评分流程；**权重和阈值尚未经过真实人工标注集校准，不声称选题质量已经提升**。当前配置版本 `v3-assessment-contract-uncalibrated`（2026-10-04 试刊问题修正）；历史冻结 v1/v2 输入和对比结果保持原样。主入口为 `python -m ai_notes.digest_selection`。

本轮重点是回答“为什么选、为什么暂缓、为什么淘汰”。保留 Python＋SQLite 和三榜规则，不把 AIHOT 的新闻注意力分直接当成实用项目价值分，也不按来源名气给不同门槛。

## 1. 从 AIHOT 借鉴什么

参考固定版本 [AIHOT 筛选流程](https://github.com/KKKKhazix/AIHOT/blob/4ed5e7603962e8589adee4e1cd23abff6fae106b/docs/selection.md)和[评分提示词](https://github.com/KKKKhazix/AIHOT/blob/4ed5e7603962e8589adee4e1cd23abff6fae106b/industry/prompts/selection-score.md)：先预筛、按内容类型改变权重、给宣传/例行更新设上限、用错例改标准、分开发集与留出集。实现为本项目自己的规则与代码，没有引入 AIHOT 代码。

主要调整：AIHOT 把事件压缩为总注意力分；这里保存每一维分数、理由、原文 URL、模型/编辑身份和人工覆盖原因。项目价值、发表资格、实际归档三个判断分别保存。AIHOT 按来源级别设置门槛、周月汇编日报的规则不采用；这里继续共享候选库独立筛选。一次评分的完整分项便于先定位错例；追加评分保留历史，但不把多次重试中最高的一次冒充双评分平均。

## 2. 四组类别权重与独立新闻权重

每维为 0–10 **整数**，权重和为 10，总分 0–100。模型或人工提供分项，Python 计算原始总分、限制后总分、建议。不从关键词、星数、热度自动伪造分数。

| profile / 栏目 | value 具体价值 | novelty 信息增量 | evidence 证据 | usability 上手/迁移 | interest 趣味/启发 |
|---|---:|---:|---:|---:|---:|
| practical：开源项目、Skills、AI 应用、MCP 服务与连接器 | 4 | 1 | 1 | 3 | 1 |
| learning：Agent 框架与编排、模型与运行工具 | 3 | 2 | 2 | 2 | 1 |
| play：游戏 | 1 | 2 | 1 | 2 | 4 |
| reading：博客、帖子与访谈 | 3 | 3 | 2 | 1 | 1 |
| news：`kind=news`，优先于所属主题的类别权重 | 3 | 3 | 3 | 1 | 0 |

游戏不需要证明办公效率，阅读内容可以提供认知和可迁移方法；小众工具可以对特定人群很有价值。老项目不因日期旧扣分；novelty 指具体差异/新认识。用户已有某类工具，不代表同类项目一律淘汰。更新项评价本次变化，不能拿整个项目多年的累积价值给小补丁高分。

无关/空泛内容可 BLOCK；原文不足或判断不明为 UNKNOWN；对象、读者、具体价值、原文支持明确才 PASS。UNKNOWN 保留候选，失败不记为零分。

有原文依据才标记以下情况，Python 执行上限：

| flag | 限制 | 处理 |
|---|---|---|
| unsupported_promotion | value≤4，evidence≤3 | 暂缓补证 |
| routine_update | novelty≤3 | 仍按总分判断，不机械淘汰 |
| unfulfilled_announcement | usability≤2，evidence≤4 | 暂缓等兑现 |
| unclear_usage | usability≤3 | 暂缓澄清入口/条件 |
| reader_mismatch | value≤3、usability≤3 | 不适合当前读者，暂缓 |
| insufficient_usage_evidence | evidence≤4、usability≤4 | 工具/方法使用依据不足，留待观察 |

当前读者不太会代码。纯编译测试/CI/代码内务工作流和实际树莓派硬件走有证据的 BLOCK；CLI/MCP/Skills 不一律排除，软件 Pi 不能按名字排除。可执行步骤、可查看演示与独立使用记录是不同层次证据，作者演示不能写成独立口碑；周月成熟度主张需要对应依据。Star 无硬门槛，高星不免核验、低星不自动淘汰。

`flag_kinds` 限制适用范围：routine_update 只作用于 update/news；新闻不使用 unfulfilled_announcement、unclear_usage、insufficient_usage_evidence 三种工具门槛。已核实的公告可有新闻价值，尚未兑现的效果必须写成计划。新闻看事件变化、影响与原始证据，不能套安装或开源要求。日报偏动态及易用工具，周月看持续使用和保留价值；不设置条数配比。

每个 flag 都要理由及已读取材料的引用，不通过关键词匹配自动触发。初始门槛：≥65 建议入选、<45 建议淘汰，中间暂缓。**这只是待校准编辑标准，不是已测得最优阈值。**

v3 保留全部权重、门槛与限分数值，收紧两类容易误用的理由：`routine_update` 必须指出原文中的有限增量，不能只因版本号或“未改变 AI 格局”触发；`unsupported_promotion` 要定位尚未支持的具体效果主张，不能以没有独立实测否定官方已经宣布的事实。两者按 `flag_basis` 分别增加 `basis={kind,claim,quote,evidence_url}`，kind 为 `limited_increment` / `unsupported_effect_claim`。程序验证引文确实在所引材料中；引文是否足以支持判断仍需模型和编辑审核，不能宣称语义已自动证明。

## 3. 评分与发表资格

1. `digest.candidates` 决定周期、同级历史和事件资格；阻断项目不占初筛名额。
2. `prepare` 冻结这一期候选、对应观察、原文片段、配置和提示词；只准备有限卡片，不自动搜寻/核验来源。
3. 模型或人工阅读原文后提交 `record`；没有可读原文时必须 null scores + UNKNOWN + defer。
4. `rank` 读取冻结输入的最新审阅，再查询当前历史资格。结果分为 available、needs_evidence、blocked、deferred、rejected、unreviewed；同组按有效分降序，URL 只做并列排序。
5. available 只意味着可以继续编辑，不能直接发表。`digest render/draft/archive` 仍检查真实时间、原文核验、更新身份、完整周期、全同级历史和月榜数量。

高分不会把 `discovered` 升为 `verified`，更不会把 documented 升为 tested。人工可以有理由地改变价值决策，但不能让缺证据/已阻断项目以 select 记录。新出现的同级归档也会在 rank 时重新阻断。尚未实际阅读的 URL 不能用于分项或 flag 引用。

候选 identity 为 canonical 项目/文章＋kind＋规范事件 URL。事件换 ID 不生成新身份；完整事件、观察批次、采集与核验时间、正文等均进入 input_hash。没有同一 event 的新输入不能偷用另一事件分数。

`prepare` 默认使用可分页的候选代表事件；同项目完整事件卡仍在 `digest candidates` 的 `event_candidates` 可查。自动流水线在核验后应传 `candidate_ids`，从第一页分页扫描精确事件集合，包含仍符合资格的非代表事件，按请求顺序冻结。重复 ID、事件消失/更换或已被阻断时明确失败，不能悄悄换成首屏其他项目。集合数量必须≤limit，不能与 offset 混用。不同事件应独立准备和评阅，不能把其他事件的核验时间用到当前事件上。初筛数量、补采与深核仍执行已有日周月预算，不因评分模块存在而扩大采集范围。

## 4. 数据与调用契约

- 配置：[config/digest_selection.json](../config/digest_selection.json)
- 系统提示词：[docs/prompts/digest-selection.md](prompts/digest-selection.md)
- 代码：[src/ai_notes/digest_selection.py](../src/ai_notes/digest_selection.py)
- 审阅数据：`data/weekly_digest/selection.sqlite3`，与原始 `digest.sqlite3` 分离，不修改/迁移历史归档。
- 表：preparations（冻结输入）、reviews（追加审阅）、label_splits（项目隔离）、labels（人工标注历史）。复用同一 review 内容为 unchanged，不重复插入。

准备原文上下文 JSON 数组（由真实只读抓取提供）：

```json
[{"url":"https://example.org/original","text":"实际读取的原文片段，不是编造的摘要","fetched_at":"2026-10-03T10:00:00+08:00"}]
```

```powershell
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_selection prepare --root . --ranking-type daily --period 2026-10-02 --limit 30 --context work/source-context.json --output work/prepared.json
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_selection record --root . --input work/review.json
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_selection rank --root . --prepare-id PREPARE_ID --output work/ranked.json
```

`prepare` 返回 `digest-selection.prepare.v1`：prepare_id、prepared_at、ranking_type、period、policy、policy_hash、prompt_hash、prompt_text、cards、coverage、blocked_candidates。cards 中 input_hash 绑定 ranking_type、material、evidence_context、observation、profile、eligibility。可选 kind_profiles 和 flag_kinds 进入冻结 policy；旧配置无新键时保留旧行为。新闻以原始文章 URL 作为无 event 时的身份回退，补齐同 URL 事件不改变候选 ID。`--offset` 继续分页，coverage.next_offset 为 null 表示结束。调用方必须累计已审阅集合，不能反复只取首屏。

精确集合 CLI 使用可重复的 `--candidate-id ID`；Python 传 `candidate_ids=[candidate_id(record), ...]`。`requested_candidate_ids` 和 `coverage.exact_scan_count` 保存集合与扫描范围；扫描只为定位已选定身份，不额外评分或增加深核预算。

`record` 的 `digest-selection.review.v1` 必需字段：

```json
{
  "schema_version": "digest-selection.review.v1",
  "prepare_id": "原样回填",
  "candidate_id": "原样回填",
  "input_hash": "原样回填",
  "reviewer": {"kind": "model", "name": "实际调用者", "model": "实际模型 ID"},
  "precheck": {"status": "UNKNOWN", "reasons": ["未取到原文，无法判断"], "evidence_refs": []},
  "scores": null,
  "flags": [],
  "decision": "defer",
  "reason": "需要补充原文后再评分",
  "override_reason": null
}
```

正常 scores 为五维对象，每维 `{score: 0..10, reason: 非空字符串, evidence_refs: 已读URL数组}`；flags 为 `{code,reason,evidence_refs}` 数组；decision 可 select/defer/reject。模型不得覆盖 Python 决策。人工 reviewer.kind=human、model=null，决策不一致需非空 override_reason。未知字段、bool/小数/NaN/越界分数、未读引用、错误输入哈希均拒绝。

v3 的两种敏感 flag 还必须提供上述 `basis`；没有配置 `flag_basis` 的旧冻结策略继续使用其旧契约，不向历史回执追加新字段。

Python 公共 API：`prepare(root, ranking_type, period, limit=30, evidence_context=None, policy_path=None, offset=0, candidate_ids=None)`；`load_preparation(root,id)`；`get_prompt(root,prepared)`；`record(root,review)`；`rank(root,id)`。provider 应用 `get_prompt` 读取被冻结提示词，不悄悄替换为最新版。准备材料是数据，不得拼入更高优先级指令。

自动 provider 只向模型索取 `{precheck,scores,flags,reason}`。先调用纯函数 `build_scoring_input(prepared,candidate_id)`，仅传原文、候选事实及类别规则，剥离资格状态、入库核验状态、历史评审、内部身份和发表门槛。完整卡片仍被冻结；`build_review` / `record` 继续用它检查发表资格，高分不能越过缺证据或重复历史。

收到原始响应后调用 `adapt_assessment(output,policy=prepared['policy'],card=card)`，返回 `status,raw_output,assessment,transformations,error`。唯一兼容转换是将非空 `precheck.reasons` 字符串包成单元素数组；不能猜测缺失分项、重命名维度、删掉越权字段或替模型补理由。有效响应限 65536 UTF-8 字节。pipeline 保存 `score-response:<candidate_id>` 检查点及 request_id，拒绝项保留原响应和原因，继续其他候选，不自动追加付费重试。断点恢复复用相同请求回执。

通过适配后调用 `build_review(root, prepare_id, candidate_id, assessment, reviewer)`，按冻结权重与 caps 生成 decision，绑定 input_hash 和真实调用模型身份，再由 `record` 落库。模型不负责求和、不回填身份、不覆盖决策。上述完整 review 是程序/人工入库契约，评分提示词不再同时要求模型返回它。

## 5. 用真实错例校准

不要求先标注固定 60 或 200 条。先导出一小组真实候选，包括边界项、冷门项目、游戏/阅读，以及此前疑似误选漏选的项；人工选择 select/reject/either 并写理由。

```powershell
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_selection export-labels --root . --prepare-id PREPARE_ID --output work/label-queue.json
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_selection label --root . --input work/human-label.json
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_selection evaluate --root . --prepare-id PREPARE_ID --split dev --output work/evaluation-dev.json
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_selection evaluate --root . --prepare-id PREPARE_ID --split holdout --output work/evaluation-holdout.json
```

人工 label 格式为 `digest-selection.label.v1`，必需 prepare_id、candidate_id、split(dev/holdout)、label(select/reject/either)、editor、reason。标签标识 `human_asserted`，含义是提交者声明人工判断；CLI 无法认证操作者真实身份。**禁止把 Codex/其他模型的试评分写成用户的人工 gold。**

同 canonical 项目的所有事件必须处于同一 split，数据库阻止跨集泄漏。evaluate 排除未标注、either、未评分和其他 split，分别报告数量、precision/recall、误选/漏选及具体理由。零分母返回 null，不生成虚假的 100%。dev 扫描 40–90、每 5 分；holdout 只验收冻结规则，不输出阈值扫描。没有人工标签时只能证明管道可运行，不能证明选题质量。

建议先看类别错例，改定义/示例，再调阈值。若还存在显著评分波动，再用同一批人工 gold 比较独立双评分的稳定性与真实成本。输入/配置/模型改变应重新准备评测版本，保留旧记录便于对照。

## 6. 验证边界

`tests/test_digest_selection.py` 使用隔离临时库和明确的合成候选，验证权重计算、程序上限、未知字段/非法分数、未读引用、输入/提示词冻结、模型不可覆盖、人工不可越过证据资格、分页适配和人工集隔离。测试通过不等于已跑通真实模型 API，也不代表真实选题效果已经经过人工验收。生产候选、实际抓取原文、评分回执、人工 gold 都保留在忽略的 data/work 路径。
