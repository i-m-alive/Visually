"""
PBIT (Power BI Template) file parser.

Extracts from a .pbit ZIP archive:
  - DataModelSchema: measures (DAX), calculated columns (DAX), relationships, source tables
  - Report/Layout: per-visual type, position, field bindings, filters

Also provides:
  - DAX → SQL translation via Bedrock LLM (async, batch, non-fatal)
  - PBIT visual ↔ vision-chart matching by normalized bounding-box IoU + type similarity
  - Helper to map Power BI model table names to DB schema-qualified names
"""
import io
import json
import re
import zlib
import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class PBITMeasure:
    table: str
    name: str
    dax_expression: str
    sql_expression: str = ""          # filled by translate_pbit_to_sql()


@dataclass
class PBITCalculatedColumn:
    table: str
    column_name: str
    dax_expression: str
    sql_expression: str = ""


@dataclass
class PBITRelationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    is_active: bool = True


@dataclass
class PBITVisual:
    visual_id: str
    visual_type: str                  # raw Power BI type string
    chart_type_hint: str              # mapped to pipeline types: bar, line, pie, kpi, …
    title: str
    page_name: str
    x_norm: float                     # normalized position [0, 1]
    y_norm: float
    w_norm: float
    h_norm: float
    field_bindings: dict              # {"Category": ["T.col"], "Y": ["T.[Measure]"]}
    filters: list                     # visual-level filter strings
    tables_referenced: list           # Power BI model table names extracted from field paths


@dataclass
class PBITModel:
    measures: list = field(default_factory=list)             # list[PBITMeasure]
    calculated_columns: list = field(default_factory=list)   # list[PBITCalculatedColumn]
    relationships: list = field(default_factory=list)        # list[PBITRelationship]
    visuals: list = field(default_factory=list)              # list[PBITVisual]
    global_filters: list = field(default_factory=list)
    report_name: str = ""
    pages: list = field(default_factory=list)
    source_tables: list = field(default_factory=list)        # Power BI model table names


# ── Visual type mapping ────────────────────────────────────────────────────────

_VISUAL_TYPE_MAP: dict = {
    "clusteredBarChart":                    "bar_vertical",
    "clusteredColumnChart":                 "bar_vertical",
    "columnChart":                          "bar_vertical",
    "barChart":                             "bar_horizontal",
    "stackedBarChart":                      "stacked_bar",
    "stackedColumnChart":                   "stacked_bar",
    "hundredPercentStackedBarChart":        "stacked_bar",
    "hundredPercentStackedColumnChart":     "stacked_bar",
    "lineChart":                            "line",
    "areaChart":                            "area",
    "stackedAreaChart":                     "area",
    "pieChart":                             "pie",
    "donutChart":                           "donut",
    "card":                                 "kpi",
    "multiRowCard":                         "kpi",
    "kpiVisual":                            "kpi",
    "tableEx":                              "table",
    "pivotTable":                           "pivot_table",
    "matrix":                               "pivot_table",
    "scatterChart":                         "scatter",
    "waterfallChart":                       "bar_vertical",
    "ribbonChart":                          "stacked_bar",
    "treemap":                              "treemap",
    "funnel":                               "funnel",
    "gauge":                                "gauge",
    "lineClusteredColumnComboChart":        "combo",
    "lineStackedColumnComboChart":          "combo",
    # Decorative — skip; mapped to None so caller can filter
    "basicShape":                           None,
    "image":                                None,
    "textbox":                              None,
    "slicer":                               "slicer",
    "advancedSlicerVisual":                 "slicer",
}


# ── Top-level parser ───────────────────────────────────────────────────────────

def parse_pbit(pbit_bytes: bytes) -> PBITModel:
    """
    Parse a .pbit file and return a PBITModel.
    Non-fatal: returns an empty model on any failure.
    """
    model = PBITModel()
    try:
        with zipfile.ZipFile(io.BytesIO(pbit_bytes)) as zf:
            names = set(zf.namelist())
            print(f"[pbit_parser] ZIP entries: {sorted(names)}", flush=True)

            # DataModelSchema — Power BI data model (measures, calc cols, relationships)
            schema_file = next(
                (n for n in names if n.lower().endswith("datamodelschema")), None
            )
            if schema_file:
                raw_schema = zf.read(schema_file)
                print(
                    f"[pbit_parser] DataModelSchema: {len(raw_schema)} bytes, "
                    f"first={raw_schema[:16].hex()}",
                    flush=True,
                )
                _parse_data_model_schema(raw_schema, model)
            else:
                print("[pbit_parser] ⚠ DataModelSchema not found in PBIT ZIP", flush=True)

            # Report/Layout — visual containers with field bindings
            layout_file = next(
                (n for n in names if n.lower() in ("report/layout", "report\\layout")),
                None,
            )
            if not layout_file:
                layout_file = next(
                    (n for n in names if "report" in n.lower() and "layout" in n.lower()),
                    None,
                )
            if layout_file:
                raw_layout = zf.read(layout_file)
                print(
                    f"[pbit_parser] Report/Layout: {len(raw_layout)} bytes, "
                    f"first={raw_layout[:16].hex()}",
                    flush=True,
                )
                _parse_report_layout(raw_layout, model)
            else:
                print("[pbit_parser] ⚠ Report/Layout not found in PBIT ZIP", flush=True)

    except zipfile.BadZipFile as exc:
        print(f"[pbit_parser] ✗ Not a valid ZIP/PBIT file: {exc}", flush=True)
    except Exception as exc:
        print(f"[pbit_parser] ✗ Unexpected error: {exc}", flush=True)

    print(
        f"[pbit_parser] parsed → measures={len(model.measures)}  "
        f"calc_cols={len(model.calculated_columns)}  "
        f"relationships={len(model.relationships)}  "
        f"visuals={len(model.visuals)}  "
        f"source_tables={model.source_tables[:5]}",
        flush=True,
    )
    return model


# ── DataModelSchema parser ─────────────────────────────────────────────────────

def _decode_bytes(raw: bytes) -> str:
    # Explicit BOM detection first
    if raw[:3] == b'\xef\xbb\xbf':
        return raw[3:].decode("utf-8", errors="replace")
    if raw[:2] == b'\xff\xfe':
        return raw[2:].decode("utf-16-le", errors="replace")
    if raw[:2] == b'\xfe\xff':
        return raw[2:].decode("utf-16-be", errors="replace")

    # Detect UTF-16 LE *without* BOM: Power BI PBIT files commonly use this.
    # Signature: null byte at every odd index in the first 32 bytes.
    # UTF-8 silently "decodes" these bytes (null is valid) but produces garbage.
    if len(raw) >= 16:
        sample = raw[:32]
        null_at_odd = sum(1 for i in range(1, len(sample), 2) if sample[i] == 0)
        null_at_even = sum(1 for i in range(0, len(sample), 2) if sample[i] == 0)
        if null_at_odd >= 6:
            try:
                return raw.decode("utf-16-le")
            except UnicodeDecodeError:
                pass
        elif null_at_even >= 6:
            try:
                return raw.decode("utf-16-be")
            except UnicodeDecodeError:
                pass

    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_json_comments(text: str) -> str:
    """Remove JavaScript-style // and /* */ comments (Power BI uses these in DataModelSchema)."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
    text = re.sub(r'(?<![:/])//[^\n]*', '', text)
    return text


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas before } or ] (valid JS, invalid JSON)."""
    return re.sub(r',\s*([}\]])', r'\1', text)


def _load_json(raw: bytes) -> Any:
    """
    Multi-strategy JSON loader for PBIT internal files.
    Order: plain → strip comments → strip trailing commas
           → same 3 after zlib/gzip/deflate decompression.
    """
    def _try_text(text: str) -> Any:
        last: Exception = ValueError("empty")
        for xfm in [
            lambda t: t,
            _strip_json_comments,
            lambda t: _strip_trailing_commas(_strip_json_comments(t)),
        ]:
            try:
                return json.loads(xfm(text))
            except (json.JSONDecodeError, ValueError) as e:
                last = e
        raise last

    try:
        return _try_text(_decode_bytes(raw))
    except Exception:
        pass

    for wbits in (15 | 16, 15, -15):   # gzip, zlib, raw-deflate
        try:
            return _try_text(_decode_bytes(zlib.decompress(raw, wbits)))
        except Exception:
            continue

    raise ValueError(
        f"Cannot parse as JSON in any known format "
        f"(size={len(raw)}, first_bytes={raw[:16].hex()})"
    )


def _coerce_str(val) -> str:
    """Power BI stores multi-line DAX expressions as JSON arrays; join them."""
    if isinstance(val, list):
        return "\n".join(str(v) for v in val).strip()
    return (val or "").strip()


def _parse_data_model_schema(raw: bytes, model: PBITModel) -> None:
    try:
        obj = _load_json(raw)
    except Exception as exc:
        print(f"[pbit_parser] ⚠ DataModelSchema JSON parse error: {exc}", flush=True)
        return

    try:
        root = obj.get("model") or obj.get("dataModel") or obj
        model.report_name = _coerce_str(root.get("name", ""))

        for tbl in root.get("tables", []):
            tname = _coerce_str(tbl.get("name", ""))
            if not tname:
                continue
            if (
                tname.startswith("$")
                or tname.startswith("LocalDateTable")
                or tname.startswith("DateTableTemplate")
                or tbl.get("isHidden")
            ):
                continue

            model.source_tables.append(tname)

            for m in tbl.get("measures", []):
                mname = _coerce_str(m.get("name"))
                mexpr = _coerce_str(m.get("expression"))
                if mname and mexpr:
                    model.measures.append(PBITMeasure(
                        table=tname, name=mname, dax_expression=mexpr,
                    ))

            for col in tbl.get("columns", []):
                cname = _coerce_str(col.get("name"))
                cexpr = _coerce_str(col.get("expression"))
                if cname and cexpr:
                    model.calculated_columns.append(PBITCalculatedColumn(
                        table=tname, column_name=cname, dax_expression=cexpr,
                    ))

        for rel in root.get("relationships", []):
            ft = _coerce_str(rel.get("fromTable"))
            fc = _coerce_str(rel.get("fromColumn"))
            tt = _coerce_str(rel.get("toTable"))
            tc = _coerce_str(rel.get("toColumn"))
            if ft and fc and tt and tc:
                model.relationships.append(PBITRelationship(
                    from_table=ft, from_column=fc,
                    to_table=tt, to_column=tc,
                    is_active=bool(rel.get("isActive", True)),
                ))

    except Exception as exc:
        print(f"[pbit_parser] ⚠ DataModelSchema processing error: {exc}", flush=True)


# ── Report/Layout parser ───────────────────────────────────────────────────────

def _parse_report_layout(raw: bytes, model: PBITModel) -> None:
    try:
        layout = _load_json(raw)
    except Exception as exc:
        print(f"[pbit_parser] ⚠ Report/Layout JSON parse error: {exc}", flush=True)
        return

    sections = layout.get("sections", [])
    model.pages = [s.get("displayName", f"Page {i+1}") for i, s in enumerate(sections)]

    for section in sections:
        page_name = section.get("displayName", "Page 1")
        # Dimensions: Power BI stores them as points (pixels) by default
        page_w = float(section.get("width", 1280) or 1280)
        page_h = float(section.get("height", 720) or 720)

        for vc in section.get("visualContainers", []):
            x = float(vc.get("x", 0))
            y = float(vc.get("y", 0))
            w = float(vc.get("width", 0))
            h = float(vc.get("height", 0))

            # config is a JSON-string (double-encoded)
            config_raw = vc.get("config", "{}")
            try:
                cfg = json.loads(config_raw) if isinstance(config_raw, str) else config_raw
            except Exception:
                continue

            sv = cfg.get("singleVisual") or {}
            visual_type = sv.get("visualType", "")
            chart_type_hint = _VISUAL_TYPE_MAP.get(visual_type)
            if chart_type_hint is None:
                # Decorative shape / textbox / image — skip
                continue

            title = _extract_visual_title(sv)
            field_bindings, tables_referenced = _extract_field_bindings(sv)
            visual_filters = _extract_visual_filters(vc)

            visual = PBITVisual(
                visual_id=cfg.get("name", f"{page_name}_{int(x)}_{int(y)}"),
                visual_type=visual_type,
                chart_type_hint=chart_type_hint,
                title=title,
                page_name=page_name,
                x_norm=x / page_w if page_w else 0.0,
                y_norm=y / page_h if page_h else 0.0,
                w_norm=w / page_w if page_w else 0.0,
                h_norm=h / page_h if page_h else 0.0,
                field_bindings=field_bindings,
                filters=visual_filters,
                tables_referenced=tables_referenced,
            )
            model.visuals.append(visual)


def _extract_visual_title(sv: dict) -> str:
    """Best-effort extraction of the visual title from singleVisual vcObjects."""
    try:
        title_objs = sv.get("vcObjects", {}).get("title", [])
        if title_objs and isinstance(title_objs, list):
            props = title_objs[0].get("properties", {})
            text_val = props.get("text", {})
            if isinstance(text_val, dict):
                # Path: text.expr.Literal.Value → "'Title Text'" (quoted string literal)
                lit = text_val.get("expr", {}).get("Literal", {}).get("Value", "")
                return lit.strip("'\"")
            if isinstance(text_val, str):
                return text_val
    except Exception:
        pass
    return ""


def _extract_field_bindings(sv: dict) -> tuple:
    """
    Extract field bindings from singleVisual.projections.
    Falls back to prototypeQuery.Select when projections are absent.
    Returns (field_bindings_dict, tables_referenced_list).
    """
    field_bindings: dict = {}
    tables_referenced: list = []

    # Primary path: projections
    projections = sv.get("projections", {})
    for role, proj_list in projections.items():
        if not isinstance(proj_list, list):
            continue
        refs = []
        for proj in proj_list:
            qref = proj.get("queryRef", "")
            if qref:
                refs.append(qref)
                tbl = qref.split(".", 1)[0].strip()
                if tbl and tbl not in tables_referenced:
                    tables_referenced.append(tbl)
        if refs:
            field_bindings[role] = refs

    if field_bindings:
        return field_bindings, tables_referenced

    # Fallback: prototypeQuery.Select
    pq = sv.get("prototypeQuery", {})
    from_map: dict = {}
    for f in pq.get("From", []):
        alias = f.get("Name", "")
        entity = f.get("Entity", "")
        if alias and entity:
            from_map[alias] = entity

    for sel in pq.get("Select", []):
        col_ref = sel.get("Column", {})
        measure_ref = sel.get("Measure", {})
        if col_ref:
            src = col_ref.get("Expression", {}).get("SourceRef", {}).get("Source", "")
            prop = col_ref.get("Property", "")
            entity = from_map.get(src, src)
            if entity and prop:
                qref = f"{entity}.{prop}"
                field_bindings.setdefault("Category", []).append(qref)
                if entity not in tables_referenced:
                    tables_referenced.append(entity)
        elif measure_ref:
            src = measure_ref.get("Expression", {}).get("SourceRef", {}).get("Source", "")
            prop = measure_ref.get("Property", "")
            entity = from_map.get(src, src)
            if entity and prop:
                qref = f"{entity}.{prop}"
                field_bindings.setdefault("Y", []).append(qref)
                if entity not in tables_referenced:
                    tables_referenced.append(entity)

    return field_bindings, tables_referenced


def _extract_visual_filters(vc: dict) -> list:
    """Parse visual-level filters from the visualContainer's filters JSON-string."""
    filters_raw = vc.get("filters", "[]")
    result: list = []
    try:
        flt_list = json.loads(filters_raw) if isinstance(filters_raw, str) else filters_raw
        for flt in flt_list:
            expr = flt.get("expression", {})
            cond = _filter_expr_to_str(expr)
            if cond:
                result.append(cond)
    except Exception:
        pass
    return result


def _filter_expr_to_str(expr: Any) -> str:
    """Best-effort conversion of a PBIT filter expression to a readable string."""
    if not expr:
        return ""
    try:
        if "Not" in expr:
            inner = _filter_expr_to_str(expr["Not"])
            return f"NOT ({inner})" if inner else ""
        if "In" in expr:
            in_e = expr["In"]
            cols = in_e.get("Expressions", [])
            vals = in_e.get("Values", [])
            col_name = ""
            if cols:
                col_name = cols[0].get("Column", {}).get("Property", "")
            val_list = []
            for v in vals[:8]:
                lit = (v[0].get("Literal", {}).get("Value", "") if v else "").strip("'\"")
                val_list.append(lit)
            if col_name and val_list:
                return f"{col_name} IN ({', '.join(repr(v) for v in val_list)})"
        if "Comparison" in expr:
            cmp = expr["Comparison"]
            op = {0: "=", 1: ">", 2: ">=", 3: "<", 4: "<="}.get(cmp.get("ComparisonKind", 0), "=")
            left = cmp.get("Left", {}).get("Column", {}).get("Property", "?")
            right = cmp.get("Right", {}).get("Literal", {}).get("Value", "?").strip("'\"")
            return f"{left} {op} {right}"
    except Exception:
        pass
    return ""


# ── PBIT visual ↔ vision chart matching ───────────────────────────────────────

def match_pbit_visual_to_chart(visual: PBITVisual, vision_chart: dict) -> float:
    """
    Return a similarity score [0, 1] between a PBIT visual and a vision-detected chart.
    Combines normalized bounding-box IoU (70%) with chart type similarity (30%).
    """
    bb = vision_chart.get("bounding_box") or {}
    # Support both {left/top/right/bottom} and {x/y/w/h} formats
    if "right" in bb:
        vx, vy = bb.get("left", 0.0), bb.get("top", 0.0)
        vw = bb.get("right", 0.0) - vx
        vh = bb.get("bottom", 0.0) - vy
    else:
        vx, vy = bb.get("x", 0.0), bb.get("y", 0.0)
        vw, vh = bb.get("w", 0.5), bb.get("h", 0.5)

    # IoU
    ix1 = max(visual.x_norm, vx)
    iy1 = max(visual.y_norm, vy)
    ix2 = min(visual.x_norm + visual.w_norm, vx + vw)
    iy2 = min(visual.y_norm + visual.h_norm, vy + vh)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = visual.w_norm * visual.h_norm + vw * vh - inter
    iou = inter / union if union > 0.0 else 0.0

    # Type similarity
    vision_type = (vision_chart.get("type") or "").lower()
    pbit_type = (visual.chart_type_hint or "").lower()
    type_score = 1.0 if vision_type == pbit_type else (
        0.6 if vision_type.split("_")[0] == pbit_type.split("_")[0] else 0.2
    )

    return iou * 0.7 + type_score * 0.3


def find_best_pbit_match(
    vision_chart: dict,
    pbit_visuals: list,
    min_score: float = 0.15,
) -> Optional[PBITVisual]:
    """
    Find the best-matching PBIT visual for a vision-detected chart.
    Returns None when no visual scores above min_score.
    """
    best: Optional[PBITVisual] = None
    best_score = min_score
    for v in pbit_visuals:
        s = match_pbit_visual_to_chart(v, vision_chart)
        if s > best_score:
            best_score = s
            best = v
    return best


# ── DB table name mapping ──────────────────────────────────────────────────────

def map_pbit_tables_to_db(pbit_tables: list, compact_tables: list) -> list:
    """
    Map Power BI model table names to DB schema-qualified names (e.g. "staging.placement").
    Uses suffix matching: "placement" matches "staging.bullhorn_placement".
    Returns only tables found in the DB schema.
    """
    db_names = [t.get("name", "") for t in compact_tables]
    result: list = []
    for pt in pbit_tables:
        pt_lower = pt.lower()
        pt_bare = pt_lower.replace("_", "").replace(" ", "")

        # Exact match (case-insensitive)
        exact = next((n for n in db_names if n.lower().split(".")[-1] == pt_lower), None)
        if exact:
            if exact not in result:
                result.append(exact)
            continue

        # Partial: DB table name contains the PBIT name
        partial = next(
            (n for n in db_names if pt_bare in n.lower().replace("_", "")),
            None,
        )
        if partial:
            if partial not in result:
                result.append(partial)

    return result


def build_pbit_join_conditions(
    pbit_relationships: list,
    pbit_tables: list,
    compact_tables: list,
) -> list:
    """
    Convert PBIT relationships for the given set of pbit_tables into
    SQL JOIN conditions using DB-mapped column names.
    Returns a list of "schema.table1.col = schema.table2.col" strings.
    """
    result: list = []
    pbit_set = set(t.lower() for t in pbit_tables)
    db_name_map = {t.get("name", "").lower().split(".")[-1]: t.get("name", "") for t in compact_tables}

    for rel in pbit_relationships:
        if not isinstance(rel, PBITRelationship):
            continue
        if rel.from_table.lower() not in pbit_set and rel.to_table.lower() not in pbit_set:
            continue
        ft_db = db_name_map.get(rel.from_table.lower(), rel.from_table)
        tt_db = db_name_map.get(rel.to_table.lower(), rel.to_table)
        if ft_db and tt_db:
            result.append(f"{ft_db}.{rel.from_column} = {tt_db}.{rel.to_column}")
    return result


# ── Derived maps for orchestrator ─────────────────────────────────────────────

def build_calc_col_map_from_pbit(model: PBITModel) -> dict:
    """
    Build {col_name_lower: sql_expression} for all translated calculated columns.
    Only includes columns where sql_expression was successfully populated.
    """
    return {
        cc.column_name.lower(): cc.sql_expression
        for cc in model.calculated_columns
        if cc.sql_expression
    }


def build_measure_map_from_pbit(model: PBITModel) -> dict:
    """
    Build {measure_name_lower: sql_expression} for all translated measures.
    """
    return {
        m.name.lower(): m.sql_expression
        for m in model.measures
        if m.sql_expression
    }


# ── DAX → SQL translation ──────────────────────────────────────────────────────

async def translate_pbit_to_sql(model: PBITModel, db_type: str = "redshift") -> PBITModel:
    """
    Translate all DAX measures and calculated columns to SQL using Bedrock.
    Mutates model in-place; returns it.  Non-fatal on failure.
    """
    items: list = []
    for m in model.measures:
        if m.dax_expression and not m.sql_expression:
            items.append(("measure", m))
    for cc in model.calculated_columns:
        if cc.dax_expression and not cc.sql_expression:
            items.append(("calc_col", cc))

    if not items:
        return model

    # Cap at 30 items per LLM call to stay within token limits
    batch = items[:30]
    dax_lines = []
    for kind, item in batch:
        if kind == "measure":
            dax_lines.append(f"MEASURE [{item.name}] (table: {item.table}): {item.dax_expression}")
        else:
            dax_lines.append(f"CALC_COLUMN [{item.column_name}] (table: {item.table}): {item.dax_expression}")
    dax_text = "\n".join(dax_lines)

    prompt = (
        f"You are a Power BI DAX to {db_type} SQL expert.\n\n"
        "Convert each DAX expression to an equivalent SQL expression for a SELECT clause "
        "(not a full SELECT statement — just the expression).\n"
        "For aggregates like SUM, COUNT, COUNTROWS, SUMX → produce the SQL aggregate.\n"
        "For calculated columns → produce a CASE WHEN or arithmetic expression.\n"
        "Omit schema/table prefixes in the expression; use plain column names.\n\n"
        "DAX EXPRESSIONS TO TRANSLATE:\n"
        f"{dax_text}\n\n"
        "Return ONLY a JSON array:\n"
        '[{"name": "expression_name", "sql": "SQL expression"}, ...]'
    )

    try:
        from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL
        response = await bedrock_invoke(
            model_id=BEDROCK_SONNET_MODEL,
            system_prompt="You are a precise DAX-to-SQL translator. Return only valid JSON.",
            user_message=prompt,
            max_tokens=2000,
        )
        text = response if isinstance(response, str) else ""
        m = re.search(r"\[.*?\]", text, re.DOTALL)
        if m:
            translations = json.loads(m.group())
            tmap = {
                t["name"].lower(): t["sql"]
                for t in translations
                if isinstance(t, dict) and t.get("name") and t.get("sql")
            }
            for kind, item in batch:
                key = (item.name if kind == "measure" else item.column_name).lower()
                sql = tmap.get(key, "")
                if sql:
                    item.sql_expression = sql
            print(f"[pbit_parser] DAX→SQL: translated {len(tmap)} expression(s)", flush=True)
    except Exception as exc:
        print(f"[pbit_parser] ⚠ DAX→SQL translation failed (non-fatal): {exc}", flush=True)

    return model


# ── Per-chart hint builder ─────────────────────────────────────────────────────

def build_pbit_chart_hint(
    visual: PBITVisual,
    measure_map: dict,
    compact_tables: list,
    pbit_relationships: list,
) -> dict:
    """
    Build the pbit_column_hint dict that will be injected into query_agent.
    Format:
    {
        "field_bindings": {...},          # role → [queryRef] from PBIT projections
        "tables_referenced": [...],       # Power BI model table names (unmapped)
        "db_tables": [...],               # DB schema-qualified table names
        "measures": {name: sql_expr},     # translated measure expressions
        "join_conditions": [...],         # SQL join conditions from PBIT relationships
        "visual_type": str,
        "title": str,
    }
    """
    db_tables = map_pbit_tables_to_db(visual.tables_referenced, compact_tables)
    join_conds = build_pbit_join_conditions(
        pbit_relationships, visual.tables_referenced, compact_tables
    )
    # Include only measure translations relevant to this visual's field bindings
    visual_measure_refs: set = set()
    for refs in visual.field_bindings.values():
        for r in refs:
            parts = r.split(".", 1)
            if len(parts) == 2:
                visual_measure_refs.add(parts[1].strip("[]").lower())

    relevant_measures = {
        k: v for k, v in measure_map.items() if k in visual_measure_refs
    }

    return {
        "field_bindings": visual.field_bindings,
        "tables_referenced": visual.tables_referenced,
        "db_tables": db_tables,
        "measures": relevant_measures,
        "join_conditions": join_conds,
        "visual_type": visual.chart_type_hint,
        "title": visual.title,
    }
