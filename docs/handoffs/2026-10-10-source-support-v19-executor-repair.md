# 首次模型调用前的执行脚本修复

原 `v19` 试验在外部观察器的旧阶段断言处失败，模型调用、账本请求及 usage 均为0。失败job、完整冻结输入、原始脚本和失败证据包保持，不重跑该job。原项目代码提交 `80d813be426863da98a2e45211740ca6b9939c1d` 和六项输入hash保持。

下载的完整失败包SHA-256：`5de56c3543a467e12a61404af6ad226d8f76f465800191add6c615c0e31b16e6`。回执JSON SHA-256：`e87db8dad2e3fc6b85df161c987b862502aa2373a278971a6755988cae28f184`。

## 唯一执行脚本修复

外部 `run_once.py` 的 `ObservedModelClient.request` 第48行：

```python
assert screen_calls==1 and kwargs['stage']=='screen-v18-complete-decisions'
```

改为核对真实新冻结输入：

```python
assert screen_calls==1 and kwargs['stage']==new_frozen['request']['stage']
```

仍须在调用前明确断言 `new_frozen['request']['stage']=='screen-v19-source-support'`。除此之外不调整模型请求、业务代码、权重、门槛或来源配置。

本机将包内实际观察器隔离到无HTTP的fake BaseModelClient，用实际新冻结请求验证：原观察器在base调用前拒绝；修复后只转发一次；第二次初筛仍被拒绝。0联网、0 API。

## 继续原授权验收

这一步继续同一批19项候选的首次付费模型验证，付费总预算仍最多37请求、重试0，不扩大周期或另取候选。此前失败根 `/workspace/ai-notes-source-support-v19-trial-20261010` 从现在起只读，不复用其失败runtime。

1. 在原云端任务使用新的私密根 `/workspace/ai-notes-source-support-v19-executor-repair-trial-20261010`；必须不存在。原根、v18根、失败v19根全部保存前后文件SHA与数据库schema/行并只读。
2. 按[原验收交接](2026-10-10-source-support-v19-validation.md)从原候选库只读backup，使用同一期、同records/cards、同新policy/prompt/request，六hash逐项相同。可以复用失败v19已冻结的全来源材料；新root实际配置仅换root，原fetch/checked时间保持，不复制失败runtime/paid输出或文章。
3. 在enqueue/work_once前对包内观察器做上面的无HTTP guard验证，连同完整准备过程记录到回执。通过后仅对新root的指定job运行一次真实 `generate` / `work_once`。正常候选暂缓继续，任何整体失败后保存停止；不重复付费请求、不重跑旧job。
4. 沿用既有GLM配置，第一次真正送达模型的请求开始纳入37总额。保存HTTP开始记录（只保留调用编号、时间、请求阶段与公开模型名，不保存headers/key）、原始输入、完整provider输出、usage/finish_reason及全部账本；无模型调用的准备步骤不计作质量验收。
5. 新Python进程只读检查、同job再次idle、三份被保护目录前后相同、完整文章/清单/各原文、真实缺口与所有失败均交付私密ZIP及SHA。包内需含本次执行脚本及其最小差异，方便本机独立审查；旧包保持原字节。

不运行smoke、采集、dispatch、schedule或自动化；不重填key、不设置ENV_FILE、不安装项目、不合并生产历史、不通知他人。原自动化仍PAUSED。单期结果仍不等于新容器持久化、通知送达或7×24运营验收。
