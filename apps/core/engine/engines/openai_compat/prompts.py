"""Prompts + content sizing for the relevance judgment call.

Lifted out of engine.py so a copy-edit on the scorer prompt doesn't churn the
engine class's git history (and so prompt iteration shows up as a 1-file diff in
review, not buried in a method). The schema is described in `SYSTEM_PROMPT` itself
so the model emits the right shape even on backends that don't enforce a JSON
schema (a `json_object` engine, e.g. TGI); strict `json_schema` adds machine
enforcement on top.
"""

SYSTEM_PROMPT = """You are a precise relevance scorer. Given a user's stated interest and an event observed from a source (Reddit, GitHub, etc.), score how strongly the event matches that interest.

Respond with a JSON object matching this schema:
- score: float between 0.0 and 1.0, relevance to the user's interest (0.0 = not relevant at all, 1.0 = an obvious, direct match)
- reason: short string under 200 characters explaining the score"""

USER_PROMPT_TEMPLATE = """User interest:
{instructions}

Item:
  Source: {source}
  Title: {title}
  Content: {content}

Respond with JSON only."""

# Cap the payload body we hand the model. Most payloads are well under this; the
# cap matters for the long-form ones (Reddit `selftext` at the upper bound).
# Truncation is purely a cost/latency lever; the title alone is usually enough signal.
CONTENT_TRUNCATE = 2000
