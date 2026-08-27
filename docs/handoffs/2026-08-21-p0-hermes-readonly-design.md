# Design Handoff — P0 Hermes 只读交接验证

Status: Draft  
Owner: 协调任务0（当前 Codex 任务）  
Phase: P0

## Goal and scope

- 问题：当前 Hermes v0.20.0 的默认 CLI/QQBot 工具权限包含项目写入和执行能力，不能用于证明“只读值守”。Codex 与 Hermes 会话也不会自动共享，需要一条明确、可审计的状态交接接口。
- In scope：定义并在后续实施中构建 `aihot_status.v1` 状态契约、单向文件快照、确定性校验适配器、无副作用 Hermes 调用、输出校验和本地验收脚本。
- In scope：通过正常、畸形、过期、超长、未知字段和注入样本验证失败关闭；通过文件指纹和配置快照验证没有项目回写或既有配置变化。
- Out of scope：修改或触发现有 heartbeat；飞书/QQ 发送；真实 AIHOT 联网抓取；Hermes Gateway/Cron；服务器、Docker 镜像或 Windows 服务账户；自动修复；P1/P2 工作。

## Decisions and assumptions

### Confirmed decisions

- 用户已批准 P0 的设计方向：先验证只读，再单次受控演练，最后才讨论整体迁移。
- P0 采用“单向文件快照 + 确定性适配器”，不使用事件总线或 HTTP API。
- Hermes 不直接读取 Codex Desktop 对话，也不获得通用文件工具；适配器读取唯一固定路径并把白名单字段传入 Hermes。
- P0 的用户可见结果只打印到本机 stdout，不发送 QQ/飞书，也不回写项目结果文件。
- 实施时不得改变默认 Hermes profile 的工具设置；使用每次调用的显式最小工具集和隔离的 P0 运行状态目录。
- 允许 Hermes 调用已配置的 OpenAI Codex 推理提供方；不允许任何工具型联网或外部写入。

### Assumptions to validate

- `hermes -t clarify` 在 v0.20.0 中会用显式工具集覆盖 CLI 平台默认工具集；本地源码只读检查已支持这一点，实施仍需用运行证据验证。
- Hermes oneshot 即使没有项目写工具，仍可能写自身会话或日志；实施必须把这些内部写入隔离在 P0 专用状态目录，并审计边界。
- P0 可以复用现有 OpenAI Codex 登录进行推理，而不复制飞书、QQ或其他外部凭据；若运行要求复制整份 `.env` 或启用额外工具，P0 必须停止。
- PowerShell 现有版本足以完成 JSON 解析、白名单验证、哈希与进程调用；不安装 Pester 或第三方包。

### Alternatives rejected and why

- 自定义 `get_latest_status` MCP/插件：能力边界更直观，但新增插件注册、工具发现和安全审查面；留到 P1 或后续独立阶段。
- Hermes 通用 `file` 工具 + 提示词约束：工具本身含读写能力，不能证明只读，拒绝。
- Hermes 通用 `file` 工具 + 当前管理员账户 ACL：管理员可改变 ACL，且 Hermes 当前是本地 terminal backend，P0 证据不足，拒绝。
- 文件事件或 HTTP API：引入重复投递、鉴权、端口、审计和网络面，超过 P0 需要。
- Docker 隔离：本机 Docker 可用，但当前未确认 Hermes 镜像；拉取或构建镜像需要单独审批，P0 不采用。

### Technical and safety constraints

- 状态文件最大 16 KiB，UTF-8 JSON，顶层只允许下列字段：`schema_version`、`trace_id`、`generated_at`、`producer`、`window_start`、`window_end`、`run_state`、`phase`、`attempt`、`candidate_count`、`selected_count`、`qualifying_count`、`source_scope`、`error_code`、`retryable`。
- 固定枚举：`schema_version=aihot_status.v1`；`producer=codex_status_fixture`；`run_state=idle|running|succeeded|failed|blocked`；`phase=collect|filter|draft|dispatch`；`source_scope=aihot_public_pool_secondary_filter`；`error_code=none|source_unavailable|invalid_json|insufficient_candidates|policy_blocked`。
- `trace_id` 必须是 UUID；时间必须是 UTC ISO-8601；记录生成时间距校验时间不得超过 15 分钟；`attempt` 为 1–3；计数为 0–1000，且 `qualifying_count <= selected_count <= candidate_count`。
- 状态契约不包含自由文本。任何未知字段、字符串指令、URL、绝对路径、凭据形态或超限值都拒绝整条记录，不静默截断。
- 文件 SHA-256 在适配器外计算并写入审计输出，不放进被哈希的 JSON，避免自引用完整性字段。
- Hermes 输出也必须通过固定 JSON Schema：只允许 `trace_id`、`observed_state`、`assessment`、`reason_code`；自由文本或未知字段判失败。

## Acceptance criteria

- [ ] 用户可见行为：给定合规快照，Hermes 仅向 stdout 返回一条合规观察 JSON；不产生飞书、QQ或项目文件输出。
- [ ] 正常契约：返回的 `trace_id` 与输入一致，`observed_state` 是输入 `run_state` 的允许映射，输出能被确定性校验器接受。
- [ ] 畸形 JSON：失败并返回非零退出码；不得调用 Hermes。
- [ ] 未知字段或注入内容：整条记录标记 `INVALID_RECORD` 或 `QUARANTINED`；不得把原始内容传给 Hermes。
- [ ] 超过 16 KiB、超过 15 分钟、UUID/枚举/计数关系不合法：失败关闭；不得自动修复或补造。
- [ ] Hermes 工具边界：调用命令显式只允许 `clarify`；无 `file`、`terminal`、`code_execution`、`browser`、`web`、`computer_use`、`delegation`、`cronjob`、消息发送或 MCP 工具。
- [ ] 凭据边界：P0 环境中没有飞书、QQ或其他发送凭据；不得读取现有飞书配置或 `.env` 内容。
- [ ] 文件边界：输入文件和现有自动化文件在每次运行前后的 SHA-256、长度和 mtime 一致。
- [ ] 配置边界：默认与 `ai-engineer` Hermes profile 的工具配置和既有 Codex automation 均不变。
- [ ] 可观测性：证据包含 `trace_id`、输入指纹、显式工具集、Hermes 退出码、输出校验结果和失败原因枚举，不包含秘密或完整外部内容。
- [ ] 失败关闭：任一安全门失败，P0 状态为失败；不会进入 P1，也不会自动扩大权限。

## Attached implementation plan

- Plan location：本文件先嵌入阶段级计划；用户批准本书面设计后，再用 writing-plans 生成逐步详细计划到 `docs/superpowers/plans/2026-08-21-p0-hermes-readonly-implementation.md`。
- Ordered steps：
  1. 重新记录 Hermes 版本、Gateway、profile、CLI/QQBot 工具集与现有 automation 指纹，作为实施基线。
  2. 创建项目级 `AGENTS.md`，限制实施写入到 `AGENTS.md`、`p0/`、`work/p0/` 和已批准文档，禁止任何外发、秘密读取和默认配置修改。
  3. 创建 `p0/contracts/aihot-status-v1.schema.json`、`p0/contracts/hermes-observation-v1.schema.json` 和契约说明。
  4. 创建正常、畸形、过期、超长、未知字段、注入和计数关系错误的 fixtures。
  5. 用测试先行方式实现状态生产 fixture、确定性校验器和固定路径读取器；生产写入采用临时文件后原子替换。
  6. 实现 Hermes wrapper：校验通过后构造无原始自由文本的提示，使用隔离 P0 运行状态目录和显式 `clarify` 工具集执行 oneshot，随后校验输出 JSON。
  7. 实现一键验收脚本，运行所有正向/反向用例并比较输入、automation 和 Hermes 默认配置指纹。
  8. 保存脱敏证据，完成一次严格只读复核；阻断问题返回原实施任务，最多一轮修复后重新验收。
- Affected modules/contracts/data：计划创建 `AGENTS.md`、`p0/contracts/`、`p0/fixtures/`、`p0/scripts/`、`p0/tests/`、`p0/README.md`；运行态和证据只放 `work/p0/`。
- Ownership and non-overlap：实施任务0是唯一写者，只拥有上述文件；协调任务不内联实施；审查任务0严格只读。不得修改 `skills-lock.json`、`outputs/`、`C:\Users\Administrator\.codex\automations\ai\automation.toml` 或现有 Hermes profiles。

## Validation plan

- Automated tests and commands：
  - `pwsh -NoProfile -File .\p0\tests\Test-P0Contract.ps1`
  - `pwsh -NoProfile -File .\p0\tests\Test-P0Readonly.ps1`
  - `pwsh -NoProfile -File .\p0\scripts\Invoke-P0Readonly.ps1`
  - 验收脚本必须汇总精确的通过数/总数并以非零退出码表示任一失败。
- Manual checks：
  - 比较执行前后状态快照、现有 automation 文件和 Hermes 默认配置指纹。
  - 复核 Hermes 命令行只含批准的 profile/状态目录、`--toolsets clarify`、`--ignore-rules` 和 oneshot 参数；不得含 `--yolo`、发送、Cron、浏览器或项目写入工具。
  - 检查 stdout 只含观察 JSON；stderr 和证据不含 token、群标识、完整周报或绝对用户路径。
- Evidence required before external/default changes：
  - P0 验收矩阵全部通过；只读审查无阻断发现；用户查看终端交接并单独批准 P1。
  - P0 通过不代表服务器可用、不代表真实定时运行成功，也不授权一次真实发送。

## Risks and approval gates

- Known risks：Hermes oneshot 会自动绕过交互式审批，因此工具集覆盖必须在调用前后都有可验证证据；若无法证明仅有无副作用工具，停止实施。
- Known risks：Hermes 推理需要访问 OpenAI Codex，P0 只能证明“无工具外发和无项目回写”，不能证明“完全离线”。
- Known risks：Hermes 可能写自身会话/日志；这些写入必须局限于 P0 隔离状态目录，且不得被表述为绝对无文件写入。
- Known risks：当前目录无 Git，无法按普通仓库流程提交设计或获得 Git diff；变更清单和哈希证据将替代提交证据。
- Decisions requiring human approval：本书面 Design Handoff；详细实施计划；实际创建 P0 profile/状态目录并运行 Hermes；任何外发、部署、凭据迁移、默认 profile 变更或停用原 automation。
- Exact next human instruction needed：`批准 P0 书面交接并编写实施计划`。
