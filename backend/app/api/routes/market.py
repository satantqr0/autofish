from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.api.routes.xianyu import require_browser_bridge
from app.core.database import get_db
from app.models import User
from app.schemas.market import MarketCapture, MarketClaim
from app.services import market_browser
from app.services.market_prices import overview

router = APIRouter(prefix="/market-prices", tags=["market-prices"])


@router.get("")
def market_prices(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return overview(db)


@router.post("/collect", status_code=202)
def request_collection(_user: User = Depends(require_operator)):
    from workers.market_prices import collect_market_prices

    job = collect_market_prices.delay(retry=True)
    return {
        "task_id": job.id,
        "message": "采集请求已排队；同一时段最多尝试三次，成功样本不会重复采集。",
    }


@router.post("/agent/claim", dependencies=[Depends(require_browser_bridge)])
def claim_market(payload: MarketClaim, db: Session = Depends(get_db)):
    return {"task": market_browser.claim(db, payload.bridge_id)}


@router.post("/agent/result", dependencies=[Depends(require_browser_bridge)])
def report_market(payload: MarketCapture, db: Session = Depends(get_db)):
    return market_browser.complete(db, payload)
