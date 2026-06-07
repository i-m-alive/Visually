"""
PDF Export router — placeholder for Phase 4 PDF export.
Accepts the same payload shape as html_export but returns a stub response.
Full implementation would use Playwright/Puppeteer headless browser to print
the generated HTML to PDF.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["pdf_export"])


class PdfExportRequest(BaseModel):
    dashboard_title: str
    theme: str = "frost"
    widgets: list[dict] = []
    page_size: str = "A4"
    landscape: bool = True


class PdfExportResponse(BaseModel):
    status: str
    message: str


@router.post("/export/pdf", response_model=PdfExportResponse)
async def generate_pdf_export(req: PdfExportRequest):
    """
    PDF export endpoint.  Currently returns a stub — full implementation
    would spawn a headless browser (Playwright) to render the HTML export
    and print it as a PDF.
    """
    return PdfExportResponse(
        status="not_implemented",
        message=(
            "PDF export is planned for a future phase. "
            "Use the /export/html endpoint to get a printable HTML page."
        ),
    )
