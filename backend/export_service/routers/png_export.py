"""
PNG Export router — placeholder for Phase 4 PNG / image export.
Full implementation would use Playwright/Puppeteer or a chart rendering service
to produce a high-resolution PNG screenshot of the dashboard.
"""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["png_export"])


class PngExportRequest(BaseModel):
    dashboard_title: str
    theme: str = "frost"
    widgets: list[dict] = []
    width: int = 1440
    height: int = 900


class PngExportResponse(BaseModel):
    status: str
    message: str


@router.post("/export/png", response_model=PngExportResponse)
async def generate_png_export(req: PngExportRequest):
    """
    PNG export endpoint.  Currently returns a stub — full implementation
    would use Playwright to screenshot the rendered HTML export.
    """
    return PngExportResponse(
        status="not_implemented",
        message=(
            "PNG export is planned for a future phase. "
            "Use the /export/html endpoint and print from browser for now."
        ),
    )
