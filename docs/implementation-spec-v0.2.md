# TaskFront / Agent Task Compiler 实现规范 v0.2

> 文档状态：Draft 0.2
>
> 目标发行版：`agent-task-compiler 0.2.0`
>
> 契约与结果 Schema：`0.2`
>
> 最低运行版本：Python 3.11
>
> 文档编码：UTF-8，无 BOM 亦可；所有平台必须按 UTF-8 读取

## 1. 规范约定

本文中的关键词具有以下含义：

- **必须（MUST）**：实现与测试不可省略；
- **不得（MUST NOT）**：明确禁止；
- **应该（SHOULD）**：除非有书面兼容理由，否则必须实现；
- **可以（MAY）**：可选能力，不影响核心兼容性。

所有示例都属于规范的一部分，必须在 CI 中完成解析或执行验证。规范正文、JSON Schema、Python 类型和 CLI 行为冲突时，以本规范明确标记为“必须”的状态不变量为准，并视为需要修复的缺陷。

## 2. v0.2 的修订目标

v0.2 保留 v0.1 的产品边界：把自然语言请求编译为可验证、可调度的结构化任务，但不执行任务、不授权操作、不调用 Agent 工具。

本版必须解决：

1. 单能力目录可能把无关请求误判为 `ready`；
2. Provider 故障与业务上的 `unsupported` 混淆；
3. 约束失败可能产生无法回答的阻塞状态；
4. 敏感值可能进入 CLI、会话文件和缓存；
5. JSON Schema 无法独立验证结果状态不变量；
6. SemVer 合法版本无法稳定比较或选择；
7. 未声明的模型字段可能被静默丢弃；
8. Provider 调用次数缺少全局预算；
9. 旧会话在契约变化后缺少兼容性检查；
10. 评测集只检查状态，无法验证产品指标。

v0.2 是预发布阶段允许的破坏性升级。v0.1 数据不得被静默当作 v0.2 数据加载。

## 3. 产品边界

### 3.1 输入

编译器只接受：

- 用户自然语言请求；
- 已验证的 Capability Catalog；
- 宿主应用显式传入的上下文；
- 当前会话、当前 revision 对应问题的澄清回答；
- 宿主应用显式传入的能力选择；
- 编译器配置和受信任扩展实例。

用户自然语言不得修改能力目录、编译器配置、Provider 配置、扩展注册表或安全策略。

### 3.2 输出

核心编译操作只产生以下领域结果：

```text
ready
needs_clarification
ambiguous
conflicting
unsupported
```

契约错误、输入错误、Provider 故障、调用预算耗尽和内部故障属于操作错误，不属于领域结果。

### 3.3 禁止能力

核心包不得：

- 执行 `DispatchReadyIntent`；
- 调用契约中描述的真实工具；
- 根据 `ready` 推导授权、审批、风险可接受或策略合规；
- 从契约或用户文本动态加载、导入或执行代码；
- 执行 shell、HTTP、数据库或文件写入；
- 管理长期凭据；
- 提供工作流调度、checkpoint、replay 或任务执行审计；
- 把模型输出解释为可执行代码、路径或命令。

文件、HTTP 和持久化能力只能位于 CLI、Provider Adapter 或 Session Adapter 中，并与确定性核心隔离。

## 4. 总体架构

```text
Request + Context + Answers
            |
            v
    Request Normalizer
            |
            v
    Interpretation Provider <---- Capability Catalog
    (static or model-backed)       SemVer Resolver
            |
            v
    Strict Provider Validator
            |
            v
    Candidate Analyzer
      |     |      |
      |     |      +---- Constraint Engine (three-valued)
      |     +----------- Source / Conflict Resolver
      +----------------- Type / Contract Matcher
            |
            v
    Candidate Resolver
       |            |
       v            v
 Clarification    Readiness
 Planner          Checker
       |            |
       +------v-----+
              |
       Result + Explain Trace
              |
      Redaction / Serialization
```

确定性边界：

- 模型可以提出候选、提取字段和改写非敏感问题文案；
- 模型不得决定最终状态、字段有效性、约束结果、来源优先级或 readiness；
- 相同规范化输入、目录、配置、扩展版本和缓存 Provider 响应必须产生相同的确定性分析结果；
- 时间、ID、Provider 调用器和遥测记录器必须可注入。

### 4.1 核心配置默认值

```python
class ProviderBudgetConfig(StrictModel):
    max_logical_model_calls: int = Field(default=2, ge=0, le=10)
    max_network_attempts: int = Field(default=3, ge=0, le=20)
    max_provider_time_seconds: float = Field(default=60, gt=0, le=600)
    max_response_bytes: int = Field(default=2_000_000, ge=1, le=20_000_000)

class QuestionWeights(StrictModel):
    candidate_elimination: float = Field(default=0.40, ge=0)
    blocking_gap: float = Field(default=0.35, ge=0)
    dependency: float = Field(default=0.15, ge=0)
    user_cost: float = Field(default=0.07, ge=0)
    sensitivity: float = Field(default=0.03, ge=0)

class CompilerConfig(StrictModel):
    max_candidates: int = Field(default=5, ge=1, le=50)
    max_questions_per_round: int = Field(default=1, ge=1, le=20)
    max_request_length: int = Field(default=20_000, ge=1, le=1_000_000)
    max_context_fields: int = Field(default=200, ge=0, le=10_000)
    max_collection_length: int = Field(default=1_000, ge=1, le=100_000)
    max_condition_depth: int = Field(default=10, ge=1, le=30)
    max_condition_nodes: int = Field(default=100, ge=1, le=1_000)
    min_static_match_score: float = Field(default=0.05, ge=0, le=1)
    allow_string_to_number: bool = False
    allow_prerelease: bool = False
    auto_select_candidate: bool = False
    auto_select_threshold: float = Field(default=0.90, ge=0, le=1)
    auto_select_margin: float = Field(default=0.20, ge=0, le=1)
    telemetry_strict: bool = False
    provider_budget: ProviderBudgetConfig = ProviderBudgetConfig()
    question_weights: QuestionWeights = QuestionWeights()
```

所有影响结果的配置必须进入 `config_digest`。问题权重必须为有限非负数，且三个收益权重不能同时为零。配置字段不得存在但不生效；CI 必须对每个公开配置至少有一个行为测试。

## 5. 推荐模块结构

```text
src/taskc/
├── api.py
├── config.py
├── errors.py                 # 操作错误与错误 Envelope
├── versions.py               # SemVer 解析、比较和能力引用
├── budget.py                 # Provider 全局调用预算
├── models/
│   ├── capability.py
│   ├── values.py             # 来源、分类与值引用
│   ├── intent.py
│   ├── clarification.py
│   ├── result.py
│   ├── session.py
│   └── trace.py
├── contracts/
│   ├── loader.py
│   ├── validator.py
│   ├── catalog.py
│   ├── linter.py             # 新增：可达性、质量和兼容性检查
│   └── migration.py          # 新增：0.1 -> 0.2 显式迁移
├── compiler/
│   ├── pipeline.py
│   ├── normalize.py
│   ├── matching.py
│   ├── conditions.py         # 三值条件求值
│   ├── constraints.py
│   ├── candidates.py
│   ├── questions.py
│   └── readiness.py
├── providers/
│   ├── base.py
│   ├── static.py
│   ├── model.py
│   ├── cache.py
│   └── openai_compatible.py
├── security/
│   ├── redaction.py          # 新增：按输出用途脱敏
│   └── secret_refs.py        # 新增：秘密值引用协议
├── extensions/
│   ├── registry.py           # 新增：受信任宿主扩展
│   └── protocols.py
├── evaluation/
│   ├── dataset.py            # 新增：带标签评测集
│   ├── metrics.py
│   └── runner.py
├── telemetry/
├── adapters/
└── cli.py
```

模块可以合并文件，但公开协议、依赖方向和安全边界不得改变。

## 6. 公共数据模型

所有公开模型必须：

- 使用 Pydantic v2 或提供完全等价的严格校验；
- 配置 `extra="forbid"`；
- 拒绝 NaN 和 Infinity；
- 为列表和对象设置长度上限；
- 存在签入仓库的 JSON Schema；
- 能由 JSON Schema 独立验证状态结构，而不依赖 Python validator。

### 6.1 来源与数据分类

```python
SourceKind = Literal[
    "user_request",
    "application_context",
    "clarification_answer",
    "contract_default",
    "model_inference",
]

DataClassification = Literal["public", "sensitive", "secret"]
SourcePolicy = Literal["any", "trusted", "user_confirmed"]
```

固定来源优先级从高到低为：

```text
clarification_answer
user_request
application_context
contract_default
model_inference
```

`SourcePolicy` 的 readiness 语义：

- `any`：所有来源都可满足字段；
- `trusted`：除 `model_inference` 外的来源可满足字段；
- `user_confirmed`：只有 `user_request` 和 `clarification_answer` 可满足字段。

v0.1 的 `must_be_explicit=true` 迁移为 `source_policy: trusted`。需要用户本人确认时必须明确使用 `user_confirmed`。

```python
class SourcedValue(StrictModel):
    value: JsonValue | None = None
    value_ref: str | None = None
    source: SourceKind
    source_ref: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    classification: DataClassification = "public"
    redacted: bool = False
```

不变量：

- `redacted=false` 时，`value` 与 `value_ref` 必须且只能存在一个；
- `redacted=true` 时，`value` 与 `value_ref` 必须都不存在，该对象只表示值曾存在；
- `secret` 必须使用 `value_ref`，不得携带字面值；
- `sensitive` 可以在内存中携带字面值，但公共序列化必须遮蔽；
- `redacted=true` 只允许出现在序列化视图中；重新作为编译输入时视为缺失值；
- `confidence` 不参与确定性 readiness，只能用于候选排序或解释。

### 6.2 Capability Contract 0.2

```python
class InputSpec(StrictModel):
    type: Literal["string", "integer", "number", "boolean", "array", "object", "enum"]
    description: str
    items: Literal["string", "integer", "number", "boolean", "object"] | None = None
    enum: list[JsonValue] | None = None
    enum_aliases: dict[str, JsonValue] = {}
    value_schema: JsonObject | None = None
    source_policy: SourcePolicy = "any"
    default: JsonValue | None = None
    classification: DataClassification = "public"
    question_template: str | None = None

class CapabilityContract(StrictModel):
    schema_version: Literal["0.2"]
    id: str
    version: str
    description: str
    examples: list[str] = []
    negative_examples: list[str] = []
    required_inputs: dict[str, InputSpec]
    optional_inputs: dict[str, InputSpec] = {}
    conditional_requirements: list[ConditionalRequirement] = []
    constraints: list[ConstraintSpec] = []
    deliverables: list[str]
```

字段规则：

- `id` 必须匹配 `^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$`；
- 输入名必须匹配 `^[a-z][a-z0-9_]{0,63}$`；
- `version` 必须符合 SemVer 2.0.0；
- required 与 optional 不得重名；
- `description`、deliverable 和示例不得为空白；
- enum 值必须唯一；alias 必须指向已声明的 enum 值；
- alias 先做 Unicode NFC，再进行大小写敏感精确匹配；
- `secret` 字段不得声明字面默认值；
- `value_schema` 只允许 Draft 2020-12 的本地、无执行子集：`type`、`properties`、`required`、`items`、`enum`、`const`、`minimum`、`maximum`、`minLength`、`maxLength`、`minItems`、`maxItems` 和 `additionalProperties`；禁止 `$ref`、远程 URI、`pattern`、自定义 format 和组合型动态引用；
- `array` 必须声明 `items` 或安全的 `value_schema`；
- `object` 若声明 `value_schema`，默认 `additionalProperties=false`；
- `required_inputs` 可以为空，但契约仍必须有明确正向匹配证据和至少一个 deliverable；
- 未知字段必须拒绝。

`question_template` 只允许 `{field}`、`{description}` 和 `{choices}` 占位符。未知占位符属于契约错误。

### 6.3 合法契约示例

```yaml
schema_version: "0.2"
id: repository.optimize_build
version: 1.1.0
description: Improve repository build speed without changing product behavior.

examples:
  - Reduce this repository's build time.
  - Make CI compile faster.

negative_examples:
  - Improve runtime API latency.

required_inputs:
  repository:
    type: string
    description: Repository or workspace to inspect.
    source_policy: trusted
  optimization_goal:
    type: enum
    description: Build stage to optimize.
    enum: [local_build, ci_build, both]
    enum_aliases:
      ci: ci_build

optional_inputs:
  ci_provider:
    type: string
    description: CI provider name.
  target_duration_seconds:
    type: integer
    description: Desired maximum build duration.
  measure:
    type: boolean
    description: Whether to record before/after measurements.
    default: true

conditional_requirements:
  - when:
      field: optimization_goal
      in: [ci_build, both]
    require: [ci_provider]

constraints:
  - code: GAP-CONSTRAINT-MEASUREMENT
    when:
      field: target_duration_seconds
      exists: true
    assert:
      field: measure
      equals: true
    targets: [measure]
    message: A target duration requires before/after measurement.
    blocking: true

deliverables:
  - optimization_plan
  - code_changes
  - before_after_measurement
```

该示例必须由 `taskc contract validate` 在 CI 中验证成功。

### 6.4 条件和约束

支持的操作符保持有限：

```text
equals
not_equals
in
exists
all
any
```

条件采用三值逻辑：`true`、`false`、`unknown`。

- 字段不存在时，`equals`、`not_equals` 和 `in` 返回 `unknown`；
- `exists: true` 对不存在返回 `false`，`exists: false` 返回 `true`；
- 字段存在且值为 JSON `null` 仍视为存在；
- `all` 中任一项为 false 则 false；全部 true 才为 true；否则 unknown；
- `any` 中任一项为 true 则 true；全部 false 才为 false；否则 unknown。

条件树最大深度默认 10，节点数默认 100。每个表达式必须且只能包含一个操作符。

```python
class ConstraintSpec(StrictModel):
    code: str
    when: ConditionExpression | None = None
    assert_condition: ConditionExpression = Field(alias="assert")
    targets: list[str]
    message: str
    blocking: bool = True
```

约束求值规则：

1. `when=false`：约束不适用；
2. `when=unknown`：约束延迟，不产生失败 Gap；
3. `when=true` 且 `assert=true`：通过；
4. `when=true` 且 `assert=unknown`：延迟，由 required/conditional requirement 负责产生缺失 Gap；
5. `when=true` 且 `assert=false`：产生约束 Gap；
6. blocking 约束必须至少有一个已声明 `target`，否则契约无效；
7. 问题规划器必须能够为每个 blocking constraint 定位到 target。

契约验证器必须检查操作数类型、enum 成员、未知字段和同一 `all` 中显然矛盾的表达式。更复杂但不能确定的可达性问题由 `contract lint` 输出 warning，不得伪装成验证成功保证。

### 6.5 SemVer 和目录版本选择

版本比较必须完整遵守 SemVer 2.0.0：

- build metadata 不参与优先级比较，但保留在版本身份中；
- stable 版本高于相同 core 的 prerelease；
- prerelease 标识符按 SemVer 数字和字符串规则比较；
- Provider 返回的 `CandidateDraft` 必须包含精确 `capability_version`；
- Provider 不得自行推断不存在的版本；
- 宿主只提供 ID 而未提供版本时，默认选择最高 stable 版本；
- 默认不得隐式选择 prerelease；
- 多个版本具有相同 SemVer precedence 但 build metadata 不同时，隐式选择必须报 `E-CONTRACT-VERSION-AMBIGUOUS`，要求精确版本；
- 重复的 `(id, exact version)` 必须拒绝。

### 6.6 候选、Gap 与诊断

```python
class CandidateIntent(StrictModel):
    capability_id: str
    capability_version: str
    score: float = Field(ge=0, le=1)
    evidence: list[MatchEvidence]
    inputs: dict[str, SourcedValue]
    unresolved_terms: list[str] = []
    viability: Literal["viable", "rejected"]

class Gap(StrictModel):
    code: str
    kind: Literal["missing", "ambiguous", "conflicting", "unverifiable", "constraint"]
    field_paths: list[str]
    message: str
    blocking: bool
    candidate_values: list[JsonValue] = []
    suggested_resolution: Literal["ask_user", "use_context", "apply_default", "choose_candidate", "reject"]

class Diagnostic(StrictModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    phase: str
    field_path: str | None = None
    contract_ref: str | None = None
    hint: str | None = None
```

Gap 表示可由补充、选择或修正任务信息解决的领域缺口；Diagnostic 表示输入、契约、Provider 或实现问题。两者不得互相替代。

敏感字段的 `candidate_values` 在公共输出中必须遮蔽；secret 字段不得出现在其中。

### 6.7 澄清问题与回答

```python
class ClarificationQuestion(StrictModel):
    id: str
    result_id: str
    revision: int
    text: str
    targets: list[str]
    answer_type: Literal["text", "single_choice", "multi_choice", "boolean", "integer", "number", "json"]
    choices: list[JsonValue] = []
    priority: float
    reason_code: str

class ClarificationAnswer(StrictModel):
    question_id: str
    result_id: str
    revision: int
    value: JsonValue | None = None
    value_ref: str | None = None
```

回答必须引用产生该问题的 `result_id` 和 `revision`。旧 revision、未知问题、重复 question ID、错误类型和非法 choice 必须作为 `invalid_input` 拒绝，不得转换为领域冲突。

问题 ID 由以下内容确定性生成：

```text
q-{revision}-{sha256(answer_type + canonical_targets + canonical_choices)[0:12]}
```

### 6.8 领域结果

```python
class CompilationResult(StrictModel):
    schema_version: Literal["0.2"]
    result_id: str
    session_id: str
    revision: int
    catalog_digest: str
    status: Literal[
        "ready",
        "needs_clarification",
        "ambiguous",
        "conflicting",
        "unsupported",
    ]
    candidates: list[CandidateIntent]
    selected_candidate: CandidateIntent | None = None
    gaps: list[Gap] = []
    questions: list[ClarificationQuestion] = []
    intent: DispatchReadyIntent | None = None
    diagnostics: list[Diagnostic] = []
    decision_codes: list[str] = []
    trace: ExplainTrace | None = None
```

状态不变量：

| 状态 | 必须满足 |
|---|---|
| `ready` | 有 selected candidate 和 intent；无 blocking Gap；无 questions |
| `needs_clarification` | 有 selected candidate；至少一个可询问的 blocking Gap；至少一个 question；无 intent |
| `ambiguous` | 至少两个 viable candidates；存在能力选择 Gap 和 question；无 selected candidate、无 intent |
| `conflicting` | 有 selected candidate；至少一个 blocking conflict；至少一个解决冲突的问题；无 intent |
| `unsupported` | 无 viable candidate；无 selected candidate、questions 和 intent；`decision_codes` 至少包含一个稳定的不匹配原因码 |

签入的 `compilation-result-v0.2.schema.json` 必须使用 `oneOf` 和状态常量独立表达这些结构。`{"status":"ready"}` 必须无法通过 JSON Schema。

### 6.9 操作错误 Envelope

```python
class OperationError(StrictModel):
    code: str
    category: Literal[
        "invalid_contract",
        "invalid_input",
        "provider_failure",
        "budget_exceeded",
        "stale_session",
        "internal_error",
    ]
    message: str
    retryable: bool
    diagnostics: list[Diagnostic]

class CompileEnvelope(StrictModel):
    schema_version: Literal["0.2"]
    kind: Literal["result", "error"]
    serialization_profile: Literal["internal", "public", "session"]
    result: CompilationResult | None = None
    error: OperationError | None = None
```

Envelope 必须通过 JSON Schema `oneOf` 保证：`kind=result` 只携带 result；`kind=error` 只携带 error。

Python SDK 的主接口对操作错误抛出类型化异常，异常必须携带同构的 `OperationError`。`try_compile` 系列接口返回 Envelope，不抛出预期操作错误。

### 6.10 DispatchReadyIntent

```python
class DispatchReadyIntent(StrictModel):
    schema_version: Literal["0.2"]
    intent_id: str
    result_id: str
    capability_id: str
    capability_version: str
    catalog_digest: str
    inputs: dict[str, SourcedValue]
    deliverables: list[str]
    compiled_at: datetime
```

契约加载时必须拒绝以下顶层输入名，避免编译到最后才崩溃：

```text
permission approval policy credential executable_code workflow_bytecode
```

`risk` 和 `effect` 可以作为普通业务描述字段，但不得被解释为授权或执行许可。

### 6.11 会话

```python
class CompilationSession(StrictModel):
    schema_version: Literal["0.2"]
    session_id: str
    revision: int
    request: NormalizedRequest
    catalog_digest: str
    config_digest: str
    provider_fingerprints: list[str]
    answers: list[AnswerRecord]
    last_result: CompilationResult
    created_at: datetime
    updated_at: datetime
```

Session Store 必须提供 compare-and-swap：

```python
class SessionStore(Protocol):
    def get(self, session_id: str) -> CompilationSession | None: ...
    def put(self, session: CompilationSession, *, expected_revision: int) -> None: ...
```

revision 不匹配返回 `stale_session`，不得静默覆盖并发修改。

## 7. 编译流水线

### Stage 0：契约加载

1. 按 UTF-8 读取 YAML/JSON；
2. 单文件默认上限 2 MB；
3. YAML 使用 safe loader，禁止自定义 tag；
4. 解析后限制深度 30、节点 10,000、集合成员 1,000；
5. JSON Schema 校验；
6. Pydantic 严格类型校验；
7. 跨字段和条件语义校验；
8. SemVer 校验与精确版本索引；
9. 计算 canonical catalog digest。

目录摘要使用 UTF-8、对象 key 排序、无无意义空白、有限 JSON 数字的规范化 JSON。数组保持声明顺序，除非该字段在规范中明确为集合。

### Stage 1：请求规范化

- 在 Unicode 规范化前后都检查长度；
- 使用 NFC，保留原始文本仅供内存内解释，不进入默认遥测；
- 默认请求长度 20,000 字符；
- 默认上下文字段 200 个；字段名最长 128 字符；
- JSON 深度默认 30，集合长度默认 1,000；
- 拒绝非字符串 key、NaN、Infinity 和非 JSON 对象；
- 能力强制选择只能来自独立 API/CLI 参数，不能从请求文本解析。

### Stage 2：候选解释

Provider 输出必须引用精确的 `capability_id@version`。候选必须包含至少一条匹配证据：正向示例、描述词项、显式宿主选择或模型结构化理由。

单能力目录不得自动视为匹配：

- 显式宿主 `requested_capability` 可以直接选中；
- 否则静态 Provider 必须达到 `min_static_match_score`，默认 `0.05`；
- 无正向证据时返回零候选，最终为 `unsupported`；
- 输入字段完整不能替代任务目标匹配证据。

静态匹配的 tokenization、Unicode 处理、负例扣分和阈值必须版本化。

v0.2 内置静态 Provider 的基线算法固定为：

1. 请求、description 和 example 使用 Unicode NFC 与 `casefold()`；
2. 拉丁数字片段按 `[A-Za-z0-9_]+` 提取，长度大于 1 才保留；
3. 连续 CJK 片段保留完整片段，并生成相邻二元字组；
4. 集合相似度使用 Jaccard：`|A ∩ B| / |A ∪ B|`；
5. `positive` 为 description 与全部 examples 的最大相似度；
6. `negative` 为全部 negative examples 的最大相似度；
7. `score = clamp(positive * (1 - 0.75 * negative), 0, 1)`；
8. `score >= min_static_match_score` 才生成候选；
9. 默认只匹配每个 capability ID 的 active stable version；
10. 排序键为 `score desc, capability_id asc, SemVer precedence desc, exact version asc`。

静态 Provider 的 `score_semantics` 为 `uncalibrated`，因此不能用于自动选择。实现如果变更 tokenization 或公式，必须提升静态 Provider `config_version` 并使旧缓存失效。

### Stage 3：Provider 输出校验

- 整个响应先做字节和 JSON 深度限制；
- 再进行 `extra=forbid` 严格模型校验；
- 未知 capability、版本或输入字段必须拒绝对应候选并产生错误诊断；
- 若响应中任一候选包含未声明字段，默认拒绝整个响应；可以配置为只拒绝该候选，但不得静默删除；
- 所有候选被 Provider 校验拒绝属于 `provider_failure`，不是 `unsupported`。

### Stage 4：字段合并和来源冲突

1. 收集所有来源值，保留 provenance；
2. 先按固定来源优先级分层；
3. 同一最高优先级存在不同规范值时产生 blocking conflict；
4. 高优先级值与低优先级值不同时，选取高优先级值并发出 `W-INPUT-SHADOWED-VALUE`；
5. 不得把低优先级冲突值从 Explain Trace 中删除；
6. 同一个 clarification 批次不得对同一 question 提交两个答案；
7. 对已展示冲突问题的新回答视为显式 resolution，但历史记录保留。

### Stage 5：类型与字段匹配

- 默认不执行字符串到数字转换；
- 宿主启用安全转换时，必须保证 round-trip 等价；
- boolean 不得作为 integer 或 number；
- enum 先精确匹配规范值，再匹配声明 alias；
- 对象和数组按 `value_schema` 或 `items` 校验；
- 不符合类型的值不得进入有效 intent inputs；
- `source_policy` 不满足时产生 blocking unverifiable Gap；
- 未声明上下文字段默认忽略并产生可配置 info trace；保留字字段不得进入 intent。

### Stage 6：条件要求和约束

1. 计算无条件 required；
2. 以三值逻辑计算 conditional requirements；
3. 条件为 true 时加入 required 集合；unknown 时记录 deferred trace；
4. 对最终 required 集合产生 missing Gap；
5. 按第 6.4 节规则求值 constraints；
6. 对同 code、同 targets、同候选值的 Gap 去重；
7. Gap 使用固定排序键：

```text
blocking desc
kind_priority asc
first_field_path asc
code asc
canonical_candidate_values asc
```

### Stage 7：候选可行性与选择

候选分为 `viable` 和 `rejected`：

- 缺少可澄清字段仍为 viable；
- 类型错误、来源不足和可修正约束失败仍为 viable；
- 未知能力、版本失效、不可修正的契约级冲突或 Provider 非法输出为 rejected；
- 只有 viable candidates 参与歧义问题和自动选择。

选择规则：

1. 零个 viable candidate：Provider 成功则 `unsupported`，Provider 失败则操作错误；
2. 一个 viable candidate：进入字段澄清或 readiness；
3. 多个 viable candidates：默认 `ambiguous`；
4. 自动选择默认关闭；
5. 自动选择启用时，Provider 必须声明 score 已校准，且同时满足阈值、margin 和宿主策略；
6. 自动选择不得仅因目录中只有一个能力而触发；
7. 自动选择后仍必须执行完整契约和 readiness 校验。

### Stage 8：澄清规划

每个 blocking Gap 必须满足以下之一：

- 被至少一个问题覆盖；
- 被另一个先行问题解锁；
- 被判定不可由用户解决并使候选 rejected。

不得返回带 blocking Gap、但 questions 为空的 `needs_clarification` 或 `conflicting`。

问题特征全部归一化到 `[0, 1]`：

```text
score(q) =
    0.40 * candidate_elimination_ratio
  + 0.35 * resolved_blocking_gap_ratio
  + 0.15 * unlocked_dependency_ratio
  - 0.07 * answer_cost
  - 0.03 * sensitivity_penalty
```

定义：

- `candidate_elimination_ratio`：问题回答后最坏情况下可排除的 viable candidate 比例；字段问题无法区分候选时为 0；
- `resolved_blocking_gap_ratio`：问题 targets 覆盖的 blocking Gap 数除以总 blocking Gap 数；
- `unlocked_dependency_ratio`：可由该问题使条件从 unknown 变为可判定的数量除以最大可解锁数量；
- `answer_cost`：single choice/boolean 为 0.2，integer/number 为 0.3，multi choice 为 0.4，text/json 为 0.6；
- `sensitivity_penalty`：public 为 0，sensitive 为 0.5，secret 为 1。

同分时按 `canonical_targets`、`answer_type`、question ID 排序。默认每轮一个问题。

问题文案优先级：契约模板、内置确定性模板、受限 renderer。secret 字段必须使用固定安全模板，模型 renderer 不得接收 secret 值或引用。

### Stage 9：Readiness

`ready` 必须同时满足：

- 恰好一个 selected viable candidate；
- 所有 required 和已触发 conditional fields 存在；
- 类型、enum、value schema 均合法；
- 每个字段满足 source policy；
- 无 blocking Gap；
- 能力精确版本仍存在于当前目录；
- 输入只包含契约字段；
- secret 只使用 value reference；
- 结果状态不变量通过模型和 JSON Schema 测试。

### Stage 10：输出、解释与脱敏

- 内部结果先完成严格模型校验；
- Explain Trace 只记录决定、代码、哈希和脱敏来源，不记录完整 Prompt；
- 然后按 serialization profile 输出；
- 默认 CLI 和会话 profile 不得输出 sensitive 字面值；
- 输出排序必须稳定；
- Clock 与 ID generator 可注入，Golden Test 使用固定实现。

## 8. Provider 规范

### 8.1 批量协议

为保证调用预算，v0.2 使用一次解释或“候选 + 批量提取”协议，不得逐候选调用模型：

```python
class InterpretationProvider(Protocol):
    provider_id: str
    config_version: str
    score_semantics: Literal["uncalibrated", "calibrated"]

    async def interpret(
        self,
        request: NormalizedRequest,
        catalog: CapabilityCatalog,
        *,
        max_candidates: int,
        budget: ProviderBudget,
    ) -> InterpretationResponse: ...
```

旧 CandidateProvider 与 ExtractionProvider 可以通过兼容 Adapter 使用，但 Adapter 必须批量提取，并遵守同一预算。

### 8.2 全局调用预算

默认值：

```yaml
max_logical_model_calls: 2
max_network_attempts: 3
max_provider_time_seconds: 60
max_response_bytes: 2000000
```

- 一次重试计入 network attempts，但不增加 logical model calls；
- 默认第一调用完成候选和字段提取；
- 第二调用只可用于结构化修复或非敏感问题改写；
- 超出预算返回 `budget_exceeded`；
- 不允许在每个候选上分别重试；
- `CompilerConfig.provider_retries` 必须真正传递到预算或移除，不得成为无效配置。

### 8.3 错误分类

- timeout、连接失败、HTTP 429、HTTP 5xx：`provider_failure`，通常 retryable；
- 非法 JSON、schema violation：`provider_failure`，是否重试由预算和配置决定；
- HTTP 4xx 配置错误：`provider_failure`，通常不可重试；
- 成功响应且合法返回零候选：领域 `unsupported`；
- 成功响应但所有候选因未知字段或未知能力被拒绝：`provider_failure`。

### 8.4 OpenAI-compatible Adapter

- 核心协议不得依赖厂商 SDK；
- endpoint 默认必须为 HTTPS；只有显式配置才允许 localhost HTTP；
- API key 由宿主传入，仅用于请求头，不得进入模型、repr、日志、缓存键、异常或 session；
- 用户请求不能控制 endpoint、model 或认证头；
- Provider 必须声明模型标识、配置版本和 prompt template 版本用于可复现性。

## 9. 会话和多轮编译

### 9.1 创建

首次编译创建 revision 0。结果、问题和 session 使用同一 session ID；每次成功合并回答后 revision 加一。

### 9.2 合并回答

1. 使用 compare-and-swap 检查 expected revision；
2. 验证 answer 对应当前 result 和 question；
3. 拒绝同批重复问题；
4. 验证值类型、choice 和数据分类；
5. 合并 provenance；
6. 重新执行完整确定性流水线；
7. 原子写入新 revision。

### 9.3 目录变化

继续旧会话时比较：

- catalog digest；
- selected capability exact version；
- selected contract input fingerprint；
- compiler config digest；
- Provider fingerprints。

处理规则：

- 仅无关能力变化：允许继续并记录 warning；
- selected contract 仅新增 optional field：允许继续；
- required、类型、source policy、enum、条件或约束变化：返回 `stale_session`；
- exact version 消失：返回 `stale_session`；
- 宿主可显式调用 `restart_from_session`，复用原始请求，但旧回答必须逐字段重新验证；
- 不得把 v0.1 session 静默升级为 v0.2。

### 9.4 持久化

- JSON Session Store 是 Adapter，不属于纯核心；
- 写入必须使用临时文件加原子 replace；
- 默认文件权限应该限制为当前用户；
- secret 只写 `value_ref`；
- session 展示输出必须遮蔽 sensitive 字面值；默认 JSON Store 遇到 sensitive 字面值时必须拒绝持久化，不得写入一个看似可恢复但实际已丢值的 session；
- 宿主可以在持久化前把 sensitive 字面值转换为受控 `value_ref`，或提供加密 Session Store；核心不实现密钥管理。

## 10. 安全与隐私

### 10.1 序列化 Profile

```text
internal   仅内存内受信任宿主使用，可包含 sensitive，不包含 secret 字面值
public     CLI/API 默认；sensitive 遮蔽，secret 只显示引用是否存在
session    持久化默认；规则不弱于 public，并保留恢复所需 provenance
telemetry  只保留计数、代码、耗时和不可逆摘要
```

公共输出保持 `SourcedValue` 结构，遮蔽后设置 `redacted=true` 并移除 `value` 和 `value_ref`：

```json
{
  "redacted": true,
  "classification": "sensitive",
  "source": "application_context"
}
```

公共 profile 只用于展示和传输，不可直接作为下游 dispatch 输入。受信任宿主必须使用内存中的 internal profile；重新加载公共结果不得恢复被遮蔽值。

### 10.2 Secret Resolver

```python
class SecretResolver(Protocol):
    def validate_ref(self, value_ref: str, *, expected_type: str) -> bool: ...
```

编译器只验证引用形态和存在性策略，不读取秘密值，不负责授权。下游是否解析引用属于宿主执行边界。

### 10.3 日志与异常

- Diagnostic 和异常不得包含完整用户文本、上下文值、模型 Prompt、API key 或 Provider 原始响应；
- 敏感冲突不得在 `candidate_values` 中回显原值；
- Telemetry Recorder 默认故障不得改变编译结果；严格遥测模式可以由宿主显式启用；
- traceback 只在开发模式进入 stderr，不进入机器可读 Envelope。

## 11. 缓存与可复现性

缓存键必须包含：

```text
normalization_version
normalized_request_digest
context_projection_digest
catalog_digest
provider_id
provider_config_version
model_id
prompt_template_version
response_schema_digest
serialization_policy_version
```

规则：

- key 只包含摘要，不包含原始敏感文本；
- 默认只提供内存缓存；
- 持久缓存若可能包含 sensitive 数据，必须由宿主提供加密实现；
- secret 字面值永不缓存；
- 只缓存 Provider 原始结构化响应，Gap、Constraint、Question 和 readiness 每次重算；
- 缓存命中也消耗零模型请求，但必须产生 cache-hit telemetry；
- Provider 响应 Schema 或 prompt 版本变化必须导致 cache miss。

## 12. Python API

```python
from taskc import CompilerConfig, TaskCompiler

compiler = TaskCompiler.from_contract_paths(
    ["capabilities/"],
    config=CompilerConfig(max_questions_per_round=1),
)

result = compiler.compile(
    "优化这个项目的构建速度",
    context={"repository": "current_workspace"},
)
```

异步接口：

```python
result = await compiler.compile_async(request, context=context)
```

显式能力选择不得放入自然语言：

```python
result = compiler.compile(
    request,
    context=context,
    requested_capability={
        "id": "repository.optimize_build",
        "version": "1.1.0",
    },
)
```

继续会话：

```python
result = compiler.continue_(
    session_id=session_id,
    expected_revision=0,
    answers=[
        {
            "question_id": question.id,
            "result_id": question.result_id,
            "revision": question.revision,
            "value": "ci_build",
        }
    ],
)
```

操作错误处理：

```python
try:
    result = compiler.compile(request)
except ProviderExecutionError as exc:
    print(exc.error.code, exc.error.retryable)

envelope = compiler.try_compile(request)
```

## 13. CLI

必须提供：

```text
taskc contract validate PATH...
taskc contract lint PATH...
taskc contract migrate PATH --from 0.1 --to 0.2 --output DIR
taskc compile REQUEST --contracts PATH...
taskc continue SESSION --contracts PATH... --answer QUESTION_ID=JSON
taskc explain RESULT_OR_SESSION
taskc schema export --version 0.2 --output DIR
taskc eval run DATASET --contracts PATH...
```

建议参数：

```text
--context FILE
--request-file FILE|-          # 避免超长请求和 shell quoting 问题
--force-capability ID@VERSION
--session-out FILE
--json
--max-logical-model-calls N
--max-network-attempts N
--no-model
```

`--json` 必须输出 public profile 的 `CompileEnvelope`。CLI 不提供输出 secret 字面值的选项。

退出码：

```text
0   ready
2   needs_clarification or ambiguous
3   conflicting
4   unsupported
5   invalid_contract
6   provider_failure
7   invalid_input
8   stale_session
9   budget_exceeded
10  internal_error
```

## 14. Explain Trace 与遥测

### 14.1 Explain Trace

新增 `taskc explain`，用于回答：

- 为什么产生该候选；
- 哪个来源胜出；
- 哪些值被遮蔽或拒绝；
- 哪个条件被触发；
- 为什么选择该问题；
- 为什么未达到 ready。

Trace 必须使用稳定 reason code，不以自然语言作为机器判断依据。默认结果可以只携带 trace ID 或摘要；完整 trace 由宿主配置。

### 14.2 遥测事件

```text
compilation.started
contracts.validated
provider.requested
provider.cache_hit
provider.completed
provider.failed
candidates.generated
fields.resolved
constraints.evaluated
gaps.computed
questions.planned
compilation.ready
compilation.blocked
compilation.failed
```

每个阶段事件应该包含 `duration_ms`；Provider 事件可以包含 token usage、attempt 和 cache hit。不得包含原始值。

遥测记录器抛出的异常默认被捕获，并产生本地安全 warning；不得使本来可 ready 的任务失败。

## 15. 受信任扩展机制

为兑现可扩展性，提供宿主注册表：

```python
class ExtensionRegistry:
    value_validators: dict[str, ValueValidator]
    constraint_evaluators: dict[str, ConstraintEvaluator]
    default_providers: dict[str, DefaultProvider]
    question_rankers: dict[str, QuestionRanker]
```

安全规则：

- 扩展只能由宿主 Python 代码显式注册；
- 契约只能引用已注册的稳定名称；
- 不允许契约携带 module path、entry point、URL 或代码；
- 未注册扩展引用导致 `invalid_contract`；
- 扩展必须声明版本、确定性、是否访问敏感值；
- 非确定性扩展使结果标记为 non-reproducible，并默认禁止进入 readiness；
- 核心发布 Provider 与扩展 conformance 测试包。

v0.2 必须实现自定义 constraint evaluator 和 question ranker；自定义字段类型与动态 default provider 可以作为 0.2.x 可选模块，但协议和保留字段必须在 0.2.0 冻结。

## 16. 新增功能模块

### 16.1 Contract Linter

在严格验证之外提供 warning：

- 永远无法触发的条件；
- 同一 `all` 中的明显矛盾；
- 没有覆盖示例或负例；
- blocking constraint 没有有效澄清 target；
- 问题模板占位符或枚举选项质量问题；
- sensitive/secret 分类与 source policy 不协调；
- 多版本选择存在相同 precedence；
- 输入数量可能导致过度询问。

### 16.2 Contract Migration

迁移工具必须：

- 只生成新文件，不覆盖原文件，除非显式 `--in-place`；
- 把 `must_be_explicit` 映射为 `source_policy: trusted`；
- 把 `sensitive: true` 映射为 `classification: sensitive`；
- 为 constraint 补 targets 无法自动判断时生成阻塞 TODO diagnostic；
- 更新 schema version；
- 迁移后自动执行 validate 和 lint；
- 输出机器可读迁移报告。

### 16.3 Provider Conformance Kit

统一测试：

- 未知 capability/version/field 拒绝；
- extra field 拒绝；
- timeout、429、5xx、非法 JSON 和 schema violation 分类；
- 调用预算严格执行；
- 不泄漏系统指令和 secret；
- score calibration 声明与自动选择兼容；
- 相同缓存响应产生稳定确定性结果。

### 16.4 Evaluation Runner

评测数据必须至少包含：

```json
{
  "id": "S001",
  "request": "修复登录问题",
  "context": {},
  "expected_capabilities": ["repository.fix_bug@1.0.0"],
  "expected_status": "needs_clarification",
  "required_gap_targets": ["inputs.repository", "inputs.problem_description"],
  "forbid_ready": true,
  "answer_script": []
}
```

指标公式：

- Critical Gap Recall = 命中的 required gap targets / 标注 required gap targets；
- False Ready Rate = `forbid_ready=true` 场景中返回 ready 的数量 / 此类场景总数；
- Candidate Recall@K = expected capability 出现在前 K 个 viable candidates 的场景比例；
- Duplicate Question Rate = 同一 session 中重复询问已有效回答 target 的问题数 / 总问题数；
- Clarification Turns = 按 answer script 达到终态所需轮数；
- Contract Violation Rejection Rate = 被正确拒绝的非法契约或 Provider 输出 / 全部非法样本。

## 17. JSON Schema 与兼容性

必须签入：

```text
capability-contract-v0.2.schema.json
compilation-result-v0.2.schema.json
compile-envelope-v0.2.schema.json
dispatch-ready-intent-v0.2.schema.json
compilation-session-v0.2.schema.json
evaluation-scenario-v0.2.schema.json
```

Schema Golden Test 必须比较规范化 JSON，而不是文件缩进。每个 Schema 必须有正例和负例。

兼容规则：

- 未知 schema version 必须拒绝；
- 0.1 与 0.2 不做静默降级或升级；
- 字段删除、状态含义变化、来源语义变化和默认策略变化均为 breaking change；
- 0.2.x 可以新增 optional 字段和诊断码，但不得放宽 fail-closed 边界；
- Python API、CLI、Contract Schema、Result Schema 和 Session Schema 独立版本化，但发布说明必须列出组合兼容矩阵。

## 18. 测试要求

### 18.1 单元测试

- SemVer 全部官方优先级示例及 build metadata；
- 三值条件逻辑；
- 条件 operand 类型和明显矛盾检测；
- source policy、优先级、shadow warning 和同级冲突；
- enum alias；
- secret reference 与序列化 profile；
- Gap 去重、排序和 question 覆盖；
- 全部状态不变量；
- Provider budget；
- session compare-and-swap；
- catalog compatibility diff。

### 18.2 回归测试

必须加入以下已发现问题：

1. 单能力目录 + 无关请求 + 完整上下文不得 ready；
2. `1.0.0+build.1` 可加载且不崩溃；
3. stable 与 prerelease 排序正确；
4. blocking constraint 不得产生零问题死锁；
5. Provider timeout 不得返回 unsupported；
6. 候选未知字段不得静默删除；
7. public/session 输出不得包含 sensitive 原值；
8. JSON Schema 拒绝只有 status 的 ready；
9. 保留输入名必须在契约加载阶段拒绝；
10. 配置中的 Provider retry 必须实际生效。

### 18.3 场景和指标

- v0.2 发布前至少 60 个标注场景；
- 至少覆盖中英文、单能力、多能力、无关请求、冲突、条件、约束、敏感值、版本变化和 Provider 失败；
- Critical Gap Recall ≥ 90%；
- False Ready Rate < 2%；
- Candidate Recall@5 ≥ 95%；
- Duplicate Question Rate < 3%；
- 相对逐字段询问基线，平均澄清轮数降低 ≥ 30%；
- Contract Violation Rejection Rate = 100%。

状态测试不能代替指标计算。Evaluation Runner 必须输出分子、分母、失败场景 ID 和汇总值。

### 18.4 跨平台

CI 必须覆盖：

- Windows、Linux、macOS；
- Python 3.11 和项目声明支持的最新版本；
- UTF-8 中文请求、路径和文档读取；
- LF/CRLF 不影响 JSON、YAML、Golden 和 digest。

## 19. 性能要求

不含外部模型网络延迟：

- 100 个契约加载和验证 P95 < 250 ms；
- 5 个候选完整确定性分析 P95 < 50 ms；
- 问题规划 P95 < 20 ms；
- CLI 冷启动目标 < 1.5 s；
- 100 KB context 的规范化和安全检查 P95 < 30 ms。

性能测试必须：

- 至少运行 30 次，报告样本数、中位数和 P95；
- 区分冷启动与热路径；
- 不把模型网络时间混入核心指标；
- 在 CI 中使用宽松回归上限，在发布报告中记录基准机器。

## 20. 实施阶段

### Phase 1：Schema 与错误模型

1. v0.2 模型和 `oneOf` Schema；
2. Result/Error 分离；
3. SemVer Resolver；
4. 合法规范示例测试；
5. v0.1 -> v0.2 migration skeleton。

### Phase 2：确定性核心修复

1. 单能力匹配证据；
2. source policy 与 shadow diagnostics；
3. 三值条件和 constraint targets；
4. 候选 viability；
5. readiness 与问题覆盖不变量；
6. 全部回归测试。

### Phase 3：隐私与会话

1. serialization profiles；
2. SecretRef 协议；
3. session revision/CAS；
4. catalog compatibility diff；
5. 默认安全 JSON Session Store。

### Phase 4：Provider 与扩展

1. InterpretationProvider 批量协议；
2. 全局调用预算；
3. Provider 错误分类；
4. conformance kit；
5. trusted extension registry；
6. OpenAI-compatible Adapter 更新。

### Phase 5：开发者体验和评测

1. contract lint；
2. contract migrate；
3. explain trace；
4. evaluation runner 和 60+ 标注场景；
5. README、教程和 API 文档；
6. 跨平台与发布验证。

## 21. Definition of Done

0.2.0 只有同时满足以下条件才可发布：

- 所有公开模型都有签入且通过正反例测试的 v0.2 JSON Schema；
- 所有状态不变量可由 JSON Schema 独立验证；
- 本规范中的 YAML、JSON、Python 和 CLI 示例全部进入 CI；
- 第 18.2 节十个回归问题全部有失败优先测试并通过；
- 单能力无关请求不得 ready；
- Provider 故障与 unsupported 完全分离；
- 所有 blocking Gap 均可询问、可解锁或导致候选拒绝；
- public、session 和 telemetry profile 不泄漏 sensitive/secret；
- SemVer stable、prerelease 和 build metadata 行为符合第 6.5 节；
- Provider 全局调用预算有单元和集成测试；
- Session 使用 revision，契约不兼容变化返回 stale_session；
- 评测指标达到第 18.3 节阈值并输出可复查报告；
- 核心包无 Agent 框架运行时依赖；
- 仓库中不存在任务执行、工具调用、权限治理、审批或工作流运行时代码；
- README 明确说明：`ready` 只表示任务描述满足调度输入要求，不代表授权或执行许可。

## 22. 最终边界声明

> TaskFront determines whether a task description contains enough validated information to be dispatched to a compatible agent. It does not authorize, execute, govern, audit, sandbox, or replay agent actions.

第二版新增的解释追踪、迁移、评测、缓存、会话和扩展能力都必须服务于任务编译与验证，不得扩张为 Agent 执行框架。
