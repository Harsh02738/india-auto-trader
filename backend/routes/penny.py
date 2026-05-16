from fastapi import APIRouter
from backend.services.data_reader import get_penny_candidates

router = APIRouter(prefix="/penny", tags=["penny"])


@router.get("/candidates")
async def candidates():
    return get_penny_candidates() or {"candidates": []}
