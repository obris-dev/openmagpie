# Connector credentials

Session material some connectors can use: cookie exports, per-session proxy
pins. Everything in this directory except this README is gitignored — nothing
you put here can be committed.

One subdirectory per connector:

```
credentials/
  twitter/    # x.com cookie exports (*.json), optional <name>.proxy pins
  youtube/    # Netscape-format cookies.txt for age-gated extraction
```

Point the connector settings here with absolute paths (inside the dev
containers the repo is mounted at /app):

```
TWITTER_CREDENTIALS_DIR=/app/apps/core/credentials/twitter
YOUTUBE_COOKIES_FILE=/app/apps/core/credentials/youtube/cookies.txt
```

Settings are read per poll, so a refreshed export applies on the next cycle
without a restart. `.env` changes themselves still need the usual
`docker compose up -d --force-recreate`.

## X / Twitter: generating a cookie export

The `twitter_search` connector authenticates with an existing x.com browser
session; there is no API key.

1. Sign in to x.com in a browser.
2. The minimal route needs just two cookies. In DevTools (Application ->
   Cookies -> https://x.com), copy the values of `auth_token` and `ct0`
   and set them directly:

   ```
   TWITTER_COOKIE_AUTH_TOKEN=<value>
   TWITTER_COOKIE_CT0=<value>
   ```

3. For the file route instead, export the site's cookies with a browser
   extension such as Cookie-Editor (export as JSON) or "Get cookies.txt
   LOCALLY" (JSON export). Both shapes are accepted: a plain
   `{name: value}` dict, or the extension's array of cookie objects. Save
   it as `credentials/twitter/<name>.json`; the connector picks the first
   usable export (sorted by filename) that carries the `auth_token`/`ct0`
   pair.
4. Optional: pin an egress proxy for one export by writing its URL to
   `credentials/twitter/<name>.proxy` (same basename). `TWITTER_PROXY`
   sets a global one.

The full priority order (first configured route wins):
`TWITTER_COOKIES_JSON` (inline JSON dict) -> the `auth_token`/`ct0` pair ->
`TWITTER_COOKIES_FILE` (one export) -> `TWITTER_CREDENTIALS_DIR`.

Sessions expire when X rotates them (or you log out in that browser);
re-export and the next poll picks it up.

## YouTube: generating cookies.txt (optional)

Public YouTube search needs no credentials at all — set this up only if
poll logs show relevant videos skipped as age-gated ("Sign in to confirm
your age").

1. Sign in to youtube.com, ideally in a private/incognito window (yt-dlp's
   recommendation: export from a private session you then close, so the
   browser doesn't rotate the exported cookies out from under you).
2. Export the cookies in **Netscape format** with a "Get cookies.txt
   LOCALLY"-style extension while on youtube.com (yt-dlp requires the
   cookies.txt format here, not JSON).
3. Save it as `credentials/youtube/cookies.txt` and set
   `YOUTUBE_COOKIES_FILE` as above.

It's recommended to use throwaway accounts for any cookies that land here:
platforms flag and sometimes lock accounts whose sessions show up in
automated traffic. These connectors use unofficial routes that may conflict
with a platform's terms of service — use at your own risk.
