# Agent Task Compiler（`taskc`）

`taskc` 面向能力契约目录，将含糊或不完整的自然语言请求编译为可验证的任务意图。编译结果要么是经过校验的 `DispatchReadyIntent`，要么是确定性的诊断信息和最小澄清计划。

> **`ready` 结果不代表授权。** 本项目只判断任务描述是否包含足够信息，可以交给兼容的 Agent 进行调度；不会授权、执行、治理、审计、沙箱隔离或重放 Agent 行为。

本项目需要 Python 3.11 或更高版本，不依赖任何 Agent 框架运行时。

## 仓库状态

本仓库包含 TaskFront / Agent Task Compiler MVP 0.1 的实现。目前已具备：

- 确定性的任务编译核心；
- 严格的能力契约校验；
- 静态 Provider 和结构化模型 Provider 接口；
- 澄清会话；
- 命令行工具；
- JSON Schema；
- 示例与自动化测试。

当前基线验证结果：

- 44 个自动化测试通过；
- 公开评测场景 22/22 通过；
- Python 3.11+ 安装和可编辑安装已验证；
- 无需 Agent 框架即可使用离线静态 Provider；
- `ready` 仍然只表示可以调度，不表示授权或执行。

本仓库有意将范围限定在“任务编译”。它不会运行 Agent 任务、调用真实工具、管理凭据或权限、执行模型生成的代码，也不提供工作流编排。

## 快速开始

```bash
python -m pip install -e .
taskc contract validate examples/single_agent/capability.yaml
taskc compile "Fix the checkout failure" \
  --contracts examples/single_agent/capability.yaml \
  --context examples/single_agent/context.json --json
```

## 使用说明

### 1. 校验能力契约

能力契约描述 Agent 能做什么、需要哪些输入以及适用的约束。编译前可以先校验一个或多个契约文件：

```bash
taskc contract validate examples/single_agent/capability.yaml
```

校验成功后会输出已加载的契约数量；如果文件格式、字段类型或契约约束不合法，命令会返回错误信息和非零退出码。

### 2. 编译任务请求

使用 `compile` 将自然语言请求与能力契约进行匹配：

```bash
taskc compile "修复结账失败" \
  --contracts examples/single_agent/capability.yaml \
  --context examples/single_agent/context.json
```

其中：

- `request` 是待编译的自然语言任务请求；
- `--contracts` 指定一个或多个 YAML/JSON 能力契约文件；
- `--context` 指定额外上下文 JSON 文件，可省略；
- `--json` 以机器可读的 JSON 格式输出结果；
- `--session-out` 将本次编译会话保存到 JSON 文件，便于后续澄清。

### 3. 处理澄清问题

当请求缺少必要信息时，结果会包含澄清问题和会话 ID。可以先保存会话：

```bash
taskc compile "修复结账失败" \
  --contracts examples/single_agent/capability.yaml \
  --session-out session.json --json
```

根据输出的问题准备答案后，使用 `continue` 继续编译：

```bash
taskc continue session.json \
  --contracts examples/single_agent/capability.yaml \
  --answer repository=current_workspace \
  --answer problem_description="结账接口返回 HTTP 500" \
  --json
```

最终结果可能为 `ready`、`needs_clarification` 或带有诊断信息的其他状态。`ready` 只表示输入已满足调度就绪性要求，不会触发任何 Agent 或工具执行。

Python API：

```python
from taskc import CompilerConfig, TaskCompiler

compiler = TaskCompiler.from_contract_paths(
    ["examples/single_agent/capability.yaml"],
    config=CompilerConfig(max_questions_per_round=1),
)
result = compiler.compile(
    "Fix the checkout failure",
    context={
        "repository": "current_workspace",
        "problem_description": "Checkout returns HTTP 500",
    },
)
print(result.model_dump_json(indent=2))
```

候选生成和字段提取均通过 Provider 协议完成。默认 Provider 具有确定性并支持离线运行；也可以注入结构化模型 Provider，而无需修改契约、信息缺口、澄清或就绪性判断逻辑。

详细说明请参阅：[核心概念](docs/concepts.md)、[能力契约 0.1](docs/capability-contract.md)、[结果模型](docs/result-model.md) 和 [MVP 实现决策](docs/implementation-decisions.md)。
