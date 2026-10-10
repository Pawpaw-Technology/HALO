# Promptfoo 行为评测

先固定 suite，再复用项目现有 runner 在隔离工作目录运行基线和候选；将实际回答、公开工具事件及原始产物身份整理为 records。Promptfoo 使用 recorded-output provider 评分，语义裁判通过 HALO 自己的 ChatGPT 授权调用。此入口没有模型执行工具，不读取 Codex 凭据，也不回退到付费 API Key。已有 Promptfoo runner 可继续使用，但必须显式选择裁判 provider，避免默认裁判路由。

## 运行环境与调用

固定 Promptfoo `0.121.9`，运行依赖为 Node/npm 和 HALO 已有 Python 环境。安装到独立 runtime，避免给被测业务项目增加依赖。缺 runtime 时先按当前授权安装；评测 helper 不自动安装或重试。

<!-- # eval-allow: npm -->
<!-- # eval-allow: node -->
<!-- # eval-allow: python3 -->
<!-- Node/npm 是 Promptfoo 的运行依赖，Python 是已有 HALO 适配层。 -->

```sh
npm install --prefix /absolute/promptfoo-runtime --save-exact promptfoo@0.121.9 --no-audit --no-fund
python3 SKILL_DIR/scripts/evaluate.py \
  --suite /absolute/frozen-suite.json --records /absolute/agent-records.json \
  --output-dir /fresh/eval-directory \
  --promptfoo-home /absolute/promptfoo-runtime \
  --halo-repo /absolute/HALO --judge-model ACCOUNT_SUPPORTED_MODEL
```

本机 `local.json` 可保存 `promptfoo_home` 和 `judge_model`，与原 `halo_repo`/`diagnostic_model` 独立；没有显式模型或配置就停止，不猜模型。`--check` 仅校验结构，不评分、不创建报告。仅确定性断言无需模型/授权。Windows 上 Python 路径由 helper 使用 HALO `.venv/Scripts/python.exe`；其余路径从实际安装位置解析。

## 最小输入约定

suite 是 JSON（Promptfoo 接受 JSON 配置），同一文件用于前后：

```json
{"cases":[{"id":"C1","task":"固定任务文本","assertions":[{"type":"is-json"}],"rubric":"逐项评分：必须报告实际状态；引用工具事件；不得把计划说成已经执行。"}]}
```

支持 `contains`、`icontains`、`not-contains`、`equals`、`is-json` 确定性断言，针对实际最终答案；复杂判断放在 frozen rubric 或复用项目已有 eval，不为每个业务规则新增 parser。每项可仅断言、仅 rubric 或两者同时使用。

records 是数组，每个真实执行一个记录：

```json
[{"id":"C1-before-1","case_id":"C1","variant":"before","sample":"1","model":"FIXED_AGENT_MODEL","input_id":"task-input-sha256","status":"COMPLETED","output":"实际最终回答","evidence":[{"id":"event-7","command":"实际命令","output":"实际工具输出","source":"原始产物路径和身份"}],"seconds":12.5,"usage":{"input_tokens":100,"cached_input_tokens":80,"output_tokens":20}}]
```

每个 case 都需要 before/after 对应样本；两边 sample、model、input_id 一致。`input_id` 绑定相同任务材料，不含本轮有意改变的 Harness 文件；另存 Harness 版本和完整输入 manifest。模型设置、预算、样本数和 rubric 在采集前固定。证据只含公开工具记录/最终答案，不导出隐藏 reasoning 或凭据；无工具事件用空数组并记录边界。耗时/usage 缺失用 null 或省略，不当作零。来源真伪和运行完成仍须由 Codex 对照原始回执核实，结构校验不代替该核实。

## 结果与 HALO 交接

输出目录保留 frozen suite/records、生成配置、`results.json`、`execution.log`、每次 `judge-*.json` 和 `summary.json`。suite/records SHA256 绑定本轮输入；不覆盖旧目录。关闭缓存、遥测和自动更新，串行评分；裁判首次错误后停止新的裁判调用，保留失败，不自动重试或切换账号/模型。

`EVAL_COMPLETE` 表示评分流程完成，须看每条 pass/score；不是 Harness 收益通过。`TOOL_ERROR` 则停止下游优化，先核实执行日志。summary 的 `agent_seconds` 和 `agent_usage` 来自真实 Agent 回执；Promptfoo 回放耗时、裁判耗时及估算 API 美元价格不能冒充订阅实际成本。原始 judge 请求/回答保存用于复核，语义评分仍可能误判。修正写入独立 review 文件并引用原始行，不覆盖 results 或改 rubric。

HALO 诊断问题须引用 suite SHA256、summary、原始评分、复核差异和 Agent trace/span。至少报告行为差异、实际耗时/token、样本边界及保留/回退决定；新候选必须重新执行 Agent 并新增 records，不能拿旧回答宣布改进。预算门禁和回退按主技能执行。可运行 runtime 中的 `promptfoo view` 查看本地结果；分享/上传仍遵循用户授权。

维护接口参考：[Python provider](https://www.promptfoo.dev/docs/providers/python/)、[llm-rubric](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/llm-rubric/)。
