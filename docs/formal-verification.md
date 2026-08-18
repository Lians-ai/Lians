# Formal verification in Lians

Lians can prove properties over an explicitly bounded formal model and bind the
result to the task contract, current evidence, source-file hashes, and exact Git
diff in one signed verification receipt.

The current backend is `finite-model-v1`. It uses exhaustive enumeration. Every
possible assignment in the declared finite domains is checked. A successful
result is a proof by cases for that model, not a sample or probabilistic test.
If a property is false, Lians records a concrete counterexample.

The `python-finite-function-v1` backend goes further. It parses one restricted
pure Python function, interprets the function's AST itself, and checks its
postconditions for every declared bounded input. It never imports or executes
the source file. A successful result is a bounded implementation proof for that
actual function under the supported Python subset.

In the August 18 local synthetic pressure run, both backends exhaustively
checked 125,000 cases in about 1.13 seconds each. All planted false claims and
the Python source regression produced failures, all counterexamples were
returned, unsafe Python calls and vacuous proofs were rejected, and no project
code was executed. These are checker throughput measurements, not application
correctness claims. See the [machine-readable report](../packages/lians-easy/benchmarks/formal-proof-report.json).

## Claim boundary

A successful result means:

- every assignment satisfying the declared assumptions was checked;
- every declared claim was true for every one of those assignments;
- the assumptions were satisfiable, so the proof was not vacuous;
- the proof manifest and bound source files match the hashes in the signed
  receipt.

It does not mean:

- the formal model perfectly represents the user's informal intent;
- an application file implements the same semantics as the formal model;
- unmodeled I/O, concurrency, networks, databases, frameworks, or deployment
  behavior are correct;
- the Python implementation of the Lians proof checker has itself been
  formally verified;
- the repository is automatically safe to merge or deploy.

Lians therefore reports `proved_by_exhaustive_enumeration` for the declared
model. The restricted Python backend may also report
`bounded_implementation_correctness_proven`, but the broad
`implementation_correctness_formally_proven` claim remains false because the
proof covers only declared finite inputs and supported pure syntax. A human ship
decision remains required.

Lean provides a stronger small-kernel proof path for general theorem statements,
but its own documentation still requires auditing theorem statements, imports,
and axioms. Dafny verifies annotated Dafny programs, and Kani checks Rust proof
harnesses within its supported semantics. Lians will add these only behind a
sandbox that can safely process agent-generated proof projects.

- [Lean proof validation and axiom boundary](https://lean-lang.org/doc/reference/latest/ValidatingProofs/)
- [Dafny verification reference](https://dafny.org/dafny/DafnyRef/DafnyRef)
- [Kani verification results and vacuity boundary](https://model-checking.github.io/kani/verification-results.html)

## Proof manifest

Create a repository file such as `proofs/authorization.proof.json`:

```json
{
  "schema": "https://lians.ai/schemas/finite-proof/v0.1",
  "scope": "Only an approved user can enter the modeled ship state.",
  "variables": {
    "role": ["guest", "member", "admin"],
    "approved": [false, true]
  },
  "definitions": {
    "may_ship": {
      "op": "and",
      "args": [
        {
          "op": "eq",
          "left": {"var": "role"},
          "right": {"const": "admin"}
        },
        {"var": "approved"}
      ]
    }
  },
  "assumptions": {"const": true},
  "claims": [
    {
      "id": "approval-required",
      "description": "Shipping always implies explicit approval.",
      "expression": {
        "op": "implies",
        "if": {"var": "may_ship"},
        "then": {"var": "approved"}
      }
    }
  ],
  "source_bindings": ["src/authorization.py"]
}
```

Supported values are booleans, bounded integers, and short strings. Supported
operations are:

- Boolean: `and`, `or`, `not`, `implies`, `if`
- Equality and order: `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `in`
- Integer: `add`, `sub`, `mul`, `neg`, `mod`

The checker rejects credential-shaped values, path traversal, symlinks,
unsatisfiable assumptions, unknown operations, oversized files, and proof
models that exceed its state-space or evaluation limits.

## Bind it to a repository task

After creating a Lians task contract, call `configure_verification` with the
ordinary file policy plus the proof obligation:

```json
{
  "task_id": "authorization-fix",
  "allowed_paths": ["src/**", "proofs/**"],
  "criterion_paths": {
    "criterion-1": ["src/**", "proofs/**"]
  },
  "required_checks": ["tests"],
  "formal_proofs": [
    {
      "id": "authorization-proof",
      "backend": "finite-model-v1",
      "manifest": "proofs/authorization.proof.json"
    }
  ]
}
```

`verify_work` checks the formal model automatically. A counterexample or proof
error blocks the repository receipt. A successful proof appears as a
`Proof-backed ship review` node in the Work Graph.

## Prove an actual restricted Python function

Given this source:

```python
def clamp(value, lower, upper):
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value
```

Create a `python-finite-function-v1` manifest:

```json
{
  "schema": "https://lians.ai/schemas/python-function-proof/v0.1",
  "scope": "The clamp result remains inside the declared bounds.",
  "source": "src/clamp.py",
  "function": "clamp",
  "variables": {
    "value": [-2, -1, 0, 1, 2],
    "lower": [-1, 0],
    "upper": [0, 1]
  },
  "assumptions": {
    "op": "le",
    "left": {"var": "lower"},
    "right": {"var": "upper"}
  },
  "claims": [
    {
      "id": "inside-lower-bound",
      "description": "The result is at least lower.",
      "expression": {
        "op": "ge",
        "left": {"var": "result"},
        "right": {"var": "lower"}
      }
    },
    {
      "id": "inside-upper-bound",
      "description": "The result is at most upper.",
      "expression": {
        "op": "le",
        "left": {"var": "result"},
        "right": {"var": "upper"}
      }
    }
  ]
}
```

Configure it with backend `python-finite-function-v1`. The supported source
subset includes scalar constants, local assignments, `if`, `return`, boolean
logic, integer addition, subtraction, multiplication, modulo, comparisons, and
conditional expressions. Calls, imports, attributes, collections, mutation,
loops, exceptions, I/O, decorators, and dynamic evaluation are rejected.
