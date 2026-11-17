# Qoder CN 支持

小花可以跟随 **Qoder CN** 的本机任务转动，等待你回答结构化问题（AskUserQuestion）时停转并亮起提醒灯，回答后继续工作时恢复转动、灭灯，任务结束后停转。桥接读取本地状态，不需要给 Qoder CN 安装插件或提供 API Key。

Qoder 适配器、来源选择和回归测试由 **Y1X1n** 在 [PR #8](https://github.com/dalaofei1154/rotating-flower/pull/8) 中贡献。维护者负责将其接入另外准备的 Windows 安装和打包实现。

## 当前交付范围与测试

PR #8 已纳入 Qoder 适配器、测试及 macOS 源码入口。本整合分支进一步提供 Windows USB 后端、安装器和打包源码，见 [Windows 预览说明](windows.md)；它不更新已有的 v0.7.0 下载包。

作者报告基于 Qoder CN **1.1.79** 的 Windows 本地记录和实际硬件验证。维护者另外在 Windows 10 22H2、Qoder CN 可执行文件报告版本 **0.5.13.0** 上测试了 preview.5 整合候选包：转停、结构化问答提醒、回答恢复、重启自启、USB 重连、stop/start、Codex/WorkBuddy/DSH 回归及 Qoder + WorkBuddy 并发均完成。两个版本号来自不同记录方式，不能据此推断同一版本范围全部兼容。

[整合候选版 Windows CI](https://github.com/dalaofei1154/rotating-flower/actions/runs/44022917649) 的 Python 3.11/3.13 两组通过。候选 Windows 安装包尚未正式发布；不要把本 PR 源码或旧 Mac 下载包当作该 Windows 安装包。macOS Qoder 入口经过软件测试，尚无本轮 macOS Qoder 实物验证。

Qoder 使用的是应用内部记录格式，升级后可能需要重新验证。

## 工作原理

Qoder CN 把本地会话追加写入 JSONL 文件：

~~~
~/.qoder-cn/projects/<工作区>/<会话 UUID>.jsonl
~~~

后台只保留任务状态、模型及强度等信息，不保存或输出提示词、工具参数、工具输出和对话正文。

| 记录 | 判定 |
| --- | --- |
| 助手正在生成或请求普通工具 | 工作中 |
| AskUserQuestion 工具调用尚未收到对应回答 | 等待回答，停转亮灯 |
| 与提问工具调用 ID 匹配的工具结果 | 解除该问题的等待状态 |
| 其他工具的结果 | 不解除仍待回答的问题 |
| 新提示或用户明确中断 | 开始新任务或中断后空闲 |
| 助手结束回复 | 空闲 |

会话中的 reasoningEffort 通常为空，适配器读取桌面应用日志中 ChatSession model policy 的 frozenReasoningEffort，按会话匹配；先于会话文件到达的强度记录最多保留600秒以等待匹配。缺失时回退到应用数据库 chat_model_preferences 的模型默认档位，再回退到 MEDIUM。

| 实际档位 | 小花 |
| --- | --- |
| low | RUN LOW |
| medium | RUN MEDIUM |
| high | RUN HIGH |
| xhigh | RUN XHIGH |
| max | RUN ULTRA |

作者报告在 Qoder CN 1.1.79（Windows）使用 OpenCode Go 的 DeepSeek V4.1 Flash 验证了 low、high、max 三档。维护者本轮看到多个设备档位命令，但未独立验收全部界面强度与模型映射。

**已知限制**：命令权限授权等待不触发提醒灯；结构化提问才触发。没有更新的会话会按默认600秒过期策略移出观察范围，不能保证未回答的问题无限期亮灯。

## 启用

### Windows

本分支包含 Windows 安装与打包源码，可按 [Windows 预览说明](windows.md) 构建和安装。preview.6 对 DSH 与安装诊断有变化，需单独验证；旧 preview.5 的通过记录不等于本包正式发布。Qoder 可通过 `--source qodercn` 或明确组合启用。

### macOS 源码安装

~~~sh
python3 m1/integrated_setup.py install --source qodercn
python3 m1/integrated_setup.py install --source codex,qodercn
~~~

--source all 仍表示 Codex + WorkBuddy + DSH，不包含 Qoder CN；需要 Qoder CN 时显式选择。默认任务目录为 ~/.qoder-cn。

### 只检查状态，不连接 USB

~~~sh
python3 m1/integrated/integrated_bridge.py --once --source qodercn
~~~

桥接入口支持 --qoder-root 和 --qoder-app-data，可用于指定会话根目录和应用数据目录。上面的 macOS 安装器命令行尚不提供这两个自定义路径参数。

## 只读与隐私

- 会话文件按需以只读方式短暂打开，读完关闭。
- 应用日志读取在存在 platform_support 模块时使用 open_read_shared，否则回退为普通只读打开。本整合分支及打包清单已包含该模块，并由隔离包测试核对依赖。不能宣称所有读取都使用 Windows 共享打开或绝不发生文件访问冲突。
- 会话和应用日志会整行 JSON 解码，但只保留状态判断需要的字段，包括工具调用 ID；其余字段不保存、不输出。
- 应用数据库以 mode=ro 打开，仅读取模型档位配置；不可用时使用回退档位。
- 不写入 Qoder CN 目录，不修改其数据。

## 排查

| 现象 | 检查 |
| --- | --- |
| readiness: not_initialized | 打开并登录 Qoder CN，开始一次任务 |
| readiness: error | 检查会话目录是否可读、是否为文件夹 |
| 转动但提问不亮灯 | 确认是结构化提问，不是命令授权或普通文字提问 |
| 状态不更新 | 保留上述只读检查的脱敏输出及应用版本，检查是否存在记录格式变化 |
