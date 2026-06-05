"""Search HTTP routes — thin: validate, one service call, shape response."""

from fastapi import APIRouter, Depends

from src.identity.dependencies import get_current_user
from src.identity.schemas import AuthContext
from src.search.dependencies import get_search_service
from src.search.schemas import SearchQuery, SearchResult
from src.search.service import SearchService

router = APIRouter()


@router.post("/search")
async def search(
    body: SearchQuery,
    actor: AuthContext = Depends(get_current_user),
    service: SearchService = Depends(get_search_service),
) -> SearchResult:
    return await service.search(actor, body)
