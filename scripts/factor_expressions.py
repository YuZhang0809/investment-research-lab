from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass
from typing import Any, Callable

from research_common import parse_bool, parse_float


NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class FactorDefinition:
    name: str
    expr: str
    group: str = ""
    include_in_score: bool = True
    description: str = ""


def configured_factor_definitions(config: dict[str, Any] | None) -> list[FactorDefinition]:
    values = ((config or {}).get("factors", {}) or {}).get("definitions", []) or []
    if not isinstance(values, list):
        raise ValueError("factors.definitions must be a list.")

    definitions: list[FactorDefinition] = []
    seen: set[str] = set()
    for index, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"factors.definitions[{index}] must be a mapping.")
        name = str(item.get("name", "")).strip()
        expr = str(item.get("expr", "")).strip()
        if not name:
            raise ValueError(f"factors.definitions[{index}].name is required.")
        if not NAME_RE.match(name):
            raise ValueError(f"Invalid factor definition name: {name!r}.")
        if name in seen:
            raise ValueError(f"Duplicate factor definition name: {name}")
        if not expr:
            raise ValueError(f"factors.definitions[{index}].expr is required.")
        seen.add(name)
        definitions.append(
            FactorDefinition(
                name=name,
                expr=expr,
                group=str(item.get("group", "") or "").strip(),
                include_in_score=parse_bool(item.get("include_in_score"), default=True) is not False,
                description=str(item.get("description", "") or "").strip(),
            )
        )
    return definitions


def factor_definition_names(config: dict[str, Any] | None, *, include_all: bool = True) -> list[str]:
    return [
        definition.name
        for definition in configured_factor_definitions(config)
        if include_all or definition.include_in_score
    ]


def factor_definition_names_for_group(config: dict[str, Any] | None, group: str) -> list[str]:
    return [
        definition.name
        for definition in configured_factor_definitions(config)
        if definition.include_in_score and definition.group == group
    ]


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    left = to_number(numerator)
    right = to_number(denominator)
    if left is None or right is None or right == 0:
        return None
    return left / right


def to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return parse_float(value)


def average_available(*values: Any) -> float | None:
    clean = [value for value in (to_number(item) for item in values) if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def clamp(value: Any, lower: Any, upper: Any) -> float | None:
    number = to_number(value)
    lower_number = to_number(lower)
    upper_number = to_number(upper)
    if number is None or lower_number is None or upper_number is None:
        return None
    return min(max(number, lower_number), upper_number)


def choose(condition: Any, if_true: Any, if_false: Any) -> Any:
    if condition is None:
        return None
    return if_true if bool(condition) else if_false


def logical_value(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


class FactorExpressionEvaluator(ast.NodeVisitor):
    """Small whitelist evaluator inspired by Qlib-style expression fields."""

    def __init__(
        self,
        variables: dict[str, Any],
        functions: dict[str, Callable[..., Any]] | None = None,
    ) -> None:
        self.variables = variables
        self.functions = {
            "abs": lambda value: None if to_number(value) is None else abs(to_number(value) or 0.0),
            "avg": average_available,
            "clamp": clamp,
            "log": self._log,
            "max": self._max,
            "min": self._min,
            "ratio": safe_ratio,
            "sqrt": self._sqrt,
            "where": choose,
            **(functions or {}),
        }

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, (int, float, bool, str)) or node.value is None:
            return node.value
        raise ValueError("Unsupported constant in factor expression.")

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id not in self.variables:
            raise ValueError(f"Unknown factor expression variable: {node.id}")
        return self.variables[node.id]

    def visit_UnaryOp(self, node: ast.UnaryOp) -> float | None:
        value = to_number(self.visit(node.operand))
        if value is None:
            return None
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return -value
        raise ValueError("Unsupported unary operator in factor expression.")

    def visit_BinOp(self, node: ast.BinOp) -> float | None:
        left = to_number(self.visit(node.left))
        right = to_number(self.visit(node.right))
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return None if right == 0 else left / right
        if isinstance(node.op, ast.Pow):
            if left == 0 and right < 0:
                return None
            try:
                value = left**right
            except (OverflowError, ZeroDivisionError, ValueError):
                return None
            if isinstance(value, complex) or not math.isfinite(value):
                return None
            return value
        raise ValueError("Unsupported binary operator in factor expression.")

    def visit_BoolOp(self, node: ast.BoolOp) -> bool | None:
        if isinstance(node.op, ast.And):
            saw_missing = False
            for value_node in node.values:
                value = logical_value(self.visit(value_node))
                if value is False:
                    return False
                if value is None:
                    saw_missing = True
            return None if saw_missing else True
        if isinstance(node.op, ast.Or):
            saw_missing = False
            for value_node in node.values:
                value = logical_value(self.visit(value_node))
                if value is True:
                    return True
                if value is None:
                    saw_missing = True
            return None if saw_missing else False
        raise ValueError("Unsupported boolean operator in factor expression.")

    def visit_Compare(self, node: ast.Compare) -> bool | None:
        left = self.visit(node.left)
        for operator, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            result = self._compare(left, operator, right)
            if result is None:
                return None
            if not result:
                return False
            left = right
        return True

    def visit_Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise ValueError("Unsupported function call in factor expression.")
        name = node.func.id
        if name not in self.functions:
            raise ValueError(f"Unsupported factor expression function: {name}")
        args = [self.visit(arg) for arg in node.args]
        kwargs = {keyword.arg: self.visit(keyword.value) for keyword in node.keywords if keyword.arg}
        if len(kwargs) != len(node.keywords):
            raise ValueError("Unsupported keyword in factor expression.")
        try:
            return self.functions[name](*args, **kwargs)
        except TypeError as exc:
            raise ValueError(f"Invalid arguments for factor expression function {name}: {exc}") from exc

    def generic_visit(self, node: ast.AST) -> Any:
        raise ValueError(f"Unsupported expression element: {type(node).__name__}")

    def _compare(self, left: Any, operator: ast.cmpop, right: Any) -> bool | None:
        left_number = to_number(left)
        right_number = to_number(right)
        if left_number is None or right_number is None:
            return None
        if isinstance(operator, ast.Lt):
            return left_number < right_number
        if isinstance(operator, ast.LtE):
            return left_number <= right_number
        if isinstance(operator, ast.Gt):
            return left_number > right_number
        if isinstance(operator, ast.GtE):
            return left_number >= right_number
        if isinstance(operator, ast.Eq):
            return left_number == right_number
        if isinstance(operator, ast.NotEq):
            return left_number != right_number
        raise ValueError("Unsupported comparison operator in factor expression.")

    def _log(self, value: Any) -> float | None:
        number = to_number(value)
        if number is None or number <= 0:
            return None
        return math.log(number)

    def _sqrt(self, value: Any) -> float | None:
        number = to_number(value)
        if number is None or number < 0:
            return None
        return math.sqrt(number)

    def _min(self, *values: Any) -> float | None:
        clean = [value for value in (to_number(item) for item in values) if value is not None]
        return min(clean) if clean else None

    def _max(self, *values: Any) -> float | None:
        clean = [value for value in (to_number(item) for item in values) if value is not None]
        return max(clean) if clean else None


def evaluate_factor_expression(
    expression: str,
    variables: dict[str, Any],
    functions: dict[str, Callable[..., Any]] | None = None,
) -> Any:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid factor expression: {expression}") from exc
    evaluator = FactorExpressionEvaluator(variables=variables, functions=functions)
    return evaluator.visit(tree)
