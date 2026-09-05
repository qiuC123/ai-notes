# 项目化学反应 Agent：Pi 可行性实验

状态：只读证据工具、报告校验和 Pi 启动入口已实现。首次真实盲测出现 WebSocket 错误并超时；补日志、改用 SSE 后成功封存，双侧证据完整性从 0.5 提升到 1.0，测试精确匹配为 4/9。现已将新报告的执行元数据交给运行器，并冻结[第二组案例](../cross-project-impact-v2/README.md)。旧报告和旧分数不变。详见 [首次盲测记录](results/2026-09-05-first-blind-run.md) 和 [SSE 重跑复核](results/2026-09-05-sse-rerun.md)。尚未接入生产。

这是独立于 Ai Notes 正式学习流程的实验目录，不是完整产品。第一步先验证 Pi 能否改善已有跨项目分析中的两项短板：**双侧证据**和**既有测试定位**。不修改正式 CLI、账本、来源排序或任何样本项目。

## 使用入口（Windows）

先只检查本机依赖、两个冻结提交和可读文件；不调用模型、不启动 Pi、不写实验产物：

```powershell
E:\devlop\ai-notes\experiments\project-chemist\start.ps1 -Check
```

启动一个独立 Pi 会话：

```powershell
E:\devlop\ai-notes\experiments\project-chemist\start.ps1
```

在 Pi 中输入：

> 分析这次冻结输入里的五个案例。逐案查找双方代码证据和既有测试；不能确定就说明缺口。完成后通过 chemist_submit 提交报告，不要实施修改。

也可以执行一次有界的无人值守盲测（使用同一输入、工具与现有模型）：

```powershell
E:\devlop\ai-notes\experiments\project-chemist\start.ps1 -Run
```

`-Run` 使用 RPC，先核对扩展和六个工具，再发送上述固定分析请求；15 分钟超时，达到 120 次工具调用停止。等待 `agent_settled` 后才判断结果，退出码为 `0=已封存`、`2=未完成`、`1=运行失败`。封存仍不是评分通过。仅支持标准 npm 安装布局，其他安装方式使用交互入口。运行器不会将主对话历史、评分标准或答案发送给 Pi。

无人值守入口现在默认 SSE（HTTP 流），在新运行目录生成唯一的 `.pi/settings.json` 并仅批准该隔离目录的配置；不更改全局 Pi 设置，也不批准样本项目配置。请求/流空闲时限为 120 秒，Agent 层最多重试 3 次（2 秒起指数退避），Provider 层重试为 0，服务端要求等待超过 60 秒则失败。重试耗时仍计入总计 15 分钟。底层 `run.py --transport auto` 可显式恢复自动传输用于对照，交互入口保留用户原有传输设置。

`/chemist-status` 可以检查加载状态，不调用模型。必须使用启动脚本；不要直接在 Ai Notes 主目录里加载扩展开展盲测，也不要将标准答案、旧分析或评分结果粘贴给这次 Pi 会话。

启动器使用用户现有 Pi 模型配置，不保存或复制凭证、不改全局设置。**用户开始分析后，工具返回的授权项目源码片段会进入所选模型的上下文，可能发送到远端模型服务。** 初版开发仅做无模型加载检查，后续真实盲测由用户明确确认后运行。

启动器创建独立临时目录并显示绝对路径，保留：

- `blind-input.json`：本次输入副本；
- `tool-audit.jsonl`：本次调用参数、成功状态及错误，不保存完整源码响应；
- `baseline.json`：只有校验通过后才独占创建，存在则禁止覆盖。
- `run-manifest.json`、`final-response.txt`：仅 `-Run` 生成，记录实际模型、运行时长、调用计数、运行代码哈希和最终回复，不保存完整思考过程或源码工具响应。
- `events.jsonl`：仅 `-Run` 生成，记录事件时间、工具状态、错误类别、重试次数/延迟与结束状态；错误原文只留哈希和长度，不记录正文、URL、头部或凭证。
- `provider-events.jsonl`：模型请求和响应时间、HTTP 状态码，不读取/记录请求正文或响应头。交互入口也会生成。

运行过程中每 15 秒报告一次心跳，并定期刷新 manifest，区分仍在接收流与没有新响应。重试最终失败、RPC 流关闭或扩展异常会明确结束，不再只能等总时限。Pi 内部未暴露的传输层重试不能由这些日志推断出来。

不保存 Pi 会话历史。临时目录不会被启动器自动删除；需要长期保留结果时由用户归档，系统清理临时文件可能使其丢失。提交结果不等于用户确认，不进入学习账本。

## 能做什么

| 工具 | 作用 |
| --- | --- |
| `chemist_context` | 获取五案、项目、固定版本、输入哈希和本次任务 ID |
| `chemist_inventory` | 分页列出固定提交内的允许文件，可按文件名片段筛选 |
| `chemist_search` | 在指定文件中做字面文本搜索，不执行正则或 shell |
| `chemist_read` | 读取固定文件的有界行范围，返回行号 |
| `chemist_test_selectors` | 静态解析 Python 测试名，不导入、不收集、不运行测试 |
| `chemist_submit` | 校验并封存符合 `impact-baseline.v1` 的报告 |

文件路径均相对于授权项目目录；例如 monorepo 内的 `CLI/wechat-oa` 只能看到该子树，不能看到同仓库其他项目。工具使用冻结 Git blob，不读取样本工作区、切换分支或执行样本代码。

直接依赖报告必须提供双方的文件和准确行范围、双方已有测试。测试 selector 用 `ClassName.test_method` 或顶层 `test_function`；不支持动态生成、参数化实例或非 Python 测试。找不到支持的测试时报告未完成，不能编造名称来过校验。

`chemist_submit` 只接受 `analysis.cases` 和两项显式为 false 的 `analysis.blind_attestation`。套件 ID、输入哈希、任务 ID、冻结仓库信息和 UTC 完成时间由运行时生成；模型不能传入或覆盖。历史 v1 报告结构的可选 `read_method` 仅允许 `git show`，新报告省略该字段，实际 `git cat-file` 读取方法记录在 `run-manifest.json`，不修改旧 schema。声明未接触答案是模型声明，不是单凭该字段就能证明隔离。

校验仅确认 JSON 结构、固定版本、引用范围、可选 symbol 字面存在以及静态测试定义存在。**不证明引用支持结论，不证明测试可收集/通过，也不证明项目间确有关系。** 语义正确性仍需封存后的独立评分和人工复核。

## 范围和限制

- 默认入口仍使用第一组五案；第二组必须显式传入 `-InputFile`，见其 README。不实现外部项目搜索、GitHub URL 提名、网页采集或每日推送；后续才连接“发现 → 理解 → 关联 → 实验”。
- 两个样本使用历史冻结提交。招聘雷达当前已经切换官网-only；实验对旧契约的分析**不能作为当前依赖现状**。
- 无知识图谱、索引、本地模型、Hermes；不读取/写入 Codex 记忆，不创建 Skill，不自动升级项目规则。
- 禁用 Pi 内置工具、自动扩展、Skills、模板和上下文文件；仅加载本扩展，并对模型工具调用设置白名单。每个会话最多 120 次 worker 调用。
- 这是应用层工具限制，不是操作系统沙箱。Pi 程序及本扩展属于受信任运行时；用户若手动加载其他扩展、执行 shell、修改输入或提供答案，必须废弃该次盲测。
- 只读文本文件最多 512 KiB，单次读取最多 200 行/24,000 字符。大文件、非支持扩展名、symlink、submodule、常见凭证文件名等跳过并计数；这不是完整的秘密扫描器，不保证普通源文件内没有硬编码敏感信息。
- 缺失提交、空文件目录、超时、超预算或引用无效均明确失败，不能当健康空结果。Git 禁止自动 lazy-fetch。

## 验证与下一步

自动化离线回归：

```powershell
E:\devlop\ai-notes\.venv\Scripts\python.exe -m unittest discover -s E:\devlop\ai-notes\tests -p test_project_chemist.py -v
```

安装版 Pi 的真实加载检查（只调用 `/chemist-status`，没有模型推理）：

```powershell
E:\devlop\ai-notes\.venv\Scripts\python.exe E:\devlop\ai-notes\experiments\project-chemist\smoke.py --pi-cli C:\Users\Mayn\AppData\Roaming\npm\node_modules\@earendil-works\pi-coding-agent\dist\bundle\cli.js
```

本机使用 Pi 0.85.0 的内置文档/API，未新增 npm 依赖。换机需使用对应实际安装路径和支持这些 CLI 参数的 Pi 版本；Python 使用 Ai Notes 现有 `.venv`。

连接日志、SSE 传输、运行时元数据及新案例测试名约定已实现。只有封存 `baseline.json` 后，知晓标准答案的主对话才使用冻结评分器；不修改旧套件或评分规则来提高分数。五案结果只用于判断是否值得继续，不代表通用关联发现能力，更不自动批准接入生产。
