#!/usr/bin/env python3
"""Train and audit the September ADU text model. Score with classify_adu.py --model."""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import tempfile
from collections import Counter
from contextlib import ExitStack, contextmanager
from itertools import islice
from pathlib import Path

import joblib
import numpy as np
import scipy
import sklearn
from scipy.sparse import hstack
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.multiclass import OneVsRestClassifier
from threadpoolctl import threadpool_limits

import classify_adu as rules

MODEL_VERSION = 1
SEED = 20261109
TARGETS = ("adu", "detached", "new_adu")
TEXT = ("DESCRIPTION", "PROJECTNAME")
TYPES = ("TYPE", "SUBTYPE")
PERMIT_ID = "BUILDINGPERMITID"
OUTPUT_FIELDS = (*TARGETS, "adu_score", "detached_score", "new_adu_score",
                 "new_adu_suggestion", "ml_review_flags", "rules_adu", "rules_detached")
INPUT_FIELDS = (PERMIT_ID, *TEXT, *TYPES)
FEATURE_SETS = ("text", "text_and_type", "text_type_and_rules")
ADU_CUE = re.compile(r"\b(?:jdu|jr\.?\s*adu|adu|jadu|dadu|accessory\s+dwelling)\b")
GLOSSARY = tuple((re.compile(pattern), expansion) for pattern, expansion in (
    (r"\(\s*e\s*\)", "existing"), (r"\(\s*n\s*\)", "new"),
    (r"\b(?:jadu|jdu|jr\.?\s*adu)\b", "junior accessory dwelling unit"),
    (r"\b(?:sfr|sfd|sfh)\b", "single family house"),
    (r"\b(?:lfl|like-for-like)\b", "like for like rebuild"),
    (r"\b(?:mh|mhr)\b", "manufactured building"),
))
NUMBERS = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)")
RUNTIME = {"scikit-learn": sklearn.__version__, "numpy": np.__version__,
           "scipy": scipy.__version__, "joblib": joblib.__version__}


@contextmanager
def read_csv(path, required):
    with rules.open_csv(path, "r") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or any(not field.strip() for field in fields):
            raise ValueError("Blank or duplicate CSV column names")
        if missing := set(required) - set(fields):
            raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")

        def records():
            seen = set()
            for line, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"Malformed CSV record {line}")
                permit = row[PERMIT_ID].strip()
                if not permit or permit in seen:
                    raise ValueError(f"Missing or duplicate permit ID at CSV record {line}")
                seen.add(permit)
                yield row

        yield fields, records()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def publish(files):
    """Link completed files without overwriting destinations, rolling back on failure."""
    published = []
    try:
        for temporary, destination in files:
            os.link(temporary, destination)
            published.append((temporary, destination))
    except BaseException:
        for temporary, destination in reversed(published):
            if destination.exists() and os.path.samefile(temporary, destination):
                destination.unlink()
        raise


@contextmanager
def new_run(directory):
    directory = Path(directory).resolve()
    if directory.exists():
        raise FileExistsError(f"Choose a new directory: {directory}")
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".adu-ml-", dir=directory.parent) as temporary:
        staging = Path(temporary)
        yield staging
        directory.mkdir(mode=0o700)
        try:
            publish((path, directory / path.name) for path in staging.iterdir())
        except BaseException:
            if not any(directory.iterdir()):
                directory.rmdir()
            raise


def group_keys(row):
    property_id = row.get("ATTOMID", "").strip()
    if property_id and property_id != "0":
        yield "property", property_id
    address = re.sub(r"[\W_]+", " ", rules.normalize(row.get("PROPERTYADDRESSFULL"))).strip()
    locality = rules.normalize(row.get("PROPERTYADDRESSCITY"))
    zip_code = row.get("PROPERTYADDRESSZIP", "")[:5]
    # Refuse placeholder addresses and never match a street across localities.
    if re.search(r"\d", address) and re.search(r"[a-z]", address):
        if locality:
            yield "city_address", row.get("STATECOUNTYFIPS", ""), locality, address
        if re.fullmatch(r"\d{5}", zip_code) and zip_code != "00000":
            yield "zip_address", zip_code, address
    description = rules.normalize(row["DESCRIPTION"])
    if len(description) >= 50:
        yield "description", description


def groups_for(rows):
    """Link records transitively by property, address or nontrivial exact description."""
    parent = list(range(len(rows)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    first_seen = {}
    for i, row in enumerate(rows):
        for key in group_keys(row):
            parent[root(i)] = root(first_seen.setdefault(key, i))
    return np.array([root(i) for i in range(len(rows))])


def glossary_text(text):
    expanded = [meaning for pattern, meaning in GLOSSARY if pattern.search(text)]
    return NUMBERS.sub(" number ", text) + (" " + " ".join(expanded) if expanded else "")


def rule_type(result):
    return {"Detached": "Yes", "Attached": "No", "Unclear": "Unsure"}.get(result.unit_type, "")


def has_adu_cue(text):
    return bool(ADU_CUE.search(text) or rules.ACRONYM.search(text) or rules.FORMAL.search(text)
                or rules.ALIASES.search(text) or rules.AMBIGUOUS.search(text))


def prepare(row):
    description, title = (rules.normalize(row[field]) for field in TEXT)
    legacy = rules.classify_description(row["DESCRIPTION"])
    categories = {field: NUMBERS.sub(" number ", rules.normalize(row[field])) or "__missing__"
                  for field in TYPES}
    signals = {"rules_adu": legacy.project, "rules_detached": rule_type(legacy) or "__inapplicable__"}
    signals.update({f"flag_{flag}": 1 for flag in legacy.flags})
    signals["title_cue"] = int(has_adu_cue(title))
    return (glossary_text(description), glossary_text(title), categories, signals,
            bool(description or title), legacy)


def vectorizers(data, train):
    transformers = (
        TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=65000, dtype=np.float32),
        TfidfVectorizer(ngram_range=(3, 5), analyzer="char_wb", min_df=3,
                        max_features=70000, dtype=np.float32),
        TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=20000, dtype=np.float32),
        DictVectorizer(dtype=np.float32),
        DictVectorizer(dtype=np.float32),
    )
    fitted = []
    for field, transformer in zip((0, 0, 1, 2, 3), transformers):
        values = [data[i][field] for i in train]
        try:
            transformer.fit(values)
        except ValueError as error:
            # A title column can legitimately be empty. Drop a text block with no vocabulary.
            if field not in (0, 1) or not any(msg in str(error) for msg in
                    ("empty vocabulary", "After pruning, no terms remain")):
                raise
            continue
        fitted.append((field, transformer))
    return fitted


def feature_matrix(fitted, data, feature_set):
    last_field = 1 + FEATURE_SETS.index(feature_set)
    return hstack([transformer.transform([row[field] for row in data])
                   for field, transformer in fitted if field <= last_field], format="csr")


def score_column(model, probabilities, label):
    return probabilities[:, list(model.classes_).index(label)]


def cutoff(scores, correct, eligible, *, precision=0, max_errors=None, support=1):
    """Return the widest high-score region satisfying the validation constraint."""
    candidates = np.flatnonzero(eligible)
    if not len(candidates):
        return None
    ordered = candidates[np.argsort(-scores[candidates], kind="stable")]
    ends = np.flatnonzero(np.r_[scores[ordered][1:] != scores[ordered][:-1], True])
    count = ends + 1
    hits = np.cumsum(correct[ordered])[ends]
    allowed = (count >= support) & (hits >= count * precision)
    if max_errors is not None:
        allowed &= count - hits <= max_errors
    return float(scores[ordered[ends[allowed][-1]]]) if allowed.any() else None


def at_least(scores, threshold):
    return scores >= threshold if threshold is not None else np.zeros(len(scores), dtype=bool)


def adu_labels(scores, has_text, thresholds):
    no = at_least(-scores, thresholds["no"])
    yes = at_least(scores, thresholds["yes"])
    return np.where(yes & has_text, "Yes", np.where(no & has_text, "No", ""))


def detached_labels(model, probabilities, thresholds, permitted):
    winner = model.classes_[probabilities.argmax(axis=1)]
    labels = np.full(len(winner), "Unsure", dtype="<U6")
    for label in ("Yes", "No"):
        scores = score_column(model, probabilities, label)
        labels[permitted & (winner == label) & at_least(scores, thresholds[label])] = label
    return labels


def rate(numerator, denominator):
    return float(numerator / denominator) if denominator else None


def binary_metrics(truth, prediction, scores=None):
    known = np.isin(truth, ["Yes", "No"])
    truth, prediction = truth[known], prediction[known]
    pos, neg = truth == "Yes", truth == "No"
    yes, no = prediction == "Yes", prediction == "No"
    tp, fp, fn, tn = (int(mask.sum()) for mask in (pos & yes, neg & yes, pos & no, neg & no))
    reviewed_pos, reviewed_neg = int(pos.sum()) - tp - fn, int(neg.sum()) - fp - tn
    stats = {
        "rows": len(truth), "tp": tp, "fp": fp, "fn_auto_no": fn, "tn": tn,
        "reviewed_positive": reviewed_pos, "reviewed_negative": reviewed_neg,
        "precision": rate(tp, tp + fp), "recall": rate(tp, pos.sum()),
        "f1": rate(2 * tp, 2 * tp + fp + fn + reviewed_pos),
        "recall_including_review_queue": rate(tp + reviewed_pos, pos.sum()),
        "auto_coverage": rate((yes | no).sum(), len(truth)),
        "balanced_accuracy": (rate(tp, pos.sum()) + rate(tn, neg.sum()))
        / 2 if pos.any() and neg.any() else None,
    }
    if scores is not None and pos.any() and neg.any():
        stats["average_precision"] = float(average_precision_score(pos, scores[known]))
    return stats


def type_metrics(truth, prediction):
    known = truth != ""
    truth, prediction = truth[known], prediction[known]
    definite = np.isin(prediction, ["Yes", "No"])
    return {"rows": len(truth), "label_counts": dict(Counter(truth)),
            "definite_calls": int(definite.sum()),
            "definite_precision": rate((definite & (truth == prediction)).sum(), definite.sum()),
            "definite_coverage": rate(definite.sum(), len(truth)),
            "agreement": rate((truth == prediction).sum(), len(truth)),
            "guesses_on_reference_unsure": int((definite & (truth == "Unsure")).sum()),
            "detached_yes_no": binary_metrics(truth, prediction)}


def train(source, directory, seed=SEED, min_precision=0.98, max_miss_rate=0.02):
    if not 0 < min_precision <= 1 or not 0 <= max_miss_rate < 1:
        raise ValueError("Precision must be in (0, 1] and miss rate in [0, 1)")
    with read_csv(source, (*INPUT_FIELDS, *TARGETS)) as (_, records):
        rows = list(records)
    adu, detached, new = (np.array([row[name].strip().capitalize() for row in rows])
                           for name in TARGETS)
    if (set(adu) - {"Yes", "No", ""} or set(detached) - {"Yes", "No", "Unsure", ""}
            or set(new) - {"Yes", "No", ""} or np.any((adu != "Yes") & ((detached != "") | (new != "")))):
        raise ValueError("Invalid target values or child labels outside adu=Yes")
    groups = groups_for(rows)
    # Stratify the two rare explicit new_adu outcomes as well as ADU prevalence.
    strata = np.where(adu == "Yes", np.where(new != "", "new_" + new, "adu"), "other")
    splitter = StratifiedGroupKFold(n_splits=7, shuffle=True, random_state=seed)
    validation, holdout, *fit_folds = [test for _, test in splitter.split(rows, strata, groups)]
    fit = np.sort(np.concatenate(fit_folds))
    data = [prepare(row) for row in rows]
    has_text = np.array([row[4] for row in data])
    legacy_adu = np.array([row[5].project for row in data])
    legacy_type = np.array([rule_type(row[5]) for row in data])
    fitted = vectorizers(data, fit)
    train_adu = fit[np.isin(adu[fit], ["Yes", "No"])]
    val_adu = validation[np.isin(adu[validation], ["Yes", "No"])]
    candidates = []
    best = best_plain = None
    with threadpool_limits(limits=1):
        for feature_set in FEATURE_SETS:
            matrix = feature_matrix(fitted, data, feature_set)
            for weight in (None, "balanced"):
                model = LogisticRegression(C=3, solver="liblinear", class_weight=weight, random_state=seed)
                model.fit(matrix[train_adu], adu[train_adu])
                scores = score_column(model, model.predict_proba(matrix[val_adu]), "Yes")
                yes = cutoff(scores, adu[val_adu] == "Yes", has_text[val_adu],
                             precision=min_precision, support=20)
                eligible_no = has_text[val_adu] & ~at_least(scores, yes)
                no = cutoff(-scores, adu[val_adu] == "No", eligible_no,
                            max_errors=int((adu[val_adu] == "Yes").sum() * max_miss_rate))
                thresholds = {"yes": yes, "no": no}
                predicted = adu_labels(scores, has_text[val_adu], thresholds)
                metrics = binary_metrics(adu[val_adu], predicted, scores)
                candidates.append({"features": feature_set, "class_weight": weight,
                                   "thresholds": thresholds, **metrics})
                rank = (metrics["recall"], metrics["auto_coverage"], metrics["average_precision"])
                candidate = rank, feature_set, model, thresholds, weight
                if best is None or rank > best[0]:
                    best = candidate
                if feature_set != "text_type_and_rules" and (best_plain is None or rank > best_plain[0]):
                    best_plain = candidate
            del matrix
        _, selected, adu_model, adu_thresholds, weight = best
        if adu_thresholds["yes"] is None:
            raise ValueError("No model meets the validation precision floor with at least 20 Yes calls")
        matrix = feature_matrix(fitted, data, selected)
        type_fit = fit[detached[fit] != ""]
        type_model = OneVsRestClassifier(LogisticRegression(
            C=3, solver="liblinear", class_weight="balanced", random_state=seed))
        if set(detached[type_fit]) != {"Yes", "No", "Unsure"}:
            raise ValueError("Detached training needs examples of Yes, No and Unsure")
        type_model.fit(matrix[type_fit], detached[type_fit])
        val_type = validation[detached[validation] != ""]
        type_scores = type_model.predict_proba(matrix[val_type])
        winner = type_model.classes_[type_scores.argmax(axis=1)]
        type_allowed = np.array(["mixed_or_conflicting_types" not in row[5].flags
                                 and "multiple_units_incomplete_types" not in row[5].flags
                                 for row in data]) & has_text
        type_thresholds = {
            label: cutoff(score_column(type_model, type_scores, label),
                          detached[val_type] == label,
                          (winner == label) & type_allowed[val_type], precision=0.90, support=10)
            for label in ("Yes", "No")
        }
        new_fit = fit[new[fit] != ""]
        new_model = LogisticRegression(C=3, solver="liblinear", class_weight="balanced", random_state=seed)
        new_model.fit(matrix[new_fit], new[new_fit])
        held_adu_score = score_column(adu_model, adu_model.predict_proba(matrix[holdout]), "Yes")
        held_type_prob = type_model.predict_proba(matrix[holdout])
        held_new_score = score_column(new_model, new_model.predict_proba(matrix[holdout]), "Yes")
        _, plain_features, plain_model, plain_thresholds, plain_weight = best_plain
        plain_holdout = feature_matrix(fitted, [data[i] for i in holdout], plain_features)
        held_plain_score = score_column(plain_model, plain_model.predict_proba(plain_holdout), "Yes")

    held_adu = adu_labels(held_adu_score, has_text[holdout], adu_thresholds)
    held_plain = adu_labels(held_plain_score, has_text[holdout], plain_thresholds)
    held_type = detached_labels(type_model, held_type_prob, type_thresholds, type_allowed[holdout])
    held_new = np.where(held_new_score >= 0.5, "Yes", "No")
    source_hash = rules.sha256(source)
    bundle = {"version": MODEL_VERSION, "runtime": RUNTIME, "rules_version": rules.DESCRIPTION_RULES_VERSION,
              "source_sha256": source_hash, "features": selected, "vectorizers": fitted,
              "adu_model": adu_model, "adu_thresholds": adu_thresholds,
              "type_model": type_model, "type_thresholds": type_thresholds, "new_model": new_model}
    report = {
        "source_sha256": source_hash, "runtime": RUNTIME, "seed": seed, "rows": len(rows),
        "groups": int(len(set(groups))), "largest_group": max(Counter(groups).values()),
        "splits": {name: {"rows": len(idx), "adu": dict(Counter(adu[idx])),
                          "detached": dict(Counter(detached[idx])), "new_adu": dict(Counter(new[idx]))}
                   for name, idx in (("train", fit), ("validation", validation), ("holdout", holdout))},
        "model_selection": {"features": selected, "class_weight": weight,
                            "min_validation_precision": min_precision,
                            "max_validation_auto_no_miss_rate": max_miss_rate,
                            "adu_thresholds": adu_thresholds, "type_thresholds": type_thresholds,
                            "plain_baseline": {"features": plain_features, "class_weight": plain_weight,
                                               "thresholds": plain_thresholds},
                            "validation_candidates": candidates},
        "holdout": {
            "adu": {"rules": binary_metrics(adu[holdout], legacy_adu[holdout]),
                    "ml_without_rules": binary_metrics(adu[holdout], held_plain, held_plain_score),
                    "model": binary_metrics(adu[holdout], held_adu, held_adu_score)},
            "detached_on_reference_adus": {"rules": type_metrics(detached[holdout], legacy_type[holdout]),
                                          "model": type_metrics(detached[holdout], held_type)},
            "detached_end_to_end": {
                "rules": type_metrics(detached[holdout], legacy_type[holdout]),
                "model": type_metrics(detached[holdout], np.where(held_adu == "Yes", held_type, ""))},
            "new_adu_pilot": {"model": binary_metrics(new[holdout], held_new, held_new_score),
                              "always_yes": binary_metrics(new[holdout], np.full(len(holdout), "Yes"))},
        },
    }
    with new_run(directory) as staging:
        model_path = staging / "model.joblib"
        joblib.dump(bundle, model_path, compress=3)
        report["model_sha256"] = rules.sha256(model_path)
        write_json(staging / "september_metrics.json", report)
        with open(staging / "september_splits.csv", "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([PERMIT_ID, "group", "split"])
            for split, indices in (("train", fit), ("validation", validation), ("holdout", holdout)):
                writer.writerows((rows[i][PERMIT_ID], groups[i], split) for i in indices)
        with open(staging / "september_holdout.csv", "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow([PERMIT_ID, *("reference_" + label for label in TARGETS),
                             "rules_adu", "rules_detached", "model_adu", "model_detached",
                             "pilot_new_adu", "adu_score", "plain_ml_adu", "plain_ml_adu_score"])
            writer.writerows((rows[i][PERMIT_ID], adu[i], detached[i], new[i], legacy_adu[i], legacy_type[i],
                              held_adu[j], held_type[j] if held_adu[j] == "Yes" else "",
                              held_new[j], held_adu_score[j], held_plain[j], held_plain_score[j])
                             for j, i in enumerate(holdout))
    return report


def score_batch(bundle, rows):
    data = [prepare(row) for row in rows]
    matrix = feature_matrix(bundle["vectorizers"], data, bundle["features"])
    adu_model, type_model, new_model = (bundle[key] for key in ("adu_model", "type_model", "new_model"))
    adu_score = score_column(adu_model, adu_model.predict_proba(matrix), "Yes")
    type_prob = type_model.predict_proba(matrix)
    type_score = score_column(type_model, type_prob, "Yes")
    new_score = score_column(new_model, new_model.predict_proba(matrix), "Yes")
    has_text = np.array([row[4] for row in data])
    adu = adu_labels(adu_score, has_text, bundle["adu_thresholds"])
    allowed = np.array(["mixed_or_conflicting_types" not in row[5].flags
                        and "multiple_units_incomplete_types" not in row[5].flags
                        for row in data]) & has_text
    types = detached_labels(type_model, type_prob, bundle["type_thresholds"], allowed)
    for i, row in enumerate(rows):
        legacy = data[i][5]
        label = adu[i]
        flags = []
        if not label:
            flags.append("adu_score_review" if has_text[i] else "no_permit_text")
        if label == "Yes":
            if types[i] == "Unsure":
                flags.append("detached_unsure")
            flags.append("new_adu_pilot")
        yield {**row, "adu": label, "detached": types[i] if label == "Yes" else "", "new_adu": "",
               "adu_score": f"{adu_score[i]:.8f}",
               "detached_score": f"{type_score[i]:.8f}" if label == "Yes" else "",
               "new_adu_score": f"{new_score[i]:.8f}" if label == "Yes" else "",
               "new_adu_suggestion": ("Yes" if new_score[i] >= 0.5 else "No") if label == "Yes" else "",
               "ml_review_flags": ";".join(flags), "rules_adu": legacy.project, "rules_detached": rule_type(legacy)}


def score_csv(input_path, output_path, model_path, summary_path):
    input_path, output_path, model_path, summary_path = map(
        lambda path: Path(path).resolve(), (input_path, output_path, model_path, summary_path))
    if len({input_path, output_path, model_path, summary_path}) != 4:
        raise ValueError("Input, output, model and summary must be distinct files")
    for path in (output_path, summary_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    bundle = joblib.load(model_path)  # Only load a model trained locally or from a trusted source.
    if (bundle["version"], bundle["runtime"], bundle["rules_version"]) != (
            MODEL_VERSION, RUNTIME, rules.DESCRIPTION_RULES_VERSION):
        raise ValueError("Model version or dependencies differ. Use the training environment or retrain.")
    counts, types, suggestions, flags = (Counter() for _ in range(4))
    with ExitStack() as stack, read_csv(input_path, INPUT_FIELDS) as (fields, records):
        if set(fields) & set(OUTPUT_FIELDS):
            raise ValueError("Input already contains model output columns")
        staged = [Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".adu-ml-", dir=path.parent)))
                  / path.name for path in (output_path, summary_path)]
        with rules.open_csv(staged[0], "w") as target, threadpool_limits(limits=1):
            writer = csv.DictWriter(target, fieldnames=fields + list(OUTPUT_FIELDS))
            writer.writeheader()
            while batch := list(islice(records, 2048)):
                for row in score_batch(bundle, batch):
                    writer.writerow(row)
                    counts[row["adu"] or "Review"] += 1
                    if row["adu"] == "Yes":
                        types[row["detached"]] += 1
                        suggestions[row["new_adu_suggestion"]] += 1
                    flags.update(row["ml_review_flags"].split(";") if row["ml_review_flags"] else ())
        summary = {"rows": sum(counts.values()), "adu": dict(counts), "detached": dict(types),
                   "new_adu_suggestions": dict(suggestions), "review_flags": dict(flags),
                   "features": bundle["features"], "training_sha256": bundle["source_sha256"],
                   "input_sha256": rules.sha256(input_path), "model_sha256": rules.sha256(model_path),
                   "output_sha256": rules.sha256(staged[0])}
        write_json(staged[1], summary)
        publish(zip(staged, (output_path, summary_path)))
    return summary


def make_audit(source, directory, seed=SEED):
    """Sample August predictions uniformly within disjoint review strata."""
    sizes = {"adu_review": 120, "auto_adu": 120, "no_with_cue": 100, "other_no": 160}
    groups = {group: [] for group in sizes}
    counts = Counter()
    rng = random.Random(seed)
    with read_csv(source, (*INPUT_FIELDS, *OUTPUT_FIELDS)) as (_, records):
        for row in records:
            if row["adu"] == "":
                group = "adu_review"
            elif row["adu"] == "Yes":
                group = "auto_adu"
            elif row["adu"] == "No":
                cue = row["rules_adu"] == "Yes" or any(
                    has_adu_cue(rules.normalize(row[field])) for field in (*TEXT, *TYPES))
                group = "no_with_cue" if cue else "other_no"
            else:
                raise ValueError("Unexpected ADU prediction in audit input")
            counts[group] += 1
            # Reservoir sampling is simple random sampling without replacement.
            if len(groups[group]) < sizes[group]:
                groups[group].append(row)
            elif (index := rng.randrange(counts[group])) < sizes[group]:
                groups[group][index] = row
    chosen = [(group, row) for group, records in groups.items() for row in records]
    rng.shuffle(chosen)
    report = {"seed": seed, "scored_sha256": rules.sha256(source), "sample_size": len(chosen),
              "population_by_stratum": dict(counts),
              "draws_by_stratum": {group: len(records) for group, records in groups.items()}}
    with new_run(directory) as staging:
        with open(staging / "blinded_review.csv", "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["review_id", *TEXT, *TYPES, "review_adu", "review_detached",
                             "review_new_adu", "review_notes"])
            writer.writerows((i, *(row[field] for field in (*TEXT, *TYPES)), "", "", "", "")
                             for i, (_, row) in enumerate(chosen, 1))
        with open(staging / "audit_key.csv", "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["review_id", PERMIT_ID, "stratum", "weight", *OUTPUT_FIELDS])
            writer.writerows((i, row[PERMIT_ID], group, counts[group] / len(groups[group]),
                              *(row[field] for field in OUTPUT_FIELDS))
                             for i, (group, row) in enumerate(chosen, 1))
        write_json(staging / "audit_manifest.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("train", "audit"):
        sub = commands.add_parser(command)
        sub.add_argument("input", type=Path)
        sub.add_argument("directory", type=Path)
        sub.add_argument("--seed", type=int, default=SEED)
        if command == "train":
            sub.add_argument("--min-precision", type=float, default=0.98)
            sub.add_argument("--max-miss-rate", type=float, default=0.02)
    args = parser.parse_args()
    try:
        result = (train(args.input, args.directory, args.seed, args.min_precision, args.max_miss_rate)
                  if args.command == "train" else make_audit(args.input, args.directory, args.seed))
    except (ValueError, OSError, csv.Error, EOFError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
