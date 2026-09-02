# Use bounded dual-entry discovery and read-only daily automation

Status: Implemented for manually triggered discovery provenance on 2026-09-02; scheduling pending

Ai Notes 同时接受 Codex 的每日主动发现和用户提交的 GitHub 地址，两类候选进入同一核验与关联流程。人工提名默认只分析一次，只有用户明确要求才持续关注；自动发现只使用当前工作区或用户明确授权的自有项目，按 80% 直接相关、20% 相邻探索分配注意力，每天最多报告三个关联机会并允许健康空结果。

每日无人值守任务保持只读，可以搜索、读取、核验、分析和报告，但不得运行陌生代码、安装依赖、修改自有项目或执行实验。这个限制牺牲自动落地速度，换取可控噪声、明确授权边界和对自有项目的安全保护。

旧 12 个 Release 来源全部退回候选池，不自动继承长期关注资格。每日同一 GitHub 组织最多出现一个项目，三条简报中至少一条来自不同技术路线；外部发现仅覆盖公开仓库。

手动验证阶段已经实现并真实验收主动发现 provenance：最多五个搜索方向、二十个元数据候选、五个深读项目，最终选择必须来自深读集合，相邻探索不得超过 20%。这些计数随成功 Finalize 写入长期账本；自动化准入不承认只有 `discovered` 标签而缺少合格指标的运行。第一次运行从 20 个候选中深读 5 个并选择 `vectorian-rs/chizu`；第二次从 16 个候选中深读 5 个并选择 `divyanshu-iitian/ContextFlux`。两次都完整通过固定版本与双侧证据校验，没有安装或执行外部项目。每日无人值守调度仍未启用。
