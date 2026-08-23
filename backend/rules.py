"""Read config/rules.yaml — the curated clinical rule table (PROJECT_CONTEXT.md §9).

The file maps a disease to the medicines one case consumes. Nothing in the
code base may add, remove or change a row; this module only reads it.

    rules = load_rules()
    rules["acute_diarrhoeal_disease"]  -> [{"medicine": "ors_packets", "per_case": 4, "source": ...}, ...]
    per_case_table(rules)              -> {"ors_packets": {"acute_diarrhoeal_disease": 4.0}, ...}
"""

import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import RULES_YAML  # noqa: E402

ALIASES_KEY = "disease_aliases"


def load_rules(path=RULES_YAML):
    """Return {disease_key: [rule_row, ...]} — every top-level key except
    disease_aliases. Each row must carry medicine, per_case and source."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    rules = {}
    for disease, rows in raw.items():
        if disease == ALIASES_KEY:
            continue
        if not isinstance(rows, list):
            raise SystemExit(f"rules.yaml: {disease!r} must be a list of rows, got {type(rows).__name__}")
        for row in rows:
            missing = {"medicine", "per_case", "source"} - set(row)
            if missing:
                raise SystemExit(f"rules.yaml: {disease!r} row {row} is missing {sorted(missing)}")
            if float(row["per_case"]) <= 0:
                raise SystemExit(f"rules.yaml: {disease!r} / {row['medicine']!r} per_case must be > 0")
        rules[disease] = rows
    return rules


def disease_aliases(path=RULES_YAML):
    """{IDSP disease label: internal disease key}."""
    with open(path, encoding="utf-8") as f:
        return dict(yaml.safe_load(f).get(ALIASES_KEY, {}))


def per_case_table(rules):
    """Invert the rule table: {medicine_id: {disease_key: per_case}}.
    A medicine used by more than one disease has one entry per disease."""
    table = {}
    for disease, rows in rules.items():
        for row in rows:
            table.setdefault(row["medicine"], {})[disease] = float(row["per_case"])
    return table


if __name__ == "__main__":
    rules = load_rules()
    print(f"rules.yaml: {len(rules)} diseases, aliases {disease_aliases()}")
    for disease, rows in rules.items():
        for r in rows:
            print(f"  {disease:<26} {r['medicine']:<18} per_case={r['per_case']:<5} source={r['source']!r}")
    print("per-medicine view:")
    for med, by_disease in per_case_table(rules).items():
        print(f"  {med:<18} {by_disease}")
