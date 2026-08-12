# TaskFront（`taskc`）

TaskFront 是一个 Agent 任务编译器：它把自然语言请求、应用上下文和能力契约编译为严格校验的 `DispatchReadyIntent`，或者返回可解释的信息缺口与最小澄清问题。

> `ready` 只表示任务描述已满足下游调度输入要求，不表示授权、审批或执行许可。TaskFront 不执行 Agent、工具、工作流或模型生成代码，也不负责权限治理、沙箱或审计。

## 仓库状态

当前代码实现 Capability Contract / Result / Session 0.2，主要能力包括：

- 严格的 0.2 能力契约、结果、错误 Envelope、会话与评测模型；
- 符合 SemVer 2.0.0 的精确版本解析，包括 prerelease 与 build metadata；
- 确定性候选匹配、来源优先级、三值条件、约束和 readiness 判断；
- 动态最小澄清问题、结果/修订绑定和会话 compare-and-swap；
- `public` 脱敏输出、SecretRef 校验以及安全 JSON Session Store；
- Provider 全局调用预算、重试约束、缓存和 conformance 工具；
- 受信任的 constraint evaluator 与 question ranker 扩展注册表；
- 契约校验、lint、0.1→0.2 迁移、Explain Trace 和 Evaluation Runner；
- 六份签入仓库并由 Golden Test 校验的 0.2 JSON Schema。

当前验证基线：76 项自动化测试通过；60 个 0.2 标注场景达到 Critical Gap Recall 100%、False Ready Rate 0%、Candidate Recall@5 100%、Duplicate Question Rate 0%。

## 安装

需要 Python 3.11 或更高版本。

```powershell
python -m pip install -e ".[dev]"
taskc --help
taskc --version
```

## 快速开始

校验契约：

```powershell
taskc contract validate .\examples\single_agent\capability.yaml
taskc contract lint .\examples\single_agent\capability.yaml
```

使用上下文编译任务：

```powershell
taskc compile "Fix the checkout failure" `
  --contracts .\examples\single_agent\capability.yaml `
  --context .\examples\single_agent\context.json `
  --json
```

JSON 输出是 `CompileEnvelope`。成功编译时 `kind=result`；契约、输入、Provider、预算或会话错误使用 `kind=error`，不会伪装成领域状态 `unsupported`。

## 多轮澄清

缺少必要信息时保存会话：

```powershell
taskc compile "Fix checkout" `
  --contracts .\examples\single_agent\capability.yaml `
  --session-out .\session.json `
  --json
```

从输出的 `result.questions[0].id` 读取动态问题 ID，例如 `q-0-0ff95a205c3b`，再提交回答：

```powershell
taskc continue .\session.json `
  --contracts .\examples\single_agent\capability.yaml `
  --answer 'q-0-0ff95a205c3b="Checkout returns HTTP 500"' `
  --json
```

问题 ID、`result_id` 和 `revision` 会共同防止旧回答或并发写入覆盖新会话。Secret 字段必须提交引用，例如：

```powershell
--answer 'q-0-example={"value_ref":"vault://taskfront/token"}'
```

普通 JSON Session Store 会拒绝持久化 sensitive 字面值；需要持久化此类值时，应由宿主提供加密 Store 或先转换为受控引用。

## 常用命令

强制使用宿主已经选定的精确能力版本：

```powershell
taskc compile "Fix checkout" `
  --contracts .\examples\single_agent\capability.yaml `
  --force-capability repository.fix_bug@1.0.0 `
  --context .\examples\single_agent\context.json
```

从 UTF-8 文件或标准输入读取长请求：

```powershell
taskc compile --request-file .\request.txt --contracts .\examples\single_agent\capability.yaml
Get-Content .\request.txt -Raw | taskc compile --request-file - --contracts .\examples\single_agent\capability.yaml
```

迁移 0.1 契约并输出机器可读报告：

```powershell
taskc contract migrate .\old-contracts `
  --from 0.1 --to 0.2 `
  --output .\migrated-contracts `
  --json
```

默认生成新文件。只有显式传入 `--in-place` 才会改写源文件。迁移会转换 `must_be_explicit` 和 `sensitive`，随后执行 validate 与 lint；无法推断 constraint target 时会输出阻塞诊断。

导出 Schema、查看解释轨迹和运行评测：

```powershell
taskc schema export --version 0.2 --output .\schemas
taskc explain .\session.json --json
taskc eval run .\examples\evaluation\scenarios-v0.2.json `
  --contracts .\examples\evaluation\contracts `
  --json
```

Provider 预算可以通过 `--max-logical-model-calls`、`--max-network-attempts`、`--max-provider-time-seconds` 和 `--max-response-bytes` 配置。

## Python API

```python
from taskc import TaskCompiler

compiler = TaskCompiler.from_contract_paths(
    ["examples/single_agent/capability.yaml"]
)

result = compiler.compile(
    "Fix checkout",
    context={"repository": "current_workspace"},
)

if result.status == "needs_clarification":
    question = result.questions[0]
    result = compiler.continue_(
        compiler.last_session_id,
        expected_revision=result.revision,
        answers=[{
            "question_id": question.id,
            "result_id": question.result_id,
            "revision": question.revision,
            "value": "Checkout returns HTTP 500",
        }],
    )

print(result.status)
```

目录或配置发生不兼容变化时，宿主可以显式调用 `restart_from_session(session_id)` 创建新会话；旧回答会按当前契约重新校验，不会被静默视为仍然有效。

面向外部传输时请使用 `taskc.adapters.result_to_json()` 或 `envelope_to_json()`；默认 `public` profile 会遮蔽 sensitive/secret 值。内存中的 `result.intent` 才是受信任宿主用于下游调度的完整对象。

## 状态与退出码

领域结果状态：

| 状态 | 含义 | CLI 退出码 |
|---|---|---:|
| `ready` | 精确能力和全部阻塞输入已通过校验 | 0 |
| `needs_clarification` | 已选能力仍缺少或无法验证输入 | 2 |
| `ambiguous` | 多个能力仍然可行 | 2 |
| `conflicting` | 同优先级来源给出冲突值 | 3 |
| `unsupported` | 成功完成匹配，但没有正向可行能力 | 4 |

操作错误退出码：`invalid_contract=5`、`provider_failure=6`、`invalid_input=7`、`stale_session=8`、`budget_exceeded=9`、`internal_error=10`。

## 开发与验证

```powershell
python -m pytest -q
python -m taskc.cli eval run .\examples\evaluation\scenarios-v0.2.json `
  --contracts .\examples\evaluation\contracts `
  --json
```

主要目录：

- `src/taskc/models`：0.2 公共模型与状态不变量；
- `src/taskc/compiler`：确定性编译流水线；
- `src/taskc/contracts`：加载、校验、lint、迁移与版本目录；
- `src/taskc/providers`：静态/模型 Provider、缓存和 conformance；
- `src/taskc/security`：脱敏与 SecretRef 协议；
- `src/taskc/evaluation`：场景模型和指标计算；
- `schemas`：签入的 0.1 历史 Schema 与 0.2 当前 Schema；
- `docs/implementation-spec-v0.2.md`：第二版完整实现规范。

更多说明见 [核心概念](docs/concepts.md)、[能力契约 0.2](docs/capability-contract.md)、[结果模型](docs/result-model.md) 和 [实现决策](docs/implementation-decisions.md)。
