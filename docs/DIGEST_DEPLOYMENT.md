# 三榜私密备份与单执行者部署

本项目提供可检查的备份、恢复和部署样例，尚未安装云服务或启用定时器。2026-10-03 用户确认使用智谱官方普通 API 的 `glm-5.3-flash`，现有 Codex heartbeat 已按用户要求暂停。先在本地验证模型接入和筛选质量，再按这里迁移。

## 运行边界

程序保留 Python + SQLite。一个主机、一个有写权限的状态目录、一个 executor 足够运行当前有界流程。模型通过 Chat Completions 兼容 API 调用；这与 Codex 订阅、Dot 的对话额度不是同一个计费入口。云主机、网络、存储、模型价格与实际费用都未核实，**费用为未知，不能按免费估算**。请求账本保存实际返回的 usage；没有价格表时不自行换算金额。

`runtime schedule` 仅使用一次真实北京时间的 `dispatch` 结果，把任务写入队列；`runtime work` 每次最多领取一个任务。`deploy/digest/one_tick.py` 先 schedule，**如果 dispatch 的 actions 为空立即结束，不执行旧队列、不联网**；存在本次动作时，按 job ID 最多执行 3 个本次派发任务（可配置 1～8），不顺带领取更早的队列，并用操作系统进程锁防止同时启动第二个 executor。单个任务仍遵守自身候选、深核和模型请求预算。进程硬退出后锁自动释放，任务恢复由 runtime 租约和检查点处理。需要在非时槽恢复已排队任务时，由操作人员单独显式调用 `python -m ai_notes.digest_runtime work --root <state-root>`，不能靠 timer 假装新时槽。

需要三个环境变量：

| 变量 | 含义 |
| --- | --- |
| `DIGEST_MODEL_BASE_URL` | 供应商的兼容 API 根地址，通常以 `/v1` 结尾 |
| `DIGEST_MODEL_NAME` | 已获权限、支持该流程结构化输出的模型名 |
| `DIGEST_MODEL_API_KEY` | 对应密钥，仅保存在机器环境或仓库外权限受限文件 |

不提供默认供应商和默认密钥。缺少配置时生成任务进入待处理状态，不能宣称已完成真实模型评分或文章生成。网络超时且收费结果不明确时，保留请求记录并要求核对供应商回执；不得通过删除账本来重试付费请求。

### 显式读取仓库外的模型配置

可以通过 `--model-env-file <path>` 或 `DIGEST_MODEL_ENV_FILE` 指定 UTF-8 `.env` 文件。程序不自动搜索凭据目录，不执行文件中的命令或变量插值。文件模式将该文件视为一套完整配置，不混入环境中另一供应商的地址或密钥；原有三个进程环境变量的方式继续支持。

当前本机文件为 `C:/Users/Mayn/.config/ai-secrets/glm.env`，结构如下（这里没有真实密钥）：

```dotenv
GLM_API_KEY=在本机私密文件中填写
DIGEST_MODEL_API_KEY_VAR=GLM_API_KEY
DIGEST_MODEL_BASE_URL=https://open.bigmodel.cn/api/paas/v4
DIGEST_MODEL_NAME=glm-5.3-flash
DIGEST_MODEL_REASONING_EFFORT=low
```

`DIGEST_MODEL_API_KEY_VAR` 引用同文件中的密钥变量，避免复制密钥；不填写时使用 `DIGEST_MODEL_API_KEY`。推理档位可省略，保留供应商默认值；设置后参与请求指纹，切换档位不会误用旧结果。GLM-5.3-Flash 强制开启思考，支持 `low/high/max`，此处用 `low` 做首次连通检查，不代表已完成评分质量校准。地址与参数来自[智谱模型文档](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)和[对话补全 API](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)。

以下检查不调度榜单、不启动定时器。`model-check` 不联网、不创建模型账本；`model-smoke` 通过真实模型客户端发送最多一次固定 JSON 测试，输出上限 1024 token，账本保存在指定沙箱，成功后重跑复用回执。它可能产生供应商 API 费用，不能当作完整评分质量验收。

```powershell
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_runtime model-check --model-env-file C:/Users/Mayn/.config/ai-secrets/glm.env
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_runtime model-smoke --root work/glm-api-check-20261003 --model-env-file C:/Users/Mayn/.config/ai-secrets/glm.env
```

本机私密文件不提交 Git；其他机器需要自行配置文件路径及凭据。用户确认恢复任务前，不运行 schedule/work，也不加 `-Execute` 启动正式流程。

## 备份内容与恢复约束

`python -m ai_notes.digest_backup` 有 `create`、`verify`、`restore` 三个命令。只收录：

- `data/weekly_digest/digest.sqlite3`、`selection.sqlite3`、`runtime.sqlite3` 三个账本；不存在的库明确列出，不虚构。
- `data/weekly_digest/source_runs/` 的原始响应、原文、采集批次与回执。
- `data/weekly_digest/incoming/` 的既有原始采集／核验批次和待归档稿件，以及 `data/weekly_digest/notice-state.json` 中旧 heartbeat 的通知去重状态。
- `outputs/digest/` 的本地文章、清单与历史入口。
- 两份 `config/digest_*.json`、三榜规则和 `docs/prompts/digest-*.md`。

不带 `.env`、日志、密钥文件、其他配置、其他项目数据、Git checkout 或虚拟环境。备份保存的是公开原文与本地编辑／执行记录，仍应作为私密运行数据迁移，不提交到公开 GitHub。备份目标必须放在项目树之外。

先停止采集、评分、归档和通知适配器的全部写入。工具对三个已存在数据库持有写锁，在同一个安静区间内分别使用 **SQLite backup API** 导出已提交数据，而不是直接复制活跃 `.sqlite3` 文件。WAL 中已提交内容也会进入备份，导出库转成独立文件。发现运行中的任务或持有中的采集锁会拒绝；非数据库材料复制后再次计算 hash，检测并发变化。不要把这些检查当作允许两套自动化同时工作的理由。

每个备份有 `manifest.json`，包含文件列表、SHA-256、字节数、库缺失情况和时间。verify 检查全部 hash、SQLite integrity 和额外未列出的文件；恢复前再验证。**restore 只接受不存在或完全空的目标目录**，拒绝覆盖或合并已有状态，路径越界和符号链接也会拒绝。配置格式／数据库迁移由相应版本的应用处理，备份工具不迁移 schema。

Windows 示例（这里只是命令，不会由文档自动执行）：

```powershell
# 先停止原 heartbeat 和其他 digest 写入，等待运行中任务结束。
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_backup create --root E:/devlop/ai-notes --destination E:/private-backups/digest-20261003
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_backup verify --backup E:/private-backups/digest-20261003
.venv/Scripts/python.exe -X utf8 -m ai_notes.digest_backup restore --backup E:/private-backups/digest-20261003 --destination E:/private-digest-state-restored
```

若目标已存在且非空，保留它并换一个新目录，禁止为了通过 restore 校验清空已有生产目录。

## 迁移与切换顺序

1. **停止旧入口。** 暂停原唯一 heartbeat 和本地手动 worker，记录最后一期、已归档数量、未完成任务和通知状态，等任务结束。此次实施不会替你更改这项现有自动化。
2. **生成私密备份。** 运行 create 和 verify；把目录通过自己的私密传输渠道送到目标机器。新机器使用相同 Git 提交的代码，记录提交号；不从公开仓库寻找被忽略的运行数据。
3. **在空状态目录恢复。** 代码与状态可以分开，例如 `/srv/ai-notes-code` 和 `/srv/ai-notes-state`。restore 的目标是后者，不能直接写进已有 checkout。
4. **核对迁移结果。** 在代码目录建立 Python >=3.11 虚拟环境，安装项目依赖（`python -m pip install -e .`）；用该虚拟环境执行 status / history，核对三账本、归档期号、清单和文章。history 会刷新本地索引，这一步必须仍在新机器的隔离状态目录里。
5. **验证模型与有限任务。** 从仓库外设置三个模型变量。先检查 `runtime status`、`runtime notices`，再显式运行一次 tick。来源和模型错误应保留回执；有费用结果不明的请求先核对供应商，不盲目重发。
6. **再启用唯一云端定时器。** 确认旧入口已经停下之后，才按所选平台安装样例服务。保持北京时间 09、10、11、19 点；10 点只处理周一、11 点只处理月初由 dispatch 决定。不要同时运行旧 heartbeat 和云端 executor。
7. **回退时先停新入口。** 保留新机器的数据和日志。在新的空目录恢复切换前备份并核对，再恢复原入口；新机器已经产生的文章或费用回执不能静默丢弃，先比较其 manifest／请求账本再决定如何保留。

## 尚未启用的样例

`deploy/digest/one_tick.py` 默认只打印命令，带 `--execute` 才运行。Windows 包装脚本同样默认预览：

```powershell
./deploy/digest/run-once.ps1 -StateRoot E:/private-digest-state-restored
# 也可以显式选择私密配置；不加 -Execute 时仍只预览命令。
./deploy/digest/run-once.ps1 -StateRoot E:/private-digest-state-restored -ModelEnvFile C:/Users/Mayn/.config/ai-secrets/glm.env
# 配置完成且明确切换后才加 -Execute。
```

Linux 样例为 `ai-notes-digest.service.example` 与 `ai-notes-digest.timer.example`。它们使用 `/srv/ai-notes-code/.venv/bin/python`、`/srv/ai-notes-state` 和专用 `ai-notes` 用户。实际使用前按主机调整这些路径、创建用户和设置状态目录写权限，再将样例复制到系统服务目录。真实密钥文件位于 `/etc/ai-notes/digest.env`，权限只给服务账户及管理员；仓库仅有空值示例 `model.env.example`。

定时器显式使用 `Asia/Shanghai`，`Persistent=false` 避免服务恢复时把错过的整点当作当前任务执行；已进入队列的任务仍可由 worker 恢复。程序未实现所有历史漏掉的日／周刊自动补发。Linux systemd 文件只做了静态检查，本次没有 Linux 主机部署验收，也没有启用 Windows 计划任务。

## 通知交付

runtime 的通知进入本地 outbox，不会自动发飞书、邮件或 Codex 消息。可使用 `runtime notices` 查看待交付记录；由后续渠道 adapter 确认实际发送成功后调用 `runtime ack --id ... --receipt ...`。receipt 应是渠道真实消息 ID 或可核对交付记录，不能在入队时就确认送达。迁移时保留整个 runtime 账本，避免重新发送已确认通知。正常候选积累和不变缺口仍保持安静。

## 已有验证与仍未知事项

备份／恢复在隔离临时目录测试，包含三个 SQLite 账本、WAL 已提交事务、原始响应、文章与配置；覆盖 hash 篡改、额外文件、路径穿越、非空目标、源内备份、活跃任务和活跃采集锁。部署脚本只能证明可以生成与调用现有 CLI；真实模型质量、单期费用、24 小时运行稳定性、云网络覆盖和渠道通知送达仍需所选环境的验收，不能由单元测试代替。
