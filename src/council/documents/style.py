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


# ---------------------------------------------------------------- sections
#
# Repetition and framing are properties of a SECTION, not of a bullet. A
# function handed one bullet at a time cannot see that four of them open with
# the same word, however carefully it is written — which is exactly what went
# wrong: the repetition rule below lived in the per-bullet rule list, was fed
# one bullet per call by the workflow, and could therefore never fire. It
# passed its unit test because the test handed it a markdown block, a shape the
# workflow never produces.
#
# So section rules are their own kind, taking the section's bullets as a list.
# The type makes the scope mistake unrepresentable rather than merely unlikely.


@dataclass(frozen=True)
class SectionRule:
    id: str
    instruction: str  # rendered into prompts alongside the per-bullet rules
    check: Callable[[list[str]], list[str]]


MIN_SECTION_BULLETS = 4  # below this, repetition is coincidence


def _first_word(bullet: str) -> str:
    return bullet.strip().lstrip("-*• ").split(" ")[0].strip(",.:;").lower()


def _repetitive_openers(bullets: list[str]) -> list[str]:
    """Every bullet starting with the same word reads as generated."""
    if len(bullets) < MIN_SECTION_BULLETS:
        return []
    counts: dict[str, int] = {}
    for bullet in bullets:
        word = _first_word(bullet)
        counts[word] = counts.get(word, 0) + 1
    return [
        f"{word} opens {n} of {len(bullets)} bullets"
        for word, n in sorted(counts.items())
        if n >= max(3, len(bullets) // 2)
    ]


# Frames that name a technology the engineer was NEAR without saying what they
# did with it. Each is a perfectly honest verb — the praised bullet "Supported a
# subset of internal services on GCP, provisioning compute and storage
# resources, configuring IAM roles..." opens with one and is exactly right,
# because it continues into the work. So this is deliberately a PROPORTION over
# a section and never a judgement on any single bullet: one "Supported" is
# accurate, a section that is mostly them is an inventory of things touched.
_INVENTORY_FRAME = re.compile(
    r"^\s*(?:"
    r"support(?:s|ed|ing)?"
    r"|work(?:s|ed|ing)?\s+(?:with|on|across)"
    r"|us(?:e|es|ed|ing)"
    r"|assist(?:s|ed|ing)?"
    r"|help(?:s|ed|ing)?"
    r"|participat\w+\s+in"
    r"|involved\s+in"
    r"|responsible\s+for"
    r"|familiar\s+with"
    r"|exposure\s+to"
    r")\b",
    re.I,
)


def _inventory_framing(bullets: list[str]) -> list[str]:
    """A section that mostly reports proximity to technologies rather than work.

    The failure this catches is invisible bullet by bullet: every statement is
    credible and true, and the section still reads as a responsibility
    inventory rather than as an engineer describing what they did.
    """
    if len(bullets) < MIN_SECTION_BULLETS:
        return []
    framed = [b for b in bullets if _INVENTORY_FRAME.match(b)]
    if len(framed) < max(3, len(bullets) // 2):
        return []
    return [
        f"{len(framed)} of {len(bullets)} bullets report proximity to a "
        f"technology rather than work done with it"
    ]


SECTION_RULES: list[SectionRule] = [
    SectionRule(
        id="vary_sentence_structure",
        instruction=(
            "Vary bullet structure and length within a section. Bullets that "
            "all open with the same verb and run the same length read as "
            "generated."
        ),
        check=_repetitive_openers,
    ),
    SectionRule(
        id="describe_the_work",
        instruction=(
            "A section is not an inventory of technologies the engineer was "
            "near. 'Supported AKS workloads' names a thing; 'Supported AKS "
            "workloads through deployment and configuration changes, "
            "investigating pod and networking failures during releases' "
            "describes work. Where the confirmed career context supports "
            "saying what was actually done, say it — and where it does not, "
            "leave the bullet short rather than inflating it."
        ),
        check=_inventory_framing,
    ),
]


def check_section(
    bullets: list[str], rules: list[SectionRule] | None = None
) -> dict[str, list[str]]:
    """Run every section-scoped check over one section's bullets."""
    rules = rules or SECTION_RULES
    findings: dict[str, list[str]] = {}
    for rule in rules:
        violations = rule.check(bullets)
        if violations:
            findings[rule.id] = violations
    return findings


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


def prompt_guidance(
    rules: list[StyleRule] | None = None,
    section_rules: list[SectionRule] | None = None,
) -> str:
    """Render the profile into prompt text — both scopes, one list.

    A writer reading this needs the section rules as much as the bullet rules;
    the split exists so the CHECKS cannot be run at the wrong scope, not to
    hide half the preferences from the model.
    """
    rules = rules or DEFAULT_RULES
    section_rules = SECTION_RULES if section_rules is None else section_rules
    return "\n".join(
        f"- {rule.instruction}" for rule in [*rules, *section_rules]
    )


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


def enforce_comma_rule(text: str) -> str:
    """Delete a comma placed immediately before 'and'.

    Applied mechanically rather than asked of the correction model. A live run
    showed the model leaving one in place even with the violation quoted back
    to it — and this rule has exactly one correct answer, so a regex is both
    more reliable and free. Quoted and fenced text is preserved, since keeping
    a source verbatim outranks house style.
    """
    fences: list[str] = []

    def _stash(match: re.Match) -> str:
        fences.append(match.group(0))
        return f"\x00{len(fences) - 1}\x00"

    stashed = re.sub(r"```.*?```|\"[^\"]*\"|'[^']*'", _stash, text, flags=re.S)
    fixed = re.sub(r"(\w),(\s+and\b)", r"\1\2", stashed, flags=re.I)
    for index, original in enumerate(fences):
        fixed = fixed.replace(f"\x00{index}\x00", original)
    return fixed


def blocking_violations(text: str, rules: list[StyleRule] | None = None) -> dict[str, list[str]]:
    rules = rules or DEFAULT_RULES
    blocking = {r.id for r in rules if r.blocking}
    return {k: v for k, v in check(text, rules).items() if k in blocking}
