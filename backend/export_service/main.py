import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from export_service.routers import html_export, pdf_export, png_export

app = FastAPI(title="Visually Export Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(html_export.router)
app.include_router(pdf_export.router)
app.include_router(png_export.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "export_service"}
