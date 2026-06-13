# VHS tape for the README Quickstart demo (https://github.com/charmbracelet/vhs):
# the REAL `curl | sh` install, montaged. Record with
# scripts/demo/quickstart-record.sh, never with `vhs` directly: the recorder
# runs this from a throwaway ../demo directory with the main stack down and a
# COMPOSE_PROJECT_NAME that keeps the demo's containers and volumes away from
# the dev stack's.
#
# The montage: the typed command and the quickstart's own prompts (clone dir,
# LLM URL, API key, model) play in real time; the build/seed and the gap before
# the summary hide behind Hide/Show jump cuts on FIXED sleeps. Not Wait: vhs's
# text-buffer polling (what Wait reads) goes stale minutes into a session while
# its screenshot pipeline stays live, so a mid-take Wait+Screen hangs to its
# timeout even with the anchor text sitting on screen. Sleeps are time-based
# and immune. Wait+Line is still used for the four prompts: those land in the
# session's first minute, inside the window where the buffer is fresh.
#
# The hidden sleeps are tuned to a WARM docker layer cache (the whole run is
# ~90s; the drain kicks off ~30s after the model pick and scoring runs ~20s).
# Beat shape: surface for the drain kickoff ("You'll see progress every
# ~minute"), hide the scoring grind with margin (overshoot is safe, the screen
# settles on the summary), surface for "Tick done." + the Ready summary. On a
# cold cache the build alone outlasts the sleeps, so pre-warm by running the
# quickstart once before recording, and tune by retaking: a take is only a
# few minutes.
#
# Rate-limit note: repeated takes re-poll the same subreddits and Reddit
# starts throttling. The connector backs off and retries (an on-screen INFO
# line, not a warning), but back-to-back takes still record slower scoring;
# space takes out, or record one with STARTER=devtools to rotate subreddits.
#
# The model beat types qwen2.5:7b (the README's suggested model); the recorder
# verifies it is on the detected endpoint first, since a typed id that is not
# in the list re-prompts and derails the take.

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

Hide
Sleep 30s
Show
Sleep 8s

Hide
Sleep 150s
Show
Sleep 14s
