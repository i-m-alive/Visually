import re
from typing import Tuple

BLOCKED_KEYWORDS = [
    r'\bINSERT\b', r'\bUPDATE\b', r'\bDELETE\b', r'\bDROP\b',
    r'\bCREATE\b', r'\bALTER\b', r'\bTRUNCATE\b', r'\bEXEC\b',
    r'\bEXECUTE\b', r'\bGRANT\b', r'\bREVOKE\b', r'\bCOPY\b',
]


def validate_sql(sql: str) -> Tuple[bool, str]:
    sql_stripped = sql.strip()
    for pattern in BLOCKED_KEYWORDS:
        if re.search(pattern, sql_stripped, re.IGNORECASE):
            return False, f"Query contains disallowed operation matching: {pattern}"

    upper = sql_stripped.upper().lstrip()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return False, "Only SELECT or WITH queries are allowed"

    return True, ""
