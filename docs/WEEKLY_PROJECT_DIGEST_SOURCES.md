# 周报栏目与来源对照

2026-09-07。根据用户确认的方向整合：实用开源项目为主，兼顾开发机会；合并语言栏目，Skills、AI 应用、Agent 与自动化、MCP 与工具连接、模型与运行工具、游戏、开源书籍与教程分别独立，与开源项目组成八个并列栏目。AIHOT 等聚合平台另列。

这是来源规划与当前状态清单，不表示下列来源已全部接入。现有 12 个 Release 源见 `config/ai_notes_sources.yaml`，雷达搜索策略见 `experiments/idea-radar/radar_analysis.py`；旧 AIHOT 等配置见 `config/sources.yaml`。完整内容分类与卡片字段见 [输出草案](WEEKLY_PROJECT_DIGEST_OUTPUT_DRAFT.md)。本轮只更新文档，没有修改采集器或定时任务。

## 1. 各栏目使用的原始资料

| 栏目 | 原始资料来源 | 看什么 | 本机接入状态 |
| --- | --- | --- | --- |
| 开源项目 | 候选的 GitHub 仓库、README、Release、官方文档、Issue / Discussion | 真实功能、安装门槛、维护变化、用户问题 | GitHub 只读渠道已使用；通用项目目前按需发现，没有全量订阅 |
| Skills | [Anthropic skills](https://github.com/anthropics/skills)、[OpenAI skills](https://github.com/openai/skills)、技能作者仓库的 SKILL.md 与脚本 | 解决的任务、适用 Agent、依赖、实际技能变化 | 两个官方仓库本轮已读到，尚未加入固定采集；不保证每项许可证和兼容性相同 |
| AI 应用 | 应用官网、帮助文档、官方变更记录、开源仓库、作者发布的 Hugging Face Spaces 页面 | 实际可用能力、限制、费用、有意义的更新 | 现有雷达按需搜索；没有独立 AI 应用订阅表 |
| Agent 与自动化 | Codex、Claude Code、OpenHands、Hermes、OpenAI Agents、LangGraph、Google ADK 的官方仓库与文档 | 新能力、重要修复、兼容变化，对用户有什么影响 | 已列入 12 源中的 7 个 Release 源，均 `trial`，不代表持续无人值守运行 |
| MCP 与工具连接 | MCP 规范官方仓库、具体 MCP 服务作者仓库和文档 | 协议实质变化、连接的服务、工具能力、认证 / 调用费用 | 规范已有 Release 配置；具体 MCP 服务按候选读取。官方 Registry 另列为发现入口 |
| 模型与运行工具 | Ollama、vLLM、SGLang、Transformers 官方仓库；[Hugging Face 模型作者页面](https://huggingface.co/models) | 新硬件支持、可运行条件、用户能感知的能力与性能变化 | 前四个已有 Release 配置；HF 模型来源仅旧配置保留，本轮未恢复定时采集 |
| 游戏 | [itch.io](https://itch.io/) 作者游戏页 / devlog / [Game Jam 作品](https://itch.io/jams)、[Steam](https://store.steampowered.com/) 游戏页 / 公告 / 玩家评论、[CrazyGames](https://www.crazygames.com/) 与 [Poki](https://poki.com/) 具体游戏页、游戏作者 GitHub | 核心玩法、设备、试玩、更新、玩家反馈、开源状态 | itch.io / CrazyGames / Poki 已在雷达游戏查询词内；本轮入口可读不代表游戏已试玩、各平台 API 已接通；Steam 单游戏深读建议补充 |
| 开源书籍与教程 | 作者书籍仓库、官方教程、课程作者原站、可操作示例 | 教什么、适合谁、前置知识、练习和可用性 | 目前按链接阅读，没有专门固定来源；每项核对许可，免费阅读不等于开源 |

同一宿主平台内也要区分发布者：GitHub 作者仓库是项目原文，GitHub Trending 是发现榜单；Steam 商店描述来自开发商，Steam 评论来自用户；Hugging Face 模型卡是作者陈述，不是独立评测。

## 2. 聚合、精选、榜单与发布社区（单列）

这些入口用来发现线索，随后仍回到上表原始资料。单列是来源管理，不把同一项目再发一次。

| 平台 | 类型 | 对应栏目 | 当前状态 / 用法 |
| --- | --- | --- | --- |
| [HelloGitHub](https://github.com/521xueweihan/HelloGitHub) | 中文编辑精选 | 八个栏目均可从中寻找线索 | 已加入当前 Codex 周报任务的来源说明；月刊每月 28 日发布，本轮前已读取第 125 期。未改飞书雷达采集器 |
| [AIHOT](https://aihot.virxact.com/) | AI 新闻聚合（按本项目旧配置定位） | AI 应用、Agent 与自动化、MCP 与工具连接、模型与运行工具；Skills 补充 | 旧配置有 selected、hot-topics 两接口且 `evidence_allowed: false`；本轮主页未取得可用正文，接口未重新实测，不算已恢复的周报源 |
| [HN / Show HN](https://news.ycombinator.com/show) | 作者发布与技术讨论社区 | 开源项目、AI 应用、Agent 与自动化等，以及游戏、开发机会 | 现有雷达按需搜索、周报已列入；不是全站完整同步 |
| [Product Hunt](https://www.producthunt.com/) | 产品发布和社区榜单 | 开源项目候选、AI 应用、开发机会 | 已在雷达查询词内，官方 API 未完成接入；产品并非都开源 |
| [GitHub Trending](https://github.com/trending) | GitHub 热门发现榜单 | 各类开源工具、Skills 和游戏 | 建议作为补充发现入口，尚无专属采集器；不按 stars 直接选入 |
| [MCP Registry](https://registry.modelcontextprotocol.io/) | 官方维护的 MCP 服务注册目录 | MCP 与工具连接 | 本轮已读到入口，建议补充；目录官方不表示所有服务均由官方制作、审计或认证 |
| [SteamDB](https://steamdb.info/) | 第三方 Steam 数据整理平台 | 游戏、游戏开发机会 | 已在雷达游戏查询词内；本轮主页可读，玩家数、价格等必须具体核对，不能把热度当成销量或收入 |
| [阮一峰科技爱好者周刊](https://github.com/ruanyf/weekly) | 中文编辑精选、每周五发布 | 开源项目、开源书籍与教程、部分 AI 应用与工具 | 本轮核验官方仓库，新增备选，尚未进入固定采集；与 HelloGitHub 做项目去重 |

CrazyGames / Poki 的目录、itch.io / Steam 的榜单也可以用于发现；上表游戏行只把具体游戏说明与试玩入口作为作品资料来源，不把榜单排名当玩法或需求证据。

## 3. 用户反馈与开发机会

开发机会是独立的观察部分，不另算项目类型，可来自任何栏目。优先读 GitHub Issues / Discussions、HN 评论、Reddit r/SaaS / r/SideProject / r/webgames / r/gamedev、Steam 玩家评论，以及 Product Hunt 讨论。Reddit 目前仅有按需搜索线索，不宣称授权 API 或浏览器登录渠道已接通。

Acquire.com 是前期讨论过的产品交易线索平台，可作为付费线索备选；本轮未核验访问和列表内容，没有接入，卖方收入陈述不能直接当审计收入。

每条机会必须交代：问题与人群、用户原文及日期、已有方案、是否已修复 / 已实现、可以验证的小切口、未知项。模型生成的主意不能冒充用户需求。

## 4. 周报的共同边界

- AI 工具的新发现与重要更新按主要用途分别归入 AI 应用、Agent 与自动化、MCP 与工具连接、模型与运行工具；CLI / API / 自托管 / 平台等作标签，CLI 不局限于 AI。
- 游戏独立成栏，可收闭源玩法观察，但必须注明非开源 / 未核实；一般开源项目栏仍需核对源码和许可证。
- 不把不同站点转述的同一公告计算为多条独立证据；按规范项目地址去重。
- 模型、框架、标准只写对具体使用有意义的变化；arXiv 论文默认不进主刊。
- Exa / Jina 是采集工具，Pi 是分析组件，飞书 / Codex 是输出入口，都不是原始内容来源。
- 当前仍沿用精简周报篇幅，不为每个栏目规定必须凑齐的条数。未修改 Sunday 20:00 的既有调度或扩大自动采集范围。
