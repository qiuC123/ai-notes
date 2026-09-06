# Repomix 共学：让采集边界可见

结论：本轮找到一个有实际样例支持的改进方向——除了提供已采集证据，还说明本地限额省略了什么。接受为待实施建议，不安装 Repomix、不扩大采集范围、不改正式排序或状态含义。

## 学习对象与方法

从上一轮候选中选择 Repomix，以“文件筛选和未处理内容如何报告”为问题。主对话通过 GitHub 搜索、固定版本源码定位，沿读取、汇总、返回和异常分支复核；Pi 使用未修改的共学入口独立分析。没有复用上轮 6,000 字符静态排序实验，也没有声称 Pi 自行发现项目或完成全仓搜索。

- 外部仓库：`yamadashy/repomix`，冻结提交 `85e3969b010c72b905203812d1a3f5beb84a2102`；元数据标记 MIT、非归档、非 fork。
- 自有仓库：`e18ad8bf1b76d37b915fbd9755b5c0bb9c3158ed`，准备及复核时工作区干净；Pi 限于 `storage.py`、`learning.py`、`review.py`。
- 队列有 10 份外部证据：元数据、README、有限文件树、fileSearch/fileRead/fileCollect/packager/validateFileSafety 实现，以及 fileCollect/fileRead 测试源码。没有运行外部项目或其测试。
- 仍是一次有人工选择范围的共学，不是独立盲测、性能对照或生产验收。

## 外部方法及反例

[fileRead.ts](https://github.com/yamadashy/repomix/blob/85e3969b010c72b905203812d1a3f5beb84a2102/src/core/file/fileRead.ts)区分正文和读取阶段的跳过原因；[fileCollect.ts](https://github.com/yamadashy/repomix/blob/85e3969b010c72b905203812d1a3f5beb84a2102/src/core/file/fileCollect.ts)同时返回已读取文件和带路径/原因的跳过项。`packager.ts:217–231,337–356` 将跳过信息传播到最终 PackResult。这是值得借鉴的数据流，不必移植打包器。

必须保留三个边界：

- `fileSearch.ts:279–285,336–339` 返回规则筛选后的路径，读取层的 skippedFiles 不包含所有搜索阶段排除项，因此不是全仓覆盖清单。
- `fileRead.ts:147–150` 将一般读取异常也标成 encoding-error；不要照搬这种归因。
- `packager.ts:247–253` 的安全过滤是另一个阶段；`validateFileSafety.ts:28–39,54–64` 表明安全检查可关闭，diff/log 检出问题后也只是警告并仍会包含。不能把“经过打包”当作“所有内容已安全过滤”。

## 自有项目的实际观察

主对话只读核对同一冻结提交的 GitHub recursive tree：上游 `truncated=false`，`type=blob` 条目共 **1,189** 个。本轮队列文件树只有 **500** 个路径，prepare 返回 `success`，`missing_scopes=[]`。

`learning.py:243–246` 会报告上游 truncated，却直接做本地 `max_tree_paths` 切片；重试分支 `learning.py:297–305` 也有同样切片。`learning.py:650–651` 根据 missing 决定状态。因此 **689 个路径未列出是本地策略裁剪，不是 689 个文件正文抓取失败**。本轮另有显式 include 的源码证据，文件树省略也不等于这些文件无法读取。

已有读取错误和 include 上限会进入 missing，不能说项目完全没有缺失管理；也没有证据证明曾因此产生错误的“无关联”结论。Pi 将问题保持为 inferred 是恰当的。实际计数由主对话额外核验，不归功于未自行联网的 Pi。

## 主复核收敛后的建议

Pi 的关联 `rel-7b3e912acf04` 建议为有界采集增加覆盖与跳过报告，并提出六夹具对照。主复核不直接扩大到统一报告框架，优先建议最小呈现：文件树旁说明“已知 1,189 个条目，展示 500 个，因本地上限省略 689 个”。

未来若实施，应先确认展示/契约入口；保留原正文、hash、范围和状态含义，首次采集与重试一致。上游截断时总量未知，只能说返回了多少，不能制造全仓覆盖率。这轮没有执行建议中的夹具实验，也没有改功能。

## 真实运行与验证

北京时间 2026-09-06 09:40:20–09:46:05，约 **345.32 秒**。模型 `openai-codex / gpt-6-astra / high`，SSE；19 次模型请求均收到 HTTP 200，18 次工具调用，1 次工具校验失败，0 模型错误、0 网络重试、0 stderr。

首次 submit 同时标记 learning-only 并建议实验，被现有校验拒绝：`Learning-only decisions cannot suggest an experiment`。Pi 自行修正来源风险语义，最终 learning_only=false 与队列一致；这不代表用户批准实施。没有放宽校验或删除失败记录。

最终 `sealed`、`agent_settled`，启动器退出 0；既有 `validate-learning` 再验证返回 valid、退出 0。关闭 settled RPC 后的子进程退出码 1 是收尾终止，不是运行失败判据。验证在上述自有冻结版本进行，后续文档提交改变 HEAD 后不能篡改旧队列来重新通过指纹检查。

本轮没有代码修改，因此没有新增或重跑代码测试；只完成真实运行、原文/指纹校验和主对话语义复核。没有写正式账本、记忆或 Skill，没有定时任务变更。agent-reach 用于 GitHub 源码获取，现有采集/验证入口用于证据冻结；没有增加新的实验基础设施。

## 审查产物

实验根目录：`E:\devlop\ai-notes\outputs\project-chemist\repomix-learning-20260906`。

- 队列：`outputs/learning/20260906T013942Z-366459cc/learning-queue.json`
- Pi 原始产物：`outputs/pi/project-learning-06230a70-fb01-4089-a606-ab612e0c958f/`，包括首次失败和最终成功的 tool-audit。
- 主对话计数观察：`main-review-observation.json`；发现来源：`discovery.json`。

| 文件 | SHA-256 |
| --- | --- |
| learning-queue.json | `38ea38c0db5545e152995e81f68a38f01ab403fa175bf7cd39c75f0d523be719` |
| decisions.json | `f4cee6d480bba6c7528d29c14cd8787fe7a36443bc5accb675f2744287adfd59` |
| run-manifest.json | `6c74dad11e76dff3d096569edcc38cdd5f41bad482ee3f742d0c56f3df400876` |
| tool-audit.jsonl | `4a5f3c1056601e8f0e92c772f2e9343de8fe3ba11a04d19a5c9f811ec1e93066` |
