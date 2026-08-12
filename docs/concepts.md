# 核心概念

TaskFront 把任务准备过程视为“编译”：自然语言请求是不可信源代码，Capability Contract 是目标类型系统，Gap 是可以通过补充信息解决的领域缺口，Diagnostic 是契约、输入、Provider 或实现问题，`DispatchReadyIntent` 是下游调度器可消费的编译产物。

编译器保持三条边界：

1. Provider 可以提出候选和字段值，但不能决定 `ready`。
2. 来源合并、条件与约束、问题排序和 readiness 都由确定性核心完成。
3. `ready` 仅说明任务信息完整，不授予权限，也不执行任何操作。

## 数据来源

每个输入都使用 `SourcedValue` 保存来源和分类。默认优先级从高到低为：

```text
clarification_answer
user_request
application_context
contract_default
model_inference
```

同一有效优先级出现不同值会产生阻塞冲突；较低优先级的不同值只产生 shadow warning。`source_policy` 可以限制字段接受 `any`、`trusted` 或 `user_confirmed` 来源。

## 候选与版本

候选必须携带精确 `capability_id@capability_version` 和结构化匹配证据。默认目录只隐式选择最高 stable SemVer；prerelease 需要显式启用。相同 SemVer precedence、不同 build metadata 的版本不能被隐式二选一。

单能力目录不会自动匹配任意请求。没有正向证据时返回 `unsupported`；多个相对可信候选保留为 `ambiguous`，除非宿主显式启用且 Provider 声明 calibrated score 的自动选择。

## 三值逻辑与 readiness

条件值为 `true`、`false` 或 `unknown`。字段缺失时，值比较为 unknown；这使尚未取得的信息不会被误判为约束失败。只有 `when=true` 且 assertion=false 才生成 constraint Gap。

`ready` 要求恰好一个可行候选、精确版本仍存在、所有必填和已触发条件字段合法、来源策略满足、Secret 仅使用引用，并且不存在 blocking Gap。

## 结果与操作错误

领域结果只有 `ready`、`needs_clarification`、`ambiguous`、`conflicting` 和 `unsupported`。契约错误、非法输入、Provider 故障、预算耗尽、会话过期和内部错误进入单独的 `CompileEnvelope.error`，不会混入领域状态。

