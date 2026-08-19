"""Bounded council -> deep escalation — Phase 3B.

The idea Auto rests on: **do not pay to predict uncertainty you can observe
cheaply.** Council runs, the models actually disagree, and only then — with the
disagreement in hand rather than guessed at — does Auto consider spending more.

Every condition below is deterministic. Deciding to escalate costs zero model
calls; only the additional deep work costs anything.

Escalation is stronger verification of the SAME request. It is never new scope.
"""

from dataclasses import dataclass, field

from council.engine.budget import MIN_DEEP_STAGES_AFTER_CHECK, MODE_BUDGETS

# Disagreement kinds evidence can actually settle. A reasoning dispute —
# "count or for_each?" — is engineering judgement; no search result decides it,
# so spending deep budget on one buys nothing.
SETTLEABLE = ("factual", "both")


@dataclass(frozen=True)
class EscalationDecision:
    escalate: bool
    reason: str
    trigger: str | None = None
    refusal: str | None = None  # machine-readable when escalate is False
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "result": "escalated" if self.escalate else "refused",
            "source_mode": "council",
            "target_mode": "deep",
            "trigger": self.trigger,
            "reason": self.reason,
            "refusal_reason": self.refusal,
            **self.detail,
        }


def evaluate(
    *,
    routed_by_auto: bool,
    check,
    budget,
    evidence_available: bool,
    spend_decision=None,
) -> EscalationDecision:
    """All conditions, in cheapest-first order.

    `spend_decision` is the forward-affordability result for the INCREMENTAL
    deep work, computed by the caller only when every free condition has
    already passed — there is no point querying spend for a request that was
    never going to escalate.
    """
    base = {
        "escalation_number": budget.escalations + 1,
        "logical_calls_already_spent": budget.spent,
        "api_attempts_already_spent": budget.api_attempts,
        "cost_already_spent_usd": round(budget.cost_usd, 6),
        "remaining_deep_allowance": MODE_BUDGETS["deep"] - budget.spent,
    }

    def no(reason: str, refusal: str) -> EscalationDecision:
        return EscalationDecision(False, reason, None, refusal, base)

    # --- free conditions ---------------------------------------------
    if not routed_by_auto:
        # An explicitly chosen mode is the user's decision, not Auto's to
        # revise. Auto never secretly overrides a choice.
        return no("mode was chosen explicitly; Auto does not override it", "not_auto_routed")

    if budget.escalations >= 1:
        return no("this request has already escalated once", "already_escalated")

    disagreement = getattr(check, "disagreement_type", "none")
    if disagreement not in SETTLEABLE:
        return no(
            f"disagreement is '{disagreement}', which evidence cannot settle",
            "not_a_factual_disagreement",
        )

    claims = list(getattr(check, "checkable_claims", []) or [])
    if not claims:
        return no(
            "no checkable factual claims, so there is nothing for evidence to check",
            "no_checkable_claims",
        )

    if not evidence_available:
        # Refusing here is the honest answer. Escalating into a deep run whose
        # tools are all unavailable would spend money to produce
        # INSUFFICIENT_BY_EVIDENCE on every claim.
        return no(
            "no evidence tool is available, so escalating could not settle anything",
            "evidence_unavailable",
        )

    # --- per-request ceiling -----------------------------------------
    remaining = MODE_BUDGETS["deep"] - budget.spent
    if remaining < MIN_DEEP_STAGES_AFTER_CHECK:
        return no(
            f"only {remaining} calls remain inside deep's ceiling; completing deep "
            f"needs {MIN_DEEP_STAGES_AFTER_CHECK}",
            "insufficient_request_allowance",
        )

    # --- dollars -------------------------------------------------------
    if spend_decision is not None and not spend_decision.allowed:
        return EscalationDecision(
            False,
            "the remaining spend budget cannot complete the additional deep work",
            None,
            "escalation_refused_budget",
            {**base, "affordability": spend_decision.as_dict()},
        )

    detail = {**base, "checkable_claims_present": len(claims)}
    if spend_decision is not None:
        detail["affordability"] = spend_decision.as_dict()

    return EscalationDecision(
        True,
        f"{disagreement} disagreement over {len(claims)} checkable claim(s); "
        "evidence can settle it and the budget allows",
        trigger=disagreement,
        detail=detail,
    )


def free_conditions_pass(*, routed_by_auto, check, budget, evidence_available) -> bool:
    """Whether it is worth spending a database query on affordability.

    Keeps the spend lookup off the path of every non-escalating request.
    """
    return evaluate(
        routed_by_auto=routed_by_auto,
        check=check,
        budget=budget,
        evidence_available=evidence_available,
    ).escalate
