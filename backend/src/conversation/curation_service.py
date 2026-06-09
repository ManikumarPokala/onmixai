"""Feedback → golden curation (Phase 6 Task 8). An owner/admin reviews UP-rated answers and
promotes the good ones into golden-set *candidates*; a human then approves or rejects them.

Two non-negotiables (CLAUDE.md §4, Phase-6 exit):
  * PII is handled on surfaced content. Every Q&A that reaches a reviewer — and everything
    persisted on a candidate — is run through the PII redactor first (always on for this surface,
    independent of any per-org pipeline toggle). Raw PII never lands in the review feed, the
    candidate rows, or the audit trail; only redaction counts are kept.
  * Candidates are human-gated and never auto-merged. Promotion creates a PENDING candidate; a
    human decision is a compare-and-set to APPROVED/REJECTED. Nothing here writes the eval golden
    files — exporting an approved candidate into the regression set is a deliberate manual step.
"""

from datetime import UTC, datetime
from uuid import UUID

from src.ai.guardrails.pii import PIIRedactor
from src.conversation.exceptions import (
    GoldenCandidateAlreadyDecidedError,
    GoldenCandidateNotFoundError,
    MessageNotFoundError,
)
from src.conversation.models import FeedbackRating, GoldenCandidate, GoldenCandidateStatus
from src.conversation.repository import ChatMessageRepository, GoldenCandidateRepository
from src.conversation.rules import candidate_decision_target
from src.conversation.schemas import (
    GoldenCandidatePage,
    GoldenCandidateResponse,
    ReviewItem,
    ReviewPage,
)
from src.identity.schemas import AuthContext
from src.shared.audit import AuditEmitter
from src.shared.config import Settings
from src.shared.pagination import decode_keyset_cursor, encode_keyset_cursor


def _merge(*counts: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for c in counts:
        for k, v in c.items():
            merged[k] = merged.get(k, 0) + v
    return merged


class FeedbackCurationService:
    """Owner/admin curation of feedback into golden candidates. Cross-collection within the org,
    org-scoped by RLS; every mutation audited. PII redaction is applied to all surfaced content."""

    def __init__(
        self,
        *,
        candidates: GoldenCandidateRepository,
        messages: ChatMessageRepository,
        redactor: PIIRedactor,
        audit: AuditEmitter,
        settings: Settings,
    ) -> None:
        self._candidates = candidates
        self._messages = messages
        self._redactor = redactor
        self._audit = audit
        self._settings = settings

    def _redact(self, text: str) -> tuple[str, dict[str, int]]:
        outcome = self._redactor.redact(text, enabled=True)  # always on for the curation surface
        return outcome.text, outcome.counts

    async def list_review_queue(
        self, actor: AuthContext, *, cursor: str | None, limit: int
    ) -> ReviewPage:
        """One newest-first page of UP-rated answers, surfaced PII-redacted for review. Server
        capped. Time: O(limit) feedback rows + O(1) message lookups each. Raises INVALID_CURSOR."""
        capped = min(limit, self._settings.admin_user_page_size)
        before = decode_keyset_cursor(cursor) if cursor is not None else None
        rows = await self._candidates.list_positive_feedback(
            actor.org_id, before=before, limit=capped + 1
        )
        has_more = len(rows) > capped
        page = rows[:capped]
        items: list[ReviewItem] = []
        for fb in page:
            answer_msg = await self._messages.get(actor.org_id, fb.message_id)
            if answer_msg is None:
                continue  # answer message gone (deleted); skip silently
            question_msg = await self._candidates.preceding_user_message(
                actor.org_id, answer_msg.session_id, answer_msg.seq
            )
            q_text, q_counts = self._redact(question_msg.content if question_msg else "")
            a_text, a_counts = self._redact(answer_msg.content)
            c_text, c_counts = self._redact(fb.comment) if fb.comment else (None, {})
            items.append(
                ReviewItem(
                    message_id=fb.message_id,
                    question=q_text,
                    answer=a_text,
                    comment=c_text,
                    redaction_counts=_merge(q_counts, a_counts, c_counts),
                    created_at=fb.created_at,
                )
            )
        next_cursor = encode_keyset_cursor(page[-1].created_at, page[-1].id) if has_more else None
        return ReviewPage(items=items, next_cursor=next_cursor)

    async def promote(self, actor: AuthContext, message_id: UUID) -> GoldenCandidateResponse:
        """Create a PENDING golden candidate from an answer message + its question, stored
        PII-redacted (audited). Raises MESSAGE_NOT_FOUND. Time: O(1)."""
        answer_msg = await self._messages.get(actor.org_id, message_id)
        if answer_msg is None:
            raise MessageNotFoundError()
        question_msg = await self._candidates.preceding_user_message(
            actor.org_id, answer_msg.session_id, answer_msg.seq
        )
        q_text, q_counts = self._redact(question_msg.content if question_msg else "")
        a_text, a_counts = self._redact(answer_msg.content)
        candidate = await self._candidates.create(
            GoldenCandidate(
                org_id=actor.org_id,
                source_message_id=message_id,
                question=q_text,
                answer=a_text,
                rating=FeedbackRating.UP,  # the review queue only surfaces UP-rated answers
                redaction_counts=_merge(q_counts, a_counts),
                status=GoldenCandidateStatus.PENDING,
                curated_by=actor.user_id,
            )
        )
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action="golden_candidate.promoted",
            resource_type="golden_candidate",
            resource_id=candidate.id,
            source_message_id=str(message_id),
            redaction_counts=candidate.redaction_counts,
        )
        return GoldenCandidateResponse.from_model(candidate)

    async def list_candidates(
        self,
        actor: AuthContext,
        *,
        status: GoldenCandidateStatus | None,
        cursor: str | None,
        limit: int,
    ) -> GoldenCandidatePage:
        """One newest-first page of golden candidates, optionally filtered by status. Time:
        O(limit). Raises INVALID_CURSOR."""
        capped = min(limit, self._settings.admin_user_page_size)
        before = decode_keyset_cursor(cursor) if cursor is not None else None
        rows = await self._candidates.list_for_org(
            actor.org_id, status, before=before, limit=capped + 1
        )
        has_more = len(rows) > capped
        page = rows[:capped]
        next_cursor = encode_keyset_cursor(page[-1].created_at, page[-1].id) if has_more else None
        return GoldenCandidatePage(
            candidates=[GoldenCandidateResponse.from_model(c) for c in page],
            next_cursor=next_cursor,
        )

    async def decide(
        self, actor: AuthContext, candidate_id: UUID, *, approve: bool
    ) -> GoldenCandidateResponse:
        """Human gate: compare-and-set PENDING → approved/rejected (audited). Never writes the eval
        golden set. Raises GOLDEN_CANDIDATE_NOT_FOUND / _ALREADY_DECIDED. Time: O(1)."""
        candidate = await self._candidates.get(actor.org_id, candidate_id)
        if candidate is None:
            raise GoldenCandidateNotFoundError()
        target = candidate_decision_target(approve)
        decided = await self._candidates.decide(
            actor.org_id, candidate_id, target, decided_by=actor.user_id, now=datetime.now(UTC)
        )
        if not decided:
            raise GoldenCandidateAlreadyDecidedError()
        self._audit.emit(
            org_id=actor.org_id,
            actor_id=actor.user_id,
            action=f"golden_candidate.{target.value}",
            resource_type="golden_candidate",
            resource_id=candidate_id,
        )
        refreshed = await self._candidates.get(actor.org_id, candidate_id)
        assert refreshed is not None  # just decided; row exists
        return GoldenCandidateResponse.from_model(refreshed)
