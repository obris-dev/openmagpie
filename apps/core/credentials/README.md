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

It's recommended to use throwaway accounts for any cookies that land here:
platforms flag and sometimes lock accounts whose sessions show up in
automated traffic. These connectors use unofficial routes that may conflict
with a platform's terms of service — use at your own risk.
