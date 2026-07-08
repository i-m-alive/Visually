"""
Project "domain" — which vertical a project's agent behaviors are tuned for.

A project's `domain` (see shared.models.projects.Project.domain) selects:
  - which AGENT SKILL INTENTS vocabulary the intent classifier recognizes
  - which skill-agent personas handle MATCH/BRIEFING/AUDIT/PROSPECT-type requests
  - whether the Brainwave-employee access gate applies
  - whether the recruitment ownership SQL filter (user_context_builder) applies

Add a new vertical by: adding its name to VALID_DOMAINS, adding it to
SKILL_DOMAINS, and adding a matching module to skill_agents/_SKILL_MODULES
plus a vocabulary block in intent_classifier.py.
"""
import os

VALID_DOMAINS = frozenset({"recruitment", "finance", "generic"})
DEFAULT_DOMAIN = "recruitment"

# Domains with an implemented set of skill agents (MATCH/BRIEFING/AUDIT/PROSPECT).
# "generic" has none on purpose — those requests always go through the normal
# chart/SQL pipeline instead of being routed to a vertical-specific persona.
SKILL_DOMAINS = frozenset({"recruitment", "finance"})

# Domains whose access gate requires a BrainwaveUserProfile row. Only the
# original recruitment product is tied to that identity model.
BRAINWAVE_GATED_DOMAINS = frozenset({"recruitment"})

# Explicit host allowlist for the Brainwave gate — comma-separated substrings/
# suffixes of the CONNECTED DATABASE's host, e.g.
#   BRAINWAVE_DB_HOSTS=prod-brainwave-cluster.xyz.redshift.amazonaws.com,staging-brainwave.xyz.redshift.amazonaws.com
# Case-insensitive substring match against DatabaseConnection.host. This makes
# the recruitment access gate identify the ACTUAL connected database, not just
# the project's domain toggle, so a random Postgres/Snowflake connection on a
# "recruitment" project never gets wrongly blocked or profile-gated.
# If unset, no additional host restriction is applied (falls back to
# domain-only gating — today's behavior) so nothing breaks before this is
# configured.
_BRAINWAVE_HOST_PATTERNS = [
    h.strip().lower() for h in os.getenv("BRAINWAVE_DB_HOSTS", "").split(",") if h.strip()
]


def normalize_domain(domain: str | None) -> str:
    """Coerce any input to a known domain, defaulting invalid/missing values."""
    return domain if domain in VALID_DOMAINS else DEFAULT_DOMAIN


def is_brainwave_host(host: str | None) -> bool:
    """True if `host` matches a configured Brainwave DB host pattern.

    When BRAINWAVE_DB_HOSTS is not configured, this imposes no restriction
    (returns True) — set the env var to tighten the recruitment access gate to
    only the actual Brainwave database(s).
    """
    if not _BRAINWAVE_HOST_PATTERNS:
        return True
    if not host:
        return False
    host_l = host.lower()
    return any(pat in host_l for pat in _BRAINWAVE_HOST_PATTERNS)
