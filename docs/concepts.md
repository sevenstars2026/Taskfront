# Concepts

Agent Task Compiler treats task preparation like compilation. Natural-language
text is untrusted source input; capability contracts are the target type system;
gaps and diagnostics are compiler errors; a `DispatchReadyIntent` is validated
output for a downstream consumer.

The compiler preserves three boundaries:

1. Providers may propose candidates and extracted values, but cannot declare a
   task ready.
2. Contract matching, gap analysis, question ordering, and readiness are
   deterministic.
3. A ready intent says that required information is present. It does not grant
   permission and does not execute anything.

Every input value carries provenance through `SourcedValue`. Explicit values
from clarification answers, user input, application context, and contract
defaults take precedence over model inference. Equal-priority contradictory
values become a blocking conflict instead of being silently overwritten.

Multiple plausible capabilities remain visible. Unless the host explicitly
enables threshold-based auto-selection, the compiler emits an `ambiguous`
result and asks the user to choose a capability.

