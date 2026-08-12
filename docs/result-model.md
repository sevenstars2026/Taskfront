# 0.2 结果模型

`CompilationResult` 使用以下领域状态：

- `ready`：恰好一个候选通过全部确定性检查，并包含 `DispatchReadyIntent`；
- `needs_clarification`：已有选中候选，但存在可询问的 blocking Gap；
- `ambiguous`：至少两个可行能力仍然合理；
- `conflicting`：同优先级来源值冲突；
- `unsupported`：Provider 成功返回，但没有正向可行能力。

每个状态的不变量同时由 Pydantic 模型和 `oneOf` JSON Schema 约束。例如，只含 `{"status":"ready"}` 的对象必定无效。

`Gap` 表示用户或宿主可通过补充、选择或修正信息解决的领域问题，包含复数 `field_paths`、稳定 code、blocking 标记和建议处理方式。`Diagnostic` 表示契约、输入、Provider 或实现问题，两者不能互换。

操作错误通过 `CompileEnvelope` 与领域结果分离：

```text
invalid_contract
invalid_input
provider_failure
budget_exceeded
stale_session
internal_error
```

Provider 超时或非法响应不会返回 `unsupported`。CLI 默认输出 public Envelope，并对 sensitive/secret 值脱敏。

`DispatchReadyIntent` 固定精确能力版本、目录摘要、来源化输入、交付物、结果 ID 和编译时间。它不包含授权、凭据、执行代码或工具调用。

