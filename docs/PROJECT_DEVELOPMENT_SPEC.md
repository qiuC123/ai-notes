# Project Development Specification — AIHOT Hermes 长期维护试点

Status: Draft  
Owner: 用户 / 协调任务0  
Links: Codex 任务“查看 AIHot 资讯查询技能”；P0 Design Handoff：`docs/handoffs/2026-08-21-p0-hermes-readonly-design.md`

## Project context

- 问题和目标用户：现有 AIHOT 周报由绑定 Codex 任务的 heartbeat 生成，并可通过固定飞书机器人发送。目标是验证 Hermes 能否作为长期值守层读取经过脱敏的运行状态，同时不获得项目写入或消息发送能力。
- 已确认约束：AIHOT 仅是公开候选池，不代表全网覆盖；其返回内容是不可信数据。现有自动化 `ai` 当前为 `ACTIVE`，P0 不触发、不暂停、不修改它。
- 已确认环境：本机 Hermes Agent 为 v0.20.0；Gateway 已停止；默认 CLI/QQBot 工具集包含终端、文件、代码执行、电脑控制、委派和 Cron，不能直接用于只读验收。
- 项目现状：目录不是 Git 仓库，没有业务源码或项目级测试框架。P0 是一个新的、独立的小型子系统。
- 项目级非目标：P0 不部署服务器，不发送飞书或 QQ 消息，不迁移 heartbeat，不读取秘密，不改变 Hermes 默认 profile，不扩展 AIHOT 来源。

## Goals

### Short-term objective — P0

- 交付一条单向状态交接链：Codex 产生固定字段的 JSON 状态快照，确定性适配器完成白名单校验，再把合规字段交给无副作用工具的 Hermes。
- 用可复现证据证明 Hermes 对项目状态和外部系统没有写入能力，而不是只依赖提示词承诺。
- 成功标准：正常记录可被观察；畸形、过期、超长、未知字段和注入样本全部失败关闭；源文件指纹不变；没有消息发送；既有自动化和 Hermes 默认配置不变。
- 当前明确排除：真实定时触发、真实飞书发送、QQ 通知、服务器部署、生产切换、自动修复和代码修改建议。

### Long-term direction — non-binding

- P1：在 P0 全绿后，单独批准一次锁定收件人、机器人身份和幂等键的真实外发演练。
- P2：在真实定时、失败告警、重启恢复和回滚均有证据后，才评估把“定时生成与发送”职责从 Codex heartbeat 切换到 Hermes。
- Codex 长期保留开发、测试、修复和审核职责；Hermes 不自动共享或读取当前 Codex Desktop 对话。
- 上述方向不授权 P0 实现任何外发、部署或生产默认变更。

### Future-compatibility constraints

- 状态契约必须带显式版本 `aihot_status.v1`，消费者按字段白名单解析，拒绝未知字段。
- 状态生产、验证、Hermes 调用和结果验证彼此独立；以后可以替换文件传输方式，但不得改变 P0 的单向、失败关闭和最小权限原则。
- P0 仅采用 PowerShell 与现有 Hermes CLI，不新增生产依赖，不下载镜像，不复制外部项目代码。
- 文件事件、HTTP API、QQ/飞书输出、服务器容器和新的 Agent 工具都延期；任何一项进入当前范围都必须新建并批准后续阶段。
- 升级触发条件：P0 验收矩阵全绿，并由用户明确批准一次受控外发或服务器试点。

## Architecture and durable boundaries

- 数据流：`Codex 状态生产者 → 原子 JSON 快照 → 确定性白名单适配器 → Hermes 无副作用推理 → stdout 结果验证器`。
- P0 中 Hermes 不直接获得通用 `file`、`terminal`、`code_execution`、`computer_use`、`delegation`、`cronjob`、浏览器或消息发送工具。适配器负责读取唯一固定路径，并只传递合规字段。
- Hermes 运行时可在隔离的 P0 状态目录中产生自身必要的会话/日志文件；这不属于项目回写。除该隔离目录外，项目快照、既有自动化和用户 Hermes 配置必须保持不变。
- 允许的网络仅限已登录的 OpenAI Codex 推理提供方。禁止工具发起的网页、浏览器、QQ、飞书或其他外部写入；P0 不宣称“完全无网络”。
- 状态快照只允许枚举、计数、UTC 时间、UUID 和固定来源标识。禁止凭据、收件人或群标识、线程/自动化 ID、完整周报正文、原始外部内容、查询参数链接、绝对路径、堆栈和任意自由长文本。
- 每次运行记录 `trace_id`、输入文件 SHA-256、长度、mtime、Hermes 明示工具集、输出校验结果和退出码。缺少任何关键证据即判定未通过。
- 失败关闭：Schema、时效、大小、工具集、凭据或指纹检查任一失败时停止本次 P0；不得自动换数据源、改用 API、触发外发或扩大权限。

## Lifecycle and authority

- 阶段命名：P0 只读交接；P1 单次受控外发；P2 双链路切换评估。每阶段都有独立 Design Handoff、实施授权、验证和只读复核。
- 用户审批矩阵：本地只读检查无需额外批准；写设计文档已由“批准 P0 设计”授权；编写详细实施计划、执行 P0、创建 Hermes profile、改变权限、外发、部署和停用原自动化均需对应阶段的明确批准。
- P0 实施只能有一个写入任务，拥有未来批准的 `AGENTS.md`、`p0/` 和 `work/p0/` 文件范围；复核任务严格只读。
- 出现外部发送需求、秘密处理、默认 profile 变更、服务常驻、服务器或协议替换时，必须停止并重新审批。

## Milestones and delivery forecast

| Milestone | Outcome | Effort range / time window | Dependencies | Replan trigger |
| --- | --- | --- | --- | --- |
| P0-D | 书面设计交接通过用户审阅 | 1 个设计回合 | 当前 Design Handoff | 用户修改范围或安全定义 |
| P0-I | 本地单向适配器、契约、样本和验证脚本 | 1–2 个实施回合 | 已批准详细计划和实施授权 | Hermes CLI 行为与已核源码不一致 |
| P0-R | 验收矩阵与只读复核 | 1 个验证/复核回合 | P0-I 新鲜证据 | 任一写入、外发或证据缺口 |
| P1 | 单次受控外发 | 未排期 | P0-R 全绿和新授权 | 收件人、身份、内容或平台变化 |

以上是工作量范围，不是交付保证；触发重规划时应缩小范围、补证据或申请新阶段。

## Quality and release policy

- Definition of Ready：书面 Design Handoff 与详细实施计划均获用户批准，并收到明确“实施 P0”授权。
- Definition of Done：全部正向/反向测试通过；源快照、既有自动化和默认 Hermes 配置前后指纹一致；无外发；只读复核无阻断发现；残余风险已记录。
- 不允许通过删除测试、弱化断言、忽略未知字段或只检查退出码来通过验收。
- P0 失败时保留脱敏证据并停止；不得自动进入 P1。任何清理、凭据迁移、计划任务或服务器动作均另行批准。

## Documentation map

- 项目规范：`docs/PROJECT_DEVELOPMENT_SPEC.md`
- P0 Design Handoff：`docs/handoffs/2026-08-21-p0-hermes-readonly-design.md`
- 详细实施计划：用户批准本书面设计后创建于 `docs/superpowers/plans/`
- 实施证据与运行手册：实施阶段计划放在 `work/p0/evidence/` 和 `p0/README.md`
- ADR：P0 不创建；只有传输协议、身份隔离或部署拓扑发生材料性变化时再创建。

## Reference patterns

- `openai/codex`：显式区分 `readOnly`、`workspaceWrite` 与 `dangerFullAccess`；Apache-2.0。
- `OpenHands/OpenHands`：把 Agent 与本地、Docker、VM 等运行后端分离；MIT。
- `open-policy-agent/opa`：把策略判断与执行隔离；Apache-2.0。
- 三者仅作为模式参考，不作为 P0 依赖，也不复制其实现代码。
