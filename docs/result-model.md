# Compilation result model

Every result uses schema version `0.1` and one of these states:

- `ready`: one candidate satisfies all deterministic readiness checks and an
  intent is present.
- `needs_clarification`: one selected candidate has blocking missing,
  unverifiable, or field-level ambiguous inputs.
- `ambiguous`: multiple capabilities remain plausible.
- `conflicting`: equally authoritative values cannot be reconciled.
- `unsupported`: no valid catalog capability matches.
- `invalid_contract`: reserved for host integrations that represent contract
  loading failures as results; the Python loader raises a typed validation error
  before compilation.

`Gap` objects have stable codes, a kind, optional field path, blocking flag,
candidate values, and a suggested resolution. `Diagnostic` objects report
contract, input, and provider failures without including secrets or complete
prompts.

`DispatchReadyIntent` binds a concrete capability ID and version, sourced
inputs, declared deliverables, a generated intent ID, and compilation time. It
contains no permission, approval, policy, credential, executable code, effect,
or workflow bytecode fields.

