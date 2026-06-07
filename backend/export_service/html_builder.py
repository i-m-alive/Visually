"""
HTML Export Builder — produces a fully self-contained, single-file HTML export of a
Visually dashboard.  The output includes:
  • Inlined Chart.js (fetched at build-time)
  • All widget data baked into a JS constant (WIDGET_DATA)
  • Theme-aware CSS (5 built-in themes)
  • Interactive filtering via chart-click / table-row-click
  • Optional floating AI chat panel powered by a short-lived export token
"""
import html
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

# ─── Theme definitions ────────────────────────────────────────────────────────

THEME_CONFIGS: dict[str, dict[str, Any]] = {
    "frost": {
        "bg_primary": "#f0f4f8",
        "bg_secondary": "#e2e8f0",
        "bg_card": "#ffffff",
        "border": "#cbd5e1",
        "text_primary": "#1e293b",
        "text_secondary": "#64748b",
        "accent": "#3b82f6",
        "accent_light": "#dbeafe",
        "chart_colors": ["#3b82f6", "#06b6d4", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444"],
        "font_family": "'Inter', 'Segoe UI', system-ui, sans-serif",
    },
    "slate": {
        "bg_primary": "#0f172a",
        "bg_secondary": "#1e293b",
        "bg_card": "#1e293b",
        "border": "#334155",
        "text_primary": "#f1f5f9",
        "text_secondary": "#94a3b8",
        "accent": "#38bdf8",
        "accent_light": "#0c4a6e",
        "chart_colors": ["#38bdf8", "#818cf8", "#34d399", "#fb923c", "#f472b6", "#a78bfa"],
        "font_family": "'Inter', 'Segoe UI', system-ui, sans-serif",
    },
    "sage": {
        "bg_primary": "#f0fdf4",
        "bg_secondary": "#dcfce7",
        "bg_card": "#ffffff",
        "border": "#86efac",
        "text_primary": "#14532d",
        "text_secondary": "#166534",
        "accent": "#16a34a",
        "accent_light": "#bbf7d0",
        "chart_colors": ["#16a34a", "#0d9488", "#2563eb", "#ca8a04", "#dc2626", "#9333ea"],
        "font_family": "'Inter', 'Segoe UI', system-ui, sans-serif",
    },
    "ember": {
        "bg_primary": "#fff7ed",
        "bg_secondary": "#ffedd5",
        "bg_card": "#ffffff",
        "border": "#fed7aa",
        "text_primary": "#431407",
        "text_secondary": "#9a3412",
        "accent": "#ea580c",
        "accent_light": "#ffedd5",
        "chart_colors": ["#ea580c", "#dc2626", "#ca8a04", "#16a34a", "#2563eb", "#9333ea"],
        "font_family": "'Georgia', 'Times New Roman', serif",
    },
    "obsidian": {
        "bg_primary": "#09090b",
        "bg_secondary": "#18181b",
        "bg_card": "#18181b",
        "border": "#27272a",
        "text_primary": "#fafafa",
        "text_secondary": "#a1a1aa",
        "accent": "#a855f7",
        "accent_light": "#3b0764",
        "chart_colors": ["#a855f7", "#ec4899", "#f97316", "#22d3ee", "#4ade80", "#facc15"],
        "font_family": "'JetBrains Mono', 'Fira Code', monospace",
    },
}

CHARTJS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"


# ─── Request dataclass ────────────────────────────────────────────────────────

@dataclass
class ExportBuildRequest:
    dashboard_title: str
    theme: str
    include_chat: bool
    export_token: str  # raw token to embed (plain text)
    api_base: str      # base URL for the agent service
    widgets: list[dict] = field(default_factory=list)
    # Each widget dict should contain:
    #   id, title, widget_type, chart_type, chart_data {rows, columns, labels, values}


# ─── Public entry point ───────────────────────────────────────────────────────

async def build_html_export(request: ExportBuildRequest) -> str:
    """
    Build and return a complete, self-contained HTML string for the dashboard export.
    """
    theme = THEME_CONFIGS.get(request.theme, THEME_CONFIGS["frost"])
    chartjs_src = await _fetch_and_encode_js(CHARTJS_CDN)

    css = _generate_css(theme)
    widgets_html = _generate_widget_html(request.widgets, theme)
    chat_html = _generate_chat_panel(request.include_chat, request.export_token, request.api_base)
    runtime_js = _generate_runtime_js()

    # Bake widget data into JS constant
    widget_data_json = json.dumps(_build_widget_data(request.widgets), ensure_ascii=False)
    export_token_js = json.dumps(request.export_token)
    api_base_js = json.dumps(request.api_base)
    dashboard_title_escaped = _escape_html(request.dashboard_title)

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{dashboard_title_escaped} — Visually Export</title>
  <style>
{css}
  </style>
</head>
<body>

<header class="export-header">
  <div class="header-inner">
    <div class="header-brand">
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect width="28" height="28" rx="6" fill="currentColor" opacity="0.15"/>
        <path d="M6 20L11 13L15 17L19 10L22 14" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span class="brand-name">Visually</span>
    </div>
    <h1 class="header-title">{dashboard_title_escaped}</h1>
    <div class="header-meta">
      <span class="export-badge">Export</span>
    </div>
  </div>
</header>

<div id="filterBar" class="filter-bar" style="display:none;">
  <div class="filter-bar-inner">
    <span class="filter-label">Filtering by: <strong id="filterValue"></strong></span>
    <button class="filter-clear-btn" onclick="clearFilter()">&#x2715; Clear filter</button>
  </div>
</div>

<main class="dashboard-grid" id="dashboardGrid">
{widgets_html}
</main>

{chat_html}

<script>
/* ── Chart.js (inlined) ──────────────────────────────────────────────────── */
{chartjs_src}
</script>

<script>
/* ── Dashboard runtime ───────────────────────────────────────────────────── */
const WIDGET_DATA = {widget_data_json};
const EXPORT_TOKEN = {export_token_js};
const API_BASE = {api_base_js};

{runtime_js}
</script>

</body>
</html>"""

    return html_doc


# ─── CSS generator ────────────────────────────────────────────────────────────

def _generate_css(theme: dict) -> str:
    bg_primary = theme["bg_primary"]
    bg_secondary = theme["bg_secondary"]
    bg_card = theme["bg_card"]
    border = theme["border"]
    text_primary = theme["text_primary"]
    text_secondary = theme["text_secondary"]
    accent = theme["accent"]
    accent_light = theme["accent_light"]
    font_family = theme["font_family"]

    return f"""
    /* ── Reset ── */
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ font-size: 16px; scroll-behavior: smooth; }}
    body {{
      font-family: {font_family};
      background: {bg_primary};
      color: {text_primary};
      min-height: 100vh;
      line-height: 1.5;
    }}

    /* ── Header ── */
    .export-header {{
      background: {bg_card};
      border-bottom: 1px solid {border};
      position: sticky;
      top: 0;
      z-index: 100;
      box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }}
    .header-inner {{
      max-width: 1600px;
      margin: 0 auto;
      padding: 0.75rem 1.5rem;
      display: flex;
      align-items: center;
      gap: 1rem;
    }}
    .header-brand {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      color: {accent};
      flex-shrink: 0;
    }}
    .brand-name {{
      font-weight: 700;
      font-size: 1rem;
      letter-spacing: -0.02em;
    }}
    .header-title {{
      flex: 1;
      font-size: 1.1rem;
      font-weight: 600;
      color: {text_primary};
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .header-meta {{
      flex-shrink: 0;
    }}
    .export-badge {{
      display: inline-block;
      padding: 0.2rem 0.6rem;
      background: {accent_light};
      color: {accent};
      border-radius: 9999px;
      font-size: 0.7rem;
      font-weight: 600;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }}

    /* ── Filter bar ── */
    .filter-bar {{
      background: {accent_light};
      border-bottom: 1px solid {border};
      position: sticky;
      top: 57px;
      z-index: 99;
    }}
    .filter-bar-inner {{
      max-width: 1600px;
      margin: 0 auto;
      padding: 0.6rem 1.5rem;
      display: flex;
      align-items: center;
      gap: 1rem;
    }}
    .filter-label {{
      font-size: 0.85rem;
      color: {text_primary};
    }}
    .filter-clear-btn {{
      background: none;
      border: 1px solid {accent};
      color: {accent};
      border-radius: 6px;
      padding: 0.25rem 0.75rem;
      font-size: 0.8rem;
      cursor: pointer;
      transition: background 0.15s;
    }}
    .filter-clear-btn:hover {{
      background: {accent};
      color: #fff;
    }}

    /* ── Dashboard grid ── */
    .dashboard-grid {{
      max-width: 1600px;
      margin: 0 auto;
      padding: 1.5rem;
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 1rem;
    }}

    /* ── Widget cards ── */
    .widget-card {{
      background: {bg_card};
      border: 1px solid {border};
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      opacity: 0;
      transform: translateY(16px);
      transition: opacity 0.4s ease, transform 0.4s ease, box-shadow 0.2s ease, border-color 0.2s ease;
    }}
    .widget-card.animate-in {{
      opacity: 1;
      transform: translateY(0);
    }}
    .widget-card.dimmed {{
      opacity: 0.3;
      pointer-events: none;
    }}
    .widget-card.highlighted {{
      border-color: {accent};
      box-shadow: 0 0 0 2px {accent}33;
    }}
    @keyframes fadeSlideUp {{
      from {{ opacity: 0; transform: translateY(20px); }}
      to   {{ opacity: 1; transform: translateY(0); }}
    }}

    .widget-header {{
      padding: 0.85rem 1rem 0.5rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }}
    .widget-title {{
      font-size: 0.85rem;
      font-weight: 600;
      color: {text_secondary};
      text-transform: uppercase;
      letter-spacing: 0.04em;
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .widget-body {{
      flex: 1;
      padding: 0.5rem 1rem 1rem;
      display: flex;
      flex-direction: column;
    }}

    /* ── KPI card ── */
    .kpi-value {{
      font-size: 2.5rem;
      font-weight: 800;
      color: {text_primary};
      letter-spacing: -0.04em;
      line-height: 1.1;
    }}
    .kpi-label {{
      font-size: 0.8rem;
      color: {text_secondary};
      margin-top: 0.25rem;
    }}
    .kpi-trend {{
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      font-size: 0.8rem;
      font-weight: 600;
      margin-top: 0.5rem;
      padding: 0.15rem 0.5rem;
      border-radius: 9999px;
    }}
    .kpi-trend.up {{
      background: #dcfce7;
      color: #16a34a;
    }}
    .kpi-trend.down {{
      background: #fee2e2;
      color: #dc2626;
    }}

    /* ── Chart canvas wrapper ── */
    .chart-wrapper {{
      position: relative;
      flex: 1;
      min-height: 220px;
    }}
    .chart-wrapper canvas {{
      display: block;
      width: 100% !important;
      height: 100% !important;
    }}

    /* ── Data table ── */
    .table-wrapper {{
      overflow: auto;
      max-height: 320px;
      border: 1px solid {border};
      border-radius: 8px;
    }}
    .data-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.8rem;
    }}
    .data-table thead th {{
      background: {bg_secondary};
      color: {text_secondary};
      text-align: left;
      padding: 0.5rem 0.75rem;
      font-weight: 600;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      border-bottom: 1px solid {border};
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    .data-table thead th:hover {{
      background: {accent_light};
      color: {accent};
    }}
    .data-table thead th .sort-icon {{
      margin-left: 0.3rem;
      opacity: 0.4;
      font-size: 0.7rem;
    }}
    .data-table thead th.sorted .sort-icon {{
      opacity: 1;
      color: {accent};
    }}
    .data-table tbody tr {{
      border-bottom: 1px solid {border};
      cursor: pointer;
      transition: background 0.12s;
    }}
    .data-table tbody tr:last-child {{
      border-bottom: none;
    }}
    .data-table tbody tr:hover {{
      background: {accent_light};
    }}
    .data-table tbody td {{
      padding: 0.5rem 0.75rem;
      color: {text_primary};
      white-space: nowrap;
    }}

    /* ── Chat panel ── */
    .chat-fab {{
      position: fixed;
      bottom: 1.5rem;
      right: 1.5rem;
      width: 54px;
      height: 54px;
      border-radius: 50%;
      background: {accent};
      color: #fff;
      border: none;
      box-shadow: 0 4px 12px rgba(0,0,0,0.2);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 200;
      transition: transform 0.2s, box-shadow 0.2s;
    }}
    .chat-fab:hover {{
      transform: scale(1.07);
      box-shadow: 0 6px 18px rgba(0,0,0,0.25);
    }}
    .chat-fab svg {{
      width: 24px;
      height: 24px;
    }}

    .chat-drawer {{
      position: fixed;
      bottom: 0;
      right: 0;
      width: 380px;
      max-width: 100vw;
      height: 520px;
      background: {bg_card};
      border: 1px solid {border};
      border-radius: 16px 16px 0 0;
      box-shadow: 0 -4px 24px rgba(0,0,0,0.15);
      display: flex;
      flex-direction: column;
      z-index: 201;
      transform: translateY(100%);
      transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }}
    .chat-drawer.open {{
      transform: translateY(0);
    }}
    .chat-drawer-header {{
      padding: 0.85rem 1rem;
      border-bottom: 1px solid {border};
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }}
    .chat-drawer-title {{
      flex: 1;
      font-weight: 600;
      font-size: 0.9rem;
      color: {text_primary};
    }}
    .chat-drawer-close {{
      background: none;
      border: none;
      cursor: pointer;
      color: {text_secondary};
      font-size: 1.1rem;
      line-height: 1;
      padding: 0.25rem;
      border-radius: 4px;
      transition: color 0.15s;
    }}
    .chat-drawer-close:hover {{
      color: {text_primary};
    }}
    .chat-messages {{
      flex: 1;
      overflow-y: auto;
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }}
    .chat-msg {{
      max-width: 85%;
      padding: 0.6rem 0.85rem;
      border-radius: 12px;
      font-size: 0.85rem;
      line-height: 1.45;
      word-break: break-word;
    }}
    .chat-msg.user {{
      align-self: flex-end;
      background: {accent};
      color: #fff;
      border-bottom-right-radius: 4px;
    }}
    .chat-msg.assistant {{
      align-self: flex-start;
      background: {bg_secondary};
      color: {text_primary};
      border-bottom-left-radius: 4px;
    }}
    .chat-msg.system {{
      align-self: center;
      background: transparent;
      color: {text_secondary};
      font-size: 0.75rem;
      font-style: italic;
    }}
    .chat-msg.loading {{
      align-self: flex-start;
      background: {bg_secondary};
      color: {text_secondary};
      font-style: italic;
    }}
    .chat-input-row {{
      padding: 0.75rem 1rem;
      border-top: 1px solid {border};
      display: flex;
      gap: 0.5rem;
      align-items: flex-end;
    }}
    .chat-input {{
      flex: 1;
      padding: 0.6rem 0.85rem;
      border: 1px solid {border};
      border-radius: 8px;
      font-size: 0.85rem;
      font-family: inherit;
      background: {bg_secondary};
      color: {text_primary};
      resize: none;
      outline: none;
      min-height: 38px;
      max-height: 120px;
      transition: border-color 0.15s;
    }}
    .chat-input:focus {{
      border-color: {accent};
    }}
    .chat-send-btn {{
      background: {accent};
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 0.6rem 1rem;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.15s;
      flex-shrink: 0;
    }}
    .chat-send-btn:hover {{
      opacity: 0.88;
    }}
    .chat-send-btn:disabled {{
      opacity: 0.5;
      cursor: not-allowed;
    }}

    /* ── Responsive ── */
    @media (max-width: 1024px) {{
      .dashboard-grid {{
        grid-template-columns: repeat(6, 1fr);
      }}
    }}
    @media (max-width: 640px) {{
      .dashboard-grid {{
        grid-template-columns: 1fr;
        padding: 0.75rem;
      }}
      .widget-card {{
        grid-column: 1 / -1 !important;
      }}
      .chat-drawer {{
        width: 100vw;
        height: 70vh;
        border-radius: 16px 16px 0 0;
      }}
    }}
    """


# ─── Widget HTML generator ────────────────────────────────────────────────────

def _generate_widget_html(widgets: list[dict], theme: dict) -> str:
    parts: list[str] = []

    for idx, widget in enumerate(widgets):
        title = _escape_html(widget.get("title", f"Widget {idx + 1}"))
        widget_type = widget.get("widget_type", "chart")
        chart_type = widget.get("chart_type", "bar_vertical")
        chart_data = widget.get("chart_data") or {}
        rows = chart_data.get("rows", [])
        columns = chart_data.get("columns", [])

        # Grid positioning
        pos_x = widget.get("position_x", 0)
        width = widget.get("width", 6)
        pos_y = widget.get("position_y", idx * 4)
        col_start = (pos_x % 12) + 1
        col_span = min(max(width, 2), 12)

        style = f"grid-column: {col_start} / span {col_span};"

        if widget_type == "kpi_card" or chart_type == "kpi_card":
            body_html = _render_kpi_card(rows, columns)
        elif widget_type == "table" or chart_type == "table":
            widget_id = _escape_html(str(widget.get("id", f"w{idx}")))
            body_html = _render_table_widget(rows, columns, widget_id)
        else:
            # Chart canvas
            body_html = (
                f'<div class="chart-wrapper">'
                f'<canvas id="chart-{idx}" data-widget-index="{idx}" data-chart-type="{_escape_html(chart_type)}"></canvas>'
                f'</div>'
            )

        parts.append(
            f'<div class="widget-card" style="{style}" data-widget-index="{idx}">'
            f'  <div class="widget-header"><span class="widget-title">{title}</span></div>'
            f'  <div class="widget-body">{body_html}</div>'
            f'</div>'
        )

    return "\n".join(parts)


def _render_kpi_card(rows: list[dict], columns: list[str]) -> str:
    if not rows or not columns:
        return '<div class="kpi-value">—</div>'
    value = rows[0].get(columns[0]) if columns else None
    label = columns[0] if columns else ""
    formatted = _format_kpi_value(value)
    label_html = f'<div class="kpi-label">{_escape_html(label)}</div>'
    return f'<div class="kpi-value">{formatted}</div>{label_html}'


def _render_table_widget(rows: list[dict], columns: list[str], widget_id: str) -> str:
    if not columns:
        return '<p style="color: var(--text-secondary); font-size: 0.8rem;">No data</p>'

    th_cells = "".join(
        f'<th onclick="sortTable(\'{widget_id}\', {i})">'
        f'{_escape_html(col)}<span class="sort-icon">&#8597;</span>'
        f'</th>'
        for i, col in enumerate(columns)
    )
    tbody_rows = ""
    for row in rows[:500]:
        cells = "".join(
            f'<td>{_escape_html(str(row.get(col, "")))}</td>'
            for col in columns
        )
        row_json = _escape_html(json.dumps({c: str(row.get(c, "")) for c in columns[:2]}))
        tbody_rows += f'<tr onclick="filterFromTable(\'{row_json}\')">{cells}</tr>'

    return (
        f'<div class="table-wrapper" id="table-{widget_id}">'
        f'<table class="data-table" data-widget-id="{widget_id}">'
        f'<thead><tr>{th_cells}</tr></thead>'
        f'<tbody>{tbody_rows}</tbody>'
        f'</table></div>'
    )


# ─── Chat panel ──────────────────────────────────────────────────────────────

def _generate_chat_panel(include_chat: bool, export_token: str, api_base: str) -> str:
    if not include_chat or not export_token:
        return ""

    return """
<button class="chat-fab" id="chatFab" onclick="toggleChat()" title="Ask AI about this dashboard" aria-label="Open AI chat">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
  </svg>
</button>

<div class="chat-drawer" id="chatDrawer" role="dialog" aria-label="AI chat panel">
  <div class="chat-drawer-header">
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="10"/>
      <path d="M12 16v-4M12 8h.01"/>
    </svg>
    <span class="chat-drawer-title">Ask about this dashboard</span>
    <button class="chat-drawer-close" onclick="toggleChat()" aria-label="Close chat">&times;</button>
  </div>
  <div class="chat-messages" id="chatMessages">
    <div class="chat-msg system">Ask any question about the data in this dashboard.</div>
  </div>
  <div class="chat-input-row">
    <textarea
      id="chatInput"
      class="chat-input"
      placeholder="Ask a question..."
      rows="1"
      onkeydown="handleChatKey(event)"
    ></textarea>
    <button id="chatSendBtn" class="chat-send-btn" onclick="sendChatMessage()">Send</button>
  </div>
</div>
"""


# ─── Runtime JavaScript ───────────────────────────────────────────────────────

def _generate_runtime_js() -> str:
    return r"""
// ── State ──────────────────────────────────────────────────────────────────
let _activeFilter = null;
let _activeFilterSource = null;
let _sortState = {};
let _chatOpen = false;
let _chatSession = [];
let _chatPending = false;

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  renderAllCharts();
  animateWidgets();
});

// ── Chart rendering ────────────────────────────────────────────────────────
function renderAllCharts() {
  const canvases = document.querySelectorAll('canvas[data-widget-index]');
  canvases.forEach(canvas => {
    const widgetIndex = parseInt(canvas.getAttribute('data-widget-index'), 10);
    const chartType = canvas.getAttribute('data-chart-type') || 'bar_vertical';
    const widget = WIDGET_DATA[widgetIndex];
    if (!widget) return;

    const { rows, columns } = widget.chart_data || {};
    if (!rows || !columns || rows.length === 0 || columns.length === 0) return;

    renderChart(canvas, chartType, rows, columns, widget, widgetIndex);
  });
}

function renderChart(canvas, type, rows, columns, widget, widgetIndex) {
  const xCol = columns[0];
  const yCol = columns[1] || columns[0];

  const labels = rows.map(r => String(r[xCol] ?? ''));
  const rawValues = rows.map(r => {
    const v = r[yCol];
    return (v === null || v === undefined) ? 0 : (typeof v === 'number' ? v : parseFloat(v) || 0);
  });

  const themeColors = (typeof WIDGET_DATA !== 'undefined' && WIDGET_DATA._theme_colors)
    ? WIDGET_DATA._theme_colors
    : ['#3b82f6', '#06b6d4', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444'];

  const opts = chartOptions(widget, type, widgetIndex);
  let config = null;

  if (type === 'pie' || type === 'donut') {
    config = {
      type: type === 'donut' ? 'doughnut' : 'pie',
      data: {
        labels,
        datasets: [{
          data: rawValues,
          backgroundColor: themeColors,
          borderWidth: 2,
          borderColor: 'transparent',
        }],
      },
      options: opts,
    };
  } else if (type === 'line' || type === 'area') {
    config = {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: widget.title || yCol,
          data: rawValues,
          borderColor: themeColors[0],
          backgroundColor: type === 'area' ? hexToRgba(themeColors[0], 0.15) : 'transparent',
          fill: type === 'area',
          tension: 0.4,
          pointRadius: 3,
          pointHoverRadius: 5,
        }],
      },
      options: opts,
    };
  } else if (type === 'bar_horizontal') {
    config = {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: widget.title || yCol,
          data: rawValues,
          backgroundColor: themeColors[0],
          borderRadius: 4,
        }],
      },
      options: { ...opts, indexAxis: 'y' },
    };
  } else if (type === 'scatter') {
    const scatterData = rows.map(r => ({
      x: parseFloat(r[xCol]) || 0,
      y: parseFloat(r[yCol]) || 0,
    }));
    config = {
      type: 'scatter',
      data: {
        datasets: [{
          label: widget.title || `${xCol} vs ${yCol}`,
          data: scatterData,
          backgroundColor: hexToRgba(themeColors[0], 0.7),
          pointRadius: 5,
        }],
      },
      options: opts,
    };
  } else if (type === 'funnel') {
    // Render funnel as horizontal bar sorted descending
    const sortedPairs = rows
      .map(r => ({ label: String(r[xCol] ?? ''), value: parseFloat(r[yCol]) || 0 }))
      .sort((a, b) => b.value - a.value);
    config = {
      type: 'bar',
      data: {
        labels: sortedPairs.map(p => p.label),
        datasets: [{
          label: widget.title || yCol,
          data: sortedPairs.map(p => p.value),
          backgroundColor: themeColors.slice(0, sortedPairs.length),
          borderRadius: 4,
        }],
      },
      options: { ...opts, indexAxis: 'y' },
    };
  } else {
    // Default: bar_vertical
    config = {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: widget.title || yCol,
          data: rawValues,
          backgroundColor: themeColors[0],
          borderRadius: 4,
          hoverBackgroundColor: themeColors[1] || themeColors[0],
        }],
      },
      options: opts,
    };
  }

  if (config) {
    new Chart(canvas, config);
  }
}

function chartOptions(widget, chartType, widgetIndex) {
  const isResponsive = true;
  const xLabel = widget.x_axis_label || '';
  const yLabel = widget.y_axis_label || '';

  const baseOpts = {
    responsive: isResponsive,
    maintainAspectRatio: false,
    animation: { duration: 600, easing: 'easeOutQuart' },
    plugins: {
      legend: {
        display: ['pie', 'donut'].includes(chartType),
        position: 'bottom',
        labels: { padding: 16, usePointStyle: true, font: { size: 11 } },
      },
      tooltip: {
        callbacks: {
          label: ctx => {
            const v = ctx.parsed.y ?? ctx.parsed ?? 0;
            return ` ${formatNumber(typeof v === 'object' ? (v.y ?? 0) : v)}`;
          },
        },
      },
    },
    onClick: (event, elements) => {
      if (!elements.length) return;
      const chart = event.chart;
      const idx = elements[0].index;
      const labels = chart.data.labels || [];
      handleChartClick(event, elements, labels, widgetIndex);
    },
  };

  if (['pie', 'donut'].includes(chartType)) {
    return baseOpts;
  }

  return {
    ...baseOpts,
    scales: {
      x: {
        title: xLabel ? { display: true, text: xLabel, font: { size: 11 } } : undefined,
        ticks: { maxTicksLimit: 10, font: { size: 10 } },
        grid: { display: false },
      },
      y: {
        title: yLabel ? { display: true, text: yLabel, font: { size: 11 } } : undefined,
        ticks: {
          font: { size: 10 },
          callback: v => formatNumber(v),
        },
        grid: { color: 'rgba(128,128,128,0.08)' },
        beginAtZero: true,
      },
    },
  };
}

function handleChartClick(event, elements, labels, widgetIndex) {
  if (!elements.length) return;
  const label = labels[elements[0].index];
  if (label !== undefined && label !== null) {
    applyFilter(String(label), widgetIndex);
  }
}

// ── Filtering ──────────────────────────────────────────────────────────────
function filterFromTable(rowJson) {
  try {
    const row = JSON.parse(rowJson);
    const keys = Object.keys(row);
    if (keys.length > 0) {
      applyFilter(row[keys[0]], -1);
    }
  } catch (_) {}
}

function applyFilter(value, sourceWidgetIndex) {
  _activeFilter = value;
  _activeFilterSource = sourceWidgetIndex;

  const filterBar = document.getElementById('filterBar');
  const filterValueEl = document.getElementById('filterValue');
  if (filterBar) filterBar.style.display = 'block';
  if (filterValueEl) filterValueEl.textContent = value;

  const cards = document.querySelectorAll('.widget-card');
  cards.forEach(card => {
    const idx = parseInt(card.getAttribute('data-widget-index'), 10);
    if (idx === sourceWidgetIndex) {
      card.classList.remove('dimmed');
      card.classList.add('highlighted');
    } else {
      // Check if this widget has the filter value in its data
      const widget = WIDGET_DATA[idx];
      if (widget && _widgetContainsValue(widget, value)) {
        card.classList.remove('dimmed');
        card.classList.add('highlighted');
      } else {
        card.classList.remove('highlighted');
        card.classList.add('dimmed');
      }
    }
  });
}

function _widgetContainsValue(widget, value) {
  const { rows, columns } = (widget.chart_data || {});
  if (!rows || !columns) return false;
  const lower = String(value).toLowerCase();
  return rows.some(row =>
    columns.some(col => String(row[col] ?? '').toLowerCase().includes(lower))
  );
}

function clearFilter() {
  _activeFilter = null;
  _activeFilterSource = null;
  const filterBar = document.getElementById('filterBar');
  if (filterBar) filterBar.style.display = 'none';
  document.querySelectorAll('.widget-card').forEach(card => {
    card.classList.remove('dimmed', 'highlighted');
  });
}

// ── Table sorting ──────────────────────────────────────────────────────────
function sortTable(widgetId, colIndex) {
  const table = document.querySelector(`.data-table[data-widget-id="${widgetId}"]`);
  if (!table) return;

  const key = `${widgetId}-${colIndex}`;
  const currentDir = _sortState[key] || 'none';
  const nextDir = currentDir === 'asc' ? 'desc' : 'asc';
  _sortState[key] = nextDir;

  // Update header icon
  table.querySelectorAll('thead th').forEach((th, i) => {
    th.classList.toggle('sorted', i === colIndex);
    const icon = th.querySelector('.sort-icon');
    if (icon) icon.textContent = i === colIndex ? (nextDir === 'asc' ? '↑' : '↓') : '↕';
  });

  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  rows.sort((a, b) => {
    const aText = a.cells[colIndex]?.textContent?.trim() || '';
    const bText = b.cells[colIndex]?.textContent?.trim() || '';
    const aNum = parseFloat(aText);
    const bNum = parseFloat(bText);
    if (!isNaN(aNum) && !isNaN(bNum)) {
      return nextDir === 'asc' ? aNum - bNum : bNum - aNum;
    }
    return nextDir === 'asc'
      ? aText.localeCompare(bText)
      : bText.localeCompare(aText);
  });
  rows.forEach(row => tbody.appendChild(row));
}

// ── Chat panel ─────────────────────────────────────────────────────────────
function toggleChat() {
  _chatOpen = !_chatOpen;
  const drawer = document.getElementById('chatDrawer');
  if (drawer) drawer.classList.toggle('open', _chatOpen);
  if (_chatOpen) {
    const input = document.getElementById('chatInput');
    if (input) setTimeout(() => input.focus(), 300);
  }
}

function handleChatKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendChatMessage();
  }
}

async function sendChatMessage() {
  if (_chatPending) return;
  const inputEl = document.getElementById('chatInput');
  const sendBtn = document.getElementById('chatSendBtn');
  if (!inputEl) return;
  const text = inputEl.value.trim();
  if (!text) return;

  inputEl.value = '';
  inputEl.style.height = 'auto';
  appendMessage(text, 'user');

  const loadingId = 'msg-loading-' + Date.now();
  appendMessage('Thinking...', 'loading', loadingId);

  _chatPending = true;
  if (sendBtn) sendBtn.disabled = true;

  try {
    const resp = await fetch(API_BASE + '/agent/export-chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + EXPORT_TOKEN,
      },
      body: JSON.stringify({ message: text, history: _chatSession }),
    });

    const loadingEl = document.getElementById(loadingId);
    if (loadingEl) loadingEl.remove();

    if (!resp.ok) {
      const errBody = await resp.text();
      appendMessage(`Sorry, I encountered an error (${resp.status}). Please try again.`, 'assistant');
    } else {
      const data = await resp.json();
      const reply = data.text || data.message || 'No response.';
      appendMessage(reply, 'assistant');
      _chatSession = _chatSession.concat(
        { role: 'user', content: text },
        { role: 'assistant', content: reply }
      );
      if (_chatSession.length > 40) _chatSession = _chatSession.slice(-40);
    }
  } catch (err) {
    const loadingEl = document.getElementById(loadingId);
    if (loadingEl) loadingEl.remove();
    appendMessage('Network error. Please check your connection.', 'assistant');
  } finally {
    _chatPending = false;
    if (sendBtn) sendBtn.disabled = false;
  }
}

function appendMessage(text, role, id) {
  const messagesEl = document.getElementById('chatMessages');
  if (!messagesEl) return;

  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;
  if (id) div.id = id;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ── Helpers ────────────────────────────────────────────────────────────────
function hexToRgba(hex, alpha) {
  const cleaned = hex.replace('#', '');
  const r = parseInt(cleaned.substring(0, 2), 16);
  const g = parseInt(cleaned.substring(2, 4), 16);
  const b = parseInt(cleaned.substring(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function formatNumber(n) {
  const num = typeof n === 'number' ? n : parseFloat(n);
  if (isNaN(num)) return String(n ?? '');
  if (Math.abs(num) >= 1e9) return (num / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
  if (Math.abs(num) >= 1e6) return (num / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (Math.abs(num) >= 1e3) return (num / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
  if (Number.isInteger(num)) return num.toLocaleString();
  return num.toFixed(2).replace(/\.?0+$/, '');
}

function animateWidgets() {
  const cards = document.querySelectorAll('.widget-card');
  cards.forEach((card, i) => {
    setTimeout(() => {
      card.classList.add('animate-in');
    }, i * 60);
  });
}
"""


# ─── Data builder ─────────────────────────────────────────────────────────────

def _build_widget_data(widgets: list[dict]) -> dict:
    """Serialize widgets into a plain JS-embeddable structure."""
    result: dict[str, Any] = {}
    for idx, widget in enumerate(widgets):
        chart_data = widget.get("chart_data") or {}
        result[idx] = {
            "id": str(widget.get("id", "")),
            "title": widget.get("title", ""),
            "widget_type": widget.get("widget_type", "chart"),
            "chart_type": widget.get("chart_type", "bar_vertical"),
            "x_axis_label": widget.get("x_axis_label", ""),
            "y_axis_label": widget.get("y_axis_label", ""),
            "chart_data": {
                "rows": chart_data.get("rows", [])[:500],
                "columns": chart_data.get("columns", []),
                "labels": chart_data.get("labels", [])[:200],
                "values": chart_data.get("values", [])[:200],
            },
        }
    return result


# ─── Fetch JS from CDN ────────────────────────────────────────────────────────

async def _fetch_and_encode_js(url: str) -> str:
    """Fetch a JavaScript file from a URL and return its source text."""
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
    except Exception:
        pass
    return ""


# ─── Value formatters ─────────────────────────────────────────────────────────

def _format_kpi_value(value: Any) -> str:
    if value is None:
        return "—"
    try:
        num = float(value)
        if abs(num) >= 1e9:
            return f"{num / 1e9:.1f}B".rstrip("0").rstrip(".")
        if abs(num) >= 1e6:
            return f"{num / 1e6:.1f}M".rstrip("0").rstrip(".")
        if abs(num) >= 1e3:
            return f"{num / 1e3:.1f}K".rstrip("0").rstrip(".")
        if isinstance(value, float):
            return f"{num:,.2f}"
        return f"{int(num):,}"
    except (ValueError, TypeError):
        return _escape_html(str(value))


def _escape_html(s: str) -> str:
    if not isinstance(s, str):
        s = str(s) if s is not None else ""
    return html.escape(s, quote=True)
