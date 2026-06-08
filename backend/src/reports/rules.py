"""Pure report rules (no I/O) — section grounding. Same validation approach as the
recommendation/chat grounding: strip every section's phantom markers; drop a section that
loses all support; if no section survives, the report cannot stand (the graph fails it as a
content decline). Branch-testable (patterns.md §4)."""

from dataclasses import dataclass

from src.reports.models import ReportStatus
from src.reports.schemas import ReportContent, ReportSection

# Report lifecycle transition map (patterns.md §3). Enforced in storage by compare-and-set
# (UPDATE ... WHERE status = :expected); this map documents + validates the legal moves.
# GENERATING→QUEUED is the sweeper requeue of a dead worker's claim; FAILED→QUEUED is a retry.
_TRANSITIONS: dict[ReportStatus, frozenset[ReportStatus]] = {
    ReportStatus.QUEUED: frozenset({ReportStatus.GENERATING}),
    ReportStatus.GENERATING: frozenset(
        {ReportStatus.READY, ReportStatus.FAILED, ReportStatus.QUEUED}
    ),
    ReportStatus.READY: frozenset(),
    ReportStatus.FAILED: frozenset({ReportStatus.QUEUED}),
}


def can_transition(frm: ReportStatus, to: ReportStatus) -> bool:
    """Whether ``frm → to`` is a legal report lifecycle move. Time/Space: O(1)."""
    return to in _TRANSITIONS[frm]


@dataclass(frozen=True, slots=True)
class GroundedReport:
    """Sections after grounding. ``sections`` is None when no section survived (every section
    lost all support) — the report is failed/declined rather than served ungrounded."""

    sections: tuple[ReportSection, ...] | None
    phantom_count: int  # citation markers that referenced a non-existent source (invention rate)
    dropped_count: int  # sections dropped for losing all support


def ground_sections(content: ReportContent, valid_markers: frozenset[int]) -> GroundedReport:
    """Strip each section's phantom markers (citing a source not retrieved); drop a section
    that ends with no valid markers. Returns the surviving sections (or None if none survive)
    plus the phantom + dropped counts. Time: O(s·m) over sections × markers. Space: O(s·m)."""
    phantom = 0
    dropped = 0
    kept_sections: list[ReportSection] = []
    for section in content.sections:
        kept = [m for m in section.citation_markers if m in valid_markers]
        phantom += len(section.citation_markers) - len(kept)
        if kept:
            kept_sections.append(
                ReportSection(heading=section.heading, body=section.body, citation_markers=kept)
            )
        else:
            dropped += 1
    if not kept_sections:
        return GroundedReport(None, phantom, dropped)
    return GroundedReport(tuple(kept_sections), phantom, dropped)
