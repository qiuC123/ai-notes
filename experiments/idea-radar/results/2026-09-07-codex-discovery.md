# Codex 中的真实项目发现测试

用户请求：在 Codex 中找几个项目并用表格展示。运行日期：2026-09-07。

实际调用开发方向雷达原有 `execute_job` 和 `radar_analysis.py`，使用隔离目录 `work/idea-radar-codex-test-20260907`，没有发送飞书消息，没有修改机器人程序。

输入范围：适合个人 Python 开发者、约两周可验证的文档处理、轻量自动化、信息监测方向；要求三个具体项目并区分现成产品与衍生假设。

## 运行结果

真实搜索、网页采集、Pi 分析、JSON 与引文校验成功。取得 12 条搜索摘录和 2 篇正文；1 篇正文读取失败。模型给出 Ocrbase、Signbee、UptimeRobot 三个候选。

记录目录：`work/idea-radar-codex-test-20260907/runs/1a9d2a007de04d999bf8b42c8860bed0/6749ad3e83c64a1184a8031b457ca375/`。

该目录保留 `evidence.json`、`report.json`、`report.md`、Pi 会话及模型输出；测试入口和结果指针位于隔离状态目录的 `test-input.json`、`test-result.json`。

## Codex 追加核查

- [Ocrbase 官方仓库](https://github.com/majcheradam/ocrbase)确认 PDF/图片到 Markdown、JSON 的自托管 OCR API。原机器人对准确率痛点的依据是 HN 搜索摘录，不是复现测试，未取得购买或收入证据。
- [Signbee 官网](https://signb.ee/)确认面向 Agent 的文档签署入口。[HN 发布讨论](https://news.ycombinator.com/item?id=47396566)有试用意向及作者关于重复请求的说明；这是历史讨论，未实测当前 API 的幂等行为。
- [UptimeRobot 官方说明](https://uptimerobot.com/cron-job-monitoring/)确认已经支持 cron/heartbeat 漏跑监测。因此，机器人提出的“定时任务漏跑监测”只能是个人集成或细分场景验证方向，不能作为原产品缺少该能力的竞争机会。
- 机器人使用的 [Psono 使用记录](https://psono.com/zh/blog/tech-behind-one-man-saas)发表于 2022-06-26，是历史用户使用陈述，不是当前增长指标。

## 本次发现的限制

现有引用校验能够约束引文来源，却不会自动查验原产品当前是否已经具有所提议的功能。本次 Codex 人工追加的官网核查发现了这个具体缺口。搜索也混入中文项目和一般 MVP 教程，说明“海外、不同品类”的检索精度仍需提高。

本记录是如实测试结果，未自动修改检索策略或扩大机器人采集范围。
