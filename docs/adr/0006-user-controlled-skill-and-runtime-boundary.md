# Keep Skill authoring user-controlled and runtime effects bounded

Status: Accepted

Ai Notes 不把正常对话自动转化为 Skill。前十次手动验证使用共学主对话、项目设计文档和 Python 确定性程序；流程达到自动化准入门槛后，只有用户明确授权才创建或更新版本化共学 Skill。定时任务只能调用用户批准的固定版本，不得自行生成、修改或升级 Skill。Codex 对已有 Skill 的自动选择与 Skill 的创建、更新是两个不同动作。

共学主对话中的 GitHub 裸地址视为一次性人工提名，其他对话必须表达“学习这个项目”等明确意图。自有项目由“将当前项目加入 Ai Notes”登记，保存规范路径、仓库标识、名称和加入原因；MVP 只使用当前项目。未来多项目共享每日最多三个机会的全局预算，每条结果标注目标项目。

Python 负责采集、缓存、账本和确定性校验。自动运行只能写 Ai Notes 的批准运行目录，自有项目始终只读；可以读取未提交工作区和 diff 来识别当前问题，但不复制或持久化完整 diff。配置使用 YAML，追加账本使用 JSONL，单次产物使用 JSON/Markdown；Schema、策略和获批 Skill 进入 Git，运行数据和缓存留在本地且不自动提交。

MVP 不使用 GitHub 凭据，匿名限额导致的缺失必须标为 `partial`。深读优先使用 API 和官方网页；仅当最终候选证据不足时，才浅克隆明确 commit 到隔离缓存，且不执行 hooks、submodule、安装、构建、测试或仓库代码。
