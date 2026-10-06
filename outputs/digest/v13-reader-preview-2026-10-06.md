# 项目发现预览：多 Agent、文件整理与项目知识库

2026-10-06。这次围绕你新确认的兴趣找了 7 个项目，并实际读取仓库和官方文档。表格是按原文人工整理的发现介绍，方便你判断兴趣；完整自动推荐通过的项目另列。这里没有软件安装或功能实测，也不是正式日、周、月榜。

| 项目 | 能帮你做什么 | 值得看的亮点 | 支持系统 |
| --- | --- | --- | --- |
| [AionUI](https://github.com/iOfficeAI/AionUi) | 在图形工作区里组织多个 AI Agent 围绕同一个项目协作。 | 负责人拆分任务，队友并行执行；共享文件夹、任务板和消息邮箱，官方列出的后端包含 Codex。 | Windows / macOS / Linux |
| [PAL MCP](https://github.com/BeehiveInnovations/pal-mcp-server) | 让当前 AI 助手把任务交给 Codex、Claude Code、Gemini CLI 等子 Agent，再收回结果。 | 子 Agent 使用独立上下文；可以按规划、审查等角色分工。 | 已核实 Windows（通过 WSL） |
| [Czkawka](https://github.com/qarmin/czkawka) | 找出项目目录里的重复文件、相似图片和空文件夹，帮助整理。 | 可按文件名、大小或哈希找重复；提供图形界面和命令行。 | Windows / macOS / Linux |
| [PeaZip](https://github.com/peazip/PeaZip) | 用图形界面把项目资料压缩、加密和分卷，方便保存或传给别人。 | 支持 200 多种归档格式；打包任务可以导出命令行脚本，供以后重复使用。 | Windows / macOS / Linux / BSD |
| [rclone](https://github.com/rclone/rclone) | 用命令行在本地和云盘之间复制、同步项目资料。 | 支持 Google Drive、OneDrive、S3 等存储；也能在两个云存储之间同步迁移。 | Windows / macOS / Linux |
| [Basic Memory](https://github.com/basicmachines-co/basic-memory) | 把项目过程和成果保存为 Markdown 知识库，让后续 AI 读取、检索和接续。 | 人与 AI 读写同一批本地文件；通过 MCP 接入 Codex 等助手，支持语义搜索和知识关联。 | 已核实 Windows / macOS |
| [Joplin](https://github.com/laurent22/joplin) | 按项目整理笔记、标签和网页资料，方便以后找回。 | 笔记本、标签、全文搜索和 Markdown 导入；离线查看及网页剪藏。 | Windows / macOS / Linux / Android / iOS |

更贴近你当前目标的三个方向是：**Basic Memory** 用于项目知识复用，**AionUI** 用于多 Agent 协作，**Czkawka** 用于整理已有文件。这是根据你表达的兴趣作出的编辑建议，尚未取得本批项目的实际兴趣反馈。

Codex 的对接有原文资料；WorkBuddy 与这些工具的互通尚未验证。PAL 的 WSL、Basic Memory 和 rclone 的系统信息来自另读的官方资料，只用于本表，没有追加到已经冻结的模型输入中。

## 本轮自动流程的实际结果

| 项目 | 实际评分与复核 | 独立核对 |
| --- | --- | --- |
| AionUI | 68 分，程序推荐，模型内容复核接受。 | 本轮公开介绍、实际重点展开和各项实质评分理由的引用通过。 |
| PeaZip | 65 分，程序推荐，模型内容复核接受。 | 本轮公开介绍、实际重点展开和各项实质评分理由的引用通过。 |
| Basic Memory、PAL MCP、Czkawka、rclone | 评分输入超过本地 60,000 字符上限，在发送请求前停止。 | 用途短介绍有据；没有完成评分，不能记为自动推荐通过。 |
| Joplin | 评分响应多出合同不允许的 `reason_note` 字段，程序拒绝。 | 短介绍有据；评分中同步和浏览器事实引用到安装页，实际出处在 README，完整推荐暂缓。 |

本轮共 **13 次真实模型请求、零付费重试、215,311 tokens**，账单金额未知。7 项原文核验响应的必填字段完整；两项完整推荐通过不代表整体筛选策略已校准。原始响应及失败记录均保留，自动化继续 **PAUSED**。

完整记录见 [实测报告](../../docs/DIGEST_V13_FEEDBACK_PILOT_2026-10-06.md)；可读清单与逐字段出处见 [预览 JSON](v13-reader-preview-2026-10-06.json)。

## 阅读反馈

可以直接按项目名标记：**想试／有意思／一般／不感兴趣**。先记录真实反馈，再决定下一轮发现方向；不把上述机器分数当成你的偏好。
