from fastapi import APIRouter

router = APIRouter()


@router.get("/api/portfolio/drivers")
def get_portfolio_drivers():
    from backend.services.portfolio_drivers import get_drivers

    return get_drivers()
