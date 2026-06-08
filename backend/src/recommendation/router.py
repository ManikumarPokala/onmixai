"""Recommendation HTTP routes — thin: validate, one service call, shape response. A decline
is a valid 200 outcome (distinguished by ``status``), not an error; an infrastructure failure
propagates as a typed 5xx from the gateway. Reads are owner-scoped (404 for a non-owner)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from src.identity.dependencies import get_current_user
from src.identity.schemas import AuthContext
from src.recommendation.dependencies import get_recommendation_service, set_user_scoped_key
from src.recommendation.schemas import (
    CreateRecommendationRequest,
    RecommendationPage,
    RecommendationResponse,
)
from src.recommendation.service import RecommendationService
from src.shared.ratelimit import RECOMMENDATION_RATE_LIMIT, limiter

router = APIRouter()


@router.post("/recommendations", dependencies=[Depends(set_user_scoped_key)])
@limiter.limit(RECOMMENDATION_RATE_LIMIT)
async def create_recommendation(
    request: Request,
    body: CreateRecommendationRequest,
    actor: AuthContext = Depends(get_current_user),
    service: RecommendationService = Depends(get_recommendation_service),
) -> RecommendationResponse:
    request_id: str = request.state.request_id
    return await service.create(actor, body.query, body.collection_scope, request_id=request_id)


@router.get("/recommendations")
async def list_recommendations(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    actor: AuthContext = Depends(get_current_user),
    service: RecommendationService = Depends(get_recommendation_service),
) -> RecommendationPage:
    return await service.list(actor, cursor=cursor, limit=limit)


@router.get("/recommendations/{recommendation_id}")
async def get_recommendation(
    recommendation_id: UUID,
    actor: AuthContext = Depends(get_current_user),
    service: RecommendationService = Depends(get_recommendation_service),
) -> RecommendationResponse:
    return await service.get(actor, recommendation_id)
