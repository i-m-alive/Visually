"""
HTML Export router — accepts a pre-built export request payload and returns
a complete self-contained HTML document as a string.
"""
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from export_service.html_builder import ExportBuildRequest, build_html_export

router = APIRouter(tags=["html_export"])

AGENT_SERVICE_URL = os.getenv("AGENT_SERVICE_URL", "http://localhost:8001")


class HtmlExportRequest(BaseModel):
    dashboard_title: str
    theme: str = "frost"
    include_chat: bool = True
    export_token: str = ""
    api_base: str = AGENT_SERVICE_URL
    widgets: list[dict] = []


class HtmlExportResponse(BaseModel):
    html_content: str
    size_bytes: int


@router.post("/export/html", response_model=HtmlExportResponse)
async def generate_html_export(req: HtmlExportRequest):
    """
    Build and return a complete self-contained HTML export for a dashboard.
    """
    try:
        build_req = ExportBuildRequest(
            dashboard_title=req.dashboard_title,
            theme=req.theme,
            include_chat=req.include_chat,
            export_token=req.export_token,
            api_base=req.api_base,
            widgets=req.widgets,
        )
        html_content = await build_html_export(build_req)
        return HtmlExportResponse(
            html_content=html_content,
            size_bytes=len(html_content.encode("utf-8")),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"HTML export failed: {exc}")
