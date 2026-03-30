"""
Confidence-based threat scoring engine.

Replaces binary block/pass decisions with a composite confidence score
from multiple detection signals. Enables gray-zone escalation to AI
analysis for uncertain cases.

Score ranges:
  >= block_threshold (0.7): BLOCK immediately (no AI needed)
  <= pass_threshold  (0.3): PASS immediately (no AI needed)
  gray zone (0.3-0.7):      ESCALATE to AI analysis if available
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Signal:
    """A single detection signal from one analysis layer."""
    source: str        # "regex_threat", "semantic", "trajectory"
    score: float       # 0.0-1.0 (normalized)
    weight: float      # Contribution weight to composite
    detail: str = ""   # Human-readable detail (e.g., matched pattern)
    category: str = "" # Attack category if detected


@dataclass
class ConfidenceResult:
    """Composite confidence assessment from all signals."""
    score: float                       # 0.0-1.0 weighted composite
    decision: str                      # "block", "pass", "escalate"
    signals: list[Signal] = field(default_factory=list)

    @property
    def is_gray_zone(self) -> bool:
        return self.decision == "escalate"


# Default weights and thresholds
_DEFAULT_WEIGHTS = {
    "regex_threat": 0.4,
    "semantic": 0.35,
    "trajectory": 0.25,
}
_DEFAULT_BLOCK = 0.7
_DEFAULT_PASS = 0.3


def calculate_confidence(
    threat_score: float = 0.0,
    threat_patterns: Optional[list[str]] = None,
    semantic_result: Optional[tuple[float, str, str]] = None,
    trajectory_score: float = 0.0,
    config: Optional[dict] = None,
) -> ConfidenceResult:
    """Calculate composite confidence from all detection signals.

    Args:
        threat_score: Raw regex threat score (0.0-2.5+ range, normalized internally)
        threat_patterns: List of matched regex pattern names
        semantic_result: (score, category, pattern) from semantic_scan, or None
        trajectory_score: Sum of recent session scores (0.0-3.0+ range)
        config: Optional dict with 'weights', 'block_threshold', 'pass_threshold'

    Returns:
        ConfidenceResult with composite score, decision, and signal breakdown
    """
    cfg = config or {}
    weights = cfg.get("weights", _DEFAULT_WEIGHTS)
    block_threshold = cfg.get("block_threshold", _DEFAULT_BLOCK)
    pass_threshold = cfg.get("pass_threshold", _DEFAULT_PASS)

    w_regex = weights.get("regex_threat", 0.4)
    w_semantic = weights.get("semantic", 0.35)
    w_trajectory = weights.get("trajectory", 0.25)

    signals = []

    # Normalize regex threat score: raw 0-2.5+ -> 0.0-1.0
    regex_norm = min(threat_score / 2.0, 1.0) if threat_score > 0 else 0.0
    signals.append(Signal(
        source="regex_threat",
        score=regex_norm,
        weight=w_regex,
        detail=", ".join(threat_patterns or []),
        category="injection" if regex_norm > 0 else "",
    ))

    # Semantic score: already 0.0-1.0
    sem_score = 0.0
    sem_category = ""
    sem_detail = ""
    if semantic_result:
        sem_score = semantic_result[0]
        sem_category = semantic_result[1]
        sem_detail = semantic_result[2]
    signals.append(Signal(
        source="semantic",
        score=sem_score,
        weight=w_semantic,
        detail=sem_detail,
        category=sem_category,
    ))

    # Normalize trajectory: raw 0-3.0+ -> 0.0-1.0
    traj_norm = min(trajectory_score / 3.0, 1.0) if trajectory_score > 0 else 0.0
    signals.append(Signal(
        source="trajectory",
        score=traj_norm,
        weight=w_trajectory,
        category="trajectory" if traj_norm > 0 else "",
    ))

    # Weighted composite
    composite = (
        regex_norm * w_regex +
        sem_score * w_semantic +
        traj_norm * w_trajectory
    )
    # Clamp to [0, 1]
    composite = max(0.0, min(1.0, composite))

    # Decision
    if composite >= block_threshold:
        decision = "block"
    elif composite <= pass_threshold:
        decision = "pass"
    else:
        decision = "escalate"

    return ConfidenceResult(
        score=round(composite, 4),
        decision=decision,
        signals=signals,
    )
