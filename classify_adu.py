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

VERSION = "1.1.0"

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
        if {"ADU project", "ADU type"} & set(fields):
            raise ValueError("Input already contains ADU columns")
        staged = []
        for path in paths[1:]:
            path.parent.mkdir(parents=True, exist_ok=True)
            directory = staging.enter_context(tempfile.TemporaryDirectory(prefix=".adu-", dir=path.parent))
            staged.append(Path(directory) / path.name)
        with open_csv(staged[0], "w") as destination, open_csv(staged[1], "w") as audit:
            writer = csv.DictWriter(destination, fieldnames=fields + ["ADU project", "ADU type"])
            writer.writeheader()
            audit_writer = csv.DictWriter(audit, fieldnames=["source_row", "BUILDINGPERMITID", "DESCRIPTION",
                "ADU project", "ADU type", "reason", "evidence", "review_flags"])
            audit_writer.writeheader()
            for row_number, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"Malformed CSV record {row_number}")
                result = classify_description(row["DESCRIPTION"])
                writer.writerow({**row, "ADU project": result.project, "ADU type": result.unit_type})
                audit_writer.writerow({"source_row": row_number, "BUILDINGPERMITID": row.get("BUILDINGPERMITID", ""),
                    "DESCRIPTION": row["DESCRIPTION"], "ADU project": result.project, "ADU type": result.unit_type,
                    "reason": result.reason, "evidence": "; ".join(result.evidence), "review_flags": "; ".join(result.flags)})
                counts[result.project] += 1
                if result.project == "Yes":
                    types[result.unit_type] += 1
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
            "original_columns": len(fields), "output_columns": len(fields) + 2,
            "project_labels": dict(counts), "adu_types": dict(types), "missing_descriptions": missing,
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
    print(json.dumps({k:summary[k] for k in ("rows","project_labels","adu_types","missing_descriptions","review_flags")},indent=2))

if __name__=="__main__":main()
