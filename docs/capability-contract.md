# Capability Contract 0.1

A contract declares one task shape. YAML and JSON are supported and unknown
fields are rejected.

```yaml
schema_version: "0.1"
id: repository.fix_bug
version: 1.0.0
description: Diagnose and fix a reproducible repository defect.
examples:
  - Fix the checkout failure.
negative_examples:
  - Improve repository build speed.
required_inputs:
  repository:
    type: string
    description: Repository or workspace to inspect.
    must_be_explicit: true
  problem_description:
    type: string
    description: Observed incorrect behavior.
optional_inputs:
  reproduction_steps:
    type: array
    items: string
    description: Steps that reproduce the defect.
deliverables:
  - code_patch
  - verification_report
```

Capability IDs are lower-case dot-separated names and versions use SemVer.
Required and optional inputs cannot overlap. Supported input types are
`string`, `integer`, `number`, `boolean`, `array`, `object`, and `enum`.

Conditional requirements use a non-executable expression tree. Leaf operators
are `equals`, `not_equals`, `in`, and `exists`; `all` and `any` combine child
conditions. Every referenced or required field must be declared by the same
contract.

```yaml
conditional_requirements:
  - when:
      field: optimization_goal
      in: [ci_build, both]
    require: [ci_provider]
```

Constraints use the same expression language and assert that a condition is
true:

```yaml
constraints:
  - code: GAP-UNVERIFIABLE-MEASUREMENT
    assert:
      field: measurement_enabled
      equals: true
    message: Before/after measurement must be enabled.
    blocking: true
```

Contracts cannot contain code, tool invocations, credentials, or arbitrary
expressions.

