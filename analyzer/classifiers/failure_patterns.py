"""
failure_patterns.py
~~~~~~~~~~~~~~~~~~~
Loads patterns from failure_patterns.yaml and merges in PATTERN_OVERRIDES
for any regex that cannot survive YAML serialisation cleanly (apostrophes
combined with \\s sequences, complex character classes, etc.).

Exposes:
  FAILURE_PATTERNS    – Dict[FailureCategory, List[re.Pattern]]
  CATEGORY_PRIORITY   – Dict[FailureCategory, int]
  CATEGORY_THRESHOLDS – Dict[FailureCategory, Thresholds]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml

from api.schemas.failure_category_schema import FailureCategory


_YAML_PATH = Path(__file__).parent / "failure_patterns.yaml"


@dataclass(frozen=True)
class Thresholds:
    regex_min: float
    semantic_min: float


# ---------------------------------------------------------------------------
# Lines matching ANY of these patterns are noise and must never contribute
# to a category score, regardless of what other tokens appear in them.
# Examples: plugin exceptions that are explicitly swallowed, INFO lines
# that happen to mention "sonar" or "token".
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# SIGNAL_IGNORE_PATTERNS — checked by the signal extractor BEFORE a signal
# is created.  If error_line matches any of these the whole signal is dropped.
# Add any Jenkins plugin that emits purely observability/metrics lines here.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# INFLUXDB_FAILURE_PATTERN — InfluxDB plugin lines that indicate a REAL
# infrastructure failure (server down, network unreachable, HTTP 5xx).
# These are NOT noise — they are classified as INFRA_FAILURE.
# Checked BEFORE the noise filter so they are never accidentally dropped.
# ---------------------------------------------------------------------------
INFLUXDB_FAILURE_PATTERN: re.Pattern = re.compile(
    r'\[InfluxDB\s+(P|p)lugin\]'
    r'.*'
    r'(connection\s+refused|timed?\s+out|network\s+error|no\s+route|http.?5\d\d'
    r'|failed\s+to\s+connect|unable\s+to\s+write|server\s+error'
    r'|Ignoring\s+Exception)',                # plugin swallowed it but it still broke
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# SIGNAL_IGNORE_PATTERNS — checked by both classifiers on error_line BEFORE
# any scoring.  If error_line matches, the signal is immediately UNKNOWN.
# Only add lines the plugin explicitly marks as swallowed / informational.
# Real failures (connection refused, HTTP 5xx) must NOT be in this list.
# ---------------------------------------------------------------------------
SIGNAL_IGNORE_PATTERNS: List[re.Pattern] = [
    # InfluxDB pure noise: INFO, WARNING, Collecting, data shipping confirmations
    # NOTE: "Ignoring Exception" is NOT here — it is classified as INFRA_FAILURE
    re.compile(
        r'\[InfluxDB\s+(P|p)lugin\]'
        r'(\s+Collecting|\s+INFO:|\s+WARNING:|.*plugin\s+data\s+found)',
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# IGNORE_LINE_PATTERNS — stripped line-by-line from pre_content/post_content
# before scoring so surrounding real errors are not contaminated.
# Same logic as SIGNAL_IGNORE_PATTERNS but applied to context fields.
# ---------------------------------------------------------------------------
IGNORE_LINE_PATTERNS: List[re.Pattern] = [
    # InfluxDB pure noise stripped from pre_content/post_content before scoring
    # NOTE: "Ignoring Exception" lines are NOT stripped — they carry failure signal
    re.compile(
        r'\[InfluxDB\s+(P|p)lugin\]'
        r'(\s+Collecting|\s+INFO:|\s+WARNING:|.*plugin\s+data\s+found)',
        re.IGNORECASE,
    ),
]


# ---------------------------------------------------------------------------
# Patterns that cannot be written cleanly in YAML.
# Use raw strings (r"...") so backslashes are always literal.
# Add to the relevant category list; they are merged at load time.
# ---------------------------------------------------------------------------
PATTERN_OVERRIDES: Dict[FailureCategory, List[str]] = {
    FailureCategory.CI_INFRA_FAILURE: [
        r"doesn't\s+match\s+anything",   # apostrophe + \s — unrepresentable in YAML
        r"doesnt\s+match\s+anything",    # variant without apostrophe
    ],
}


def _load() -> tuple[
    Dict[FailureCategory, List[re.Pattern]],
    Dict[FailureCategory, int],
    Dict[FailureCategory, Thresholds],
]:
    with open(_YAML_PATH, "r") as fh:
        raw = yaml.safe_load(fh)

    patterns: Dict[FailureCategory, List[re.Pattern]] = {}
    priority: Dict[FailureCategory, int] = {}
    thresholds: Dict[FailureCategory, Thresholds] = {}

    for category_name, cfg in raw["categories"].items():
        try:
            cat = FailureCategory(category_name)
        except ValueError:
            raise ValueError(
                f"'{category_name}' in failure_patterns.yaml is not a valid FailureCategory"
            )

        priority[cat] = cfg["priority"]
        thresholds[cat] = Thresholds(
            regex_min=cfg["thresholds"]["regex_min"],
            semantic_min=cfg["thresholds"]["semantic_min"],
        )

        compiled = [re.compile(p, re.IGNORECASE) for p in cfg["patterns"]]

        for override in PATTERN_OVERRIDES.get(cat, []):
            compiled.append(re.compile(override, re.IGNORECASE))

        patterns[cat] = compiled
    return patterns, priority, thresholds


FAILURE_PATTERNS, CATEGORY_PRIORITY, CATEGORY_THRESHOLDS = _load()