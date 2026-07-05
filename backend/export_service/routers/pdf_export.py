"""
PDF Export — renders the dashboard's self-contained HTML export to a real PDF
using headless Chromium (Playwright). PNG variant lives in png_export.py.

Requires: pip install playwright && playwright install chromium.
Degrades to HTTP 501 with install instructions when Playwright is missing.
"""
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from export_service.html_builder import ExportBuildRequest, build_html_export

router = APIRouter(tags=["pdf_export"])


class PdfExportRequest(BaseModel):
    dashboard_title: str
    theme: str = "frost"
    widgets: list[dict] = []
    page_size: str = "A4"
    landscape: bool = True


async def _render_html(req: PdfExportRequest) -> str:
    build_req = ExportBuildRequest(
        dashboard_title=req.dashboard_title,
        theme=req.theme,
        include_chat=False,          # static document — no chat runtime
        export_token="",
        api_base="",
        widgets=req.widgets,
    )
    return await build_html_export(build_req)


@router.post("/export/pdf")
async def generate_pdf_export(req: PdfExportRequest):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Playwright not installed — run: pip install playwright && playwright install chromium",
        )
    try:
        html = await _render_html(req)
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])
            try:
                page = await browser.new_page(viewport={"width": 1440, "height": 900})
                await page.set_content(html, wait_until="networkidle")
                await page.wait_for_timeout(600)  # chart animation settle
                pdf_bytes = await page.pdf(
                    format=req.page_size,
                    landscape=req.landscape,
                    print_background=True,
                    margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
                )
            finally:
                await browser.close()
        safe_name = "".join(c for c in req.dashboard_title if c.isalnum() or c in " -_")[:60] or "dashboard"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF export failed: {exc}")
