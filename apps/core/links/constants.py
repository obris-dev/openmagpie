"""Short-link code generation + validation rules.

Auto-generated codes are a fixed-length base62 token (URL-safe, nothing to
strip). A caller-supplied custom slug is validated against SLUG_RE instead: the
same URL-safe set plus '-' and '_', up to CODE_MAX_LENGTH (the `code` column width).
"""

import re
import string

# Base62 (digits + lower + upper). No look-alike stripping: codes are clicked, not typed.
CODE_ALPHABET = string.digits + string.ascii_letters
CODE_LENGTH = 6
# The `code` column width. The custom-slug bound and the model column derive from it.
CODE_MAX_LENGTH = 64

# A custom (caller-supplied) slug: URL-safe, 1..CODE_MAX_LENGTH chars.
SLUG_PATTERN = rf"^[A-Za-z0-9_-]{{1,{CODE_MAX_LENGTH}}}$"
SLUG_RE = re.compile(SLUG_PATTERN)

# Display placeholder when a click has no CF-IPCountry (unknown / not geolocated).
UNKNOWN_COUNTRY = "??"
# ISO 3166-1 alpha-2; single-sources the `country` column width and the CF-header slice.
COUNTRY_CODE_LENGTH = 2

# Cloudflare edge headers (WSGI META keys), trusted only when SHORTLINK_TRUST_CF_HEADERS
# is on (i.e. the origin is reachable only through the CF tunnel). Off-tunnel these
# are client-forgeable, so the config gates whether they're read.
CF_CONNECTING_IP_HEADER = "HTTP_CF_CONNECTING_IP"
CF_IPCOUNTRY_HEADER = "HTTP_CF_IPCOUNTRY"

# One ClickEvent per visitor per link within this window: a refresh or crawler
# loop from the same IP collapses to a single row, bounding writes to the shared
# DB. 30m kills rapid re-hits while a genuine later revisit still counts.
CLICK_DEDUP_WINDOW_SECONDS = 30 * 60
# Dedicated cache alias (see settings CACHES) so the high-volume public dedup writes
# never cull the default cache's scheduler job locks / sessions / throttle counters.
CLICK_DEDUP_CACHE_ALIAS = "clickdedup"
