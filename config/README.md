# Your OpenMagpie config

Your feed and watch configs live here. `templates/quickstart/` is the template
the quickstart starts from; when you run the quickstart it writes your seeded
listener to `quickstart/`. Keep configs you write from here on in this directory
too.

## Layout

- `templates/quickstart/` (checked in): the template the quickstart seeds from.
  A clean starting point to copy.
- `quickstart/` (created by the quickstart, not checked in): your seeded feed and
  watch as editable YAML, with your subreddits, your filter, and the real feed id.

## The pieces

**Feed** (`feed.yaml`): the set of sources OpenMagpie polls for new posts. A feed
only gathers posts; it does not decide what matters.

**Watch** (`watch.yaml`): subscribes to one or more feeds and runs an ordered
chain of actions over each new post. Its `feed_ids` is what links it to a feed.

**Actions**: the ordered steps a watch runs on each new post from its feeds.

- `semantic_filter`: your LLM rates how well each post matches your plain-language
  `instructions`, from 0 to 1, and the post passes when that relevance score is at
  least `threshold` (0 keeps everything, 1 only exact matches; higher is stricter).
  This is what makes a watch a listener instead of a firehose.
- `log`: prints the posts that clear the filter (the prefixed lines you see when
  the pipeline runs).
- `webhook` (optional): POSTs each match to a URL. See [examples/README.md](../examples/README.md).

## Editing and reusing

To change your listener, edit `quickstart/feed.yaml` or `quickstart/watch.yaml`
and re-apply to the existing feed/watch (the ids print in the quickstart summary,
or from `magpie feed list` / `magpie watch list`):

    magpie feed edit <feed-id> -f config/quickstart/feed.yaml
    magpie watch edit <watch-id> -f config/quickstart/watch.yaml

To build a separate listener, copy a config, change its `name`, and create it.
Create the feed first, then put its printed id into the new watch's `feed_ids`.
(If you copy the template `watch.yaml`, that means replacing its
`REPLACE_WITH_FEED_ID` placeholder, which `watch create` rejects as-is.)

    magpie feed create -f config/my-feed.yaml
    magpie watch create -f config/my-watch.yaml

A freshly created feed scores only posts that arrive after it. To backfill (score
recent posts on the first tick), set a past `last_event_at` on each source in the
feed YAML before creating it, e.g. `last_event_at: 2026-01-01T00:00:00Z`.

Inspect a watch any time:

    magpie watch list
    magpie watch action get <action-id>
    magpie activity list --action <action-id>
