# MVP implementation decisions

The 0.1 product and implementation specifications intentionally leave several
host-policy choices open. This implementation uses the following conservative
defaults:

- Source precedence is `clarification_answer`, `user`, `application_context`,
  `explicit_default`, then `model_inference`. Different values at the same
  effective precedence become a blocking conflict.
- A direct answer to a surfaced conflict explicitly resolves the current value;
  superseded answers remain in `CompilationSession.answers` for provenance.
- Strings are not converted to numbers unless
  `CompilerConfig.allow_string_to_number` is enabled. Enum matching is exact.
- Multiple plausible capabilities return `ambiguous`. Score-based automatic
  selection is disabled unless the host opts in and configures both a threshold
  and a margin.
- Any blocking conflict yields `conflicting`; other blocking field gaps yield
  `needs_clarification`. No valid candidate yields `unsupported`.
- Clarification answers count as explicit input for `must_be_explicit` fields.
- The static provider is an offline lexical baseline. Model-backed candidate and
  extraction providers are optional and their outputs pass through strict
  Pydantic validation before deterministic analysis.
- Model responses may be retried once by default. An in-memory raw-response
  cache wrapper is available, but readiness and gap calculations are always
  recomputed.
- Compilation automatically creates an in-memory session. The latest ID is
  available as `TaskCompiler.last_session_id`; the CLI can persist the session
  with `--session-out` and resume it with `taskc continue`.
- If a catalog digest changes, continuation recompiles against the current
  catalog and emits `E-CONTRACT-CATALOG-CHANGED`.
- Constraint entries use `{code, assert, message, blocking}` where `assert`
  contains the same finite condition language used by conditional requirements.

