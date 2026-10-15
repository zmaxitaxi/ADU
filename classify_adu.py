#!/usr/bin/env python3
"""Description-only, deterministic ADU permit classifier. Python 3.9+, stdlib.

No text is executed and no external model or service is called. Original fields
are copied as strings. See README.md for the classification policy and limits.
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

VERSION = "1.2.0"
LABEL_FIELDS = ["ADU project", "ADU type", "ADU action"]
ACTION_CATEGORIES = ("New construction", "Conversion", "Addition", "Legalization",
    "Repair/remodel", "Demolition/removal", "Reconstruction", "Utility work",
    "Site preparation", "Administrative", "Unclear")

def rx(pattern):
    return re.compile(pattern, re.I)

ACRONYM = rx(r"(?<![a-z])(?:j(?:\s*\.\s*|\s+)a(?:\s*\.\s*|\s+)d(?:\s*\.\s*|\s+)u\.?|a(?:\s*\.\s*|\s+)d(?:\s*\.\s*|\s+)u\.?|jr\.?\s*adu|jadu|dadu|aadu|aduu|adu)(?:s|'s)?(?![a-z])")
FORMAL = rx(r"\b(?:(?:junior|jr\.?)\s+)?access(?:ory|ary|or[yi])\s+(?:(?:dwelling|dweilling)(?:\s+units?)?|living\s+units?)\b")
ALIASES = rx(r"\b(?:(?:mother[ -])?in[ -]law\s+(?:units?|quarters|apartments?|suites?)|granny\s+(?:flats?|units?)|secondary\s+(?:dwelling(?:\s+units?)?|units?)|second\s+dwelling(?:\s+units?)?)\b")
AMBIGUOUS = rx(r"\b(?:guest[ -]?(?:house|quarters)|casita|(?:second|2nd)\s+(?:units?|dwelling)|garage\s+conversion|accessory\s+(?:structure|building)|duplex)\b")
BOILERPLATE = rx(r"including\s+adus?\s+38\s*kw\s+or\s+less")
PASSING_REFERENCE = rx(r"\b(?:j?adu|accessory dwelling unit)\s+(?:is\s+)?under\s+(?:a\s+)?separate\s+(?:plan\s*check\s+and\s+)?permit\b|\bref(?:erence)?\s*:\s*[a-z0-9_-]+\s*\[[^\]]*\b(?:j?adu|accessory dwelling unit)\b[^\]]*\]")
SCOPE_REMOVAL = rx(r"(?:not\s+doing\s+(?:an?\s+)?(?:j?adu|accessory dwelling unit)|rather\s+than\s+(?:an?\s+)?j?adu|scope.{0,65}(?:updated|changed|changes).{0,50}(?:remove|exclude|eliminate)\s+(?:the\s+)?j?adu|(?:remov(?:ing|al\s+of)|exclud(?:ing|e))\s+all\s+adu[ -]related\s+elements)")
MARKER = "zzadu"
QUAL = r"(?:de[ -]?attached|detached|detach|det|attached|attach|att)"
BLOCK = r"garage|carport|patio|porch|deck|shed|storage|pool|office|laundry|structure|arts|room|plans|photos|documents|residence|house|sfr|sdu|dwelling|zzadu|detached|attached|detach|attach"
FILLER = rf"(?:(?!\b(?:{BLOCK})\b)[a-z0-9\s/(),.'#&+\-]){{0,105}}?"
DIRECT_BEFORE = rx(rf"\b(?P<qual>{QUAL})\b(?!\s+to\b)\.?{FILLER}(?P<unit>zzadu)")
DIRECT_AFTER = rx(rf"(?P<unit>zzadu)\s*(?:is\s+|will\s+be\s+|to\s+be\s+|[-:,]\s*)?(?P<qual>detached|attached|detach|attach)\b(?!\s+(?:to|{BLOCK})\b)")
GARAGE_CONVERSION = rx(r"\b(?P<qual>detached|attached|detach|attach)\b\)?\s+(?:(?:existing|two[ -]car|one[ -]car|single[ -]car|\d+[ -]car)\s+)?garage\b(?P<gap>(?:(?!zzadu).){0,150}?)(?P<unit>zzadu)")
PRIMARY_LINK = rx(r"(?P<unit>zzadu)(?:(?!\b(?:zzadu|utilities|utility|meter|service|water|sewer|line|pipes|panel|gas)\b).){0,125}?\b(?:attached|attach|physically\s+connected|integrated)\s+(?:to|with|into)\s+(?:(?:the|an?|existing|e|main|primary|single[ -]family)\s+|\(e\)\s*)*(?:house|home|residence|dwelling|sfr|sdu)\b")
DETACHED_GARAGE_LINK = rx(r"(?P<unit>zzadu)(?:(?!zzadu).){0,100}?\b(?:attached|attach|above|over|on\s+top\s+of)\s+(?:to\s+)?(?:(?:the|an?|existing|e|new|rear|of)\s+|\(e\)\s*)*detached\s+garage\b")
DIRECT_TO_DETACHED_GARAGE = rx(r"(?P<unit>zzadu)\s+to\s+(?:(?:the|existing|e|new)\s+|[\[(]e[\])]\s*)*detached\s+garage\b")
ATTACHED_GARAGE_ABOVE = rx(r"(?P<unit>zzadu)(?:(?!zzadu).){0,70}?\b(?:above|over|on\s+top\s+of)\s+(?:(?:the|an?|existing|e|new)\s+|\(e\)\s*)*attached\s+garage\b")
INTERIOR_CONVERSION = rx(r"\b(?:convert(?:ing|ed)?|conversion|converting)\b(?:(?!zzadu).){0,100}?\b(?:portion\s+of\s+(?:the\s+)?(?:existing\s+)?(?:house|home|residence|dwelling)|(?:existing\s+)?(?:single[ -]family|primary|main)\s+(?:house|home|residence|dwelling)|basement\s+(?:of|in)\s+(?:the\s+)?(?:house|home|residence))\b(?P<gap>(?:(?!zzadu).){0,100}?)(?P<unit>zzadu)")
PRIMARY_LEVEL = rx(r"\b(?:basement|second[ -]floor|2nd[ -]floor|first[ -]floor|1st[ -]floor)\b(?:(?!zzadu).){0,65}?\b(?:of|within|inside|in)\s+(?:(?:the|existing|main|primary|single[ -]family)\s+)*(?:house|home|residence|dwelling)\b(?:(?!zzadu).){0,70}?(?P<unit>zzadu)")
SEPARATE_PRIMARY = rx(r"(?P<unit>zzadu)(?:(?!zzadu).){0,60}?\b(?:detached|separate|free[ -]standing)\s+from\s+(?:(?:the|existing|main|primary|single[ -]family)\s+)*(?:house|home|residence|dwelling)\b")

@dataclass(frozen=True)
class Classification:
    project: str
    unit_type: str
    reason: str
    evidence: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()

def normalize(description):
    text = unicodedata.normalize("NFKC", description or "").lower()
    text = text.translate(str.maketrans({"’": "'", "–": "-", "—": "-"}))
    text = re.sub(r"(\d\s*(?:sf|sqft))(?=j?adu\b)",r"\1 ",text)
    return re.sub(r"\s+", " ", text).strip()

def is_negated(text, start, end):
    before = text[max(0, start - 85):start]
    after = text[end:end + 55]
    # "New not detached ADU" negates a physical relationship, not the unit.
    if rx(r"\b(?:new|existing|proposed)\s+not\s+(?:attached|detached)\s+$").search(before):
        return False
    return bool(rx(r"(?:\b(?:no|not|without)\s+(?:(?:a|an|new|proposed|additional|existing|attached|detached|junior)\s+)*|\bno\s+additional\s+square\s+footage\s+or\s+|\bnot\s+dwelling\s+units?\s+or\s+|\bexclude[sd]?\s+(?:the\s+)?|\bnon[- ]?)$").search(before)
                or rx(r"^\s+(?:is\s+|are\s+)?(?:not\s+(?:included|part\s+of\s+(?:the\s+)?scope)|excluded\s+from\s+(?:the\s+)?scope)").search(after))

def classify_description(description: str | None) -> Classification:
    text = normalize(description)
    if not text:
        return Classification("No", "", "No description evidence", flags=("missing_description",))
    # Mask ONLY the observed generic eligibility phrase, retaining specific ADUs
    # elsewhere in the same description.
    flags = []
    passing = tuple(m.group() for m in PASSING_REFERENCE.finditer(text))
    if passing:
        flags.append("passing_adu_reference")
        text = PASSING_REFERENCE.sub("reference to separate work", text)
    if BOILERPLATE.search(text):
        flags.append("generic_adu_boilerplate")
        text = BOILERPLATE.sub("generic eligible dwelling", text)
    removal = SCOPE_REMOVAL.search(text)
    if removal:
        return Classification("No", "", "ADU explicitly removed from current scope", (removal.group(),), ("scope_removal",))
    matches = sorted([*( (m, "explicit") for m in ACRONYM.finditer(text)),
                      *( (m, "explicit") for m in FORMAL.finditer(text)),
                      *( (m, "alias") for m in ALIASES.finditer(text))], key=lambda x:x[0].start())
    active = [(m,kind) for m,kind in matches if not is_negated(text,m.start(),m.end())]
    if active and all(rx(r"shared\s+plans\s+with.{0,100}$").search(text[max(0,m.start()-125):m.start()]) and text[m.end():m.end()+1].isdigit() for m,_ in active):
        return Classification("No", "", "ADU acronym appears only in shared-plan permit identifier",tuple(m.group() for m,_ in active),("identifier_only_reference",))
    if not active:
        if matches:
            flags.append("negated_adu")
        if AMBIGUOUS.search(text):
            flags.append("ambiguous_dwelling_language")
        return Classification("No", "", "No affirmative ADU evidence", tuple(m.group() for m,_ in matches) + passing, tuple(flags))
    if any(kind == "alias" for _,kind in active):
        flags.append("alias_requires_review")
    if matches != active:
        flags.append("negated_and_affirmative_mentions")
    if rx(r"separate\s+(?:plan\s+check\s+and\s+)?permit|\bref\s*:|\bshared\s+plans\b").search(text):
        flags.append("scope_reference_requires_review")
    if any(text[m.end():m.end()+1].isdigit() for m,_ in active):
        flags.append("compact_or_identifier_reference")
    # Replace only affirmative mentions, allowing relation rules to use one token.
    marked = text
    for m,_ in reversed(active):
        marked = marked[:m.start()] + MARKER + marked[m.end():]
    marked = re.sub(r"zzadu\s*\(zzadu\)",MARKER,marked)
    direct = collections.defaultdict(list)
    contextual = collections.defaultdict(list)
    for pattern in (DIRECT_BEFORE,DIRECT_AFTER):
        for m in pattern.finditer(marked):
            if rx(r"\bnot\s+$").search(marked[max(0,m.start("qual")-5):m.start("qual")]):
                continue
            qual=m.group("qual")
            value="Detached" if qual.startswith("de") else "Attached"
            direct[m.start("unit")].append((value,m.group()))
    for pattern,value in ((PRIMARY_LINK,"Attached"),(DETACHED_GARAGE_LINK,"Detached"),(ATTACHED_GARAGE_ABOVE,"Attached"),
                          (INTERIOR_CONVERSION,"Attached"),(PRIMARY_LEVEL,"Attached"),
                          (SEPARATE_PRIMARY,"Detached")):
        for m in pattern.finditer(marked):
            if rx(r"\bnot\s+(?:attached|detached|attach|detach)\b").search(m.group()):
                continue
            if pattern is INTERIOR_CONVERSION and not rx(r"\b(?:to|into|as|create)\s+(?:(?!\b(?:and|enlarge|remodel|house|kitchen|bathroom)\b)[a-z0-9\s().]){0,45}$").search(m.group("gap")):
                continue
            contextual[m.start("unit")].append((value,m.group()))
    for m in GARAGE_CONVERSION.finditer(marked):
        # Orientation is relevant only if garage is converted into the unit.
        preceding=marked[max(0,m.start()-65):m.start()]
        if rx(r"\b(?:convert|converting|conversion|converted)\b").search(preceding+m.group("gap")) or rx(r"\b(?:into|to)\s+(?:a\s+|an\s+|new\s+)?$").search(m.group("gap")):
            contextual[m.start("unit")].append(("Detached" if m.group("qual").startswith("det") else "Attached",m.group()))
    evidence=[]
    values=set()
    for pos in sorted(set(direct)|set(contextual)):
        clues=direct[pos] or contextual[pos]
        # A stated separation from the primary dwelling controls over attachment
        # to another ADU or an outbuilding.
        separate=[m for m in SEPARATE_PRIMARY.finditer(marked) if m.start("unit")==pos]
        if separate:clues=[("Detached",m.group()) for m in separate]
        detached_garage=[m for m in DETACHED_GARAGE_LINK.finditer(marked) if m.start("unit")==pos]
        detached_garage += [m for m in DIRECT_TO_DETACHED_GARAGE.finditer(marked) if m.start("unit")==pos and any(value=="Attached" for value,_ in direct[pos])]
        if detached_garage:clues=[("Detached",m.group()) for m in detached_garage]
        for value,phrase in clues:
            values.add(value)
            evidence.append(phrase.replace(MARKER,"ADU"))
    if len(values)==1:
        unit_type=next(iter(values));reason="Affirmative ADU evidence and physical relationship"
    else:
        unit_type="Unclear"
        reason="Affirmative ADU evidence; " + ("mixed or conflicting attachment clues" if values else "attachment not established")
        flags.append("mixed_or_conflicting_types" if values else "type_not_stated")
    # DADU is explicit shorthand for detached ADU; other junior acronyms alone
    # do not establish a physical relationship under this description-only policy.
    if not values and any(m.group().startswith("dadu") for m,_ in active):
        unit_type="Detached";reason="Detached ADU abbreviation";flags.remove("type_not_stated")
    # Distinct units with known and unstated type cannot share one confident
    # permit-level label. Repeated mentions alone do not imply multiple units.
    positions={m.start() for m in re.finditer(MARKER,marked)}
    separate_units=rx(r"zzadu(?:(?!zzadu).){0,100}\b(?:and|plus)\b\s+(?:addition|new|attached|detached)(?:(?!zzadu).){0,100}zzadu").search(marked)
    known_positions=set(direct)|set(contextual)
    if values and separate_units and positions-known_positions:
        unit_type="Unclear";reason="Multiple units; at least one unit has no attachment evidence"
        flags.append("multiple_units_incomplete_types")
    return Classification("Yes",unit_type,reason,tuple(dict.fromkeys([m.group() for m,_ in active]+evidence)),tuple(dict.fromkeys(flags)))


@dataclass(frozen=True)
class ActionClassification:
    label: str
    reason: str
    evidence: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()


def action_components(label):
    """Validate a canonical, ordered set. Unclear cannot accompany known actions."""
    if not label:
        return ()
    parts = tuple(label.split("; "))
    expected = tuple(category for category in ACTION_CATEGORIES if category in parts)
    if parts != expected or ("Unclear" in parts and len(parts) != 1):
        raise ValueError("Invalid ADU action label")
    return parts


# Action rules refer to affirmative unit mentions, not arbitrary nearby verbs.
ACTION_OBJECT_BLOCK = r"main\s+(?:house|home|residence)|primary\s+(?:house|home|residence)|single[ -]family|\bsfr\b|\bsdu\b|main\s+dwelling|patio|pool|barn|structures|pathway|porch|deck"
ACTION_GAP = rf"(?:(?!zzadu|[;|]|\.(?!\d)|\b(?:{ACTION_OBJECT_BLOCK})\b).)"
CONVERSION_GAP = r"(?:(?!zzadu|[;|]|\.(?!\d)).)"
UNIT_GAP = rf"(?:(?!zzadu|[;|]|\.(?!\d)|\b(?:{ACTION_OBJECT_BLOCK}|garage|shed|storage|barn|mh|playroom|pergola|pool|patio|deck|bedroom|bathroom|kitchen|roof|roofing|shingles?|window|door|water\s+heater|panels?|sub[ -]?panel|electrical|plumbing|mechanical|meters?|system|fans?|fire|sprinkler|sprincler|pv|pvs|solar|photovoltaic|sewer|power|ess|hp|hvac|mini[ -]?split|heat\s+pump|staircase|stairs|ramp|sb9|above|over|access)\b).)"
ACTION_PATTERNS = {
    "New construction": (
        rf"\b(?:construct(?:ing|ed)?|construction\s+(?:of|for)|build|building\s+(?:a|an|new)|erect(?:ing|ion)?)\b{UNIT_GAP}{{0,110}}?(?P<unit>zzadu)",
        r"\binstall(?:ing|ation\s+of)?\s+(?:(?:an?|new|detached|attached|prefabricated|prefab|modular|sf|sqft|unit\s+feature|story)\s+|[\d,.()\s-]){0,100}(?P<unit>zzadu)",
        rf"\bnew\s+(?:residential\s+)?construction\b{UNIT_GAP}{{0,100}}?(?P<unit>zzadu)",
        r"(?P<unit>zzadu)\s*(?:[-:]\s*)?(?:new\s+construction|to\s+be\s+(?:built|constructed)|construction)\b",
        rf"\b(?:factory[ -]built|prefabricated|modular)\b{UNIT_GAP}{{0,80}}?(?P<unit>zzadu)",
    ),
    "Conversion": (
        rf"\bconvert(?:ing|ed)?\b{CONVERSION_GAP}{{0,170}}?\b(?:to|into|as)\s+(?:(?!\band\b|zzadu|[.;|]).){{0,75}}?(?P<unit>zzadu)",
        rf"\bconversion\b{ACTION_GAP}{{0,140}}?(?P<unit>zzadu)",
        r"(?P<unit>zzadu)\s*(?:[-:]\s*)?(?:(?:garage|living\s+area)\s+)?conversion\b",
        rf"(?P<unit>zzadu){ACTION_GAP}{{0,85}}?\bby\s+convert(?:ing)?\b",
        rf"\b(?:create|creating)\b{UNIT_GAP}{{0,80}}?(?P<unit>zzadu){ACTION_GAP}{{0,90}}?\bconversion\b",
        rf"\b(?:remodel|convert(?:ing)?)\b{ACTION_GAP}{{0,110}}?\bto\s+create\s+(?:(?!zzadu|[.;|]).){{0,65}}?(?P<unit>zzadu)",
        r"(?P<unit>zzadu)\s+(?:construction\s+)?(?:in|within|inside)\s+(?:(?:the|an?|existing|conditioned|habitable)\s+|\(e\)\s*)*(?:garage|living\s+space|habitable\s+space|space)\b",
    ),
    "Addition": (
        rf"\b(?:addition|expansion|enlargement)\s+(?:to|for)\b{UNIT_GAP}{{0,90}}?(?P<unit>zzadu)",
        rf"\b(?:addition|expansion|enlargement)\b{UNIT_GAP}{{0,70}}?\b(?:to|for)\b{UNIT_GAP}{{0,70}}?(?P<unit>zzadu)",
        rf"(?P<unit>zzadu)\s+(?:(?:with|w)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:\d[\d,.]*\s*(?:sf|sq\.?\s*ft|sqft)\s*)?)?(?:addition|expansion)\b",
        rf"\b(?:add|expand|enlarge)\b{UNIT_GAP}{{0,80}}?(?P<unit>zzadu)",
        rf"\b(?:\(n\)\s*)?\d[\d,.]*\s*(?:sf|sq\.?\s*ft|sqft)\s+addition\b{ACTION_GAP}{{0,110}}?(?P<unit>zzadu)",
        rf"\bconstruct\s+(?:\(\s*)?\d[\d,.]*\s*(?:sf|sqft)\s*\)?\s+to\s+(?P<unit>zzadu)",
    ),
    "Legalization": (
        rf"\blegali[sz](?:e|ing|ation)\b{ACTION_GAP}{{0,120}}?(?P<unit>zzadu)",
        r"(?P<unit>zzadu)\s*(?:[-:]\s*)?(?:legalization|to\s+be\s+legalized)\b",
        rf"\b(?:retroactive\s+permit|permit\s+(?:the\s+)?existing\s+unpermitted)\b{ACTION_GAP}{{0,110}}?(?P<unit>zzadu)",
    ),
    "Repair/remodel": (
        rf"\b(?:repair(?:s|ing)?|remodel(?:ing)?|renovat(?:e|ion|ing)|alteration|reroof|re[ -]roof|replace|replacement\s+of|change[ -]?out|retrofit(?:s|ting)?)\b{ACTION_GAP}{{0,110}}?(?P<unit>zzadu)",
        rf"(?P<unit>zzadu)\s*(?:[-:]\s*)?(?:repair|remodel|renovation|alteration|reroof|re[ -]roof)\b",
    ),
    "Demolition/removal": (
        rf"\b(?:demolish|demolition\s+of|demo|remove|removal\s+of)\b{UNIT_GAP}{{0,75}}?(?P<unit>zzadu)",
        r"(?P<unit>zzadu)\s*(?:[-:]\s*)?(?:to\s+be\s+)?(?:demolished|removed|demolition|removal)\b(?!\s+of\b)",
        rf"\b(?:convert|restore|revert)\b{ACTION_GAP}{{0,70}}?(?P<unit>zzadu)\s+(?:unit\s+)?(?:to|into|back\s+to)\s+(?:an?\s+|the\s+|existing\s+)*(?:garage|storage|ag\s+employee\s+dwelling|aed)\b",
        r"\bdemolish\s+(?:an?\s+)?existing\s+attached-to-a-detached-garage\s+(?:\d+\s+story\s+)?non-conforming\s+(?P<unit>zzadu)",
    ),
    "Reconstruction": (
        rf"\b(?:rebuild|reconstruct(?:ion|ing)?|replacement\s+(?:of|for))\b{UNIT_GAP}{{0,100}}?(?P<unit>zzadu)",
        rf"\b(?:eaton|palisades|fire|disaster)\b{ACTION_GAP}{{0,40}}?\brebuild\b{ACTION_GAP}{{0,90}}?(?P<unit>zzadu)",
        r"(?P<unit>zzadu)\s*(?:[-:]\s*)?(?:rebuild|reconstruction)\b",
    ),
    "Utility work": (
        rf"\b(?:electrical|electric|plumbing|mechanical|sewer|solar|photovoltaic|pvs?|hvac|mini[ -]split|main\s+panel|sub[ -]?panel|mpu|sprinkler|sprincler|meter|water\s+heater|heat\s+pump)\b{ACTION_GAP}{{0,85}}?(?P<unit>zzadu)",
        rf"(?P<unit>zzadu){ACTION_GAP}{{0,65}}?\b(?:electrical|plumbing|mechanical|sewer|solar|hvac|mini[ -]split|meter|sprinkler|water\s+heater|main\s+panel|sub[ -]?panel)\b",
    ),
    "Site preparation": (
        rf"\b(?:grading|drainage|earthwork|excavation|trenching|road\s+closure|crane|site\s+clearance)\b{ACTION_GAP}{{0,160}}?(?P<unit>zzadu)",
        rf"\b(?:demolish|demo|demolition\s+of)\b{ACTION_GAP}{{0,70}}?\b(?:garage|shed|storage|paving|structure)\b{ACTION_GAP}{{0,110}}?\bfor\s+(?:a\s+|the\s+|new\s+|proposed\s+)*(?P<unit>zzadu)",
        rf"(?P<unit>zzadu){ACTION_GAP}{{0,35}}?\bdemolition\s+of\s+(?:an?\s+|the\s+|existing\s+)*(?:structure|garage|shed|paving)\b",
    ),
}
ACTION_PATTERNS = {name: tuple(rx(pattern) for pattern in patterns) for name, patterns in ACTION_PATTERNS.items()}
ACTION_ADMIN_PREFIX = rx(r"^[\W\d]*(?:this\s+)?(?:(?:for\s+)?certificate\s+of\s+occupancy|completion\s+permit|(?:request\s+(?:new\s+)?|establish\s+)?address(?:\s+assignment)?|application\s+for\s+delay\s+of\s+enforcement|plan\s*check|pre[ -]alt(?:er(?:ation)?)?|pre[ -]inspection|renew(?:al|\s+expired)?|reissu\w*|(?:minor\s+|major\s+)?revision|revised|rev\s*\d|supplemental\s+(?:to\s+)?permit|deferred\s+submittal|addendum|permit\s+extension)\b")
ACTION_TRADE_PREFIX = rx(r"^[\W\d]*(?:(?:new\s+construction\s+)?(?:solar|photovoltaic)|amp\s+(?:upgrade|service)|electrical|electric|el|ele|elec|plumbing|pl|plum|mechanical|me|mec|mech|mep|se|sewer|pv|hvac|fire\s+sprinkler|mpu)\b")
ACTION_SITE_PREFIX = rx(r"^[\W\d]*(?:(?:minor\s+|major\s+)?grading|drainage|earthwork|excavation|traffic\s+control|road\s+closure|crane|site\s+clearance)\b")
ACTION_DEMO_PREFIX = rx(r"^[\W\d]*(?:demolition\s+permit|demolish|demo)\b")


def classify_action(description, project=None):
    """Current permit purpose only. Several explicit actions retain separate tags."""
    if project is None:
        project = classify_description(description).project
    if project == "No":
        return ActionClassification("", "Not applicable to a No project")
    text = BOILERPLATE.sub("generic eligibility", PASSING_REFERENCE.sub("separate permit reference", normalize(description)))
    text = rx(r"\b(?:sq\.?\s*ft\.?|s\.f\.|sf\.)").sub("sf", text)
    text = rx(r"\b\d+\s*[- ]?\s*(?:bedrooms?|beds?|bd|bathrooms?|baths?|ba)\b").sub("unit feature", text)
    text = rx(r"^\*?note:[^*]{0,60}\*\s*").sub("", text)
    matches = sorted([m for pattern in (ACRONYM, FORMAL, ALIASES) for m in pattern.finditer(text)], key=lambda m:m.start())
    active = [m for m in matches if not is_negated(text, m.start(), m.end())]
    if not active:
        return ActionClassification("Unclear", "No usable current ADU action evidence", flags=("action_scope_requires_review",))
    for match in reversed(active):
        text = text[:match.start()] + MARKER + text[match.end():]
    text = re.sub(r"zzadu\s*\(zzadu\)", MARKER, text)
    # Quoted statutory language and dated/historical statements are not current work.
    text = rx(r"(?:pursuant\s+to|under)\s*:?[\s]*(?:state\s+)?(?:law|government\s+code|gov(?:t)?\.?\s+code).{0,1000}").sub("statutory reference", text)
    if rx(r"\blegalize\s*[/]\s*revert\b").search(text):
        return ActionClassification("Unclear", "Alternative retention/removal outcomes are unresolved", flags=("action_alternative_outcomes",))
    purpose_text = rx(r"^.{0,250}?description\s+of\s+work:\s*").sub("", text)
    admin, trade, site = ACTION_ADMIN_PREFIX.search(purpose_text), ACTION_TRADE_PREFIX.search(purpose_text), ACTION_SITE_PREFIX.search(purpose_text)
    for match, category, flag in ((admin, "Administrative", "action_administrative_wrapper"),
                                 (trade, "Utility work", "action_trade_wrapper"),
                                 (site, "Site preparation", "action_site_wrapper")):
        if match:
            return ActionClassification(category, "Current permit purpose controls over related building scope",
                (match.group().replace(MARKER, "ADU"),), (flag,))
    hookup = rx(r"\b(?:connect|reconnect|reroute)\b[^.;]{0,70}\b(?:sewer|water\s+supply)\b").search(text)
    if hookup and hookup.start() < 120 and not rx(r"\b(?:construct|build|convert|demolish|legalize)\b").search(text[:hookup.start()]):
        return ActionClassification("Utility work", "Current scope changes utility hookups",
            (hookup.group(),), ("action_trade_wrapper",))
    ancillary = rx(r"^relocate\s+garage\s+door\b").search(text)
    if ancillary and rx(r"\ballow\s+for\s+installation\b").search(text):
        return ActionClassification("Site preparation", "Access work enables a separately described ADU installation",
            (ancillary.group(),), ("action_ancillary_access_work",))
    hits = {name: [] for name in ACTION_PATTERNS}
    for name, patterns in ACTION_PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(text):
                prefix = text[max(0, match.start()-55):match.start()]
                if rx(r"\b(?:no|not|without)\s+(?:(?:need|plans?|proposal|proposed|intend|to|a|any|new|scope|for)\s+)*$").search(prefix):
                    continue
                if rx(r"^\s+(?:(?:is|are|will\s+be)\s+)?not\s+(?:proposed|included|authorized|part\s+of|in\s+scope)").search(text[match.end():match.end()+65]):
                    continue
                if rx(r"\b(?:previously|formerly|already|originally|was|were)\s+$").search(prefix):
                    continue
                if rx(r"\b(?:in|under\s+permit)\s+(?:19|20)\d{2}\b").search(match.group()):
                    continue
                # Do not bridge an independent action to another object.
                before_unit = match.group()[:match.start("unit")-match.start()]
                if name == "Demolition/removal" and (":" in before_unit or rx(r"\b(?:garage|shed|barn|roof|patio|deck)\s+$").search(prefix)):
                    continue
                if name == "New construction" and rx(r"\ballow\s+(?:for\s+)?$").search(prefix):
                    continue
                if name in {"Conversion", "Addition", "Legalization", "Repair/remodel", "Demolition/removal", "Reconstruction"} and rx(r"(?:\band|&|\bplus)\s+(?:construct|build(?:ing)?|legalize|convert|repair|remodel|demolish|install)\b").search(before_unit):
                    continue
                hits[name].append(match)
    flags = []
    partial = {m.start("unit") for m in hits["Demolition/removal"] if rx(r"\bremove\s+\d[\d,.]*\s*(?:sf|sqft)\s+of\b").search(m.group())}
    if partial:
        hits["Repair/remodel"].extend(m for m in hits["Demolition/removal"] if m.start("unit") in partial)
        hits["Demolition/removal"] = [m for m in hits["Demolition/removal"] if m.start("unit") not in partial]
        flags.append("action_partial_removal")
    replacement = rx(r"zzadu(?:(?!zzadu|[.;|]).){0,100}?\bto\s+(?:build(?:ing)?|construct)\b(?:(?!zzadu|[.;|]).){0,90}?(?P<unit>zzadu)").search(text)
    if hits["Demolition/removal"] and replacement:
        # A linked replacement is reconstruction. Independent units keep their own actions.
        rebuilt = [m for m in hits["New construction"] if m.start("unit") == replacement.start("unit")]
        hits["Reconstruction"].extend(rebuilt)
        hits["New construction"] = [m for m in hits["New construction"] if m not in rebuilt]
    if hits["Site preparation"] and ACTION_DEMO_PREFIX.search(text) and not hits["Demolition/removal"]:
        flags.append("action_non_adu_demolition")
    if ACTION_DEMO_PREFIX.search(text) and not any(hits.values()):
        return ActionClassification("Unclear", "Demolition target or its ADU relationship is not established",
            flags=("action_demolition_target_unclear",))
    # 'New' alone does not distinguish a newly built unit from a conversion.
    # Suppress building verbs tied to the same converted/legalized/rebuilt unit.
    for name in ("Conversion", "Addition", "Legalization", "Reconstruction"):
        positions = {m.start("unit") for m in hits[name]}
        hits["New construction"] = [m for m in hits["New construction"] if m.start("unit") not in positions]
    legalized = {m.start("unit") for m in hits["Legalization"]}
    hits["Conversion"] = [m for m in hits["Conversion"] if m.start("unit") not in legalized]
    # Integral utilities/remodeling during creation do not identify another purpose.
    creation = {m.start("unit") for name in ("New construction", "Conversion", "Reconstruction") for m in hits[name]}
    hits["Repair/remodel"] = [m for m in hits["Repair/remodel"] if m.start("unit") not in creation]
    structural = creation | {m.start("unit") for name in ("Addition", "Legalization", "Repair/remodel", "Demolition/removal") for m in hits[name]}
    hits["Utility work"] = [m for m in hits["Utility work"] if m.start("unit") not in structural]
    if rx(r"^[\W\d]*new\b").search(text) and not rx(r"\b(?:install|provide|upgrade|replace|connect|work|separated|billing)\b").search(text):
        # Equipment listed in a bare-new-unit description does not establish a trade permit.
        hits["Utility work"] = []
    labels = tuple(name for name in ACTION_CATEGORIES if hits.get(name))
    if not labels:
        flags = ["action_not_stated"]
        if rx(r"\bnew\b.{0,65}zzadu").search(text):
            flags.append("action_new_unit_pathway_unclear")
        return ActionClassification("Unclear", "ADU involvement is explicit but the current action is not established", flags=tuple(flags))
    evidence = tuple(dict.fromkeys(m.group().replace(MARKER, "ADU") for name in labels for m in hits[name]))
    flags = tuple(flags) + (("action_multiple_purposes",) if len(labels) > 1 else ())
    scoped_positions = {m.start("unit") for name in labels for m in hits[name]}
    if {m.start() for m in re.finditer(MARKER, text)} - scoped_positions:
        flags += ("action_unscoped_mentions",)
    if "Demolition/removal" in labels:
        flags += ("action_removal_requires_imagery_review",)
    return ActionClassification("; ".join(labels), "Explicit current ADU action evidence", evidence, flags)

def open_csv(path, mode):
    return (gzip.open if str(path).endswith(".gz") else open)(path,mode+"t",encoding="utf-8-sig" if mode=="r" else "utf-8",newline="")

def sha256(path):
    digest=hashlib.sha256()
    with open(path,"rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):digest.update(block)
    return digest.hexdigest()

def process(input_path, output_path, audit_path, summary_path):
    paths = [Path(x).resolve() for x in (input_path, output_path, audit_path, summary_path)]
    if len(set(paths)) != 4:
        raise ValueError("Input, output, audit and summary must be distinct files")
    for path in paths[1:]:
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path}; choose a new destination")

    counts, types, flags = collections.Counter(), collections.Counter(), collections.Counter()
    actions, components, action_flags = collections.Counter(), collections.Counter(), collections.Counter()
    statuses, dates, states = collections.Counter(), collections.Counter(), collections.Counter()
    ids = set()
    duplicate_ids = missing = rows = 0
    # Keep all writes private until parsing, classification, and summary succeed.
    # Each staging directory shares its destination's filesystem for publishing.
    with ExitStack() as staging, open_csv(paths[0], "r") as source:
        reader = csv.DictReader(source, strict=True)
        fields = reader.fieldnames
        if not fields or "DESCRIPTION" not in fields:
            raise ValueError("Input needs a DESCRIPTION column")
        if any(not name.strip() for name in fields):
            raise ValueError("Blank input column names")
        if len(fields) != len(set(fields)):
            raise ValueError("Duplicate input column names")
        if set(LABEL_FIELDS) & set(fields):
            raise ValueError("Input already contains ADU columns")
        staged = []
        for path in paths[1:]:
            path.parent.mkdir(parents=True, exist_ok=True)
            directory = staging.enter_context(tempfile.TemporaryDirectory(prefix=".adu-", dir=path.parent))
            staged.append(Path(directory) / path.name)
        with open_csv(staged[0], "w") as destination, open_csv(staged[1], "w") as audit:
            writer = csv.DictWriter(destination, fieldnames=fields + LABEL_FIELDS)
            writer.writeheader()
            audit_writer = csv.DictWriter(audit, fieldnames=["source_row", "BUILDINGPERMITID", "DESCRIPTION",
                "ADU project", "ADU type", "reason", "evidence", "review_flags",
                "ADU action", "action_reason", "action_evidence", "action_flags"])
            audit_writer.writeheader()
            for row_number, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"Malformed CSV record {row_number}")
                result = classify_description(row["DESCRIPTION"])
                action = classify_action(row["DESCRIPTION"], result.project)
                writer.writerow({**row, "ADU project": result.project, "ADU type": result.unit_type, "ADU action": action.label})
                audit_writer.writerow({"source_row": row_number, "BUILDINGPERMITID": row.get("BUILDINGPERMITID", ""),
                    "DESCRIPTION": row["DESCRIPTION"], "ADU project": result.project, "ADU type": result.unit_type,
                    "reason": result.reason, "evidence": "; ".join(result.evidence), "review_flags": "; ".join(result.flags),
                    "ADU action": action.label, "action_reason": action.reason, "action_evidence": "; ".join(action.evidence), "action_flags": "; ".join(action.flags)})
                counts[result.project] += 1
                if result.project == "Yes":
                    types[result.unit_type] += 1
                    actions[action.label] += 1
                    components.update(action_components(action.label))
                action_flags.update(action.flags)
                flags.update(result.flags)
                statuses[row.get("STATUS", "")] += 1
                dates[row.get("EFFECTIVEDATE", "")] += 1
                states[row.get("PROPERTYADDRESSSTATE", "")] += 1
                missing += not bool(row["DESCRIPTION"].strip())
                rows += 1
                identifier = row.get("BUILDINGPERMITID", "")
                if identifier.strip():
                    duplicate_ids += identifier in ids
                    ids.add(identifier)
        nonempty_dates = [date for date in dates if date]
        summary = {
            "classifier_version": VERSION, "input_path": str(paths[0]), "input_sha256": sha256(paths[0]),
            "output_path": str(paths[1]), "output_sha256": sha256(staged[0]), "rows": rows,
            "original_columns": len(fields), "output_columns": len(fields) + 3,
            "project_labels": dict(counts), "adu_types": dict(types), "missing_descriptions": missing,
            "adu_actions": dict(actions), "adu_action_categories": dict(components), "action_review_flags": dict(action_flags),
            "action_categories": list(ACTION_CATEGORIES),
            "duplicate_permit_ids": duplicate_ids, "property_states": dict(states),
            "effective_date_range": [min(nonempty_dates), max(nonempty_dates)] if nonempty_dates else [None, None],
            "effective_dates": dict(sorted(dates.items())), "statuses": dict(statuses.most_common()),
            "review_flags": dict(flags), "limitations": [
                "Description evidence only; No does not establish physical absence of an ADU.",
                "Counts refer to permit records, not unique ADU units or properties.",
                "Mixed statuses and partial-month coverage retained; no approval filter applied.",
                "Rule-based labels are provisional; accuracy requires independently adjudicated data."]}
        staged[2].write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        published = []
        try:
            for temporary, destination in zip(staged, paths[1:]):
                # link() fails if a destination appeared after the initial check.
                os.link(temporary, destination)
                published.append((temporary, destination))
        except BaseException:
            for temporary, destination in reversed(published):
                if destination.exists() and os.path.samefile(temporary, destination):
                    destination.unlink()
            raise
    return summary

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path);parser.add_argument("output",type=Path)
    parser.add_argument("--audit",type=Path,required=True);parser.add_argument("--summary",type=Path,required=True)
    args=parser.parse_args()
    try:
        summary=process(args.input,args.output,args.audit,args.summary)
    except (ValueError, OSError, csv.Error, EOFError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps({k:summary[k] for k in ("rows","project_labels","adu_types","adu_actions","missing_descriptions","review_flags")},indent=2))

if __name__=="__main__":main()
