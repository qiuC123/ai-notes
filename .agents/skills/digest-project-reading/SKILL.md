---
name: digest-project-reading
description: 为三榜候选定向读取原文，内部核对用途、操作、条件和阅读范围；正文采用发现式介绍时不要求写成说明书，不替代评分或软件实测。
---

# 三榜项目阅读

用于当前三榜流程中候选的资料理解。后台由 `ai_notes.digest_reading` 和 `ai_notes.digest_understanding` 实现同一阅读方法；安装本 Skill 不会自动改变模型 API。

1. 先确定对象：完整项目、具体更新／新闻事件、阅读文章。沿用 `TASK.md` 和已冻结的三榜规则。更新只评价该事件，新闻和阅读不套开源项目的安装／许可证门槛。
2. 带着问题读取：谁用它完成什么；输入什么、输出什么；怎样进入；哪些条件必需、哪些只适用于某功能／渠道；收费或限时条件影响谁。
3. 仓库按同一 commit 读取 README、实际许可证与相关使用文档，必要时才补读相关代码。使用模块只读入口：

   ```powershell
   .venv/Scripts/python.exe -X utf8 -m ai_notes.digest_reading --root . --url https://github.com/owner/repo --max-documents 4 --max-chars 14000
   ```

4. 保存原文、出处、文件路径、commit、哈希、时间和具体阅读范围。Gitingest 仅整理已经取回的选定文件；它输出的统计或第三方 AI 总结不充当原始事实。
5. 整理有段落引用的目的、输入、操作、输出和条件。兼容修复不能变成最低依赖；文本转语音与语音转文字不同；促销必须保留适用模型、计划和时限；片段不能写成已读全文。未知明确留空。
6. 评分和复核必须读取同一份事实卡与原文。卡片本身也是待核对模型判断，段落引用存在不代表含义正确。程序能核对范围与结构，不能证明所有语义。
7. 每候选按已保存输入和回执恢复，不循环付费重试，不安装候选软件、不恢复已暂停的生产任务。新合同不改写旧冻结输入、分数或归档。

`introduction_contract=discovery.v1` 时，正文是名称、用途、亮点、已知支持系统和链接的发现式介绍，配图按需。前述输入、输出、操作和条件用于内部核对，不要求正文全部展开。复核只拦实际写出的实质事实错误；只有遗漏使已写出的主张错误或实质误导时才 defer，不因没有列全条件、操作和步骤而拒绝。内部卡不充当说明书。已知支持系统须单独绑定原文证据；未知或不适用留 null，不能从使用条件字符串自动猜。这个 marker 不改评分权重、门槛、flags 或来源等级，缺省旧合同沿用原冻结规则。

方法借鉴 [Repomix Explorer](https://github.com/yamadashy/repomix/blob/main/skills/repomix-explorer/SKILL.md) 的定向搜索和文件证据原则；原文整理工具为 [Gitingest](https://github.com/coderamp-labs/gitingest)。本 Skill 面向普通读者的工具用途介绍，不要求建立全仓架构或代码审计报告。
