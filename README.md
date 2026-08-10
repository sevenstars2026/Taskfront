# Agent Task Compiler (`taskc`)

`taskc` compiles ambiguous or incomplete natural-language requests against a
catalog of capability contracts. It returns either a validated
`DispatchReadyIntent` or deterministic diagnostics and a minimal clarification
plan.

> **A `ready` result is not authorization.** This project only determines
> whether a task description contains enough information to be dispatched to a
> compatible agent. It does not authorize, execute, govern, audit, sandbox, or
> replay agent actions.

The package targets Python 3.11+ and has no Agent-framework runtime dependency.

## Repository status

This repository contains the TaskFront / Agent Task Compiler MVP 0.1
implementation. The current baseline includes the deterministic compiler core,
strict capability-contract validation, static and structured-model Provider
interfaces, clarification sessions, CLI commands, JSON Schemas, examples, and
tests.

Validation status for this baseline:

- 44 automated tests passing;
- 22/22 public evaluation scenarios passing;
- Python 3.11+ package and editable installation verified;
- offline static-provider path available without an Agent framework;
- `ready` remains a dispatch-readiness result, not authorization or execution.

The repository is intentionally limited to task compilation. It does not run
agent tasks, call real tools, manage credentials or permissions, execute model
generated code, or provide workflow orchestration.

## Quick start

```bash
python -m pip install -e .
taskc contract validate examples/single_agent/capability.yaml
taskc compile "Fix the checkout failure" \
  --contracts examples/single_agent/capability.yaml \
  --context examples/single_agent/context.json --json
```

Python API:

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

Candidate generation and field extraction are provider protocols. The default
static providers are deterministic and offline; structured model providers can
be injected without changing the contract, gap, clarification, or readiness
logic.

See [Concepts](docs/concepts.md),
[Capability Contract 0.1](docs/capability-contract.md),
[Result model](docs/result-model.md), and
[MVP implementation decisions](docs/implementation-decisions.md) for the public
behavior and extension boundaries.
