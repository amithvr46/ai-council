"""Writing-preference profile — data, not prose baked into a prompt.

The contract requires this to be extensible: more preferences will be added
over time, and adding one must not mean rewriting a prompt. So each rule is a
record with an optional mechanical check. Rules with a check become regression
tests automatically; rules without one are rendered into prompt guidance.

AI-tell detection is deliberately advisory rather than blocking — flagging
"leveraged" is useful signal, but a hard failure on a word list would produce
worse writing than it prevents.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class StyleRule:
    id: str
    instruction: str  # rendered into prompts
    check: Callable[[str], list[str]] | None = None  # returns violations
    blocking: bool = False  # blocking rules force a correction pass


def _comma_before_and(text: str) -> list[str]:
    """Permanent rule: never a comma immediately before 'and'.

    Quoted or fenced source text is exempt — preserving a source verbatim
    outranks house style.
    """
    scrubbed = re.sub(r"```.*?```", " ", text, flags=re.S)
    scrubbed = re.sub(r"\"[^\"]*\"|'[^']*'", " ", scrubbed)
    return [m.group(0) for m in re.finditer(r"\w+,\s+and\b", scrubbed, re.I)]


_AI_TELLS = re.compile(
    r"\b(?:leverag(?:e|ed|ing)|spearhead(?:ed|ing)?|cutting[- ]edge|state[- ]of[- ]the[- ]art|"
    r"operational excellence|seamless(?:ly)?|robust and scalable|best[- ]in[- ]class|"
    r"transformative|synerg\w+|holistic|paradigm|game[- ]chang\w+|world[- ]class|"
    r"drive (?:innovation|excellence|success)|deliver(?:ing)? value|utili[sz]e[ds]?)\b",
    re.I,
)


def _ai_tells(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _AI_TELLS.finditer(text)})


def _repetitive_openers(text: str) -> list[str]:
    """Every bullet starting with the same word reads as generated."""
    openers = [
        line.strip().lstrip("-*• ").split(" ")[0].lower()
        for line in text.splitlines()
        if line.strip().startswith(("-", "*", "•"))
    ]
    if len(openers) < 4:
        return []
    counts: dict[str, int] = {}
    for opener in openers:
        counts[opener] = counts.get(opener, 0) + 1
    return [
        f"{word} opens {n} of {len(openers)} bullets"
        for word, n in counts.items()
        if n >= max(3, len(openers) // 2)
    ]


DEFAULT_RULES: list[StyleRule] = [
    StyleRule(
        id="no_comma_before_and",
        instruction=(
            "Never place a comma immediately before the word 'and' — write "
            "'Terraform, Ansible and Helm', not 'Terraform, Ansible, and Helm'. "
            "The only exception is preserving quoted or source text verbatim."
        ),
        check=_comma_before_and,
        blocking=True,
    ),
    StyleRule(
        id="no_ai_tells",
        instruction=(
            "Write like an experienced engineer, not an assistant. Avoid "
            "'leveraged', 'spearheaded', 'cutting-edge', 'operational "
            "excellence', 'seamless', 'robust and scalable' and similar filler. "
            "Prefer concrete engineering activity and real implementation "
            "detail over impressive-sounding abstraction."
        ),
        check=_ai_tells,
    ),
    StyleRule(
        id="vary_sentence_structure",
        instruction=(
            "Vary bullet structure and length. Bullets that all open with the "
            "same verb and run the same length read as generated."
        ),
        check=_repetitive_openers,
    ),
    StyleRule(
        id="no_invented_metrics",
        instruction=(
            "Never invent percentages, counts, dollar figures, team sizes, "
            "dates or performance improvements. Describe what the work was, "
            "not a manufactured measure of it."
        ),
    ),
    StyleRule(
        id="concrete_over_generic",
        instruction=(
            "Prefer 'Investigated deployment failures by reviewing rollout "
            "status, pod events, configuration changes and application "
            "telemetry' over 'Leveraged Kubernetes to drive operational "
            "excellence'. Every bullet needs a reason to exist."
        ),
    ),
]


def prompt_guidance(rules: list[StyleRule] | None = None) -> str:
    """Render the profile into prompt text."""
    rules = rules or DEFAULT_RULES
    return "\n".join(f"- {rule.instruction}" for rule in rules)


def check(text: str, rules: list[StyleRule] | None = None) -> dict[str, list[str]]:
    """Run every mechanical check. Returns {rule_id: violations}."""
    rules = rules or DEFAULT_RULES
    findings: dict[str, list[str]] = {}
    for rule in rules:
        if rule.check is None:
            continue
        violations = rule.check(text)
        if violations:
            findings[rule.id] = violations
    return findings


def blocking_violations(text: str, rules: list[StyleRule] | None = None) -> dict[str, list[str]]:
    rules = rules or DEFAULT_RULES
    blocking = {r.id for r in rules if r.blocking}
    return {k: v for k, v in check(text, rules).items() if k in blocking}
