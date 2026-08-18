"""Safe, exhaustive proofs for explicitly bounded formal models.

This module intentionally does not import or execute project code. It checks a
small JSON expression language over finite domains, so every satisfying input
can be enumerated and every claim can be proved or disproved by cases. Source
files are hash-bound to the result, but equivalence between those files and the
formal model remains a separate refinement obligation.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
import json
import re
from pathlib import Path
from typing import Any

_SCHEMA = "https://lians.ai/schemas/finite-proof/v0.1"
_BACKEND = "finite-model-v1"
_PYTHON_SCHEMA = "https://lians.ai/schemas/python-function-proof/v0.1"
_PYTHON_BACKEND = "python-finite-function-v1"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_CLAIM_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SECRET = re.compile(
    r"(?:sk-ant-api[0-9A-Za-z_-]{20,}|sk-(?:proj-)?[0-9A-Za-z_-]{24,}|"
    r"(?:ghp_|github_pat_)[0-9A-Za-z_]{24,}|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)
_MAX_MANIFEST_BYTES = 256_000
_MAX_BOUND_FILE_BYTES = 5_000_000
_MAX_BOUND_TOTAL_BYTES = 20_000_000
_MAX_STATE_SPACE = 250_000
_MAX_EXPRESSION_NODES = 20_000
_MAX_TOTAL_EVALUATIONS = 10_000_000
_MAX_INTEGER = 10**15


class FormalProofError(ValueError):
    """A proof manifest could not be checked safely or unambiguously."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _clean_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    rendered = " ".join(value.strip().split())
    if not rendered:
        raise FormalProofError(f"{field} cannot be blank")
    if len(rendered) > maximum:
        raise FormalProofError(f"{field} must be {maximum} characters or fewer")
    if _SECRET.search(rendered):
        raise FormalProofError(f"{field} appears to contain credential material")
    return rendered


def _clean_path(value: Any, *, field: str) -> str:
    rendered = _clean_text(value, field=field, maximum=500).replace("\\", "/")
    if (
        rendered.startswith(("/", "!"))
        or re.match(r"^[A-Za-z]:", rendered)
        or any(part in {"", ".", ".."} for part in rendered.split("/"))
        or "\x00" in rendered
        or any(character in rendered for character in "*?[]{}")
    ):
        raise FormalProofError(f"{field} must be one concrete repository-relative file")
    return rendered


def _bound_file(root: Path, relative: str, *, maximum: int) -> tuple[Path, bytes]:
    try:
        resolved_root = root.resolve(strict=True)
        resolved = (resolved_root / relative).resolve(strict=True)
        resolved.relative_to(resolved_root)
        size = resolved.stat().st_size
    except (OSError, RuntimeError, ValueError) as exc:
        raise FormalProofError(f"Bound proof file is unavailable: {relative}") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise FormalProofError(f"Bound proof path must be a regular file: {relative}")
    if size > maximum:
        raise FormalProofError(f"Bound proof file exceeds its size limit: {relative}")
    try:
        return resolved, resolved.read_bytes()
    except OSError as exc:
        raise FormalProofError(f"Bound proof file could not be read: {relative}") from exc


def _scalar(value: Any, *, field: str) -> bool | int | str:
    if type(value) not in {bool, int, str}:
        raise FormalProofError(f"{field} must be a boolean, integer, or string")
    if type(value) is int and abs(value) > _MAX_INTEGER:
        raise FormalProofError(f"{field} integer is outside the supported range")
    if isinstance(value, str):
        if len(value) > 120:
            raise FormalProofError(f"{field} string must be 120 characters or fewer")
        if _SECRET.search(value):
            raise FormalProofError(f"{field} appears to contain credential material")
    return value


def _typed_key(value: bool | int | str) -> tuple[str, str]:
    return type(value).__name__, json.dumps(value, ensure_ascii=False, sort_keys=True)


def _expression_nodes(value: Any, *, depth: int = 0) -> int:
    if depth > 64:
        raise FormalProofError("Proof expression is too deeply nested")
    if isinstance(value, dict):
        total = 1 + sum(
            _expression_nodes(item, depth=depth + 1) for item in value.values()
        )
    elif isinstance(value, list):
        total = 1 + sum(_expression_nodes(item, depth=depth + 1) for item in value)
    else:
        total = 1
    if total > _MAX_EXPRESSION_NODES:
        raise FormalProofError("Proof expressions exceed the structural size limit")
    return total


class _ExpressionEvaluator:
    def __init__(self) -> None:
        self.nodes = 0

    def evaluate(self, expression: Any, environment: dict[str, Any], *, depth: int = 0) -> Any:
        self.nodes += 1
        if self.nodes > _MAX_EXPRESSION_NODES:
            raise FormalProofError("Proof expressions exceeded the evaluation budget")
        if depth > 64 or not isinstance(expression, dict):
            raise FormalProofError("Proof expression is invalid or too deeply nested")
        if set(expression) == {"var"}:
            name = expression["var"]
            if not isinstance(name, str) or not _NAME.fullmatch(name) or name not in environment:
                raise FormalProofError("Proof expression references an unknown value")
            return environment[name]
        if set(expression) == {"const"}:
            return _scalar(expression["const"], field="expression.const")
        op = expression.get("op")
        if not isinstance(op, str):
            raise FormalProofError("Proof expression must contain var, const, or op")

        if op in {"and", "or", "add", "mul"}:
            self._keys(expression, {"op", "args"})
            args = expression.get("args")
            if not isinstance(args, list) or not 1 <= len(args) <= 20:
                raise FormalProofError(f"{op} requires 1-20 arguments")
            values = [self.evaluate(item, environment, depth=depth + 1) for item in args]
            if op in {"and", "or"}:
                self._booleans(values, op)
                return all(values) if op == "and" else any(values)
            self._integers(values, op)
            result = sum(values) if op == "add" else self._product(values)
            return self._bounded_integer(result, op)

        if op in {"not", "neg"}:
            self._keys(expression, {"op", "arg"})
            value = self.evaluate(expression.get("arg"), environment, depth=depth + 1)
            if op == "not":
                self._booleans([value], op)
                return not value
            self._integers([value], op)
            return self._bounded_integer(-value, op)

        if op == "implies":
            self._keys(expression, {"op", "if", "then"})
            premise = self.evaluate(expression.get("if"), environment, depth=depth + 1)
            conclusion = self.evaluate(expression.get("then"), environment, depth=depth + 1)
            self._booleans([premise, conclusion], op)
            return (not premise) or conclusion

        if op == "if":
            self._keys(expression, {"op", "condition", "then", "else"})
            condition = self.evaluate(
                expression.get("condition"), environment, depth=depth + 1
            )
            self._booleans([condition], op)
            branch = "then" if condition else "else"
            return self.evaluate(expression.get(branch), environment, depth=depth + 1)

        if op in {"eq", "ne", "lt", "le", "gt", "ge", "sub", "mod"}:
            self._keys(expression, {"op", "left", "right"})
            left = self.evaluate(expression.get("left"), environment, depth=depth + 1)
            right = self.evaluate(expression.get("right"), environment, depth=depth + 1)
            if op == "eq":
                return type(left) is type(right) and left == right
            if op == "ne":
                return type(left) is not type(right) or left != right
            if op in {"lt", "le", "gt", "ge"}:
                if type(left) is not type(right) or type(left) not in {int, str}:
                    raise FormalProofError(f"{op} requires two values of the same ordered type")
                return {
                    "lt": left < right,
                    "le": left <= right,
                    "gt": left > right,
                    "ge": left >= right,
                }[op]
            self._integers([left, right], op)
            if op == "sub":
                return self._bounded_integer(left - right, op)
            if right == 0:
                raise FormalProofError("mod divisor cannot be zero")
            return left % right

        if op == "in":
            self._keys(expression, {"op", "item", "values"})
            item = self.evaluate(expression.get("item"), environment, depth=depth + 1)
            raw_values = expression.get("values")
            if not isinstance(raw_values, list) or not 1 <= len(raw_values) <= 100:
                raise FormalProofError("in requires 1-100 literal values")
            values = [
                _scalar(value, field=f"expression.values[{index}]")
                for index, value in enumerate(raw_values)
            ]
            return any(type(item) is type(value) and item == value for value in values)

        raise FormalProofError("Unsupported proof operation")

    @staticmethod
    def _keys(expression: dict[str, Any], expected: set[str]) -> None:
        if set(expression) != expected:
            raise FormalProofError(
                f"Proof operation {expression.get('op')} has missing or unknown fields"
            )

    @staticmethod
    def _booleans(values: list[Any], operation: str) -> None:
        if any(type(value) is not bool for value in values):
            raise FormalProofError(f"{operation} requires boolean values")

    @staticmethod
    def _integers(values: list[Any], operation: str) -> None:
        if any(type(value) is not int for value in values):
            raise FormalProofError(f"{operation} requires integer values")

    @staticmethod
    def _bounded_integer(value: int, operation: str) -> int:
        if abs(value) > _MAX_INTEGER:
            raise FormalProofError(f"{operation} result is outside the supported range")
        return value

    @staticmethod
    def _product(values: list[int]) -> int:
        result = 1
        for value in values:
            result *= value
            if abs(result) > _MAX_INTEGER:
                raise FormalProofError("mul result is outside the supported range")
        return result


class FiniteModelProofChecker:
    """Prove all claims for every satisfying assignment in a finite model."""

    backend = _BACKEND

    def verify(self, root: Path, manifest_path: str) -> dict[str, Any]:
        clean_manifest_path = _clean_path(manifest_path, field="manifest")
        _, raw_manifest = _bound_file(
            root,
            clean_manifest_path,
            maximum=_MAX_MANIFEST_BYTES,
        )
        try:
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise FormalProofError("Proof manifest must be valid UTF-8 JSON") from exc
        if not isinstance(manifest, dict):
            raise FormalProofError("Proof manifest must be a JSON object")
        allowed_fields = {
            "schema",
            "scope",
            "variables",
            "definitions",
            "assumptions",
            "claims",
            "source_bindings",
        }
        unknown = set(manifest) - allowed_fields
        if unknown:
            raise FormalProofError(
                f"Proof manifest has unknown fields: {', '.join(sorted(unknown))}"
            )
        if manifest.get("schema") != _SCHEMA:
            raise FormalProofError("Proof manifest schema is unsupported")
        scope = _clean_text(manifest.get("scope"), field="scope", maximum=1_000)
        variables = self._variables(manifest.get("variables"))
        definitions = self._definitions(manifest.get("definitions"), variables)
        claims = self._claims(manifest.get("claims"))
        assumptions = manifest.get("assumptions", {"const": True})
        bindings = self._bindings(root, manifest.get("source_bindings"))

        state_space = 1
        for domain in variables.values():
            state_space *= len(domain)
        if state_space > _MAX_STATE_SPACE:
            raise FormalProofError(
                f"Proof state space {state_space} exceeds the {_MAX_STATE_SPACE} case limit"
            )
        nodes_per_assignment = _expression_nodes(assumptions)
        nodes_per_assignment += sum(_expression_nodes(item) for item in definitions.values())
        nodes_per_assignment += sum(
            _expression_nodes(item["expression"]) for item in claims
        )
        if nodes_per_assignment * state_space > _MAX_TOTAL_EVALUATIONS:
            raise FormalProofError("Proof model exceeds the total evaluation safety limit")

        claim_results = {
            item["id"]: {
                "id": item["id"],
                "description": item["description"],
                "status": "proved",
                "checked_assignments": 0,
                "counterexample": None,
            }
            for item in claims
        }
        satisfying_assignments = 0
        names = list(variables)
        domains = [variables[name] for name in names]
        counterexamples_recorded = 0
        for values in itertools.product(*domains):
            environment = dict(zip(names, values, strict=True))
            evaluator = _ExpressionEvaluator()
            for name, expression in definitions.items():
                environment[name] = evaluator.evaluate(expression, environment)
            assumption = evaluator.evaluate(assumptions, environment)
            if type(assumption) is not bool:
                raise FormalProofError("assumptions must evaluate to a boolean")
            if not assumption:
                continue
            satisfying_assignments += 1
            for claim in claims:
                result = evaluator.evaluate(claim["expression"], environment)
                if type(result) is not bool:
                    raise FormalProofError(f"Claim {claim['id']} must evaluate to a boolean")
                claim_result = claim_results[claim["id"]]
                claim_result["checked_assignments"] += 1
                if not result:
                    claim_result["status"] = "disproved"
                if (
                    not result
                    and claim_result["counterexample"] is None
                    and counterexamples_recorded < 3
                ):
                    claim_result["counterexample"] = {
                        name: environment[name] for name in [*names, *definitions][:16]
                    }
                    counterexamples_recorded += 1

        if satisfying_assignments == 0:
            raise FormalProofError("Proof assumptions are unsatisfiable; vacuous proofs are rejected")
        rendered_claims = list(claim_results.values())
        status = (
            "proved"
            if all(item["status"] == "proved" for item in rendered_claims)
            else "disproved"
        )
        result = {
            "backend": _BACKEND,
            "checker_version": 1,
            "status": status,
            "scope": scope,
            "manifest": {
                "path": clean_manifest_path,
                "sha256": hashlib.sha256(raw_manifest).hexdigest(),
            },
            "source_bindings": bindings,
            "model": {
                "variable_count": len(variables),
                "definition_count": len(definitions),
                "state_space": state_space,
                "satisfying_assignments": satisfying_assignments,
                "enumeration_complete": True,
                "maximum_evaluations": nodes_per_assignment * state_space,
            },
            "claims": rendered_claims,
            "proof_scope": "all satisfying assignments in the declared finite model",
            "trust": {
                "method": "exhaustive_enumeration",
                "project_code_executed": False,
                "proof_checker_formally_verified": False,
                "source_to_model_refinement_proven": False,
            },
        }
        result["proof_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
        return result

    @staticmethod
    def _variables(value: Any) -> dict[str, list[bool | int | str]]:
        if not isinstance(value, dict) or not 1 <= len(value) <= 12:
            raise FormalProofError("variables must define 1-12 finite domains")
        variables: dict[str, list[bool | int | str]] = {}
        for name, raw_domain in value.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise FormalProofError(f"Invalid proof variable name: {name}")
            if not isinstance(raw_domain, list) or not 1 <= len(raw_domain) <= 100:
                raise FormalProofError(f"Variable {name} must contain 1-100 values")
            domain = [
                _scalar(item, field=f"variables.{name}[{index}]")
                for index, item in enumerate(raw_domain)
            ]
            if len({_typed_key(item) for item in domain}) != len(domain):
                raise FormalProofError(f"Variable {name} contains duplicate values")
            variables[name] = domain
        return variables

    @staticmethod
    def _definitions(value: Any, variables: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if value is None:
            return {}
        if not isinstance(value, dict) or len(value) > 30:
            raise FormalProofError("definitions must contain 30 items or fewer")
        definitions: dict[str, dict[str, Any]] = {}
        for name, expression in value.items():
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise FormalProofError(f"Invalid proof definition name: {name}")
            if name in variables:
                raise FormalProofError(f"Definition shadows proof variable: {name}")
            if not isinstance(expression, dict):
                raise FormalProofError(f"Definition {name} must be an expression object")
            definitions[name] = expression
        return definitions

    @staticmethod
    def _claims(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not 1 <= len(value) <= 20:
            raise FormalProofError("claims must contain 1-20 proof obligations")
        claims: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(value):
            if not isinstance(item, dict) or set(item) != {"id", "description", "expression"}:
                raise FormalProofError(f"claims[{index}] has missing or unknown fields")
            claim_id = str(item.get("id") or "").strip().casefold()
            if not _CLAIM_ID.fullmatch(claim_id):
                raise FormalProofError(f"claims[{index}].id is invalid")
            if claim_id in seen:
                raise FormalProofError(f"Duplicate proof claim: {claim_id}")
            seen.add(claim_id)
            description = _clean_text(
                item.get("description"),
                field=f"claims[{index}].description",
                maximum=200,
            )
            expression = item.get("expression")
            if not isinstance(expression, dict):
                raise FormalProofError(f"claims[{index}].expression must be an object")
            claims.append(
                {"id": claim_id, "description": description, "expression": expression}
            )
        return claims

    @staticmethod
    def _bindings(root: Path, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not 1 <= len(value) <= 20:
            raise FormalProofError("source_bindings must contain 1-20 files")
        bindings: list[dict[str, Any]] = []
        seen: set[str] = set()
        total = 0
        for index, item in enumerate(value):
            path = _clean_path(item, field=f"source_bindings[{index}]")
            if path in seen:
                raise FormalProofError(f"Duplicate source binding: {path}")
            seen.add(path)
            _, content = _bound_file(root, path, maximum=_MAX_BOUND_FILE_BYTES)
            total += len(content)
            if total > _MAX_BOUND_TOTAL_BYTES:
                raise FormalProofError("Proof source bindings exceed the total size limit")
            bindings.append(
                {
                    "path": path,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                }
            )
        return bindings


_ALLOWED_PYTHON_NODES = {
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Assign,
    ast.AnnAssign,
    ast.If,
    ast.Return,
    ast.Expr,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Mod,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.IfExp,
}


class _PythonFunctionInterpreter:
    def __init__(self) -> None:
        self.nodes = 0

    def execute(self, function: ast.FunctionDef, environment: dict[str, Any]) -> Any:
        returned, value = self._statements(function.body, environment)
        if not returned:
            raise FormalProofError("A supported Python function path does not return a value")
        return value

    def _statements(
        self, statements: list[ast.stmt], environment: dict[str, Any]
    ) -> tuple[bool, Any]:
        for index, statement in enumerate(statements):
            self._step()
            if (
                index == 0
                and isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                continue
            if isinstance(statement, ast.Assign):
                if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                    raise FormalProofError("Python proof assignments require one local name")
                environment[statement.targets[0].id] = self._expression(
                    statement.value, environment
                )
                continue
            if isinstance(statement, ast.AnnAssign):
                if not isinstance(statement.target, ast.Name) or statement.value is None:
                    raise FormalProofError("Python proof assignments require one local name")
                environment[statement.target.id] = self._expression(statement.value, environment)
                continue
            if isinstance(statement, ast.If):
                condition = self._expression(statement.test, environment)
                if type(condition) is not bool:
                    raise FormalProofError("Python proof conditions must be boolean")
                branch = statement.body if condition else statement.orelse
                returned, value = self._statements(branch, environment)
                if returned:
                    return True, value
                continue
            if isinstance(statement, ast.Return):
                if statement.value is None:
                    raise FormalProofError("Python proof functions must return a scalar value")
                return True, self._expression(statement.value, environment)
            raise FormalProofError("Python proof function contains an unsupported statement")
        return False, None

    def _expression(self, node: ast.expr, environment: dict[str, Any]) -> Any:
        self._step()
        if isinstance(node, ast.Constant):
            return _scalar(node.value, field="python constant")
        if isinstance(node, ast.Name):
            if node.id not in environment:
                raise FormalProofError("Python proof function references an unknown name")
            return environment[node.id]
        if isinstance(node, ast.BoolOp):
            for item in node.values:
                value = self._expression(item, environment)
                if type(value) is not bool:
                    raise FormalProofError("Python proof boolean operations require booleans")
                if isinstance(node.op, ast.And) and not value:
                    return False
                if isinstance(node.op, ast.Or) and value:
                    return True
            return isinstance(node.op, ast.And)
        if isinstance(node, ast.UnaryOp):
            value = self._expression(node.operand, environment)
            if isinstance(node.op, ast.Not):
                if type(value) is not bool:
                    raise FormalProofError("Python proof not requires a boolean")
                return not value
            if isinstance(node.op, ast.USub):
                if type(value) is not int:
                    raise FormalProofError("Python proof negation requires an integer")
                return _ExpressionEvaluator._bounded_integer(-value, "python negation")
        if isinstance(node, ast.BinOp):
            left = self._expression(node.left, environment)
            right = self._expression(node.right, environment)
            if type(left) is not int or type(right) is not int:
                raise FormalProofError("Python proof arithmetic requires integers")
            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.Mod):
                if right == 0:
                    raise FormalProofError("Python proof modulo divisor is zero")
                result = left % right
            else:
                raise FormalProofError("Python proof arithmetic operation is unsupported")
            return _ExpressionEvaluator._bounded_integer(result, "python arithmetic")
        if isinstance(node, ast.Compare):
            left = self._expression(node.left, environment)
            for operator, comparator in zip(node.ops, node.comparators, strict=True):
                right = self._expression(comparator, environment)
                if isinstance(operator, ast.Eq):
                    passed = left == right
                elif isinstance(operator, ast.NotEq):
                    passed = left != right
                else:
                    if type(left) is not type(right) or type(left) not in {int, str}:
                        raise FormalProofError(
                            "Python proof ordering requires matching ordered values"
                        )
                    if isinstance(operator, ast.Lt):
                        passed = left < right
                    elif isinstance(operator, ast.LtE):
                        passed = left <= right
                    elif isinstance(operator, ast.Gt):
                        passed = left > right
                    elif isinstance(operator, ast.GtE):
                        passed = left >= right
                    else:
                        raise FormalProofError("Python proof comparison is unsupported")
                if not passed:
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            condition = self._expression(node.test, environment)
            if type(condition) is not bool:
                raise FormalProofError("Python proof conditions must be boolean")
            return self._expression(node.body if condition else node.orelse, environment)
        raise FormalProofError("Python proof expression is unsupported")

    def _step(self) -> None:
        self.nodes += 1
        if self.nodes > _MAX_EXPRESSION_NODES:
            raise FormalProofError("Python proof execution exceeded its safety budget")


class PythonFiniteFunctionProofChecker:
    """Prove bounded postconditions against one restricted pure Python function."""

    backend = _PYTHON_BACKEND

    def verify(self, root: Path, manifest_path: str) -> dict[str, Any]:
        clean_manifest_path = _clean_path(manifest_path, field="manifest")
        _, raw_manifest = _bound_file(root, clean_manifest_path, maximum=_MAX_MANIFEST_BYTES)
        try:
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise FormalProofError("Proof manifest must be valid UTF-8 JSON") from exc
        if not isinstance(manifest, dict):
            raise FormalProofError("Proof manifest must be a JSON object")
        expected = {
            "schema",
            "scope",
            "source",
            "function",
            "variables",
            "assumptions",
            "claims",
        }
        if set(manifest) != expected or manifest.get("schema") != _PYTHON_SCHEMA:
            raise FormalProofError("Python proof manifest schema or fields are invalid")
        scope = _clean_text(manifest["scope"], field="scope", maximum=1_000)
        source_path = _clean_path(manifest["source"], field="source")
        function_name = _clean_text(
            manifest["function"], field="function", maximum=64
        )
        if not _NAME.fullmatch(function_name):
            raise FormalProofError("Python proof function name is invalid")
        variables = FiniteModelProofChecker._variables(manifest["variables"])
        claims = FiniteModelProofChecker._claims(manifest["claims"])
        assumptions = manifest["assumptions"]
        _, source = _bound_file(root, source_path, maximum=1_000_000)
        try:
            module = ast.parse(source.decode("utf-8"), filename=source_path)
        except (UnicodeError, SyntaxError) as exc:
            raise FormalProofError("Python proof source could not be parsed") from exc
        functions = [
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        ]
        if len(functions) != 1:
            raise FormalProofError("Python proof source must contain exactly one target function")
        function = functions[0]
        self._validate_function(function, list(variables))

        state_space = 1
        for domain in variables.values():
            state_space *= len(domain)
        if state_space > _MAX_STATE_SPACE:
            raise FormalProofError(
                f"Proof state space {state_space} exceeds the {_MAX_STATE_SPACE} case limit"
            )
        nodes_per_assignment = len(list(ast.walk(function)))
        nodes_per_assignment += _expression_nodes(assumptions)
        nodes_per_assignment += sum(
            _expression_nodes(item["expression"]) for item in claims
        )
        if nodes_per_assignment * state_space > _MAX_TOTAL_EVALUATIONS:
            raise FormalProofError("Python proof exceeds the total evaluation safety limit")

        claim_results = {
            item["id"]: {
                "id": item["id"],
                "description": item["description"],
                "status": "proved",
                "checked_assignments": 0,
                "counterexample": None,
            }
            for item in claims
        }
        runtime_result = {
            "id": "runtime-safety",
            "description": "The supported function semantics return without an evaluation error.",
            "status": "proved",
            "checked_assignments": 0,
            "counterexample": None,
        }
        satisfying_assignments = 0
        counterexamples_recorded = 0
        names = list(variables)
        domains = [variables[name] for name in names]
        for values in itertools.product(*domains):
            inputs = dict(zip(names, values, strict=True))
            evaluator = _ExpressionEvaluator()
            assumption = evaluator.evaluate(assumptions, dict(inputs))
            if type(assumption) is not bool:
                raise FormalProofError("assumptions must evaluate to a boolean")
            if not assumption:
                continue
            satisfying_assignments += 1
            try:
                output = _PythonFunctionInterpreter().execute(function, dict(inputs))
            except FormalProofError:
                runtime_result["status"] = "disproved"
                if runtime_result["counterexample"] is None:
                    runtime_result["counterexample"] = inputs
                continue
            runtime_result["checked_assignments"] += 1
            environment = {**inputs, "result": output}
            for claim in claims:
                passed = evaluator.evaluate(claim["expression"], environment)
                if type(passed) is not bool:
                    raise FormalProofError(f"Claim {claim['id']} must evaluate to a boolean")
                claim_result = claim_results[claim["id"]]
                claim_result["checked_assignments"] += 1
                if not passed:
                    claim_result["status"] = "disproved"
                if (
                    not passed
                    and claim_result["counterexample"] is None
                    and counterexamples_recorded < 3
                ):
                    claim_result["counterexample"] = environment
                    counterexamples_recorded += 1
        if satisfying_assignments == 0:
            raise FormalProofError("Proof assumptions are unsatisfiable; vacuous proofs are rejected")
        rendered_claims = [runtime_result, *claim_results.values()]
        status = (
            "proved"
            if all(item["status"] == "proved" for item in rendered_claims)
            else "disproved"
        )
        result = {
            "backend": _PYTHON_BACKEND,
            "checker_version": 1,
            "status": status,
            "scope": scope,
            "manifest": {
                "path": clean_manifest_path,
                "sha256": hashlib.sha256(raw_manifest).hexdigest(),
            },
            "source_bindings": [
                {
                    "path": source_path,
                    "sha256": hashlib.sha256(source).hexdigest(),
                    "bytes": len(source),
                    "function": function_name,
                    "line": function.lineno,
                }
            ],
            "model": {
                "variable_count": len(variables),
                "state_space": state_space,
                "satisfying_assignments": satisfying_assignments,
                "enumeration_complete": True,
                "maximum_evaluations": nodes_per_assignment * state_space,
            },
            "claims": rendered_claims,
            "proof_scope": "the target restricted Python function for every satisfying bounded input",
            "trust": {
                "method": "restricted_ast_interpretation_and_exhaustive_enumeration",
                "project_code_executed": False,
                "proof_checker_formally_verified": False,
                "source_to_model_refinement_proven": True,
                "bounded_implementation_correctness_proven": status == "proved",
            },
        }
        result["proof_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
        return result

    @staticmethod
    def _validate_function(function: ast.FunctionDef, variables: list[str]) -> None:
        arguments = function.args
        positional = [*arguments.posonlyargs, *arguments.args]
        if (
            [item.arg for item in positional] != variables
            or arguments.vararg is not None
            or arguments.kwarg is not None
            or arguments.kwonlyargs
            or arguments.defaults
            or arguments.kw_defaults
            or any(item.annotation is not None for item in positional)
            or function.decorator_list
            or function.returns is not None
            or function.type_comment is not None
        ):
            raise FormalProofError(
                "Python proof function arguments must exactly match the declared variables"
            )
        nodes = list(ast.walk(function))
        if len(nodes) > _MAX_EXPRESSION_NODES:
            raise FormalProofError("Python proof function exceeds the AST size limit")
        if any(type(node) not in _ALLOWED_PYTHON_NODES for node in nodes):
            raise FormalProofError(
                "Python proof function uses syntax outside the supported pure subset"
            )
        if sum(isinstance(node, ast.FunctionDef) for node in nodes) != 1:
            raise FormalProofError("Nested Python proof functions are not supported")
