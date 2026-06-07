# Validator Agent Skills

Models:
- `BEDROCK_SONNET_MODEL` for structural/semantic validation
- `BEDROCK_HAIKU_MODEL` for axis-label similarity checks

## Skills

### validate_chart_type
Verifies that the SQL result shape (column count, dtypes) matches the requested chart type.
Returns a boolean pass/fail with a rationale string.

**Script:** `scripts/validate_chart_type.py`

---

### validate_axis_labels
Uses the Haiku model to check whether the SQL result column names semantically match the
requested x/y axis labels. Returns a similarity score (0–1).

**Script:** `scripts/validate_axis_labels.py`

---

### analyze_shape_match
Compares the SQL result data shape against the chart specification:
- Row count plausibility
- Column cardinality for categorical dimensions
- Numeric range plausibility for metrics

Returns a score (0–1) and a list of issues.

**Script:** `scripts/analyze_shape_match.py`

---

### check_completeness
Checks whether the result set covers the full date/category range implied by the intent.
Returns coverage ratio and missing segments.

**Script:** `scripts/check_completeness.py`

---

### generate_structured_feedback
Synthesises validation dimension scores into a prioritised retry prompt that the Query Agent
can use to correct the SQL on the next attempt.

**Script:** `scripts/generate_structured_feedback.py`

---

### score_screenshot_mode
Screenshot-replication validator. Compares live query results against vision-extracted chart
data using:
- **DTW distance** for time-series shape (normalised values)
- **KL divergence** for categorical distributions (bar/pie)
- **Label similarity** for axis/title text

Threshold: 95% for acceptance (vs 80% in intent mode).

**Script:** `scripts/score_screenshot_mode.py`
