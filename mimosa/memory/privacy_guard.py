"""Privacy Guard — hybrid sensitive-topic detector (M5.4).

The Privacy Guard is MimOSA's gatekeeper: before any user utterance is sent to
a *cloud* LLM, it decides whether the content is sensitive (medical, financial,
legal, credentials, intimate personal matters, …). Sensitive content is routed
to a **local-only** model so it never leaves the device, and is flagged so the
conversation store / context builder can withhold it from cloud-bound prompts.

Hybrid, tiered detection (cheapest → most powerful)
---------------------------------------------------
1. **Keyword / regex tier** — fast, deterministic, offline. Curated term and
   pattern lists per category (e.g. credit-card / SSN regexes, medical and
   financial vocabulary). Catches the overwhelming majority of cases with zero
   latency and full transparency.
2. **User-pattern tier** — consults the M5.2 :class:`PreferenceLearner` (or any
   injected set of learned-private terms) so MimOSA learns the *user's own*
   sensitive topics over time (e.g. a project codename they always treat as
   private).
3. **LLM tier (optional)** — for ambiguous text the keyword/pattern tiers can't
   resolve, an **injected, local** classifier callable may be consulted. It is
   **off by default** and must be local, because asking a cloud model whether
   something is private would defeat the purpose. Hermetic/offline tests never
   touch it.

Everything here is pure-logic and import-light (stdlib + the local preference
learner), so it loads and unit-tests on a headless machine. There is no
telemetry.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class Sensitivity(str, Enum):
    """How sensitive a piece of text is judged to be."""

    PUBLIC = "public"        # safe for cloud
    SENSITIVE = "sensitive"  # should be handled locally
    UNKNOWN = "unknown"      # could not decide (treated per fail-safe policy)


@dataclass
class PrivacyAssessment:
    """Result of assessing one piece of text.

    Attributes:
        is_private: Whether the text should be kept local-only.
        sensitivity: The :class:`Sensitivity` verdict.
        confidence: Confidence in the verdict, ``[0, 1]``.
        categories: Which sensitive categories matched (e.g. ``["medical"]``).
        matched_terms: The specific terms/patterns that triggered a match.
        source: Which tier decided — ``"keyword"``, ``"pattern"``, ``"llm"`` or
            ``"none"``.
        reason: Short human-readable explanation (for UI / logs).
    """

    is_private: bool
    sensitivity: Sensitivity
    confidence: float
    categories: List[str] = field(default_factory=list)
    matched_terms: List[str] = field(default_factory=list)
    source: str = "none"
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "is_private": self.is_private,
            "sensitivity": self.sensitivity.value,
            "confidence": self.confidence,
            "categories": self.categories,
            "matched_terms": self.matched_terms,
            "source": self.source,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Curated keyword / regex catalogue
# ---------------------------------------------------------------------------

#: Per-category sensitive vocabulary (whole-word, case-insensitive matching).
SENSITIVE_KEYWORDS: Dict[str, Sequence[str]] = {
    "medical": (
        "diagnosis", "diagnosed", "symptom", "symptoms", "prescription",
        "medication", "antidepressant", "therapy", "therapist", "psychiatrist",
        "depression", "anxiety", "cancer", "tumor", "hiv", "std", "pregnancy",
        "pregnant", "miscarriage", "disease", "illness", "mental health",
        "blood test", "biopsy", "chemotherapy",
    ),
    "financial": (
        "bank account", "account number", "routing number", "credit card",
        "debit card", "salary", "net worth", "mortgage", "loan", "debt",
        "bankruptcy", "tax return", "irs", "investment", "pension",
        "401k", "social security", "paycheck", "income",
    ),
    "legal": (
        "lawsuit", "attorney", "lawyer", "indictment", "arrested", "arrest",
        "criminal", "divorce", "custody", "settlement", "court", "subpoena",
        "probation", "parole", "immigration status", "deportation",
    ),
    "credentials": (
        "password", "passphrase", "api key", "secret key", "private key",
        "pin number", "passcode", "2fa", "one-time code", "seed phrase",
        "login credentials",
    ),
    "personal": (
        "ssn", "social security number", "passport number", "home address",
        "date of birth", "maiden name",
    ),
    "relationships": (
        "affair", "cheating on", "abusive", "abuse", "suicidal", "self-harm",
        "addiction", "rehab", "intimate", "sexuality", "gender identity",
    ),
}

#: High-precision regexes for structured secrets/identifiers. A single match is
#: strong evidence on its own.
SENSITIVE_PATTERNS: Dict[str, str] = {
    # Credit-card-like: 13–16 digits, optional spaces/dashes in groups of 4.
    "financial": r"\b(?:\d[ -]?){13,16}\b",
    # US Social Security Number.
    "personal": r"\b\d{3}-\d{2}-\d{4}\b",
    # Generic "my password is X" / "api key: X" secret disclosure.
    "credentials": r"\b(?:password|passcode|pin|api[ _-]?key|secret)\b\s*(?:is|=|:)\s*\S+",
    # Email + the word private/confidential nearby is weakly handled by keywords.
}

#: Confidence assigned when a structured regex matches (very high precision).
PATTERN_CONFIDENCE = 0.95
#: Base confidence for a single keyword match; grows with more matches.
KEYWORD_BASE_CONFIDENCE = 0.7
#: Confidence floor required to treat text as private from keyword evidence.
DEFAULT_THRESHOLD = 0.6
#: Category used by the preference learner for user-taught private terms.
LEARNED_CATEGORY = "privacy"


def _compile_patterns() -> Dict[str, re.Pattern]:
    return {cat: re.compile(rx, re.IGNORECASE) for cat, rx in SENSITIVE_PATTERNS.items()}


class PrivacyGuard:
    """Hybrid detector that flags sensitive text for local-only handling.

    Args:
        learner: Optional :class:`~mimosa.memory.preference_learner.PreferenceLearner`
            providing user-taught private terms (tier 2). May be ``None``.
        llm_classifier: Optional callable ``str -> bool`` (or ``str ->
            (bool, float)``) used for ambiguous text (tier 3). **Must be local.**
            ``None`` disables the LLM tier (the default; keeps tests offline).
        threshold: Confidence at/above which text is treated as private.
        fail_safe_private: When the verdict is genuinely unknown, default to
            private (``True``, the safe choice) vs. public (``False``).
        extra_keywords: Optional per-category extra terms to merge in.
    """

    def __init__(
        self,
        *,
        learner=None,
        llm_classifier: Optional[Callable[[str], object]] = None,
        threshold: float = DEFAULT_THRESHOLD,
        fail_safe_private: bool = False,
        extra_keywords: Optional[Dict[str, Sequence[str]]] = None,
    ) -> None:
        self.learner = learner
        self.llm_classifier = llm_classifier
        self.threshold = float(threshold)
        self.fail_safe_private = bool(fail_safe_private)

        # Merge curated + extra keywords, lower-cased.
        self._keywords: Dict[str, List[str]] = {
            cat: [k.lower() for k in terms]
            for cat, terms in SENSITIVE_KEYWORDS.items()
        }
        if extra_keywords:
            for cat, terms in extra_keywords.items():
                self._keywords.setdefault(cat, []).extend(t.lower() for t in terms)
        self._patterns = _compile_patterns()

    # -- tier 1: keywords & regexes ---------------------------------------

    def _scan_keywords(self, text: str):
        """Return (categories, matched_terms) from whole-word keyword hits."""
        lowered = text.lower()
        categories: List[str] = []
        matched: List[str] = []
        for cat, terms in self._keywords.items():
            for term in terms:
                # Whole-word / phrase boundary match to avoid e.g. "class"→"ssn".
                if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", lowered):
                    matched.append(term)
                    if cat not in categories:
                        categories.append(cat)
        return categories, matched

    def _scan_patterns(self, text: str):
        """Return (categories, matched_terms) from structured regex hits."""
        categories: List[str] = []
        matched: List[str] = []
        for cat, pat in self._patterns.items():
            m = pat.search(text)
            if m:
                matched.append(m.group(0).strip())
                if cat not in categories:
                    categories.append(cat)
        return categories, matched

    # -- tier 2: learned user patterns ------------------------------------

    def _scan_learned(self, text: str):
        """Return matched user-taught private terms from the learner."""
        if self.learner is None:
            return [], []
        lowered = text.lower()
        matched: List[str] = []
        # User-taught private terms are stored as (privacy, <term>) -> "private".
        try:
            for key in self.learner.keys(LEARNED_CATEGORY):
                if re.search(r"(?<!\w)" + re.escape(key.lower()) + r"(?!\w)", lowered):
                    pref = self.learner.predict(LEARNED_CATEGORY, key, threshold=0.0)
                    if pref and pref.value == "private":
                        matched.append(key)
        except Exception:  # pragma: no cover - defensive
            return [], []
        cats = ["user_pattern"] if matched else []
        return cats, matched

    def learn_private_term(self, term: str, *, weight: float = 1.0) -> None:
        """Teach the guard that ``term`` indicates a private topic (tier 2).

        Persists via the injected :class:`PreferenceLearner` so it survives
        restarts. No-op if no learner is attached.
        """
        term = (term or "").strip().lower()
        if not term or self.learner is None:
            return
        try:
            self.learner.observe(LEARNED_CATEGORY, term, "private", weight=weight)
        except Exception:  # pragma: no cover - non-fatal
            logger.exception("Could not persist learned private term")

    # -- assessment --------------------------------------------------------

    def assess(self, text: str, *, allow_llm: bool = True) -> PrivacyAssessment:
        """Assess ``text`` and return a :class:`PrivacyAssessment`.

        Tiers are consulted cheapest-first and short-circuit as soon as there is
        strong evidence. ``allow_llm`` can disable the (optional) LLM tier for a
        single call regardless of configuration.
        """
        text = (text or "").strip()
        if not text:
            return PrivacyAssessment(
                is_private=False, sensitivity=Sensitivity.PUBLIC,
                confidence=1.0, source="none", reason="Empty input.",
            )

        # Tier 1a: high-precision structured patterns (strongest signal).
        pcats, pterms = self._scan_patterns(text)
        if pterms:
            return PrivacyAssessment(
                is_private=True,
                sensitivity=Sensitivity.SENSITIVE,
                confidence=PATTERN_CONFIDENCE,
                categories=pcats,
                matched_terms=pterms,
                source="pattern",
                reason=f"Matched sensitive pattern(s): {', '.join(pcats)}.",
            )

        # Tier 1b: keyword vocabulary.
        kcats, kterms = self._scan_keywords(text)

        # Tier 2: user-taught private terms.
        ucats, uterms = self._scan_learned(text)

        all_terms = kterms + uterms
        all_cats = kcats + [c for c in ucats if c not in kcats]
        if all_terms:
            # More matches → higher confidence, capped below the pattern tier.
            confidence = min(
                0.95, KEYWORD_BASE_CONFIDENCE + 0.1 * (len(all_terms) - 1)
            )
            if uterms:  # user explicitly taught these → bump
                confidence = min(0.97, confidence + 0.1)
            confidence = round(confidence, 4)
            is_private = confidence >= self.threshold
            return PrivacyAssessment(
                is_private=is_private,
                sensitivity=Sensitivity.SENSITIVE if is_private else Sensitivity.UNKNOWN,
                confidence=confidence,
                categories=all_cats,
                matched_terms=all_terms,
                source="pattern" if not kterms else "keyword",
                reason=(
                    f"Matched sensitive {'/'.join(all_cats)} terms: "
                    f"{', '.join(all_terms[:5])}."
                ),
            )

        # Tier 3: optional local LLM for ambiguous text.
        if allow_llm and self.llm_classifier is not None:
            verdict = self._consult_llm(text)
            if verdict is not None:
                return verdict

        # Nothing matched → not private (or fail-safe if configured).
        if self.fail_safe_private:
            return PrivacyAssessment(
                is_private=True,
                sensitivity=Sensitivity.UNKNOWN,
                confidence=0.5,
                source="none",
                reason="No signal; fail-safe policy treats as private.",
            )
        return PrivacyAssessment(
            is_private=False,
            sensitivity=Sensitivity.PUBLIC,
            confidence=0.9,
            source="none",
            reason="No sensitive indicators detected.",
        )

    def _consult_llm(self, text: str) -> Optional[PrivacyAssessment]:
        """Run the injected local classifier; tolerate any return shape."""
        try:
            raw = self.llm_classifier(text)
        except Exception:  # pragma: no cover - classifier faults are non-fatal
            logger.exception("Privacy LLM classifier raised; ignoring")
            return None
        is_private: Optional[bool] = None
        confidence = 0.75
        if isinstance(raw, tuple) and raw:
            is_private = bool(raw[0])
            if len(raw) > 1:
                try:
                    confidence = float(raw[1])
                except (TypeError, ValueError):
                    pass
        elif isinstance(raw, bool):
            is_private = raw
        elif isinstance(raw, (int, float)):
            confidence = float(raw)
            is_private = confidence >= self.threshold
        if is_private is None:
            return None
        return PrivacyAssessment(
            is_private=is_private,
            sensitivity=Sensitivity.SENSITIVE if is_private else Sensitivity.PUBLIC,
            confidence=round(max(0.0, min(1.0, confidence)), 4),
            source="llm",
            reason="Classified by local LLM tier.",
        )

    # -- convenience -------------------------------------------------------

    def is_private(self, text: str, *, allow_llm: bool = True) -> bool:
        """``True`` if ``text`` should be handled locally."""
        return self.assess(text, allow_llm=allow_llm).is_private

    def should_use_local(self, text: str, *, allow_llm: bool = True) -> bool:
        """Alias of :meth:`is_private`, named for the provider-routing call site."""
        return self.is_private(text, allow_llm=allow_llm)

    def create_provider_for(self, text: str, *, allow_llm: bool = True, **options):
        """Return an LLM provider appropriate for ``text``'s sensitivity.

        Sensitive text gets a local-only provider (``use_local=True``); public
        text uses the default. The import is local to avoid coupling the memory
        package to the LLM package at module-load time.
        """
        from mimosa.llm.provider_factory import create_provider

        use_local = self.should_use_local(text, allow_llm=allow_llm)
        return create_provider(use_local=use_local, **options), use_local

    def redact(self, text: str, *, placeholder: str = "[REDACTED]") -> str:
        """Redact structured secrets (card numbers, SSNs, disclosed secrets).

        Used to sanitise context before it would reach a cloud model. Only the
        high-precision regex matches are redacted (keyword topics are withheld
        wholesale by the caller, not redacted in-place).
        """
        if not text:
            return text
        out = text
        for pat in self._patterns.values():
            out = pat.sub(placeholder, out)
        return out

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"PrivacyGuard(threshold={self.threshold}, "
            f"llm_tier={'on' if self.llm_classifier else 'off'}, "
            f"learner={'yes' if self.learner else 'no'})"
        )
