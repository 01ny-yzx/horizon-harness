# 参与 Horizon Runtime 贡献

[English](CONTRIBUTING.md)

感谢你帮助改进 Horizon Runtime。

## 开始之前

- Bug、功能建议和设计讨论可以使用公开 Issue。
- 安全漏洞请使用 [SECURITY.zh-CN.md](SECURITY.zh-CN.md) 中的私密反馈方式。
- 修改应保持聚焦，并保留当前 ToolCall 执行约定和确定性安全边界。

## 开发环境

```bash
git clone https://github.com/01ny-yzx/horizon-runtime.git
cd horizon-runtime
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

如需修改前端：

```bash
cd frontend
npm install
```

不要提交真实凭据、`.env`、本地 MCP 配置、Runtime 数据、浏览器产物或构建输出。

## 修改流程

1. 从 `main` 创建新分支。
2. 遵循项目现有目录结构和命名风格。
3. 保持 Runtime 策略的确定性；metrics 和 trace 只能用于观察。
4. 行为修改应新增或更新对应的 Smoke。
5. 不要在同一个 Pull Request 中混入无关清理。

## 验证

先运行与修改直接相关的检查。提交 Pull Request 前，建议完成：

```bash
python scripts/check_git_safety.py
for test in scripts/smoke_*.py; do python "$test" || exit 1; done
python evals/run_evals.py
python -m compileall -q core tools prompts workflows providers api desktop cloud_mcp scripts evals
git diff --check
```

前端修改还需要运行：

```bash
cd frontend
npm run build
```

## Pull Request 内容

请说明：

- 修改了什么以及修改原因；
- 影响了哪些 Runtime 或安全行为；
- 执行了哪些测试及结果；
- 是否存在兼容或迁移注意事项。

提交贡献即表示你同意该贡献使用项目的 MIT License。
