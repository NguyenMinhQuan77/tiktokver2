from fastapi import APIRouter, HTTPException
from backend.models import ProductAnalyzeRequest, ProductAnalyzeResponse
from backend.services.scraper import scrape_product
from backend.services import product_service as showcase_service

router = APIRouter(prefix="/product", tags=["product"])


@router.post("/analyze", response_model=ProductAnalyzeResponse)
async def analyze_product(request: ProductAnalyzeRequest):
    """Scrape product information from the given affiliate link."""
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL không được để trống")

    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL không hợp lệ — phải bắt đầu bằng http:// hoặc https://")

    try:
        product_info = await scrape_product(url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Không thể lấy thông tin sản phẩm: {str(e)}"
        )

    return ProductAnalyzeResponse(
        name=product_info.name,
        description=product_info.description,
        price=product_info.price,
        currency=product_info.currency,
        images=product_info.images,
        shop_type=product_info.shop_type,
        original_url=product_info.original_url,
    )


@router.get("/showcase/list")
async def list_showcase_products():
    """Return cached TikTok Shop showcase products."""
    products = showcase_service.get_cached_products()
    return {"products": products, "count": len(products)}


@router.post("/showcase/refresh")
async def refresh_showcase_products():
    """Re-fetch showcase products from TikTok Studio (opens Chrome)."""
    try:
        products = await showcase_service.fetch_products()
        return {"products": products, "count": len(products)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
