# 五项工具发现预览（2026-10-06）

这五条原模型短介绍经原文核对可以展示，供发现工具使用。评分理由的引用检查尚未通过，本页不是正式榜单，也没有安装或试用这些软件。

详细实验结果见 [评分输入修复与实测](../../docs/DIGEST_SCORE_INPUT_FIX_2026-10-06.md)。

## Basic Memory：把项目经验存成 AI 可检索的 Markdown 知识库

Basic Memory 是一个本地优先的 AI 记忆工具：知识以普通 Markdown 文件存在你自己的磁盘上，AI（Claude、Codex、Cursor、ChatGPT 等支持 MCP 的客户端）和人可以共同读写与检索，跨会话记住项目上下文。亮点包括本地纯文本存储不锁定、双向同步、语义搜索，以及可选的云端跨设备同步。

[项目仓库](https://github.com/basicmachines-co/basic-memory)

## PAL MCP：在 AI 助手之间委派任务与传递上下文

PAL MCP（原名 Zen MCP）是一个 MCP 服务器，把 Claude Code、Gemini CLI、Codex CLI 等命令行 AI 工具连接到 Gemini、OpenAI、Anthropic、Ollama 等多种模型，进行多模型协作与上下文延续。亮点包括：clink 工具可在当前 CLI 内启动隔离的子 CLI 实例执行代码审查等重任务，只回传最终结果；跨工具的对话上下文延续；可指定或自动选择模型完成不同子任务。

[项目仓库](https://github.com/BeehiveInnovations/pal-mcp-server)

## rclone：在本地与云盘之间复制和同步项目资料

命令行文件工具，可在本地文件系统与大量云存储之间复制、同步文件和目录，被称为“云存储版的 rsync”。代表亮点：支持 Google Drive、OneDrive、S3、Dropbox 等几十种存储后端；copy/sync 等命令对所有远程存储都可用；可配置后把配置复制到其他机器。

[项目仓库](https://github.com/rclone/rclone)

## Czkawka / Krokiet：查找重复文件、相似图片和空文件夹的本地清理工具

一套用 Rust 编写的本地文件清理工具，可按名称、大小或哈希查找重复文件，找出空文件夹、空文件、临时文件、最大文件，以及相似图片、相似视频和重复音乐等。仓库现在主推新 GUI 前端 Krokiet（支持 Linux、macOS、Windows），另提供命令行 CLI、核心库和安卓触屏前端 Cedinia；旧的 Czkawka GTK 前端 12.0 是最后发布的版本，不再提供新安装包。

支持系统：Krokiet 图形界面支持 Linux / macOS / Windows；另有 Android 应用 Cedinia（视频工具在安卓上不可用）

[项目仓库](https://github.com/qarmin/czkawka)

## Joplin：收集、标记与同步项目笔记和附件

免费开源的离线优先笔记与待办应用：笔记可按笔记本组织、打标签、全文搜索，支持 Evernote 与纯 Markdown 导入，并可经 Nextcloud、Dropbox、OneDrive、Joplin Cloud 等服务端到端加密同步；另有 Firefox/Chrome 网页剪藏扩展。

支持系统：Windows / macOS / Linux / Android / iOS

[项目仓库](https://github.com/laurent22/joplin)

---

系统资料未明确的条目省略系统行；没有据安装方式推测。程序原始评分与独立核对状态保存在 [预览清单](v14-reader-preview-2026-10-06.json)。
