# 每周实用开源项目汇总

2026-09-07：用户选择“实用开源项目为主，兼顾开发机会”，并指定增加 HelloGitHub。
本文件是周报的来源清单与编辑规则；不是旧采集器的配置文件，不表示飞书机器人已加载这些规则。投递位置、调度状态以实际自动化记录为准。

调度已创建并读回：Codex 当前任务的 heartbeat，名称“每周实用开源项目汇总”，ID `automation`，状态 `ACTIVE`，每周日 20:00（本机北京时间）。用户已确认内容方向；投递位置尚未回复，本轮采用已说明的默认项“当前 Codex 任务”。尚未完成第一次定时执行，不代表已验证周报端到端运行。

## 当前来源盘点

| 路径 / 入口 | 已有内容 | 当前边界 |
| --- | --- | --- |
| `config/ai_notes_sources.yaml` | 12 个 GitHub Release Atom 源，全部 `trial` | 配置存在不代表每周运行；适合跟踪已知项目更新，不能代替新项目发现 |
| `src/aihot/ai_notes.py` | 独立 GitHub 安全公告采集，映射已注册仓库或包 | 不是综合新闻源 |
| `src/ai_notes/learning.py` | 用户提名、Agent 主动发现及来源记录 | 主动发现最多 20 个初筛、5 个深读；不是固定订阅网站列表 |
| `experiments/idea-radar/radar_analysis.py` | Exa 搜索 + Jina 正文读取 | 按需运行，最多 3 组搜索、每组 4 条、3 篇正文；并非所有平台的完整直连 |
| `config/sources.yaml`、`config/scoring.yaml` | 旧 0.1 新闻来源和打分规则 | 旧配置，不能当成当前周报已经启用的来源或筛选标准 |

12 个 Release 来源：MCP 规范、OpenAI Agents Python、LangGraph、Google ADK Python、Hermes Agent、Claude Code、Codex、OpenHands、Ollama、vLLM、SGLang、Transformers。

雷达的工具类搜索覆盖 HN Show HN、GitHub、Product Hunt、Reddit r/SaaS / r/SideProject；小游戏搜索覆盖 itch.io / Game Jam、Reddit r/webgames / r/gamedev、CrazyGames、Poki、SteamDB。这里的“覆盖”仅指查询词，不证明每个平台都有结果或可读正文。

旧 0.1 配置共 11 个入口：AIHOT 两个聚合接口、OpenAI News、Hugging Face Blog、Hermes / MCP servers / OpenAI Python / Transformers 四个 Release Feed、Hugging Face 热门模型、arXiv cs.AI / cs.CL。它们与 12 源部分重叠，不可相加后宣称独立来源数量。

检查本机 Windows 计划任务未发现关联 Ai Notes / idea-radar 的任务；检查 Codex automations 配置未发现相关周报。此项是本轮创建任何新调度之前的快照。

## 周报来源清单

| 来源 | 用途 | 获取方法与边界 |
| --- | --- | --- |
| HelloGitHub（用户指定新增） | 编辑精选的实用开源项目 | 首选 `521xueweihan/HelloGitHub` 的 `content/HelloGitHub*.md`；取得项目链接后再读项目官方仓库。月刊每月 28 日发布，没有新一期时不重复充当周更新 |
| Hacker News / Show HN | 发现新项目与使用反馈 | 官方公开 API 或搜索发现后读取讨论原文；评论与作者宣传分开 |
| GitHub 官方仓库、Release、Issue / Discussion | 核对功能、维护状态、已解决问题 | 直接读取公开原文；GitHub 搜索与 Trending 仅提供发现线索，不把 star 排名作为入选依据 |
| 用户提交的项目、当周共学候选 | 保留个人相关性 | 回读官网或仓库，不仅复述之前的聊天结论 |
| Reddit、Product Hunt（补充） | 对值得深读的候选补查用户反馈、替代品与购买陈述 | 按当前可用权限与工具获取；失败记录缺口，不宣称全面采集 |

Exa 是发现工具、Jina 是正文读取工具、Pi 是分析组件、飞书是投递入口，均不算独立的原始信息源。

HelloGitHub 核验：

- 官方频率说明：<https://github.com/521xueweihan/HelloGitHub>。
- 已实际读取第 125 期：<https://github.com/521xueweihan/HelloGitHub/blob/master/content/HelloGitHub125.md>。
- GitHub API 核验该文件存在，blob SHA `4c49ca511a9e5a70cf3fcec30ae2e4888c1098f5`，大小 27,147 bytes；这不是周报时间范围内发布的证明。
- 官网另有 Latest / Monthly / Yearly 入口；本轮没有核实公众号所有文章的更新频率，不能把“月刊月更”等同于“所有内容每月只更新一次”。
- 月刊用于发现并注明出处；周报回到项目原始资料独立编写短评，不搬运整期文章。

## 筛选规则

每期最多 5 个实用项目、2 条开发机会，宁缺毋滥。优先 Windows 日常效率、文件与资料管理、开发辅助、自动化；不限 AI，也不要求每项都能集成现有项目。

实用项目入选必须说明：

1. **解决什么事**：明确对象和实际操作场景，不只写“强大”“智能”。
2. **为何值得看**：相对用户已有工具或常见替代品，至少一个具体差异；已有 OCR 能力的普通重复方案降权。
3. **证据在哪里**：项目原文可读、有实际源码及许可证信息；无法确认则进入待核验，不假称已核实的开源工具。
4. **使用门槛**：操作系统、安装方式、是否依赖服务器 / GPU / 付费 API；区分“文档声称”和“本机实测”。
5. **本周理由**：是本周新发现、正式发布实质变化，还是旧项目回顾；发现日期与发布日期分别记录，不能把旧项目写成本周新发布。

开发机会必须额外具备：可引用的具体用户问题、对现有功能/替代品的核对、可执行的小验证。没有独立用户证据的创意不放入开发机会正式栏目。收入自述标“作者/用户自述”；价格表不等于购买，stars 不等于付费需求。

重复处理：按规范仓库地址合并不同来源；近四期已推荐且无实质变化的项目不重推。用户明确“已有 / 不需要”的方向降权；“有用 / 想试”用于下一期选择，不自动写入 Codex 记忆或项目规则。

## 时间与输出

- 默认统计窗口：运行时刻向前 7 天，以 Asia/Shanghai 标注起止；错过执行时明确覆盖缺口，不伪造完整周覆盖。
- HelloGitHub 当周无更新可补充少量尚未推荐的历史精选，必须标“历史精选 / 本周首次收录”。
- 每期表格：项目与原文链接｜解决的问题｜值得看的理由｜使用门槛｜证据与新旧状态｜建议（试用 / 收藏 / 跳过）。
- 开发机会另列：用户问题及日期｜原文｜现有方案｜待验证差异｜最小验证。
- 末尾列实际读取来源、失败来源及未核实事项。采集不足可交付部分结果；不能用旧内容补齐后假称完整。
- 本轮采用每周日 20:00（北京时间），在当前 Codex 任务生成周报；用户后续可修改时间或投递入口。电脑需保持开机、联网且 Codex 应用运行。

本清单是独立周报编辑规则，不修改旧 Release `trial` 等级，不启动全量回测，也不改变现有飞书雷达每轮最多 3 个候选的运行逻辑。
