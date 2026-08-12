# 0.2 实现决策

- 默认使用离线静态 Provider。它要求正向词法证据，并以相对分数过滤明显弱候选，防止“单能力目录 + 完整上下文”误判为 `ready`。
- 静态分数声明为 uncalibrated，不能用于自动能力选择。只有 calibrated Provider 且宿主显式启用阈值与 margin 时才允许自动选择。
- 字符串默认不转换为数字；`allow_string_to_number` 是显式兼容开关。Enum alias 在 Unicode NFC 后精确、区分大小写匹配。
- 条件使用三值逻辑。constraint assertion 为 unknown 时延迟，不生成虚假失败；blocking constraint 的 target 必须能映射为问题。
- Provider 默认预算为 2 次逻辑模型调用、3 次网络尝试、60 秒和 2 MB 响应。重试消耗网络预算，错误与领域 `unsupported` 分离。
- Session revision 使用 compare-and-swap。回答必须引用当前 `question_id`、`result_id` 和 revision；同批重复问题是 `invalid_input`。
- 目录变化仅在无关能力变化或 selected contract 只新增 optional 字段时兼容继续；精确版本消失或 required/type/source policy/enum/条件/约束变化会返回 `stale_session`。
- 公共输出遮蔽 sensitive 字面值和 secret 引用。默认 JSON Session Store 拒绝 sensitive 字面值，而不是写入不可恢复的伪会话。
- 扩展只能由宿主显式注册。未注册或默认未获许可的非确定性扩展在编译器构造阶段 fail closed。
- 0.1 和 0.2 不静默互转；使用 `taskc contract migrate` 生成迁移文件和诊断报告。
