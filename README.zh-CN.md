<div align="center">

# Horizon Harness

终端里的 AI 助手。

[English](README.md) · [简体中文](README.zh-CN.md)

</div>

![Horizon 终端界面](docs/assets/home.png)

<details>
<summary>更多界面预览</summary>

![欢迎页](docs/assets/welcome.png)
![对话界面](docs/assets/conversation.png)

</details>

## 安装

Horizon 支持 macOS 和 Windows。Apple Silicon Mac 与 Windows x64 的独立运行包已实机验证；Intel Mac 已准备 CI 构建流程，仍待验证。

> npm、Homebrew、curl 安装入口尚未公开发布。目前可以通过下方命令从源码运行。

### 从源码运行

需要 Python 3.11+，建议使用 Python 3.12。

```sh
git clone https://github.com/01ny-yzx/horizon-harness.git
cd horizon-harness
```

macOS：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m tui
```

<details>
<summary>Windows PowerShell</summary>

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m tui
```

</details>

在欢迎页按 Enter 进入首页，通过 `/models` 选择模型并填写 API Key。本地服务随 TUI 自动启动和关闭。

## 使用

- 在终端查看流式回答和工具执行过程。
- 处理文件、代码、Git、文档和已配置的联网搜索。
- 切换历史会话、恢复标签页，任务执行时也能排队发送消息。
- 按 `Tab` 切换 `read_only` 和 `full_access`。
- 支持中文、英文、日文，界面跟随终端背景。

输入 `/` 查看命令，用 ↑↓ 选择、Enter 确认。

| 命令 | 用途 |
| --- | --- |
| `/models` | 设置全局默认模型 |
| `/session` | 打开历史会话 |
| `/new` | 新建会话 |
| `/search` | 配置联网搜索 |
| `/language` | 切换界面语言 |
| `/help` | 帮助与快捷键设置 |

新会话使用全局默认模型，旧会话保留原来的模型。Enter 发送，Shift+Enter 换行；输入 `exit` 或按 `Ctrl+Q` 退出。

## 文档

[使用指南](docs/usage.zh-CN.md) · [开发指南](docs/development.zh-CN.md) · [架构说明](docs/architecture.zh-CN.md)

## 参与贡献

欢迎提交 Issue 和 PR。参与开发请看[贡献指南](CONTRIBUTING.zh-CN.md)，安全问题请通过[安全说明](SECURITY.zh-CN.md)反馈。

## 许可证

[MIT](LICENSE)。另提供[中文参考译文](LICENSE.zh-CN)。
