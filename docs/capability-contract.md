# Capability Contract 0.2

能力契约声明一个 Agent 能力可处理的任务、所需输入、条件、约束和交付物。契约是纯数据，不能包含模块路径、URL、可执行代码、权限或工作流字节码。

```yaml
schema_version: "0.2"
id: repository.optimize_build
version: 1.1.0
description: Improve repository build speed.
examples: [Make build faster, Reduce CI compile time]
negative_examples: [Improve API runtime performance]

required_inputs:
  repository:
    type: string
    description: Repository to inspect.
    source_policy: trusted
  optimization_goal:
    type: enum
    description: Build stage.
    enum: [local_build, ci_build, both]
    enum_aliases: {ci: ci_build}

optional_inputs:
  ci_provider: {type: string, description: CI provider.}
  target_duration_seconds: {type: integer, description: Target duration.}
  measure: {type: boolean, description: Measure before and after., default: true}

conditional_requirements:
  - when: {field: optimization_goal, in: [ci_build, both]}
    require: [ci_provider]

constraints:
  - code: GAP-CONSTRAINT-MEASUREMENT
    when: {field: target_duration_seconds, exists: true}
    assert: {field: measure, equals: true}
    targets: [measure]
    message: A target duration requires measurement.
    blocking: true

deliverables: [optimization_plan, code_changes, measurement]
```

## 输入字段

支持 `string`、`integer`、`number`、`boolean`、`array`、`object` 和 `enum`。数组必须声明 `items` 或安全的 `value_schema`。对象 `value_schema` 默认 `additionalProperties=false`。

`classification` 可为 `public`、`sensitive` 或 `secret`。Secret 不允许字面默认值，运行时也只能使用 `value_ref`。`question_template` 只允许 `{field}`、`{description}` 和 `{choices}`。

以下输入名被保留并在加载阶段拒绝：`permission`、`approval`、`policy`、`credential`、`executable_code`、`workflow_bytecode`。

## 条件与约束

内置操作符为 `equals`、`not_equals`、`in`、`exists`、`all` 和 `any`。每个表达式只能包含一个操作符；默认最大深度 10、节点数 100。blocking constraint 必须声明至少一个可询问 target。

约束可以使用内置 `assert`，或者通过 `evaluator` 引用宿主预注册的受信任扩展。契约不能自行加载 Python 模块。顶层 `question_ranker` 同样只能引用宿主注册表中的稳定名称。

## 工具

```powershell
taskc contract validate .\contracts
taskc contract lint .\contracts
taskc contract migrate .\contracts-v01 --from 0.1 --to 0.2 --output .\contracts-v02 --json
```

完整字段和安全约束见 [第二版实现规范](implementation-spec-v0.2.md)。

