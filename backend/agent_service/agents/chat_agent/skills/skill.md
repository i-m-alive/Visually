# Chat Agent Skills

Model: `BEDROCK_SONNET_MODEL` (configurable via `BEDROCK_SONNET_MODEL_ID` env var)

## Skills

### build_system_prompt
Constructs the per-session system prompt by injecting:
- Live database schema summary (table names, key columns)
- Current dashboard widget list (title, chart type, SQL snippet)
- Conversation guidelines (stay data-focused, never reveal raw credentials)

**Script:** `scripts/build_system_prompt.py`

---

### respond_with_history
Sends the full conversation history + new user message to Bedrock and returns the assistant
reply. Maintains the Anthropic messages format `[{role, content}]`.

**Script:** `scripts/respond_with_history.py`

---

### manage_conversation_history
Handles Redis-backed conversation storage:
- Load history by session key (`chat:{project_id}:{user_id}`)
- Append new user/assistant turns
- Trim to last N turns when over limit
- TTL: 4 hours

**Script:** `scripts/manage_conversation_history.py`
