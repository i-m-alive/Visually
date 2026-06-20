"""
intelligence_orchestrator.py — Parallel multi-agent intelligence report generation

Replaces the single monolithic Opus call (/intelligence/analyze) with a
map-reduce orchestration that is both FASTER and HIGHER QUALITY:

    POST /intelligence/orchestrate

    ┌─────────────┐   ┌──────────────────────────┐   ┌──────────────┐   ┌──────────┐
    │  PLANNER    │ → │  WRITERS  (fan-out, ‖)    │ → │  CRITIC  (‖) │ → │ REDUCER  │
    │  1 small    │   │  1 call per section,      │   │ validate +   │   │ globals: │
    │  call,      │   │  run concurrently. Each   │   │ 1 bounded    │   │ KPIs,    │
    │  decides    │   │  writes ONE focused       │   │ re-write per │   │ brief,   │
    │  sections   │   │  section's JSON           │   │ failed sec   │   │ title    │
    └─────────────┘   └──────────────────────────┘   └──────────────┘   └──────────┘

Why this is faster: output-token generation is sequential, so one 8K-token
monolith is the latency floor. N parallel writers each emit ~1.5-2K tokens, so
wall-clock ≈ the slowest single section, not the sum. Truncation disappears.

Why it is higher quality: the planner assigns each section a distinct angle (no
two sections tell the same story), each writer focuses on one chapter, and a
dedicated critic enforces the report rules (exact values, no repeated figures,
no data-quality talk, valid icons/chart types) with one bounded re-write.

Cost is held down by a Bedrock prompt-cache breakpoint: the large shared context
(schema + every widget's pre-computed stats + correlations) is cached once and
re-read by every writer and critic call, so fan-out does not N× the input cost.

The endpoint returns the SAME JSON shape the frontend already parses from
/intelligence/analyze ({ "text": "<raw json>" }), so parseResponse /
sanitizeAnalysis / the deterministic injection passes are unchanged.
"""

import asyncio
import hashlib
import json
import os
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.redis_client import get_redis
from shared.bedrock_client import (
    BEDROCK_SONNET_MODEL,
    BEDROCK_OPUS_MODEL,
    _BEDROCK_EXECUTOR,
    get_bedrock_client,
    _track_usage,
)

router = APIRouter(tags=["intelligence"])

# ─── Model routing (env-overridable) ─────────────────────────────────────────
# Writers do the heavy creative lifting → Opus by default for quality. Planner /
# critic / reducer are short structural calls → Sonnet for speed + cost.
_PLANNER_MODEL = os.getenv("INTELLIGENCE_PLANNER_MODEL", BEDROCK_SONNET_MODEL)
_WRITER_MODEL  = os.getenv("INTELLIGENCE_WRITER_MODEL",  BEDROCK_OPUS_MODEL)
_CRITIC_MODEL  = os.getenv("INTELLIGENCE_CRITIC_MODEL",  BEDROCK_SONNET_MODEL)
_REDUCER_MODEL = os.getenv("INTELLIGENCE_REDUCER_MODEL", BEDROCK_SONNET_MODEL)

# Bounded fan-out — the Bedrock executor has 24 threads; cap concurrent section
# calls so a 6-section report doesn't starve other in-flight requests.
_ORCH_CONCURRENCY = int(os.getenv("INTELLIGENCE_ORCH_CONCURRENCY", "6"))

# Per-call output budgets. One section is small, so 6K is generous headroom.
_PLAN_MAX_TOKENS   = int(os.getenv("INTELLIGENCE_PLAN_MAX_TOKENS",   "2048"))
_WRITER_MAX_TOKENS = int(os.getenv("INTELLIGENCE_WRITER_MAX_TOKENS", "6144"))
_CRITIC_MAX_TOKENS = int(os.getenv("INTELLIGENCE_CRITIC_MAX_TOKENS", "1024"))
_REDUCER_MAX_TOKENS = int(os.getenv("INTELLIGENCE_REDUCER_MAX_TOKENS", "3072"))

_MIN_SECTIONS = 4
_MAX_SECTIONS = 6

_ORCH_CACHE_TTL = int(os.getenv("INTELLIGENCE_ORCH_CACHE_TTL", "1800"))  # 30 min
_ORCH_LOCK_TTL  = int(os.getenv("INTELLIGENCE_ORCH_LOCK_TTL",  "180"))
_ORCH_LOCK_WAIT = int(os.getenv("INTELLIGENCE_ORCH_LOCK_WAIT", "300"))
_ORCH_LOCK_POLL = float(os.getenv("INTELLIGENCE_ORCH_LOCK_POLL", "2.0"))

VALID_ICON_NAMES = (
    "overview | trending_up | trending_down | users | bar_chart | pie_chart | "
    "lightbulb | target | activity | dollar_sign | shopping_cart | building | "
    "globe | database | cpu | settings | calendar | clock | file_text | filter | "
    "map_pin | percent | shield | award | check_circle | alert_triangle | arrow_up | "
    "arrow_down | refresh | layers | package | briefcase | heart_pulse | line_chart | zap"
)
VALID_CHART_TYPES = (
    "bar | line | area | pie | donut | combo | waterfall | scatter | bullet | "
    "treemap | funnel | radar | table"
)

_SECTION_JSON_SHAPE = """{
  "id": "sec_1",
  "label": "<short section title>",
  "icon": "<ONE icon from the icon list>",
  "data_story": "<bold headline with ONE specific number>",
  "key_finding": "<1 sentence with an exact metric NOT used in data_story>",
  "narrative": "<2-3 sentences of ADDITIONAL breakdown detail — new numbers only, must NOT repeat data_story/key_finding>",
  "recommendation": "<one concrete, measurable action>",
  "insights": [{"icon":"<icon>","headline":"...","detail":"<specific stat>","type":"positive|negative|neutral|warning","confidence":4}],
  "top_performers": [{"label":"...","value":0,"formatted_value":"...","pct_of_total":0,"rank":1}],
  "bottom_performers": [{"label":"...","value":0,"formatted_value":"...","rank":1}],
  "kpis": [{"label":"...","value":"...","trend":"up|down|neutral","trend_pct":"+X%","sparkline_data":[],"explanation":"<1 sentence: what this KPI measures and why it matters>"}],
  "charts": [{"title":"...","type":"__CHART_TYPES__","insight":"<1-2 sentence finding specific to this chart's data>","data":[{"name":"...","value":0}],"series":[{"key":"...","type":"bar|line"}],"target_value":0,"x_key":"...","y_key":"..."}]
}""".replace("__CHART_TYPES__", VALID_CHART_TYPES)


# ═══════════════════════════════════════════════════════════════════════════════
# Bedrock invoke — JSON-prefilled, prompt-cached shared context
# ═══════════════════════════════════════════════════════════════════════════════

def _system_blocks(base_instruction: str, shared_context: str) -> list:
    """System as content blocks with a cache breakpoint on the large shared
    context, so all writer/critic calls re-read the cached prefix (~5 min TTL)."""
    blocks = [{"type": "text", "text": base_instruction}]
    if shared_context:
        blocks.append({
            "type": "text",
            "text": shared_context,
            "cache_control": {"type": "ephemeral"},
        })
    return blocks


async def _invoke_json(
    model_id: str,
    base_instruction: str,
    shared_context: str,
    user_message: str,
    max_tokens: int,
    temperature: float = 0.3,
    label: str = "orch",
) -> str:
    """Invoke Bedrock and return raw JSON text. Uses an assistant prefill of "{"
    to force pure-JSON output, and a cache breakpoint on shared_context."""
    def _invoke():
        client = get_bedrock_client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": _system_blocks(base_instruction, shared_context),
            "messages": [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": "{"},
            ],
        }
        t0 = time.time()
        response = client.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        _track_usage(model_id, result)
        content = result.get("content") or []
        raw = "{" + (content[0].get("text", "") if content else "")
        usage = result.get("usage", {})
        print(
            f"[intel-orch:{label}] ← {time.time()-t0:.1f}s  model={model_id.split('.')[-1][:24]}"
            f"  stop={result.get('stop_reason','?')}"
            f"  in={usage.get('input_tokens','?')}  out={usage.get('output_tokens','?')}"
            f"  cache_r={usage.get('cache_read_input_tokens',0)}  cache_w={usage.get('cache_creation_input_tokens',0)}",
            flush=True,
        )
        return raw

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_BEDROCK_EXECUTOR, _invoke)


# ═══════════════════════════════════════════════════════════════════════════════
# JSON extraction (mirrors the frontend's tolerant parser)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_json(text: str):
    """Parse the first valid balanced JSON object/array from model output."""
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"^```(?:json)?\n?|```$", "", s, flags=re.MULTILINE).strip()
    # direct attempt (with trailing-comma cleanup)
    for candidate in (s, re.sub(r",(\s*[}\]])", r"\1", s)):
        try:
            return json.loads(candidate)
        except Exception:
            pass
    # balanced-brace scan, longest first
    objs: list[str] = []
    for i, ch in enumerate(s):
        if ch != "{":
            continue
        depth, in_str, esc = 0, False, False
        for j in range(i, len(s)):
            c = s[j]
            if esc:
                esc = False
                continue
            if c == "\\" and in_str:
                esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    objs.append(s[i:j + 1])
                    break
    for cand in sorted(objs, key=len, reverse=True):
        try:
            return json.loads(re.sub(r",(\s*[}\]])", r"\1", cand))
        except Exception:
            continue
    return None


_WIDGET_TITLE_RE = re.compile(r'WIDGET:\s*"(.*?)"')


def _widget_titles(widget_blocks: list[str]) -> list[str]:
    titles: list[str] = []
    for b in widget_blocks:
        m = _WIDGET_TITLE_RE.search(b or "")
        titles.append(m.group(1) if m else (b or "")[:40])
    return titles


def _summaries(widget_blocks: list[str]) -> str:
    """Compact one-line-per-widget summaries for the planner (title + narrative)."""
    lines: list[str] = []
    for i, b in enumerate(widget_blocks):
        head = (b or "").splitlines()
        title = head[0] if head else f"Widget {i+1}"
        narrative = next((l for l in head if l.startswith("NARRATIVE:")), "")
        lines.append(f"[{i}] {title}  {narrative[:160]}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Shared context (cached prefix) + the four stage prompts
# ═══════════════════════════════════════════════════════════════════════════════

def _build_shared_context(schema_block: str, correlations: list[str], widget_blocks: list[str]) -> str:
    corr = "\n".join(f"- {c}" for c in (correlations or [])) or "None detected"
    widgets = "\n\n---\n\n".join(widget_blocks)
    return f"""{schema_block or ''}

CROSS-WIDGET CORRELATIONS:
{corr}

━━━ WIDGET DATA (pre-computed statistical facts; build every number from this) ━━━
{widgets}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


# ── STAGE 1: PLANNER ──────────────────────────────────────────────────────────
async def _plan(canvas_name: str, widget_blocks: list[str]) -> list[dict]:
    titles = _widget_titles(widget_blocks)
    instruction = (
        "You are the lead editor planning an executive intelligence report. "
        "Given short summaries of every data widget, design the report's section "
        "structure. Each section is ONE distinct business story (e.g. Revenue "
        "Performance, Customer Trends, Regional Breakdown, Forecast). Assign each "
        "widget index to the single section where it best belongs. Give each "
        f"section a DISTINCT angle so no two overlap. Create {_MIN_SECTIONS}-{_MAX_SECTIONS} sections.\n\n"
        "Output ONLY raw JSON: "
        '{"sections":[{"label":"...","icon":"<one icon>","angle":"<one line on this section\'s unique focus>","widget_indices":[0,2,5]}]}\n'
        f"ICON LIST: {VALID_ICON_NAMES}"
    )
    user = f'REPORT: "{canvas_name}"\n\nWIDGET SUMMARIES:\n{_summaries(widget_blocks)}'
    try:
        raw = await _invoke_json(_PLANNER_MODEL, instruction, "", user, _PLAN_MAX_TOKENS, 0.4, "plan")
        parsed = _extract_json(raw) or {}
        secs = parsed.get("sections") or []
    except Exception as exc:
        print(f"[intel-orch:plan] failed ({exc}); using fallback grouping", flush=True)
        secs = []

    clean: list[dict] = []
    n = len(widget_blocks)
    for i, s in enumerate(secs[:_MAX_SECTIONS]):
        idxs = [int(x) for x in (s.get("widget_indices") or []) if isinstance(x, (int, float)) and 0 <= int(x) < n]
        clean.append({
            "id": f"sec_{i+1}",
            "label": str(s.get("label") or f"Section {i+1}")[:80],
            "icon": str(s.get("icon") or "bar_chart"),
            "angle": str(s.get("angle") or "")[:300],
            "widget_indices": idxs,
            "titles": [titles[j] for j in idxs],
        })

    # Fallback: deterministic grouping if the planner produced nothing usable.
    if not clean:
        per = max(1, (n + _MIN_SECTIONS - 1) // _MIN_SECTIONS)
        for i in range(0, n, per):
            idxs = list(range(i, min(i + per, n)))
            clean.append({
                "id": f"sec_{len(clean)+1}",
                "label": f"Analysis {len(clean)+1}",
                "icon": "bar_chart",
                "angle": "",
                "widget_indices": idxs,
                "titles": [titles[j] for j in idxs],
            })
            if len(clean) >= _MAX_SECTIONS:
                break
    return clean


# ── STAGE 2: WRITER ───────────────────────────────────────────────────────────
_WRITER_INSTRUCTION = f"""You are a senior executive intelligence analyst writing ONE section of a business report.

The shared context below contains the full database schema, cross-widget correlations, and pre-computed statistical facts for EVERY widget (trends, forecasts, profiles, top/bottom performers, dimension breakdowns). Build every number you cite from that data — never invent figures.

FOCUS: produce BUSINESS insights (revenue, customers, trends, segments, performance, risk, opportunity). Do NOT discuss data quality, null rates, completeness, or data hygiene anywhere.

NUMBER FORMAT (follow exactly — these rules are how your draft is judged):
- Cite figures from the widget facts' "EXACT VALUES", "TOP PERFORMERS (exact)" and "BREAKDOWN" lines — these are the precise source numbers. Copy them VERBATIM, keeping their scale. Do NOT rescale or convert units: if a value is 30.26 on a "revenue (millions)" widget, write "30.26 million" (or "$30.26M") — NEVER "$30,260,000".
- Do NOT recompute or derive new numbers (no summing, no ratios/percentages you calculate yourself). Use values as given.
- Do NOT fabricate or rescale chart points (e.g. never turn 4.52 into 452). Chart "data" values, "value", "target_value", "max_value", "sparkline_data" use the EXACT source numbers (decimals allowed, no thousand separators).
- Avoid VAGUE words entirely: never "about", "roughly", "~", "approximately", "nearly", "over X". State the exact figure.
- Prose may use thousands separators ("4,200,000") and percentages in any form ("18%", "43.6%").
- data_story, key_finding and narrative must each feature a DIFFERENT headline figure — do not repeat the same number across these THREE prose fields. (Reusing a number in top_performers / kpis / charts is fine.)

RULES:
- Build 2-3 charts from the real row data; pick the BEST type per data shape. Max 20 data points per chart.
- For a table chart, preserve ALL meaningful column names as keys (e.g. {{"customer":"Acme","revenue":120000,"region":"West"}}), up to 50 rows.
- top_performers / bottom_performers: 3 rows each from the TOP/BOTTOM PERFORMERS facts.
- SPARKLINES: set "sparkline_data" ONLY for genuine TIME-SERIES KPIs (values ordered over time, from a time-series widget). For ranked/top-N, categorical, or single-point metrics, use "sparkline_data": [] — NEVER put rankings or category counts in a sparkline (a sparkline implies a trend over time). Never invent a data label like "Latest" or a percentage that is not in the widget facts.
- Use ONLY icons from: {VALID_ICON_NAMES}

Output ONLY raw JSON for this single section — no markdown, no prose:
{_SECTION_JSON_SHAPE}"""


async def _write_section(canvas_name: str, shared_context: str, sec: dict, fix_notes: str = "") -> Optional[dict]:
    focus = "\n".join(f'- "{t}"' for t in sec["titles"]) or "(use the most relevant widgets)"
    fix = f"\n\nREVISION REQUIRED — the previous draft failed review. Fix exactly these issues:\n{fix_notes}" if fix_notes else ""
    user = (
        f'REPORT: "{canvas_name}"\n'
        f'SECTION TO WRITE: "{sec["label"]}"  (id: {sec["id"]}, icon: {sec["icon"]})\n'
        f'ANGLE (keep this section focused on this and nothing else): {sec["angle"] or sec["label"]}\n'
        f'PRIMARY WIDGETS for this section:\n{focus}{fix}'
    )
    try:
        raw = await _invoke_json(
            _WRITER_MODEL, _WRITER_INSTRUCTION, shared_context, user,
            _WRITER_MAX_TOKENS, 0.2, f'write:{sec["id"]}',
        )
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            return None
        # Unwrap if the model wrapped it in {"sections":[...]}
        if "sections" in parsed and isinstance(parsed["sections"], list) and parsed["sections"]:
            parsed = parsed["sections"][0]
        parsed.setdefault("id", sec["id"])
        parsed.setdefault("label", sec["label"])
        parsed.setdefault("icon", sec["icon"])
        return parsed
    except Exception as exc:
        print(f'[intel-orch:write:{sec["id"]}] failed: {exc}', flush=True)
        return None


# ── STAGE 3: CRITIC ───────────────────────────────────────────────────────────
_CRITIC_INSTRUCTION = """You are an editor doing a LIGHT validation pass on ONE section of an executive report. DEFAULT TO PASS — when in doubt, pass.

NEVER FLAG (these are 100% acceptable — do not mention them at all):
- Percentages in ANY form or precision: "96%", "38%", "10%", "43.6%". NEVER recompute a percentage and NEVER ask for more decimals.
- Number notation/scale: "30.26 million", "$30,260,000", "6,762", "6.8K", with or without thousands-separators.
- An EMPTY "sparkline_data": [] — this is ALWAYS correct on any KPI; never ask to remove or justify it.
- Business language such as "risk", "margin compression", "churn", "attrition", "concentration", "exposure" — these are insights, NOT data-quality talk.
- A "trend"/"trend_pct" on a KPI; rounded-but-specific numbers; stylistic wording.

Fail (ok=false) ONLY for one of these concrete violations:
1. VAGUE / APPROXIMATE WORDING — the literal words "about", "roughly", "~", "approximately", "nearly", or "over X" before a number. (A bare percentage like "96%" is NOT vague — do not flag it.)
2. The SAME number repeated across ALL THREE prose fields (data_story AND key_finding AND narrative). Overlap with top_performers/kpis/charts is fine.
3. EXPLICIT data-hygiene talk only: "null rate", "% complete", "missing values", "X nulls", "data quality score", "data hygiene". (Business risk/margin/churn wording does NOT count.)
4. A chart with an empty/missing "data" array or an invalid "type".
5. A FABRICATED or RESCALED figure — a value/time-series that appears NOWHERE in the widget facts (e.g. inventing per-month values for a metric the facts only give as a total, or multiplying 4.52 into 452). A number matching any value in "EXACT VALUES"/"TOP PERFORMERS"/"BREAKDOWN" is grounded — accept it. Do NOT flag values you merely cannot verify.
6. A MISSING required field: data_story, key_finding, narrative, recommendation, or all charts.
7. A NON-EMPTY "sparkline_data" whose values are a ranking/top-N or category counts (not a time series), or an invented label like "Latest". (Empty [] is fine — see NEVER FLAG.)

Output ONLY raw JSON: {"ok": true|false, "issues": ["concise, actionable fix", ...]}
If acceptable, return {"ok": true, "issues": []}."""


async def _critique(section: dict, shared_context: str) -> dict:
    user = "SECTION UNDER REVIEW:\n" + json.dumps(section, default=str)[:12000]
    try:
        raw = await _invoke_json(
            _CRITIC_MODEL, _CRITIC_INSTRUCTION, shared_context, user,
            _CRITIC_MAX_TOKENS, 0.1, f'critic:{section.get("id","?")}',
        )
        parsed = _extract_json(raw) or {}
        ok = bool(parsed.get("ok", True))
        issues = [str(x) for x in (parsed.get("issues") or [])][:8]
        verdict = {"ok": ok or not issues, "issues": issues}
        sid = section.get("id", "?")
        if verdict["ok"]:
            print(f'[intel-orch:critic] {sid} VERDICT=pass', flush=True)
        else:
            print(f'[intel-orch:critic] {sid} VERDICT=fail  issues={issues}', flush=True)
        return verdict
    except Exception as exc:
        print(f'[intel-orch:critic] {section.get("id","?")} failed (treating as pass): {exc}', flush=True)
        return {"ok": True, "issues": []}


async def _write_and_verify(canvas_name: str, shared_context: str, sec: dict, sem: asyncio.Semaphore) -> Optional[dict]:
    """Write a section, critique it, and do ONE bounded re-write if it fails."""
    async with sem:
        section = await _write_section(canvas_name, shared_context, sec)
    if section is None:
        print(f'[intel-orch] section {sec["id"]} ({sec["label"]}) WRITE FAILED → dropped', flush=True)
        return None
    nch = len(section.get("charts") or [])
    print(f'[intel-orch] section {sec["id"]} ({sec["label"]}) written  charts={nch} → critic', flush=True)
    async with sem:
        verdict = await _critique(section, shared_context)
    if verdict["ok"]:
        print(f'[intel-orch] section {sec["id"]} ACCEPTED (no re-write)', flush=True)
        return section
    print(f'[intel-orch] section {sec["id"]} failed review → 1 bounded re-write', flush=True)
    async with sem:
        revised = await _write_section(canvas_name, shared_context, sec, "\n".join(f"- {i}" for i in verdict["issues"]))
    print(f'[intel-orch] section {sec["id"]} {"RE-WRITTEN" if revised else "re-write failed, kept original"}', flush=True)
    return revised or section


# ── STAGE 4: REDUCER ──────────────────────────────────────────────────────────
_REDUCER_INSTRUCTION = """You are the editor-in-chief assembling the global layer of an executive report from its finished sections. You are given each section's label, data_story, key_finding and KPIs.

Produce:
- title: "Executive Intelligence: <specific topic>"
- subtitle: one crisp line on what this report measures
- morning_brief: exactly 3 sentences — open with the single biggest finding (a specific number), cover the top 2 themes, end with a risk or opportunity. Use EXACT numbers, never rounded.
- kpis: 4-6 top-level KPIs drawn from the most important section metrics.

Do NOT discuss data quality. Output ONLY raw JSON:
{"title":"...","subtitle":"...","morning_brief":"...","kpis":[{"label":"...","value":"...","trend":"up|down|neutral","trend_pct":"+X%","sparkline_data":[],"explanation":"<1 sentence: what this KPI measures and why it matters>"}]}"""


async def _reduce(canvas_name: str, sections: list[dict]) -> dict:
    digest = []
    for s in sections:
        digest.append({
            "label": s.get("label"),
            "data_story": s.get("data_story"),
            "key_finding": s.get("key_finding"),
            "kpis": s.get("kpis", []),
        })
    user = f'REPORT: "{canvas_name}"\n\nFINISHED SECTIONS:\n' + json.dumps(digest, default=str)[:12000]
    try:
        raw = await _invoke_json(_REDUCER_MODEL, _REDUCER_INSTRUCTION, "", user, _REDUCER_MAX_TOKENS, 0.3, "reduce")
        parsed = _extract_json(raw) or {}
    except Exception as exc:
        print(f"[intel-orch:reduce] failed ({exc}); using deterministic globals", flush=True)
        parsed = {}
    return {
        "title": parsed.get("title") or f"Executive Intelligence: {canvas_name}",
        "subtitle": parsed.get("subtitle") or "",
        "morning_brief": parsed.get("morning_brief") or "",
        "kpis": parsed.get("kpis") or [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Orchestration entry
# ═══════════════════════════════════════════════════════════════════════════════

async def _orchestrate(canvas_name: str, widget_blocks: list[str], schema_block: str, correlations: list[str]) -> dict:
    t0 = time.time()
    shared_context = _build_shared_context(schema_block, correlations, widget_blocks)
    print(
        f"[intel-orch] START  canvas={canvas_name!r}  widgets={len(widget_blocks)}"
        f"  shared_ctx_len={len(shared_context)}",
        flush=True,
    )

    # STAGE 1 — plan
    plan = await _plan(canvas_name, widget_blocks)
    print(f"[intel-orch] plan: {len(plan)} sections → {[p['label'] for p in plan]}", flush=True)

    # STAGE 2+3 — parallel write + critic loop
    sem = asyncio.Semaphore(_ORCH_CONCURRENCY)
    written = await asyncio.gather(*[
        _write_and_verify(canvas_name, shared_context, sec, sem) for sec in plan
    ])
    sections = [s for s in written if s]
    print(f"[intel-orch] writers done: {len(sections)}/{len(plan)} sections produced", flush=True)

    if not sections:
        raise RuntimeError("all section writers failed")

    # STAGE 4 — reduce (global layer)
    onstage = time.time()
    globals_ = await _reduce(canvas_name, sections)
    print(f"[intel-orch] reduce done: kpis={len(globals_.get('kpis') or [])}  title={globals_.get('title')!r}  ({time.time()-onstage:.1f}s)", flush=True)

    result = {
        "title": globals_["title"],
        "subtitle": globals_["subtitle"],
        "morning_brief": globals_["morning_brief"],
        "kpis": globals_["kpis"],
        "sections": sections,
    }
    print(
        f"[intel-orch] DONE  {time.time()-t0:.1f}s  sections={len(sections)}"
        f"  kpis={len(result['kpis'])}",
        flush=True,
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class OrchestrateRequest(BaseModel):
    canvas_name: Optional[str] = "Report"
    widget_blocks: list[str]
    schema_block: Optional[str] = ""
    correlations: list[str] = []
    force: bool = False


@router.post("/intelligence/orchestrate")
async def intelligence_orchestrate(body: OrchestrateRequest, redis=Depends(get_redis)):
    """Parallel multi-agent report generation. Returns {"text": "<raw json>"} so
    the frontend's existing parse/sanitize/inject pipeline is unchanged."""
    canvas = body.canvas_name or "Report"
    print(
        f"[intel-orch] ▶ REQUEST  canvas={canvas!r}  widget_blocks={len(body.widget_blocks or [])}"
        f"  schema_block_len={len(body.schema_block or '')}  correlations={len(body.correlations or [])}"
        f"  force={body.force}"
        f"  models[plan={_PLANNER_MODEL.split('.')[-1][:20]} write={_WRITER_MODEL.split('.')[-1][:20]}"
        f" critic={_CRITIC_MODEL.split('.')[-1][:20]} reduce={_REDUCER_MODEL.split('.')[-1][:20]}]",
        flush=True,
    )
    if not body.widget_blocks:
        raise HTTPException(status_code=400, detail="widget_blocks is required")

    key_src = canvas + "\x1f" + body.schema_block + "\x1f" + "\x1e".join(body.widget_blocks) + "\x1f" + "\x1e".join(body.correlations)
    h = hashlib.sha256(key_src.encode()).hexdigest()[:24]
    cache_key = f"intel:orch:result:{h}"
    lock_key = f"intel:orch:lock:{h}"

    # ── cached result ──
    if not body.force and redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                print(f"[intel-orch] CACHE HIT hash={h[:8]} canvas={canvas}", flush=True)
                return {"text": raw}
        except Exception as exc:
            print(f"[intel-orch] redis read failed (non-fatal): {exc}", flush=True)
    elif body.force and redis is not None:
        try:
            await redis.delete(cache_key)
        except Exception:
            pass

    # ── coalesce concurrent duplicates ──
    lock_acquired = True
    if redis is not None:
        try:
            lock_acquired = bool(await redis.set(lock_key, "1", nx=True, ex=_ORCH_LOCK_TTL))
        except Exception:
            lock_acquired = True
        if not lock_acquired:
            t_wait = time.time()
            while time.time() - t_wait < _ORCH_LOCK_WAIT:
                await asyncio.sleep(_ORCH_LOCK_POLL)
                try:
                    cached = await redis.get(cache_key)
                    if cached:
                        raw = cached.decode() if isinstance(cached, bytes) else cached
                        print(f"[intel-orch] PEER RESULT waited={time.time()-t_wait:.1f}s hash={h[:8]}", flush=True)
                        return {"text": raw}
                    if not await redis.exists(lock_key):
                        lock_acquired = bool(await redis.set(lock_key, "1", nx=True, ex=_ORCH_LOCK_TTL))
                        if lock_acquired:
                            break
                except Exception:
                    break

    try:
        result = await _orchestrate(canvas, body.widget_blocks, body.schema_block or "", body.correlations or [])
        raw = json.dumps(result, default=str)
        if redis is not None:
            try:
                await redis.set(cache_key, raw.encode(), ex=_ORCH_CACHE_TTL)
            except Exception as exc:
                print(f"[intel-orch] redis write failed (non-fatal): {exc}", flush=True)
        return {"text": raw}
    except Exception as exc:
        print(f"[intel-orch] FAILED: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(status_code=502, detail=f"Orchestration failed: {exc}")
    finally:
        if redis is not None and lock_acquired:
            try:
                await redis.delete(lock_key)
            except Exception:
                pass
