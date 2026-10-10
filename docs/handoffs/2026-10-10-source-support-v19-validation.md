# 原文与评分依据修正：一次有界云端验收

用户在原任务确认执行。只复用现有 Dot 云端任务、智谱普通 API 和已有历史；原自动化保持暂停。代码更新到主控提供的精确提交，工作区有改动则停止，不能 reset。无需重做 smoke、历史恢复、采集或密钥配置。

## 输入与预检

原状态根 `/workspace/ai-notes-digest-state-trial-20261008`，原 job `ff114c88126f75512d92d29b546592a931a0d8950d7640cf2e4196cc761276e4`。目标仍是 `daily / 2026-10-09`；19 个原 records/cards，初筛上限30、深核12、最多37模型请求，付费重试0。不得因为执行跨小时而换期或采集。

只读原冻结 `screen-input`。按 `digest_runtime._hash` 核对：records `d0749cc0c8d9fd2d95659e92d59b0407aa290bba91f01cc0e49f77fbeabbd6ee`；cards `62dd71c2a1bce1a200ace6ac5666e7eb6ed99ca00a887afcbe4abf8fcccfcd79`；旧policy `b71adcc45e7df2e323e9d3caeab62165b1604af435c7f51dce74de9218db2fa6`。新 canonical policy 仅增加 `source_support_contract=source-support.v1`，移除该字段须与旧policy完全一致，权重、阈值、flags与来源等级不变。

新policy hash `64ef6452037cebd33aefb798131d50510282fb3aa673eecf144e1d0cb10dc245`；用 `_screen_request` 为相同cards、daily、deep_limit12重建request hash `2f18a2360ba99a523d9369aa9c8b9fc6e1f85a9fa3e8373c6c0a8aaccdb575a7`；`load_prompt`字符串的runtime hash `6152ffaf98fa624db16086ad1f34a3ed80dba9c1a9e33048250ea7ad6114af70`。付费前全部匹配，否则记录差异停止。

## 隔离执行

1. 保存原根全部文件SHA、全部数据库schema/行、业务计数、旧失败job、旧17条输出、原outbox和请求/usage；原根全程只读。确认48批/307观察/169入选/22归档/3草稿、123候选。复制为此前不存在的私密 `/workspace/ai-notes-source-support-v19-trial-20261010`：SQLite从只读源backup，保留候选/历史/来源材料；不复制原runtime库/WAL/SHM，初始化新账本。目标期已归档则停止。
2. 新目录的config/prompt使用本次提交；其余来源配置与旧输入保持。仅入队目标generate，用有租约的受控handler先保存新screen-input：records/offset/eligible_count/source_review_contract来自原输入，policy/prompt使用已比对的新合同，request调用真实 `_screen_request`。
3. 为比较已有7项错例，可复用 v18 隔离根中 `original-input:<id>` 的完整已抓取 contexts/checks/reading_plans。不得复制其旧 request、response、facts、score、editorial 或 archive。在新handler内调用 `_bounded_source_request` 从这些全原文构建全新source request，保存完整 `full_sources` 与新packet receipt，再以 `original-input:<id>` 冻结新request/contexts/checks/failures/base_original_request_id=None。未有供文的入选项目按真实generate正常只读取得原文。旧供文fetched_at/checked_at不可刷新或冒充本轮新抓取；新documented核验时间由真实新模型回执产生。
4. 调用真实 `digest_pipeline.generate`，指定同ID `work_once` 一次。正常单候选暂缓继续；整体失败后保留错误停止，不重试、不另取样、不手动补分、删许可理由或改旧响应为合格。全日榜37请求上限，初筛仍5888输出tokens；未知日期不补午夜/时区。新核验原文最多20000字符，完整wire仍必须≤60000，不抬高预算。
5. 保存实际初筛19项覆盖、每项核验/评分/内容复核及其原始响应、最终文章/草稿与清单、全部请求/usage/finish_reason、真实失败和入选集合。单独比较 Mushrooms 是否仍因许可被挡、Parrot是否发出核验、两条新闻日期是否绑定正确item、Qwen/moral是否仍出现无据便利性/可选性理由；不预设应入选或高分。
6. 同ID再次 `work_once` 应idle；新Python进程只读核对新目录全部状态/文件、请求及usage不增。原根全部前后快照保持；v18隔离根也保持。新目录试稿不合并原生产历史，outbox pending不能写成已通知。

## 回执

完整JSON回执必须包含：精确Git SHA/导入与解释器、所有预检实际值、新冻结screen和source输入（含全原文与来源文件文本或包内文件）、逐候选三阶段结果、全部runtime及业务DB前后快照、原根/v18根保护、新根终态与idle快照、文章清单字节SHA及内容、实际usage与错误。用Library给可下载完整ZIP和SHA-256，优先压缩避免大JSON下载失败；完整原始响应和原文保持私密，不上传GitHub。不能只交成功摘要。

原API绑定直接复用，不读取或重填key，不设置ENV_FILE。本次不运行dispatch/schedule，不恢复heartbeat、不新建定时、不通知他人、不安装候选软件、不发布公众号。单期通过也不能称新容器持久化、通知送达或7×24运营验收通过。
