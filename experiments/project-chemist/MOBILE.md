# 飞书手机共学入口

用户已于2026-09-06确认：使用本机常开电脑、飞书、独立的新机器人，并明确开始实施。这取代交接文档当时的暂停状态。

## 手机怎么用

1. 在飞书私聊“项目共学助手”，发送 `配对 <本机配对码>`；只接受配对用户的私聊。
2. 发一个公开 GitHub 项目链接，可附问题。例如 `https://github.com/psf/requests 这个项目解决什么问题？`
3. 收到排队确认，等待“Pi 初步分析”。直接继续发文字可以追问；新链接开始新话题。
4. `状态` 查看最近任务；`结果` 重发最近结果；`帮助` 查看说明。

链接可以省略 `https://`，例如 `github.com/gastownhall/gastown，介绍一下这个项目`。支持链接紧接中文标点、括号包裹和 Markdown 链接；同一个链接重复出现只算一个项目。

支持仓库、分支/tree、Release、Issue、PR 链接。暂不支持任意网页、文件/blob链接、群聊、图片或语音。

分析默认比较 Ai Notes 的 `storage.py`、`learning.py`、`review.py`。没有读取招聘雷达等其他自有项目。外部证据为 README、最多500项的树和按固定规则选择的最多6个源码文件，每份证据限制64 KiB，超限会记录为缺失。输出显示有限范围、冻结版本和缺失证据，不能当作完整仓库审计。追问复用上次冻结队列及最近一次讨论（最多12000字），不自动扩展源码范围；本地项目发生变化会使校验失败，重新发链接可重新采集。

## 配置独立机器人

本机 Python 环境安装 `lark-oapi`（本次验证版本1.7.3）：

```powershell
uv pip install --python .venv/Scripts/python.exe 'lark-oapi>=1.7.3,<2'
.\experiments\project-chemist\mobile-start.ps1 -Action init
```

优先使用官方 SDK 的创建应用流程，完成后直接保存本机加密凭证，避免手工传递 App Secret：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe experiments/project-chemist/mobile-register.py
```

打开 `work/mobile-chemist/registration.json` 中的飞书官方注册链接，在飞书完成登录和创建确认。申请的权限只有接收私聊消息 `im:message.p2p_msg:readonly`、以机器人身份发消息 `im:message:send_as_bot`，订阅 `im.message.receive_v1`。脚本设置 `preset=false`，不使用 SDK 的额外默认权限。需要本人完成飞书账号验证；默认等待最多10分钟，不自动重试创建。

如果所在租户不支持该流程，在飞书开发者后台手工创建企业自建应用、开启机器人能力、添加上述权限，然后运行：

```powershell
.\experiments\project-chemist\mobile-configure.ps1
```

若应用已经创建、但本机凭证保存失败，修复保存问题后运行 `mobile-register.py --app-id <已创建应用的App ID>`，重新连接同一个应用，不重复创建。注册前会用临时凭证验证本机加密保存能力。Windows PowerShell 子进程会清除继承的 PowerShell 7 模块路径，避免安全模块加载失败。

App Secret 隐藏输入，凭证用 Windows DPAPI 加密，仅当前电脑当前 Windows 用户可解密，保存在 Git 忽略的 `work/mobile-chemist/feishu-credential.xml`。不复用其他业务机器人。

随后启动本机服务，在应用的“事件与回调”中确认使用长连接接收事件及 `im.message.receive_v1`，按飞书提示发布应用版本并将自己加入可用范围。配置页面要求先检测到长连接时，先运行下方 Start，再保存配置。

## 启动和检查

在仓库根目录运行：

```powershell
.\experiments\project-chemist\mobile-start.ps1 -Action doctor
.\experiments\project-chemist\mobile-start.ps1 -Action check-app
.\experiments\project-chemist\mobile-service.ps1 -Action Start
.\experiments\project-chemist\mobile-service.ps1 -Action Status
```

`doctor` 检查本地 Python、SDK、Pi CLI 和凭证是否存在；`check-app` 实际校验应用凭证，但不能证明消息权限、订阅或手机回传成功。`Status` 显示 Python 进程、已建立的 TCP 连接和队列状态；TCP 连接也不代替一次真实手机收发验收。启动器会隐藏窗口。停止：

```powershell
.\experiments\project-chemist\mobile-service.ps1 -Action Stop
```

配对码在 `work/mobile-chemist/config.json`。第一次配对后绑定用户，其他人和群消息被忽略。电脑保持开机、联网、不休眠；锁屏可以。当前启动入口需在电脑重启后手工执行 Start，没有安装登录自启或定时任务。

## 运行和恢复

消息回调只进行 SQLite 事务，提交后返回；模型运行在独立子进程。按消息 ID 去重，本机一次分析一个任务，最多20个待处理任务，每个总时限20分钟（模型15分钟/120次工具）。进程树在停止或超时后终止。SDK 自动重连；已收到并落盘的任务和未发送回复可在重启后恢复。未落盘且发生在电脑离线期间的消息，不承诺一定补投。

回复分段并保存到 outbox，每段使用稳定 UUID 重试，最多12次，之后记录发送失败并继续其他消息。飞书的服务端去重有时效，因此跨长时间中断不承诺严格“只发送一次”；发送 `结果` 可重新获取最近结果。分析中进程崩溃会重新分析该任务，可能再次消耗模型额度。

- `work/mobile-chemist/inbox.sqlite3`：收件、排队、最近上下文、发件状态。
- `work/mobile-chemist/service.log`：任务状态、发送重试，不记录消息或凭证正文。
- `work/mobile-chemist/requests/<job-id>.log`：本机分析子进程错误。
- `work/mobile-chemist/runs/<job-id>/<attempt>/`：冻结证据、Pi 记录、引用校验结果、完整文本。
- `work/mobile-chemist/launcher-error.log`：启动错误。

模型沿用现有 Pi 配置。飞书凭证不传入分析子进程；分析工具仅限已有读取和校验工具。不会执行外部代码、安装外部项目、修改自有代码、Finalize、写正式账本、写记忆或创建 Skill。

## 依据

- [飞书官方 Python SDK：创建应用与长连接](https://github.com/larksuite/oapi-sdk-python)
- [接收消息事件](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)
- [回复消息接口](https://open.feishu.cn/document/server-docs/im-v1/message/reply)

实际验收结果见本任务交付记录；未完成手机真实收发前，不宣称机器人已经可用。
