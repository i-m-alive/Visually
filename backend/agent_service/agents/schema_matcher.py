"""
Schema Matcher Agent — ranks schema tables/column combinations for a given chart spec.
Accepts an EnrichedSchema (from schema_cache) so it gets disambiguation context and
pre-built compact table list for free — no re-processing per call.

Speed: uses Haiku (5× faster, 10× cheaper than Sonnet) — table ranking is a
classification task that doesn't require deep reasoning.

Accuracy: TF-IDF pre-filter reduces the table set sent to the LLM, shrinking the
prompt and focusing the model on the most relevant tables.
"""
import json
import re
from typing import TYPE_CHECKING, Optional
from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema

# Haiku is sufficient for table ranking (classification, not generation)
MATCHER_MODEL = BEDROCK_HAIKU_MODEL

SYSTEM_PROMPT = """You are a database schema analyst specializing in data visualization.
Given a chart specification detected from a screenshot and an enriched database schema,
identify which tables and columns are MOST LIKELY needed to reproduce the chart.

Use the COLUMN DISAMBIGUATION and TABLE SEMANTICS sections to resolve ambiguous column names
and understand what each table is for before picking candidates.

ANALYSIS APPROACH:
1. Match chart title keywords → table names, descriptions, column names
2. Match x-axis label / x-tick labels → candidate dimension or date columns
3. Match y-axis label / estimated values → candidate metric/aggregate columns
4. Match legend labels → candidate grouping columns
5. chart type inference: KPI→single aggregate, line→time series, bar→category+metric
6. data_point_count hints at expected row granularity
7. Consider joins when a single table can't supply both dimension and metric

SEMANTIC GRAIN CHECK (critical — wrong grain = wrong table):
- Chart title says "jobs by role" or "open positions" → look for a table whose grain is JOB/POSTING (one row per job), not employee or profile table
- Chart title says "revenue by month" → look for a transaction or order table, not a product or customer table
- Chart title says "employees by department" → look for an employee or headcount table, not a jobs table
- PENALIZE tables that are at the wrong entity level — even if they share a column name
- When TABLE SEMANTICS section is present, read the "purpose" and "grain" fields before assigning confidence

FK COLUMN AVOIDANCE (critical for correctness):
- Do NOT assign integer/FK columns as key_columns.dimension or key_columns.group_by.
  A foreign key column (e.g. "replacement", "joborderid", "clientcorporationid") contains numeric
  IDs — grouping by it produces meaningless integer labels (4145, 6208...) instead of categories.
- Check the RELATIONSHIPS section of each table: if a column appears there as a FK, it is NOT a
  valid dimension. Choose a string/VARCHAR column instead ("status", "employment_type", "name", etc.)
- Columns named *id, *_id, *_fk are almost always FK integers — never use them as dimension.
- The ONLY exception: if no VARCHAR dimension column exists at all in the candidate table.

CONFIDENCE CALIBRATION:
- Start from 1.0 and subtract based on mismatches
- Mismatch in grain: -0.4
- Mismatch in key_metric_cols: -0.2
- Mismatch in chart title keywords vs table description: -0.1 per keyword miss
- If a better-matching table exists in the schema, assign the wrong one confidence ≤ 0.3

Return ONLY valid JSON."""

USER_TEMPLATE = """CHART TO REPLICATE:
{chart_json}

{context_block}
{disambiguation_block}

{semantics_block}

ALL AVAILABLE TABLES ({table_count} total):
{schema_json}

Database type: {db_type}

Return a JSON array of up to 5 candidate table combinations ranked by confidence (highest first):
[
  {{
    "tables": ["table_name"],
    "key_columns": {{"dimension": "col_name", "metric": "col_name", "date": "col_or_null", "group_by": "col_or_null"}},
    "join": null,
    "reasoning": "Why this table best matches the chart — reference disambiguation if applicable",
    "confidence": 0.95
  }},
  {{
    "tables": ["table_a", "table_b"],
    "key_columns": {{"dimension": "col", "metric": "col", "date": null, "group_by": null}},
    "join": "table_a.fk_col = table_b.pk_col",
    "reasoning": "Join needed because ...",
    "confidence": 0.70
  }}
]

Rules:
- Only use table names that exist in the schema above
- confidence 0.8+ = strong match, 0.5-0.8 = plausible, <0.5 = speculative
- When a column name is ambiguous, pick the occurrence from the most relevant table (see DISAMBIGUATION)
- When x_tick_labels show job titles / roles (e.g. "Software Engineer", "Manager"), the dimension col must be a role/title column in a JOBS or POSITIONS table — not an employee profile or account table
- When x_tick_labels show company names or clients, prefer tables with a company/client grain
- Return at least 1 candidate"""


def _tfidf_prefilter(
    chart_spec: dict,
    compact_tables: list,
    top_n: int = 15,
    context_keywords: Optional[list] = None,
) -> list:
    """
    Word-overlap pre-filter: scores each table by how many chart-spec words appear
    in its name + description + column names.  Keeps top_n tables before calling
    the LLM.  Recall-focused (not Jaccard) — we prefer not to miss the right table.

    context_keywords: additional high-priority terms from the user's Mode 3 context.
    These are added twice to query_terms so they carry more weight in the overlap score.

    When the schema has ≤ top_n tables the function returns the original list unchanged.
    """
    if not compact_tables or len(compact_tables) <= top_n:
        return compact_tables

    # Collect query terms from the chart spec
    query_terms: set[str] = set()

    def _add(text: str) -> None:
        if text:
            for w in re.sub(r"[^a-z0-9_]", " ", text.lower()).split():
                if len(w) > 2:
                    query_terms.add(w)
                    # 5-char prefix stem: "employee" → "emplo", "revenue" → "reven"
                    # This lets abbreviated table names (emp, rev) still match chart terms
                    if len(w) > 5:
                        query_terms.add(w[:5])

    _add(chart_spec.get("title", ""))
    _add(chart_spec.get("x_axis_label", ""))
    _add(chart_spec.get("y_axis_label", ""))
    for lbl in (chart_spec.get("x_tick_labels") or [])[:6]:
        _add(str(lbl))
    for lbl in (chart_spec.get("legend_labels") or [])[:4]:
        _add(str(lbl))
    # estimated_values keys are a strong signal for KPI charts that have no title
    # e.g. {"Active Placements": 1234} → adds "active", "placements" to query_terms
    for key in (chart_spec.get("estimated_values") or {}).keys():
        _add(str(key))

    # Mode 3: inject user context keywords — added as high-priority signal
    if context_keywords:
        for kw in context_keywords:
            _add(str(kw))

    if not query_terms:
        # Nothing to match on — rank by table size; largest tables are most likely
        # to contain the chart data in a typical analytics database.
        print(
            f"[schema_matcher] TF-IDF no query terms — fallback to row_count order",
            flush=True,
        )
        return sorted(compact_tables, key=lambda t: t.get("row_count") or 0, reverse=True)[:top_n]

    def _score(table: dict) -> float:
        tterms: set[str] = set()

        def _add_t(text: str) -> None:
            if text:
                for w in re.sub(r"[^a-z0-9_]", " ", text.lower()).split():
                    if len(w) > 2:
                        tterms.add(w)
                        # Mirror prefix stem so "empl" in query_terms matches "employees" table
                        if len(w) > 5:
                            tterms.add(w[:5])

        _add_t(table.get("name", ""))
        _add_t(table.get("description", ""))
        # Score against ALL column names.  Use pre-built all_column_names flat list when
        # available (lightweight) — falls back to iterating the full columns list so this
        # works with both old and new compact_tables shapes.
        all_col_names = table.get("all_column_names") or []
        if all_col_names:
            for cname in all_col_names:
                _add_t(cname)
        else:
            for col in (table.get("columns") or []):
                _add_t(col.get("name", ""))
                _add_t(col.get("description", ""))
        if not tterms:
            return 0.0
        return len(query_terms & tterms) / len(query_terms)

    scored = sorted(compact_tables, key=_score, reverse=True)
    # If the top score is still 0 (no term overlap with any table), fall back to
    # row_count sort — better than an arbitrary stable-sort order.
    if scored and _score(scored[0]) == 0.0:
        print(
            f"[schema_matcher] TF-IDF zero overlap — fallback to row_count order",
            flush=True,
        )
        scored = sorted(compact_tables, key=lambda t: t.get("row_count") or 0, reverse=True)
    result = scored[:top_n]

    # Force-include any user-hinted tables that didn't make the top_n cut.
    # chart_spec["user_hint_tables"] can be set by the orchestrator when the user or
    # a prior failed attempt explicitly named a table to try.
    hint_names = set(chart_spec.get("user_hint_tables") or [])
    if hint_names:
        result_names = {t.get("name") for t in result}
        missing_hints = [
            t for t in compact_tables
            if t.get("name") in hint_names and t.get("name") not in result_names
        ]
        if missing_hints:
            # Replace the lowest-scoring tail entries to keep total at top_n
            result = result[:max(0, top_n - len(missing_hints))] + missing_hints
            print(
                f"[schema_matcher] forced {len(missing_hints)} hint table(s) into pool: "
                + ", ".join(t.get("name", "") for t in missing_hints),
                flush=True,
            )

    print(
        f"[schema_matcher] TF-IDF pre-filter: {len(compact_tables)} → {len(result)} tables",
        flush=True,
    )
    return result


class SchemaMatcher:
    async def rank_candidates(
        self,
        chart_spec: dict,
        enriched: "EnrichedSchema",
        user_context: str = "",
        parsed_context: Optional[dict] = None,
    ) -> list[dict]:
        """
        Rank schema tables by relevance to this chart spec using the enriched schema.
        Returns up to 5 candidates sorted by confidence desc.

        user_context: raw user free-text (Mode 3) — added to LLM prompt for semantic boost.
        parsed_context: structured signals from context_parser — keywords boost TF-IDF.
        """
        tables = enriched.schema_doc.get("tables", [])
        if not tables:
            return []

        # Mode 3: extract context keywords to boost TF-IDF
        context_keywords: Optional[list] = None
        if parsed_context:
            context_keywords = parsed_context.get("context_keywords") or []
            if context_keywords:
                print(
                    f"[schema_matcher] Mode 3 context keywords injected into TF-IDF: {context_keywords[:8]}",
                    flush=True,
                )

        # TF-IDF pre-filter: send at most 25 tables to the LLM (wider net catches renamed/abbreviated tables)
        filtered_tables = _tfidf_prefilter(
            chart_spec, enriched.compact_tables, top_n=25, context_keywords=context_keywords
        )

        # Mode 3: force-include tables mentioned by name in the user context
        if user_context and filtered_tables:
            ctx_lower = user_context.lower()
            all_names = {t.get("name", "") for t in filtered_tables}
            force_names: list[str] = []
            for tbl in enriched.compact_tables:
                tname = tbl.get("name", "")
                # Check both qualified name and bare table name after the dot
                bare = tname.split(".")[-1]
                if (tname.lower() in ctx_lower or bare.lower() in ctx_lower) and tname not in all_names:
                    force_names.append(tname)
            if force_names:
                force_tbls = [t for t in enriched.compact_tables if t.get("name") in force_names]
                filtered_tables = filtered_tables[:max(0, 25 - len(force_tbls))] + force_tbls
                print(
                    f"[schema_matcher] Mode 3 forced {len(force_tbls)} context-named table(s): {force_names}",
                    flush=True,
                )

        chart_summary = {
            "type": chart_spec.get("type"),
            "title": chart_spec.get("title"),
            "x_axis_label": chart_spec.get("x_axis_label"),
            "y_axis_label": chart_spec.get("y_axis_label"),
            "x_tick_labels": (chart_spec.get("x_tick_labels") or [])[:10],
            "estimated_values": chart_spec.get("estimated_values") or {},
            "legend_labels": (chart_spec.get("legend_labels") or [])[:8],
            "data_point_count": chart_spec.get("data_point_count", 0),
        }

        disambiguation_block = enriched.get_disambiguation_text()
        # Only show semantics for the filtered tables to keep prompt short
        filtered_names = [t.get("name") for t in filtered_tables]
        semantics_block = enriched.get_table_semantics_text(table_names=filtered_names)

        # Build context block for LLM prompt (Mode 3)
        context_block = ""
        if user_context:
            context_block = (
                f"USER CONTEXT (highest priority — this is what the user says the chart shows):\n"
                f"\"{user_context.strip()}\"\n"
                f"Use this context to choose tables/columns that best match the described business data.\n"
            )
            if parsed_context:
                intent = parsed_context.get("chart_intent")
                groupby = parsed_context.get("implied_groupby_hint")
                agg = parsed_context.get("implied_aggregation")
                filters = parsed_context.get("implied_filters") or []
                dr = parsed_context.get("implied_date_range") or {}
                if intent:
                    context_block += f"Parsed intent: {intent}\n"
                if groupby:
                    context_block += f"Group by: {groupby}\n"
                if agg:
                    context_block += f"Aggregation: {agg}\n"
                if filters:
                    context_block += "Filters: " + ", ".join(
                        f"{f.get('column_hint')} {f.get('operator')} '{f.get('value')}'"
                        for f in filters
                    ) + "\n"
                if dr.get("start"):
                    context_block += f"Date range: {dr['start']} → {dr.get('end')}\n"

        user_msg = USER_TEMPLATE.format(
            chart_json=json.dumps(chart_summary, indent=2),
            context_block=context_block,
            disambiguation_block=disambiguation_block if disambiguation_block else "",
            semantics_block=semantics_block if semantics_block else "",
            schema_json=json.dumps(filtered_tables, indent=2),
            table_count=len(filtered_tables),
            db_type=enriched.db_type,
        )

        try:
            raw = await bedrock_invoke(
                model_id=MATCHER_MODEL,
                system_prompt=SYSTEM_PROMPT,
                user_message=user_msg,
                temperature=0.1,
            )
            raw = raw.strip()
            # Strip code fences
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
                raw = re.sub(r"\n?```\s*$", "", raw)
                raw = raw.strip()
            # Extract just the JSON array — Haiku often appends prose/notes after the
            # closing ']', which causes json.loads to fail with "Extra data".
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            if m:
                raw = m.group(0)
            candidates = json.loads(raw)
            if isinstance(candidates, list):
                # validate_names must use compact_tables names (schema-qualified when the DB
                # has non-default schemas) — NOT raw schema_doc names (bare names).
                # The LLM sees compact_tables in its prompt so its output uses the same format.
                valid_names = {t.get("name") for t in enriched.compact_tables}
                valid = []
                for c in candidates:
                    tbls = c.get("tables", [])
                    fixed = [t for t in tbls if t in valid_names]
                    if fixed:
                        c["tables"] = fixed
                        valid.append(c)

                # Deduplicate: keep only the highest-confidence entry per unique table set
                seen_keys: dict[tuple, dict] = {}
                for c in valid:
                    key = tuple(sorted(c.get("tables", [])))
                    if key not in seen_keys or c.get("confidence", 0) > seen_keys[key].get("confidence", 0):
                        seen_keys[key] = c
                valid = list(seen_keys.values())

                ranked = sorted(valid, key=lambda x: x.get("confidence", 0), reverse=True)[:5]

                # Validate join paths against the FK relationship graph
                for candidate in ranked:
                    tbls = candidate.get("tables", [])
                    if len(tbls) < 2:
                        continue
                    t_a, t_b = tbls[0], tbls[1]
                    graph = enriched.relationship_graph
                    if not graph.path_exists(t_a, t_b):
                        # No FK path — nullify the join suggestion.
                        # Only penalise confidence when the tables are in DIFFERENT schemas:
                        # same-schema tables often join without explicit FK declarations
                        # (implicit foreign keys) so penalising them tanks good candidates.
                        if candidate.get("join"):
                            same_schema = t_a.split(".")[0] == t_b.split(".")[0]
                            print(
                                f"[schema_matcher] ⚠ no FK path {t_a}↔{t_b} — "
                                f"clearing join"
                                + ("" if same_schema else ", penalising confidence"),
                                flush=True,
                            )
                            candidate["join"] = None
                            if not same_schema:
                                candidate["confidence"] = round(
                                    candidate.get("confidence", 0.5) * 0.6, 3
                                )
                    else:
                        # FK path confirmed — replace LLM-guessed condition with real one
                        real_cond = graph.get_join_condition(t_a, t_b)
                        if real_cond and not candidate.get("join"):
                            candidate["join"] = real_cond
                            print(
                                f"[schema_matcher] ✓ FK join confirmed {t_a}↔{t_b}: {real_cond}",
                                flush=True,
                            )

                # Re-sort after confidence adjustments
                ranked = sorted(ranked, key=lambda x: x.get("confidence", 0), reverse=True)

                # Fix 3: clear dimension/group_by key_columns that are declared FK columns.
                # The LLM sometimes picks a FK integer column (e.g. "replacement" which holds
                # a placement ID) as the dimension, producing IDs instead of category labels.
                # We use the table's relationships list as the source of truth for FK columns.
                _all_tbl_map = {t.get("name"): t for t in (enriched.compact_tables or [])}
                for _cand in ranked:
                    _key_cols = _cand.get("key_columns") or {}
                    _dim = _key_cols.get("dimension")
                    _grp = _key_cols.get("group_by")
                    if not _dim:
                        continue
                    # Build FK column set for all tables in this candidate
                    _fk_cols: set = set()
                    for _tname in (_cand.get("tables") or []):
                        _tbl_entry = _all_tbl_map.get(_tname, {})
                        for _rel in (_tbl_entry.get("relationships") or []):
                            if _rel.get("column"):
                                _fk_cols.add(_rel["column"])
                    if _dim in _fk_cols:
                        print(
                            f"[schema_matcher] ⚠ clearing FK dimension '{_dim}' "
                            f"(declared FK in {_cand.get('tables')}) — "
                            "will let query_agent use calc_col_map substitution instead",
                            flush=True,
                        )
                        _key_cols["dimension"] = None
                        if _grp == _dim:
                            _key_cols["group_by"] = None

                print(f"[schema_matcher] candidates: {[(c['tables'], round(c.get('confidence',0),2)) for c in ranked]}", flush=True)
                return ranked
        except Exception as e:
            print(f"[schema_matcher] ⚠ ranking failed: {e} — fallback to row_count order", flush=True)

        # Fallback: top 5 tables by row_count — use compact_tables so names are schema-qualified
        sorted_tables = sorted(enriched.compact_tables, key=lambda t: t.get("row_count") or 0, reverse=True)
        return [
            {
                "tables": [t["name"]],
                "key_columns": {"dimension": None, "metric": None, "date": None, "group_by": None},
                "join": None,
                "reasoning": "Fallback — largest table by row count",
                "confidence": max(0.05, 0.3 - i * 0.05),
            }
            for i, t in enumerate(sorted_tables[:5])
        ]
