# Orchestrator Skills

Models:
- `BEDROCK_HAIKU_MODEL` for dashboard decomposition
- Delegates to Intent Classifier, Query Agent, Validator Agent, Vision Agent

## Skills

### run_single_viz_pipeline
End-to-end pipeline for a single chart request:
1. Classify intent (IntentClassifier)
2. Fetch live schema (latest SchemaSnapshot)
3. Generate SQL (QueryAgent)
4. Execute SQL (QueryExecutor service)
5. Validate result (ValidatorAgent) — up to 3 retries
6. Render chart (Render service)
7. Persist Widget + emit WebSocket events

**Script:** `scripts/run_single_viz_pipeline.py`

---

### run_dashboard_pipeline
End-to-end pipeline for a multi-chart dashboard request:
1. Classify intent → DASHBOARD
2. Decompose into 2–5 sub-intents using Haiku
3. Run `run_single_viz_pipeline` for each sub-intent in parallel (asyncio.gather)
4. Create Dashboard + Widgets
5. Emit `dashboard.assembled` WebSocket event

**Script:** `scripts/run_dashboard_pipeline.py`

---

### run_screenshot_pipeline
Screenshot replication pipeline:
1. Normalise uploaded images (Pillow, 1000px wide, RGB PNG)
2. Vision Agent → detect all charts + bounding boxes
3. Deduplicate across multiple screenshots
4. Assign grid layout positions
5. For each chart, run `_run_chart_replication_loop` (up to 5 attempts):
   - QueryAgent.generate_from_chart_spec
   - Execute SQL
   - ValidatorAgent.score_chart_screenshot_mode (95% threshold)
   - Attempt 4: request user hint (90s wait via asyncio.Event)
   - Attempt 5: accept best result regardless of score
6. Assemble final dashboard with grid layout from chart specs

**Script:** `scripts/run_screenshot_pipeline.py`
