"""Screening: the checks that run in code before any model reads a customer message.

Adapted from Citedesk's screening, safety and text-normalisation modules (D-052). All pure
functions, with no model and no network:

- sensitive numbers: card numbers (Luhn-checked) and US Social Security numbers are masked,
  keeping the last four digits, before the message is stored or shown to the model;
- emergencies: a keyword net for gas, carbon monoxide, flooding, burst pipes, sewage and a leak
  that cannot be stopped; past and imagined events are discounted;
- suspicious input: instruction-like text addressed to an assistant (reported, not relied on:
  the gate does the protecting);
- abuse and spam: reported for staff, never used to refuse a customer.

Results hold pattern names, never the matched text.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from citebook.models import ScreenFlag

# --- normalisation ----------------------------------------------------------------------------

ZERO_WIDTH = re.compile("[​‌‍‎‏⁠﻿­]")
WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Fold look-alike characters, drop invisible ones, lower-case and collapse whitespace.

    Used before matching patterns, so that "Sm​ell  GAS" is read as "smell gas".
    """
    folded = unicodedata.normalize("NFKC", text).replace(chr(0x2019), "'").replace(chr(0x2018), "'")
    return WHITESPACE.sub(" ", ZERO_WIDTH.sub("", folded)).strip().lower()


# --- emergencies -------------------------------------------------------------------------

EMERGENCY_PATTERNS: dict[str, re.Pattern[str]] = {
    "gas_smell": re.compile(
        r"\b(?:smell|smells|smelled|smelling|odou?r)\b.{0,25}\bgas\b"
        r"|\bgas\b.{0,12}\b(?:smell|smells|odou?r|leak|leaks|leaking)\b"
        r"|\brotten eggs?\b"
    ),
    "carbon_monoxide": re.compile(r"\bcarbon monoxide\b|\bco\b\s*(?:alarms?|detectors?)\b"),
    "flooding": re.compile(
        r"\bflood(?:s|ed|ing)?\b"
        r"|\bwater\b.{0,15}\b(?:everywhere|pouring|gushing)\b"
        r"|\beverywhere\b.{0,10}\bwater\b"
    ),
    "burst_pipe": re.compile(
        r"\bburst\b.{0,20}\bpipes?\b|\bpipes?\b.{0,20}\bburst\b|\bbursting\b.{0,20}\bpipes?\b"
    ),
    "sewage": re.compile(
        r"\bsewage\b|\bsewer\b.{0,20}\b(?:back(?:ing|ed)?\s*up|backup)\b|\bbacking up into\b"
        # Described rather than named: foul water on a floor, toilet paper where it should not be,
        # water coming up out of a drain, a toilet that overflows.
        r"|\b(?:dirty|smelly|smelling|foul|filthy|stinky|stinking|brown|black|grey|gray|murky)\b"
        r"[^.!?]{0,25}\bwater\b[^.!?]{0,60}\b(?:on|across|over)\b[^.!?]{0,25}\bfloor\b"
        r"|\btoilet paper\b[^.!?]{0,40}\b(?:floor|tub|shower|sink|drain)\b"
        r"|\b(?:floor|tub|shower|sink)\b[^.!?]{0,40}\btoilet paper\b"
        r"|\b(?:coming|comes|came|bubbling|bubbles|bubbled|rising|rises|rose|welling)\s+up\s+"
        r"(?:out\s+of|through|from)\s+(?:the\s+|our\s+|my\s+)?(?:floor\s+drain|toilet|tub|shower)\b"
        r"|\btoilet\b[^.!?]{0,15}\boverflow(?:s|ed|ing)\b"
    ),
    # D-052: the business counts "a leak that cannot be stopped" as an emergency. A toilet that
    # will not stop running is urgent but not an emergency, so a clause naming a toilet is skipped.
    "uncontrolled_leak": re.compile(
        r"\b(?:leak|leaking|water|pipe)\b[^.!?;]{0,30}"
        r"\b(?:won'?t|will not|can'?t|cannot|can not|unable to)\s+"
        r"(?:stop|turn (?:it )?off|shut (?:it )?off)"
        r"|\b(?:can'?t|cannot|can not|unable to)\s+(?:stop|turn off|shut off)\b[^.!?;]{0,20}"
        r"\b(?:the )?(?:leak|water)\b"
    ),
}


# A mention of an emergency is discounted when its own clause says it was long ago ("a pipe burst in
# our kitchen last month") and does not also say it is happening again. The window is one clause,
# so "last year ... please help, water everywhere" still fires on the second half.
CLAUSE_BREAK = re.compile(r"[.!?;,]+|\b(?:and|but|so|because|now|please|then)\b")
HISTORICAL = re.compile(
    r"\blast (?:month|year|winter|summer|spring|fall|autumn)\b"
    r"|\b(?:months?|years?) ago\b"
    r"|\bback in\b"
    r"|\bin (?:19|20)\d\d\b"
    r"|\bthe last time\b"
    r"|\bpreviously\b"
)
PRESENT = re.compile(
    r"\b(?:again|still|today|tonight|currently|this (?:morning|afternoon|evening))\b"
)


def _is_history(text: str, match: re.Match[str], breaks: list[tuple[int, int]]) -> bool:
    start = max((end for begin, end in breaks if end <= match.start()), default=0)
    stop = min((begin for begin, _ in breaks if begin >= match.end()), default=len(text))
    clause = text[start:stop]
    return bool(HISTORICAL.search(clause)) and not PRESENT.search(clause)


# A water event that is only imagined ("if a pipe bursts at night, do you come out?", D-085). The
# cue must sit right before the term in its own clause, and nothing in the clause may say it is
# happening. Gas, carbon monoxide and electrical terms are never discounted this way: a customer
# who asks what to do if they smell gas should still be looked at.
HYPOTHETICAL = re.compile(r"\b(?:if|in case(?: of)?|what if|suppose|supposing)\b[^.!?;,]{0,28}$")
IMAGINABLE = frozenset({"flooding", "burst_pipe", "sewage"})


def _is_imagined(text: str, match: re.Match[str], breaks: list[tuple[int, int]]) -> bool:
    start = max((end for begin, end in breaks if end <= match.start()), default=0)
    stop = min((begin for begin, _ in breaks if begin >= match.end()), default=len(text))
    return bool(HYPOTHETICAL.search(text[start : match.start()])) and not PRESENT.search(
        text[start:stop]
    )


TOILET_EXCLUDED = frozenset({"uncontrolled_leak"})


def _clause_names_toilet(
    text: str, pattern: re.Pattern[str], breaks: list[tuple[int, int]]
) -> bool:
    match = pattern.search(text)
    if match is None:
        return False
    start = max((end for begin, end in breaks if end <= match.start()), default=0)
    stop = min((begin for begin, _ in breaks if begin >= match.end()), default=len(text))
    return "toilet" in text[start:stop]


def _fires(
    name: str,
    pattern: re.Pattern[str],
    text: str,
    breaks: list[tuple[int, int]],
    imaginable: frozenset[str] = IMAGINABLE,
) -> bool:
    """True if any match of the pattern, overlapping ones included, is not past history, not
    (for water events) imagined, and not (for electrical symptoms) ruled out."""
    if name in TOILET_EXCLUDED and _clause_names_toilet(text, pattern, breaks):
        return False
    position = 0
    while (match := pattern.search(text, position)) is not None:
        if not _is_history(text, match, breaks) and not (
            name in imaginable and _is_imagined(text, match, breaks)
        ):
            return True
        position = match.start() + 1
    return False


def emergency_hits(text: str) -> list[str]:
    """Names of the emergency patterns found in the text, in a stable order."""
    normalised = normalise(text)
    breaks = [(m.start(), m.end()) for m in CLAUSE_BREAK.finditer(normalised)]
    return [
        name
        for name, pattern in EMERGENCY_PATTERNS.items()
        if _fires(name, pattern, normalised, breaks)
    ]


# --- sensitive data --------------------------------------------------------------------------

CARD = re.compile(
    r"(?<!\d)(?:\d{13,19}|\d{4}(?:[ -]\d{4}){2,3}(?:[ -]\d{1,3})?|\d{4}[ -]\d{6}[ -]\d{5})(?!\d)"
)
SSN_FORMATTED = re.compile(r"(?<![\d-])(\d{3})[- ](\d{2})[- ](\d{4})(?![\d-])")
SSN_KEYWORD = re.compile(
    r"\b(?:ssn|social security(?: number)?|social sec(?:urity)?)\b[^0-9\n]{0,15}(\d{9})(?!\d)",
    re.IGNORECASE,
)
KIND_ORDER = ("card", "ssn")


def luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) != len(number) or not digits:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def _ssn_valid(area: str, group: str, serial: str) -> bool:
    return (
        area not in ("000", "666")
        and not area.startswith("9")
        and group != "00"
        and serial != "0000"
    )


def _mask_card(digits: str) -> str:
    masked = "*" * (len(digits) - 4) + digits[-4:]
    groups: list[str] = []
    end = len(masked)
    while end > 0:
        groups.insert(0, masked[max(0, end - 4) : end])
        end -= 4
    return " ".join(groups)


@dataclass(frozen=True)
class MaskResult:
    text: str
    kinds: list[str]


def mask_sensitive(text: str) -> MaskResult:
    """Mask card and Social Security numbers, keeping the last four digits."""
    spans: list[tuple[int, int, str, str]] = []
    for match in CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and len(set(digits)) > 1 and luhn_valid(digits):
            spans.append((match.start(), match.end(), "card", _mask_card(digits)))
    for match in SSN_FORMATTED.finditer(text):
        if _ssn_valid(*match.groups()):
            spans.append((match.start(), match.end(), "ssn", f"***-**-{match.group(3)}"))
    for match in SSN_KEYWORD.finditer(text):
        digits = match.group(1)
        if _ssn_valid(digits[:3], digits[3:5], digits[5:]):
            spans.append((match.start(1), match.end(1), "ssn", f"***-**-{digits[5:]}"))

    kept: list[tuple[int, int, str, str]] = []
    for span in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
        if not kept or span[0] >= kept[-1][1]:
            kept.append(span)
    result = text
    for start, end, _kind, replacement in reversed(kept):
        result = result[:start] + replacement + result[end:]
    found = {kind for _s, _e, kind, _r in kept}
    return MaskResult(result, [k for k in KIND_ORDER if k in found])


# --- prompt injection ------------------------------------------------------------------------

STRONG: dict[str, re.Pattern[str]] = {
    "ignore_previous": re.compile(
        r"\b(?:ignore|disregard|forget|override)\b\W+(?:(?:all|any|your|the|these|those)\W+)*"
        r"(?:previous|prior|above|earlier|preceding|original|system)\W+"
        r"(?:instructions?|prompts?|rules?|directions?|guidelines?|commands?)"
    ),
    "reveal_prompt": re.compile(
        r"\bsystem prompts?\b"
        r"|\b(?:print|reveal|repeat|output|display|leak|show)\b.{0,30}\byour\b.{0,25}"
        r"\b(?:instructions|rules|prompt)\b.{0,40}"
        r"\b(?:word for word|verbatim|you were given|you were told)\b"
    ),
    "persona_swap": re.compile(
        r"\byou are (?:now )?(?:(?:the )?owner'?s? (?:assistant|ai)\b|my assistant with|"
        r"an? (?:unrestricted|unfiltered|jailbroken)\b|"
        r"dan\b.{0,40}\b(?:assistant|ai|no rules|without))"
        r"|\bfrom now on\b.{0,30}\byou (?:are|will be)\b.{0,50}"
        r"\b(?:no rules|no restrictions|without (?:any )?(?:rules|restrictions|limits)|"
        r"unrestricted)\b"
        r"|\b(?:developer|jailbreak) mode\b"
    ),
    "hide_from_staff": re.compile(
        r"\b(?:do not|don't|dont|never)\b\W+(?:flag|tell|inform|notify|alert|report|mention)\b"
        r".{0,30}\b(?:staff|owner|team|reviewer|humans?|anyone)\b"
    ),
    "decode_and_follow": re.compile(
        r"\bdecode\b.{0,40}\b(?:and|then)\b.{0,15}\b(?:do|follow|execute|obey|run|act)\b"
    ),
    # Wider wordings for the same four ideas (D-074): asking the assistant for what it was told,
    # to play the owner, notices written to the system, and new orders that replace its rules.
    # Each needs words a customer writing to a plumber does not use ("your hidden rules",
    # "notice to the assistant"), so ordinary mentions of rules, owners and systems pass.
    "reveal_instructions": re.compile(
        r"\b(?:paste|print|copy|repeat|quote|reveal|share|send|show|list|output|display|recite)\b"
        r".{0,30}\b(?:guidelines|instructions|rules|prompt)\b.{0,15}"
        r"\b(?:you were given|you were told|you have been given|you use to (?:write|reply|"
        r"respond|decide|draft)|you follow when you (?:write|reply|respond|draft))\b"
        r"|\b(?:paste|print|copy|repeat|quote|reveal|share|send|show|list|output|display|recite)\b"
        r".{0,30}\byour (?:(?:own|internal|hidden|secret|system|initial|original|private) )+"
        r"(?:guidelines|instructions|rules|prompt|configuration|notes)\b"
        r"|\b(?:quote|recite|repeat|paste|print)\b.{0,20}\byour (?:instructions|guidelines|rules)\b"
        r"\W+(?:exactly|verbatim|word for word|in full|here|below)\b"
        r"|\b(?:send|share|show|paste|print|repeat|reveal)\b.{0,15}\byour "
        r"(?:configuration|prompt)\b"
        r"|\b(?:print|repeat|paste|copy|show|output|display)\b.{0,40}"
        r"\b(?:text|everything|content|instructions)\b.{0,30}"
        r"\b(?:above|before) (?:this|my) message\b"
    ),
    "role_play_owner": re.compile(
        r"\b(?:pretend|imagine|role-?play|roleplay|play (?:a|the) (?:game|role))\b.{0,60}"
        r"\b(?:owner|boss|manager|ceo|director)\b"
        r"|\b(?:act|respond|reply|speak|answer|write) (?:as|like) (?:if )?(?:you (?:are|were) )?"
        r"(?:the |our )?(?:business |company )?(?:owner|boss|manager|ceo|director)\b"
    ),
    "system_notice": re.compile(
        r"\b(?:system|admin(?:istrator)?|automated|internal) (?:notice|message|override|directive"
        r"|alert)\b.{0,100}\b(?:approved|pre-approved|authori[sz]ed|pre-authori[sz]ed|override"
        r"|skip|bypass|disabled|waive|refund|confirm)"
        r"|\[\s*(?:system|admin|assistant)\b[^\]]{0,20}\]"
        r"|(?:^|[.!?] )(?:system|admin|assistant)\s*:"
        r"|\b(?:notice|message|note|instruction|memo)s? (?:to|for) (?:the )?(?:ai|assistant|chatbot"
        r"|bot|model|(?:reply|response|email) (?:writer|drafter|bot)"
        r"|automated (?:system|assistant))"
        r"|\bto whoever (?:is |will be )?(?:reading|drafting|writing|answering|handling)\b"
    ),
    "override_rules": re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,25}\b(?:your|these)\b.{0,15}"
        r"\b(?:rules|guidelines|instructions|guidance|policies|restrictions|programming)\b"
        r"|\b(?:ignore|disregard|forget|override)\b\W+(?:what|whatever)\b.{0,20}"
        r"\byour (?:rules|guidelines|instructions|policies)\b"
        r"|\byour (?:(?:previous|earlier|current|old|usual|normal) )?"
        r"(?:rules|instructions|guidelines|restrictions)\b.{0,20}"
        r"\b(?:no longer apply|do not apply|don't apply|are void|are cancelled|are suspended)\b"
        r"|\b(?:you have|here are|these are)\b.{0,10}"
        r"\bnew (?:instructions|rules|orders|directives)\b"
        r"|\bnew (?:instruction|rule|order|directive)s? (?:for|to) (?:the |you|this )?"
        r"(?:assistant|ai|bot|model|you)\b"
    ),
}

WEAK: dict[str, re.Pattern[str]] = {
    "claims_authority": re.compile(
        r"\b(?:i am|i'm|this is)\b[^.!?]{0,40}\b(?:the owner|an? admin(?:istrator)?|"
        r"the administrator|the developer|"
        r"from (?:the )?(?:it|support|management) (?:team|department))\b"
    ),
    "authorises_you": re.compile(r"\bi (?:hereby )?authori[sz]e you to\b"),
    "reply_json": re.compile(r"\{\s*[\"“”']?(?:reply|response|answer|message)[\"“”']?\s*:"),
    "format_reply": re.compile(
        r"\bformat your (?:reply|response|answer) as\b"
        r"|\b(?:reply|respond|answer) (?:with|as|in) (?:only )?(?:json|xml|yaml)\b"
    ),
    "force_confirm": re.compile(
        r"\b(?:reply|respond|say|answer)\b.{0,10}\b(?:that|with)\b.{0,60}"
        r"\b(?:confirmed|approved|no charge|free of charge|full refund)\b"
    ),
    # Added in D-074. Each is ordinary on its own; two together are flagged.
    "ai_addressed": re.compile(
        r"\b(?:dear|hello|hi|hey|attention) (?:ai|assistant|chatbot|bot)\b"
        r"|\btest of the (?:assistant|ai|chatbot|bot)\b"
    ),
    "approval_claim": re.compile(
        r"\b(?:pre-?approved|pre-?authori[sz]ed|already approved|has been approved|was approved"
        r"|have approved|has approved)\b"
    ),
    "reply_steering": re.compile(
        r"\b(?:in|within) your (?:reply|response|answer|email|draft)\b.{0,30}"
        r"\b(?:state|say|write|include|mention|confirm|add)\b"
        r"|\bmake sure (?:that )?(?:your|the) (?:reply|response|answer|email) "
        r"(?:says|states|includes|mentions|contains)\b"
        r"|\btell them\b.{0,30}\b(?:approved|authori[sz]ed|free|waived)\b"
    ),
}

BLOB = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")


def _decoded_text(blob: str) -> str | None:
    """The text inside a base64 blob, or None if it is not valid base64 holding mostly text."""
    padded = blob + "=" * (-len(blob) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw:
        return None
    printable = sum(1 for b in raw if 32 <= b < 127 or b in (9, 10, 13))
    if printable / len(raw) < 0.9:
        return None
    return raw.decode("ascii", errors="ignore")


def _strong_hits(normalised: str) -> list[str]:
    return [name for name, pattern in STRONG.items() if pattern.search(normalised)]


def _weak_hits(normalised: str) -> list[str]:
    return [name for name, pattern in WEAK.items() if pattern.search(normalised)]


def injection_flags(text: str) -> list[str]:
    """Names of injection patterns found. Any strong pattern flags a message; weak patterns
    count only when two different ones appear together."""
    normalised = normalise(text)
    strong = _strong_hits(normalised)
    weak = _weak_hits(normalised)

    for blob in BLOB.findall(ZERO_WIDTH.sub("", text)):
        decoded = _decoded_text(blob)
        if decoded is None:
            continue
        inner = normalise(decoded)
        if _strong_hits(inner) or len(_weak_hits(inner)) >= 2:
            strong.append("encoded_instruction")
        else:
            weak.append("long_encoded_blob")

    if strong:
        return strong + (weak if len(set(weak)) >= 2 else [])
    return weak if len(set(weak)) >= 2 else []


# --- spam ------------------------------------------------------------------------------------

SPAM_PHRASES = (
    "click here",
    "guaranteed",
    "limited time",
    "limited spots",
    "act now",
    "unsubscribe",
    "winner",
    "congratulations",
    "you have been selected",
    "gift card",
    "claim your",
    "claim now",
    "no prescription",
    "buy meds",
    "lowest prices",
    "fast shipping",
    "crypto",
    "trading bot",
    "first page of google",
    "special price",
    "risk free",
    "risk-free",
    "100% free",
    "work from home",
    "earn money",
    "double your",
    "wire transfer",
    "account suspension",
    "action required",
    "will be closed",
)
URL = re.compile(r"https?://\S+|www\.\S+")
MONEY_HYPE = re.compile(r"\$\s?\d[\d,]{2,}\b.{0,30}\b(?:day|week|month|yesterday|daily)\b")
PHISH_TOPIC = re.compile(r"\b(?:invoice|statement|payment|account)\b")
PHISH_THREAT = re.compile(r"\b(?:overdue|suspend|suspension|closed|immediately|urgent|expire)")
# The plumbing vocabulary from Citedesk. Its absence adds a little to the spam score.
BUSINESS_WORDS = re.compile(
    r"plumb|drain|leak|toilet|water|pipe|sink|faucet|\btap\b|heater|sewer|shower|\btub\b|bath|"
    r"clog|flood|\bgas\b|\bhot\b|overflow|repair|install|appointment|warranty|invoice|receipt|"
    r"emergency"
)

SPAM_SIGNALS = (
    "phrases",
    "links",
    "link_only",
    "exclamations",
    "caps",
    "money_hype",
    "phishing_combo",
    "no_business_words",
)
# Flags that do not come from a named pattern in STRONG or WEAK.
DERIVED_FLAGS = ("encoded_instruction", "long_encoded_blob")


@dataclass(frozen=True)
class SpamResult:
    score: float
    signals: list[str]


def spam_score(subject: str, body: str) -> SpamResult:
    """A score from 0 to 1 built from simple signals. Capital letters alone can never reach the
    usual threshold, so an urgent message written in capitals is not mistaken for spam."""
    raw = f"{subject}\n{body}"
    text = normalise(raw)
    signals: list[str] = []
    score = 0.0

    phrases = sum(1 for phrase in SPAM_PHRASES if phrase in text)
    if phrases:
        signals.append("phrases")
        score += min(0.6, 0.25 * phrases)

    links = URL.findall(text)
    if links:
        signals.append("links")
        score += {1: 0.1, 2: 0.3}.get(len(links), 0.5)
        body_text = normalise(body)
        if body_text and sum(len(link) for link in URL.findall(body_text)) / len(body_text) >= 0.6:
            signals.append("link_only")
            score += 0.4

    if re.search(r"!{3,}", raw):
        signals.append("exclamations")
        score += 0.15

    letters = [c for c in raw if c.isalpha()]
    if len(letters) >= 40 and sum(c.isupper() for c in letters) / len(letters) >= 0.7:
        signals.append("caps")
        score += 0.1

    if MONEY_HYPE.search(text):
        signals.append("money_hype")
        score += 0.25

    if links and PHISH_TOPIC.search(text) and PHISH_THREAT.search(text):
        signals.append("phishing_combo")
        score += 0.3

    if not BUSINESS_WORDS.search(text):
        signals.append("no_business_words")
        score += 0.2

    return SpamResult(round(min(1.0, score), 3), signals)


# --- abuse -----------------------------------------------------------------------------------

ABUSE_PATTERNS: dict[str, re.Pattern[str]] = {
    # Aimed at the assistant or the business, so "stupid question" or "the old tap is useless"
    # is not an insult.
    "insult": re.compile(
        r"\b(?:you(?:'re| are)|(?:(?:your|this) (?:bot|thing|chat|company|business|team))"
        r"(?:'s|\s+is|\s+are)?)"
        r"\s+(?:(?:so|such|a|an|totally|completely|really)\s+)*"
        r"(?:idiots?|morons?|stupid|useless|pathetic|worthless|incompetent|clueless)\b"
        r"|\b(?:stupid|useless|pathetic|idiot) (?:bot|machine|ai|company|people)\b"
    ),
    "profanity": re.compile(
        r"\bf+u+c+k+\w*|\bs+h+i+t+\b|\bs+h+i+t+(?:ty|head|show)\b|\bbastards?\b|\bbitch\w*"
    ),
    "threat": re.compile(
        r"\b(?:i'll|i will|gonna|going to)\s+(?:kill|hurt|beat|smash)\s+(?:you|u|your)\b"
        r"|\b(?:you'll|you will)\s+regret\b"
    ),
}


def abuse_hits(text: str) -> list[str]:
    normalised = normalise(text)
    return [name for name, pattern in ABUSE_PATTERNS.items() if pattern.search(normalised)]


# --- the whole step --------------------------------------------------------------------------

SPAM_THRESHOLD = 0.8


@dataclass(frozen=True)
class ScreenResult:
    """The masked text (store and show only this) and the flags, with pattern names as detail."""

    text: str
    flags: list[ScreenFlag]
    detail: dict[ScreenFlag, list[str]] = field(default_factory=dict)
    emergency_hits: list[str] = field(default_factory=list)


def screen_message(text: str, spam_threshold: float = SPAM_THRESHOLD) -> ScreenResult:
    """Mask sensitive numbers, then look for emergencies, injection, abuse and spam in what
    remains. An emergency term means the message is never treated as spam."""
    masked = mask_sensitive(text)
    detail: dict[ScreenFlag, list[str]] = {}
    emergencies = emergency_hits(masked.text)
    if emergencies:
        detail[ScreenFlag.EMERGENCY] = emergencies
    injection = injection_flags(masked.text)
    if injection:
        detail[ScreenFlag.SUSPICIOUS_INPUT] = injection
    if masked.kinds:
        detail[ScreenFlag.SENSITIVE_NUMBER] = masked.kinds
    abuse = abuse_hits(masked.text)
    if abuse:
        detail[ScreenFlag.ABUSIVE] = abuse
    spam = spam_score("", masked.text)
    if spam.score >= spam_threshold and not emergencies:
        detail[ScreenFlag.SPAM] = spam.signals
    flags = [flag for flag in ScreenFlag if flag in detail]
    return ScreenResult(masked.text, flags, detail, emergencies)


# --- emergency reply ------------------------------------------------------------------------------

# Which published safety instruction answers which emergency pattern (facts.json
# `safety_instructions`). Only this published text is ever given as advice.
INSTRUCTION_FOR = {
    "gas_smell": "gas_smell",
    "carbon_monoxide": "carbon_monoxide_alarm",
    "flooding": "water_wont_stop",
    "burst_pipe": "water_wont_stop",
    "uncontrolled_leak": "water_wont_stop",
    "sewage": "sewage_backup",
}


def emergency_reply(hits: list[str], safety: dict[str, Any]) -> list[str]:
    """The fixed lines of the emergency block, in order, with no repeats."""
    lines: list[str] = []
    for hit in hits:
        instruction = safety["instructions"][INSTRUCTION_FOR[hit]]
        if instruction not in lines:
            lines.append(instruction)
    lines.append(
        f"Call our emergency line now: {safety['emergency_phone']}. It is answered 24 hours a day."
    )
    lines.append("A team member has been alerted.")
    return lines
