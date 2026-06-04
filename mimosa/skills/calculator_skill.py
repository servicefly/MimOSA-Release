"""Calculator skill -- safe arithmetic, handled locally.

Like :mod:`mimosa.skills.time_skill`, this skill is **fully local** and never
calls the LLM or the network. Arithmetic is something local code does perfectly,
instantly, and for free -- so there's no reason to spend an LLM round-trip on it.

Security
--------
We do **not** use Python's built-in :func:`eval`, which would allow arbitrary
code execution (a serious injection risk on transcribed user input). Instead we
parse the expression into an Abstract Syntax Tree with :mod:`ast` and evaluate
only an explicit allow-list of numeric operations. Anything else (function
calls, names, attribute access, etc.) is rejected.

Handled questions include:
    * "What is 25 times 17?"
    * "Calculate 100 divided by 4"
    * "What's 2 to the power of 10?"
"""

from __future__ import annotations

import ast
import operator
import re
from typing import List, Optional

from mimosa.skills.base_skill import BaseSkill, SkillResult


class CalculatorError(ValueError):
    """Raised when an expression is invalid or uses disallowed operations."""


# Allow-listed binary and unary operators -> their safe implementations.
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Map spoken/word operators to symbols so natural phrasing works.
_WORD_REPLACEMENTS = [
    (r"\bplus\b", "+"),
    (r"\bminus\b", "-"),
    (r"\btimes\b", "*"),
    (r"\bmultiplied by\b", "*"),
    (r"\bdivided by\b", "/"),
    (r"\bover\b", "/"),
    (r"\bto the power of\b", "**"),
    (r"\bpower of\b", "**"),
    (r"\bsquared\b", "**2"),
    (r"\bcubed\b", "**3"),
    (r"\bmod(ulo)?\b", "%"),
    (r"\bx\b", "*"),  # "3 x 4"
    (r"×", "*"),
    (r"÷", "/"),
    (r"\^", "**"),
]


def safe_eval(expression: str) -> float:
    """Safely evaluate an arithmetic ``expression`` and return the result.

    Only numeric literals and the operators in :data:`_BIN_OPS` /
    :data:`_UNARY_OPS` are permitted. Anything else raises
    :class:`CalculatorError`.

    Args:
        expression: A math expression like ``"25 * 17"``.

    Returns:
        The numeric result as a float (or int-valued float).

    Raises:
        CalculatorError: If the expression is empty, malformed, or contains a
            disallowed construct.
    """
    if not expression or not expression.strip():
        raise CalculatorError("empty expression")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"could not parse expression: {exc}") from exc

    return _eval_node(tree.body)


def _eval_node(node: ast.AST) -> float:
    """Recursively evaluate an allow-listed AST node."""
    if isinstance(node, ast.Constant):  # numbers only
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("only numeric constants are allowed")
        return node.value

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BIN_OPS:
            raise CalculatorError(f"operator {op_type.__name__} is not allowed")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        try:
            return _BIN_OPS[op_type](left, right)
        except ZeroDivisionError as exc:
            raise CalculatorError("division by zero") from exc

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise CalculatorError(f"unary operator {op_type.__name__} is not allowed")
        return _UNARY_OPS[op_type](_eval_node(node.operand))

    # Anything else (Call, Name, Attribute, Subscript, ...) is rejected.
    raise CalculatorError(f"disallowed expression element: {type(node).__name__}")


def extract_expression(text: str) -> str:
    """Turn a natural-language math question into an evaluable expression.

    Replaces spoken operators ("times", "divided by") with symbols and strips
    everything except digits, operators, parentheses, and decimal points.
    """
    lowered = (text or "").lower()
    for pattern, repl in _WORD_REPLACEMENTS:
        lowered = re.sub(pattern, repl, lowered)

    # Keep only characters that are valid in a numeric expression.
    cleaned = re.sub(r"[^0-9+\-*/%.()\s]", " ", lowered)
    # Collapse whitespace.
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _format_number(value: float) -> str:
    """Render a result without a trailing ``.0`` for whole numbers."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    # Round long floats for speakability.
    if isinstance(value, float):
        return f"{round(value, 6):g}"
    return str(value)


class CalculatorSkill(BaseSkill):
    """Evaluate arithmetic safely and locally (no eval, no LLM, no network)."""

    name = "calculator"
    intents = ["calculator", "math"]
    uses_llm = False

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        expression = extract_expression(text)
        if not expression:
            return SkillResult(
                text="I couldn't find a calculation in that. Try something like "
                "'what is 25 times 17?'.",
                success=False,
                skill=self.name,
            )

        try:
            result = safe_eval(expression)
        except CalculatorError as exc:
            self.logger.info("Calculator rejected %r (%s)", expression, exc)
            return SkillResult(
                text="Sorry, I couldn't compute that. Please rephrase the "
                "calculation.",
                success=False,
                skill=self.name,
                metadata={"expression": expression, "error": str(exc)},
            )

        pretty = _format_number(result)
        return SkillResult(
            text=f"The answer is {pretty}.",
            skill=self.name,
            metadata={"expression": expression, "result": result},
        )
