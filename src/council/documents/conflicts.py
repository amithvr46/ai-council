"""Material factual conflicts between authoritative career sources (A3).

Two of the user's own documents can disagree. A master resume says a role ran
Nov 2022 – Oct 2024; an older supporting document says Sep 2022 – Oct 2024. One
of them is wrong, and the system has no way to know which.

"Latest document wins" is not the policy. That rule silently puts a wrong date
on a submitted resume, and a wrong date is the kind of thing an interviewer
checks. So a genuine conflict is persisted, the disputed fact is withheld from
generation until the user resolves it, and no certainty is manufactured.

Scope is deliberately narrow. This must NEVER fire on ordinary Tier 2 wording
differences — two resumes describing the same Terraform work in different words
are not in conflict, they are two drafts. Only material hard facts qualify:

    employer dates, role dates, education, certifications, exact achievements

Everything else is wording, and wording is the system's job to write (A4).
"""

import re
from dataclasses import dataclass, field

from council.documents.profile import CAREER_AUTHORITIES

# Only these authorities can be in conflict with one another. A tailored resume
# is a selective view (A1) — its omissions and re-emphasis are expected, not a
# disagreement. It participates only through dates it explicitly states.
AUTHORITATIVE = ("profile", "master_resume", "supporting")

_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)

# "Senior Cloud Operations Engineer | Wells Fargo | Charlotte, NC | Nov 2024 - Present"
_ROLE_LINE = re.compile(
    rf"(?P<title>[A-Z][\w /&.-]{{3,60}}?)\s*[|–—-]\s*"
    rf"(?P<employer>[A-Z][\w &.,'-]{{2,60}}?)\s*[|–—-].{{0,60}}?"
    rf"(?P<start>(?:{_MONTHS})\.?\s+(?:19|20)\d{{2}})\s*[–—-]+\s*"
    rf"(?P<end>(?:{_MONTHS})\.?\s+(?:19|20)\d{{2}}|present|current)",
    re.I,
)

_DEGREE_LINE = re.compile(
    r"(?P<degree>(?:bachelor|master|doctor|ph\.?d|b\.?s|m\.?s|b\.?tech|m\.?tech|mba)"
    r"[\w .,/-]{0,60}?)\s*[|–—,-]\s*"
    r"(?P<institution>[A-Z][\w &.,'-]{4,80}?)\s*[|–—,].{0,40}?"
    r"(?P<year>(?:19|20)\d{2})",
    re.I,
)

_CERT_LINE = re.compile(
    r"\b(?P<cert>(?:az|aws|gcp|ckad?|cka|cks|sc|ms|dp|ai)-?\d{3}|"
    r"(?:aws|azure|google)\s+certified[\w ]{0,40}|"
    r"certified kubernetes[\w ]{0,30})\b",
    re.I,
)

CONFLICT_KINDS = ("role_dates", "education", "certification")

# A denied technology that a career document also asserts. Kept out of
# CONFLICT_KINDS because it is not extracted from document text by
# `find_conflicts` — it comes from the assembled ConfirmedExperience — but it
# is persisted and surfaced through exactly the same mechanism, which is the
# point: the user should not have to learn a second place to look for
# "my sources disagree about my career".
CONFLICT_EXPERIENCE_DENIED = "experience_denied"


def _norm_month_year(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip().lower().replace(".", ""))
    return {"current": "present"}.get(value, value)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass
class Conflict:
    kind: str
    subject: str
    values: list[dict] = field(default_factory=list)  # [{"source": ..., "value": ...}]

    def as_dict(self) -> dict:
        return {"kind": self.kind, "subject": self.subject, "values": self.values}

    @property
    def distinct_values(self) -> list[str]:
        return sorted({v["value"] for v in self.values})


def extract_facts(text: str) -> dict[str, dict[str, str]]:
    """Material hard facts a single document asserts, keyed by subject."""
    facts: dict[str, dict[str, str]] = {"role_dates": {}, "education": {}, "certification": {}}

    for m in _ROLE_LINE.finditer(text):
        subject = f"{_key(m.group('title'))} @ {_key(m.group('employer'))}"
        facts["role_dates"][subject] = (
            f"{_norm_month_year(m.group('start'))} - {_norm_month_year(m.group('end'))}"
        )

    for m in _DEGREE_LINE.finditer(text):
        subject = f"{_key(m.group('degree'))} @ {_key(m.group('institution'))}"
        facts["education"][subject] = m.group("year")

    for m in _CERT_LINE.finditer(text):
        facts["certification"][_key(m.group("cert"))] = "held"

    return facts


def find_conflicts(documents: list[dict]) -> list[Conflict]:
    """Material disagreements between authoritative career sources.

    documents: [{"authority": ..., "title": ..., "text": ...}]

    Wording differences are invisible here by construction — only the parsed
    hard facts above are compared, so two differently-worded descriptions of
    the same role never register.
    """
    # subject -> {value -> [source labels]}
    seen: dict[str, dict[str, dict[str, list[str]]]] = {k: {} for k in CONFLICT_KINDS}

    for document in documents:
        authority = document.get("authority")
        if authority not in AUTHORITATIVE or authority not in CAREER_AUTHORITIES:
            continue
        label = f"{authority}:{document.get('title') or 'document'}"
        for kind, facts in extract_facts(document.get("text") or "").items():
            for subject, value in facts.items():
                bucket = seen[kind].setdefault(subject, {})
                bucket.setdefault(value, [])
                if label not in bucket[value]:
                    bucket[value].append(label)

    conflicts: list[Conflict] = []
    for kind, subjects in seen.items():
        for subject, values in subjects.items():
            if len(values) < 2:
                continue  # agreement, or only one source said anything
            conflicts.append(
                Conflict(
                    kind=kind,
                    subject=subject,
                    values=[
                        {"source": source, "value": value}
                        for value, sources in sorted(values.items())
                        for source in sources
                    ],
                )
            )
    return conflicts


def denial_conflicts(confirmed) -> list[Conflict]:
    """Where a career source claims a technology the user says they never used.

    This is NOT symmetrical with the date conflicts above. There, two sources
    of equal standing disagree and the fact is withheld from both. Here the
    outcome is already decided — the user outranks a document about their own
    career, so the term stays unconfirmed — and the conflict exists to make
    that visible and auditable rather than to ask a question.

    Recording it matters because the alternative is silent disagreement: a
    master resume that lists Harness, a user who says they never used it, and
    nothing anywhere pointing out that one of the two needs correcting.
    """
    conflicts: list[Conflict] = []
    for term in confirmed.contradicted():
        denied = confirmed.denied[term]
        values = [
            {"source": source, "value": "used"}
            for source in sorted(confirmed.sources.get(term, []))
        ]
        values.append({"source": "user_statement:denied by you", "value": denied.kind})
        conflicts.append(
            Conflict(kind=CONFLICT_EXPERIENCE_DENIED, subject=term, values=values)
        )
    return conflicts


def disputed_subjects(conflicts: list[Conflict]) -> set[str]:
    """Facts generation must not assert. Withholding is the safe direction:
    a resume missing one date is recoverable, a resume with the wrong date
    read out in an interview is not."""
    return {c.subject for c in conflicts}
