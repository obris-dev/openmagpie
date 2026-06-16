# VHS tape for the README Quickstart demo (https://github.com/charmbracelet/vhs):
# the REAL `curl | sh` install, montaged. Record with
# scripts/demo/quickstart-record.sh, never with `vhs` directly: the recorder
# runs this from a throwaway ../demo directory with the main stack down and a
# COMPOSE_PROJECT_NAME that keeps the demo's containers and volumes away from
# the dev stack's.
#
# The montage: the typed command and the quickstart's seven prompts play in real
# time - first the engine prompts (clone dir, LLM URL, API key, model), then,
# after the build, the listener prompts the seed asks (which subreddits, what to
# flag, how strict). The build and the scoring grind hide behind Hide/Show jump
# cuts. The hidden windows use FIXED sleeps, not Wait: vhs's text-buffer polling
# (what Wait reads) goes stale minutes into a session while its screenshot
# pipeline stays live, so a mid-take Wait+Screen hangs to its timeout even with
# the anchor text on screen. Sleeps are time-based and immune. Wait+Line is still
# used for the prompts: the engine ones land in the session's first minute and
# the listener ones block on /dev/tty read, so they sit on screen until typed and
# Wait+Line catches them whenever the build's hidden window ends.
#
# The hidden sleeps are tuned to a WARM docker layer cache (the build runs ~30s;
# scoring the day's backlog runs ~1-2 min, an LLM call per post). Beat shape:
# hide the build, surface for the three listener prompts, hide the scoring grind
# with margin (overshoot is safe, the screen settles on the summary), surface for
# "Tick done." + the Ready summary. On a cold cache the build outlasts its sleep,
# so pre-warm by running the quickstart once before recording, and tune by
# retaking: a take is only a few minutes.
#
# Rate-limit note: repeated takes re-poll the same subreddits and Reddit starts
# throttling. The connector backs off and retries (an on-screen INFO line, not a
# warning), but back-to-back takes still record slower scoring; space them out.
#
# The model beat types qwen2.5:7b (the README's suggested model); the recorder
# verifies it is on the detected endpoint first, since a typed id that is not in
# the list re-prompts and derails the take.

Output quickstart.gif

Set Shell bash
Set FontSize 15
Set Width 1500
Set Height 800
Set Padding 16
Set TypingSpeed 30ms
Set Theme "Catppuccin Mocha"
Set WaitTimeout 5m

Type "# One command to your first real match"
Enter
Sleep 1.5s
Type "curl -fsSL https://openmagpie.ai | sh"
Enter

Wait+Line /Clone into \[/
Sleep 1.5s
Enter

Wait+Line /URL \[/
Sleep 2s
Enter

Wait+Line /API key/
Sleep 1.5s
Enter

Wait+Line /Which model\?/
Sleep 2s
Type "qwen2.5:7b"
Enter
Sleep 2s

# Build + migrate + install the CLI (hidden), then the seed's three listener
# prompts surface. They block on /dev/tty read, so each waits on screen until
# typed; Wait+Line re-syncs after the hidden build window regardless of its
# exact length.
Hide
Sleep 30s
Show

Wait+Line /Which subreddits/
Sleep 1.5s
Type "selfhosted, LocalLLaMA"
Enter

Wait+Line /What should it flag/
Sleep 1.5s
Type "self-hosting and open-source alternatives to paid software"
Enter

Wait+Line /Match threshold/
Sleep 2s
Enter
Sleep 6s

# Scoring grind (an LLM call per post) hides; surface on "Tick done." + summary.
Hide
Sleep 130s
Show
Sleep 14s
