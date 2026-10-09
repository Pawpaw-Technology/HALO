# HALO 使用 ChatGPT 订阅授权

HALO 通过官方 **Sign in with ChatGPT** 注册独立客户端，并由用户授权使用 ChatGPT
订阅额度。登录状态属于 HALO，不读取或复用 Codex 的凭据。原有 API Key 模式仍是默认模式。
本次适配覆盖 Python CLI 和分析引擎，桌面 UI 的账号设置保持现状。

## 安装和登录

在本仓库目录执行：

```sh
uv sync --locked
uv run halo-chatgpt login
uv run halo-chatgpt models
```

在系统浏览器中完成 **Continue with ChatGPT**，选择账号并授权 HALO。
从 `models` 返回的列表选择模型，将下列命令中的 `YOUR_MODEL` 替换为对应模型标识：

```sh
uv run halo-chatgpt check --model YOUR_MODEL
uv run halo tests/fixtures/tiny_traces.jsonl \
  --auth-mode chatgpt --model YOUR_MODEL \
  --max-depth 0 --max-turns 3 \
  -p "查看 trace 中的证据，然后给出一个具体发现和简短的改进建议。"
```

`check` 发起一次小型实际请求，只在收到 `response.completed` 后报告成功。
第二条命令使用仓库自带示例数据，验证工具调用和报告生成；它不代表 Windows 驱动验收，
也不能证明某项 Harness 改动提升了效果。

分析自己的任务时，替换 JSONL 路径，并可添加 `--repo-path /path/to/your/harness`
以启用只读代码检查。输入数据需要符合 HALO 现有的 trace 格式。

## 账号管理

```sh
uv run halo-chatgpt status
uv run halo-chatgpt login --new-account
uv run halo-chatgpt use SAVED_CLIENT_ID
uv run halo-chatgpt login --enable-plan
uv run halo-chatgpt logout
```

- `login` 复用当前账号已注册的客户端。
- `--new-account` 为其他账号或工作空间发起新注册。
- `use` 选择 `status` 中列出的客户端 ID；运行中的分析仍绑定启动时的账号。
- 如果只完成身份登录、未获得订阅额度权限，HALO 保留账号但禁止推理。
  `--enable-plan` 显式请求重新授权，普通登录不会反复强制弹出授权页面。
- `logout` 尝试撤销远程会话并清除本地令牌，保留账号、客户端和主机映射。
  如果远程撤销未获确认，可在 ChatGPT 设置中断开 HALO。

## 凭据保存与刷新

凭据默认保存在 `$XDG_CONFIG_HOME/halo/chatgpt.json`，未设置该变量时使用
`~/.config/halo/chatgpt.json`。也可以设置 `HALO_CHATGPT_AUTH_FILE`，或使用：

```sh
uv run halo-chatgpt --auth-file /protected/path/chatgpt.json login
uv run halo /path/to/traces.jsonl --auth-mode chatgpt \
  --auth-file /protected/path/chatgpt.json --model YOUR_MODEL -p "分析任务中的问题。"
```

凭据应放在仓库外。文件使用原子替换，并在 Unix 上限制为仅文件所有者可读写。
Windows 上需使用受用户 ACL 保护的目录；本实现不管理 Windows ACL 或系统钥匙串。
刷新由跨进程文件锁串行化，保存轮换后的刷新令牌，并遵守服务端规定的最早刷新时间。
`status` 不输出令牌；不要分享凭据文件或包含授权头的 HTTP 调试日志。

## 模型调用行为

- 主分析和子任务通过 Agents SDK 调用 Responses API，本地函数工具放入 `halo`
  命名空间；历史中的工具调用在发送前补回该命名空间。
- 总结和上下文压缩也使用流式 Responses，不再依赖 Chat Completions。
- 推理请求固定使用 `https://api.openai.com/v1/responses`、`store=false`、`stream=true`
  和 HALO 自身的 OAuth 令牌，每轮发送所需的本地上下文。
- ChatGPT 模式拒绝混用 API Key、自定义 URL/请求头、温度和输出令牌上限。
  其他 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 或 API 项目环境设置不会改变此模式的路由。
- 失败、未完成和中断的流不会被当作成功报告。额度或能力拒绝正确终止请求，
  不自动切换到付费 API Key。
- 此模式关闭 Agents SDK 的托管 tracing；HALO 独立的 `--telemetry` 功能仍需显式开启。

可用模型和服务能力以当前账号为准。授权额度与应用访问可在
[ChatGPT 设置 → Usage](https://chatgpt.com/settings/usage) 中管理。

## 验证与回滚

```sh
uv run python scripts/validate_pinned_versions.py
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest tests/unit
```

适配已通过 562 项单元测试，其中 31 项覆盖 OAuth、刷新、请求约束、工具回传和失败处理。
实际验证完成了浏览器登录、账号模型列表、小型推理请求和示例 trace 分析。
自动刷新经过离线测试；未额外强制刷新有效的实际令牌。Windows 运行环境与驱动任务尚未验收。

省略 `--auth-mode chatgpt` 即可继续使用 API Key 模式。代码回滚不撤销独立保存的授权，
需要撤销时单独执行 `halo-chatgpt logout`，并保留分析数据。

Pawpaw 使用分支为 `codex/chatgpt-plan-auth`；上游 PR 使用
`codex/chatgpt-plan-auth-upstream`，代码实现一致，上游文档和 PR 使用英文。
英文使用说明见 [chatgpt-login.md](chatgpt-login.md)。
