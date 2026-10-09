# 运行与 trace 参考

本技能由 HALO 仓库维护，安装副本不应独立演化。源位于 `skills/codex/halo-loop/`；上游原工作流位于 `skills/claude/SKILL.md`。

## 定位运行环境

`scripts/diagnose.py` 按显式 `--halo-repo`、`HALO_REPO`、本技能目录内 `local.json` 的顺序查找 checkout。模型采用显式 `--model`，否则使用 `local.json` 的 `diagnostic_model`。没有配置则报告前置错误，不猜cwd或模型。

安装时生成的 `local.json` 只保存本机运行位置和诊断模型，例如：

```json
{"halo_repo": "/absolute/HALO", "diagnostic_model": "ACCOUNT_SUPPORTED_MODEL"}
```

该文件不是凭据，不应复制令牌到技能或仓库。checkout移动后修改本机路径；源技能不硬编码某台电脑。

先复用现有环境；缺环境时按当前任务授权执行HALO维护的安装说明。源码 checkout 内的权威文档：

- `docs/chatgpt-login.zh-CN.md` / `docs/chatgpt-login.md`：官方独立登录及失败处理。
- `docs/integrations/openai-agents-sdk.md`：SDK trace集成。
- `engine/traces/models/canonical_span.py`：实际结构。
- `halo_cli/main.py`：实际CLI选项及只读repo工具。

可在 checkout 中运行 `uv run --locked halo-chatgpt status` 查看非秘密状态；仅首次未授权或用户要求重新授权时运行 `login`。`models`查询当前账号可用模型。模型拒绝/额度不足时保留错误，不换API Key、不重复登录或自动升级模型。诊断模型与被测任务模型是独立设置，配对eval的任务模型保持固定。

## Trace边界

纯JSONL，每行是OTel-shaped span。核心字段为 trace_id、span_id、name、kind、start_time、end_time、status、resource、scope、attributes；parent_span_id默认可为空。OpenInference/inference扩展字段依现有 exporter维护，不能把缺字段统计当零用量。

SDK集成复用现有 `setup_tracing`；清除SDK默认上传器后添加本地处理器，并在finally中shutdown。不要用全局禁用tracing替代清除上传器。导出Codex原始事件时保留来源session、call/event身份和原始材料绑定；不导出隐藏reasoning或凭据。成本取实际运行usage，不从缺失的OTel字段推断。

不要删旧trace以实现“fresh”：用新的输出槽分别保存基线/候选，引用清楚的范围。多进程trace需检查身份后合并；重复span或坏JSON须先修材料。gzip应另存解压副本，保留原件。大型trace优先overview/query/search/view_spans，避免重复view_trace或无界总结。

## 迁移对应

保留Claude版的基线→数据诊断→源码核实→最小改动→重跑比较；诊断问题、候选不可信、迭代停止及大trace按需读原则仍在。Codex版将API Key示例改为独立ChatGPT授权；按当前实现允许只读repo工具。旧模型示例/CLI默认值以实际源码和账号模型列表为准。

原技能只有SKILL.md，没有需迁移的附属脚本或资源。本版新增checked helper，是针对已观察到的错误cwd/空prompt仍启动模型的失败；没有把某次Windows任务的题目或答案写成通用规则。新增行为eval、固定判据、成本及回退条件，使“工具通过”和“AI收益”分开。
