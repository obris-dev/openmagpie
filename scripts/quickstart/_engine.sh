#!/bin/sh
#
# Engine (OpenAI-compatible LLM) setup for the quickstart, SOURCED by run.sh
# (after _lib.sh, whose env_get_default/env_set these build on). Kept out of
# run.sh so the orchestrator stays a readable top-to-bottom flow. Just function
# definitions, no side effects.
#
# The engine talks to any backend over the OpenAI /v1 API, so setup only needs an
# endpoint + (maybe) a key + a validate call - no backend identification.
#
# POSIX sh, like the rest of scripts/ (shellcheck -s sh in pre-commit + CI).

# Validate + list models at $1 (optional API key $2): echo the model ids one per
# line, empty on any failure (unreachable/auth/none). The probe lives in the
# Python module engine.scripts.probe (httpx-only, Django-free); the shell shells
# out so it never re-implements it. Runs from apps/core so `-m engine.scripts.probe`
# resolves the `engine` package (callers are cd'd to repo root).
engine_probe_models() {
    # Pass the key via ENGINE_PROBE_KEY (env), NOT a positional arg, so it never
    # lands in process argv where `ps`/`/proc/<pid>/cmdline` would leak it.
    ( cd apps/core || exit 1; ENGINE_PROBE_KEY="${2:-}" uv run --package openmagpie-core python -m engine.scripts.probe "$1" ) 2>/dev/null || true
}

# Echo the first common local /v1 endpoint that answers GET /v1/models, else
# nothing. A dumb reachability probe (no backend identification) to pre-fill the
# setup default. Needs curl; silent no-op without it.
engine_find_local() {
    command -v curl >/dev/null 2>&1 || return 0
    for base in http://localhost:11434/v1 http://localhost:8000/v1 http://localhost:1234/v1 http://localhost:8080/v1; do
        if curl -fsS -m 1 "$base/models" >/dev/null 2>&1; then
            printf '%s' "$base"
            return 0
        fi
    done
}

# host <-> container URL translation. The probe runs on the HOST (which may not
# resolve host.docker.internal, esp. on Linux); ENGINE_BASE_URL is consumed by the
# CONTAINER (which reaches the host via host.docker.internal, see compose
# extra_hosts). Remote/hosted URLs (api.openai.com, ...) are left untouched.
# Match only the WHOLE host (the name followed by `:`, `/`, or end-of-string), so
# `localhost.mycorp.com` isn't rewritten to `host.docker.internal.mycorp.com`.
# Three cases instead of an anchored alternation, which BSD sed (macOS) lacks.
engine_url_for_probe() {
    printf '%s' "$1" | sed \
        -e 's#//host\.docker\.internal:#//localhost:#' -e 's#//host\.docker\.internal/#//localhost/#' -e 's#//host\.docker\.internal$#//localhost#' \
        -e 's#//127\.0\.0\.1:#//localhost:#' -e 's#//127\.0\.0\.1/#//localhost/#' -e 's#//127\.0\.0\.1$#//localhost#'
}
engine_url_for_save() {
    printf '%s' "$1" | sed \
        -e 's#//localhost:#//host.docker.internal:#' -e 's#//localhost/#//host.docker.internal/#' -e 's#//localhost$#//host.docker.internal#' \
        -e 's#//127\.0\.0\.1:#//host.docker.internal:#' -e 's#//127\.0\.0\.1/#//host.docker.internal/#' -e 's#//127\.0\.0\.1$#//host.docker.internal#'
}

# Pick a model from a NON-EMPTY newline-separated list ($1) by number or by typing
# a listed id (the only caller, configure_engine, returns early before here when the
# list is empty). Parses by LINE (awk NR / sed Np / grep -xF), not IFS word-splitting,
# so a model id can't mis-split and it behaves the same in any shell. Prompts on
# /dev/tty (so menu text doesn't pollute the captured value); echoes the chosen id.
pick_model() {
    _models="$1"
    printf '%s\n' "$_models" | awk '{ printf "    %d) %s\n", NR, $0 }' > /dev/tty
    _count="$(printf '%s\n' "$_models" | wc -l | tr -d ' ')"
    while : ; do
        printf '  Which model? [1]: ' > /dev/tty
        IFS= read -r _sel < /dev/tty || _sel=""
        _sel="${_sel:-1}"
        case "$_sel" in
            *[!0-9]*)
                # A typed id: accept only if it's actually in the list, so a stray
                # "skip" or a typo can't become ENGINE_MODEL; otherwise re-ask.
                if printf '%s\n' "$_models" | grep -qxF "$_sel"; then
                    printf '%s' "$_sel"
                    return
                fi
                printf '    "%s" is not in the list; pick a number or a listed id\n' "$_sel" > /dev/tty
                continue
                ;;
        esac
        if [ "$_sel" -ge 1 ] && [ "$_sel" -le "$_count" ]; then
            printf '%s\n' "$_models" | sed -n "${_sel}p"  # the Nth listed id
            return
        fi
        printf '    pick 1-%s, or type a model id\n' "$_count" > /dev/tty  # out of range -> re-ask
    done
}

# Resolve ENGINE_BASE_URL/MODEL/API_KEY into apps/core/.env BEFORE `up` (so the
# container reads the final config). Interactive: ask where the LLM is (pre-filled
# with a reachable local endpoint if one's up), the key, then validate + pick a
# model from its /v1/models. Non-interactive: keep whatever .env has (the example
# defaults boot fine). The API key is never echoed.
configure_engine() {
    _envf="apps/core/.env"
    [ -t 1 ] && [ -r /dev/tty ] || return 0

    _cur_url="$(env_get_default ENGINE_BASE_URL)"
    # Default to a reachable local endpoint if one's up, else the current .env
    # value (normalized to localhost for the host-side probe + prompt).
    _default="$(engine_find_local)"
    [ -n "$_default" ] || _default="$(engine_url_for_probe "$_cur_url")"

    printf '\n%s\n' "Where is your LLM running? We need the URL of an OpenAI-compatible /v1 endpoint (e.g. Ollama, vLLM, LM Studio, llama.cpp, or OpenAI itself)."
    _skipped=0
    while : ; do
        printf '  URL [%s]: ' "$_default" > /dev/tty
        IFS= read -r _in_url < /dev/tty || _in_url=""
        _url="$(printf '%s' "${_in_url:-$_default}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

        # Blank keeps the existing key (used by BOTH the probe below and the
        # write), so a returning hosted user who presses Enter doesn't blank their
        # key or fail the probe. "none" when there's nothing to keep.
        _cur_key="$(env_get ENGINE_API_KEY "$_envf")"
        [ -n "$_cur_key" ] && _khint="****, Enter keeps it" || _khint="none"
        printf '  API key (blank for a local server) [%s]: ' "$_khint" > /dev/tty
        # Read the key with echo off; restore it (and abort) on Ctrl-C too, so an
        # interrupt mid-prompt never leaves the terminal stuck with echo disabled.
        trap 'stty echo 2>/dev/null; exit 130' INT
        stty -echo 2>/dev/null || true
        IFS= read -r _key < /dev/tty || _key=""
        stty echo 2>/dev/null || true
        trap - INT
        printf '\n' > /dev/tty
        # Trim whitespace: an invisible trailing space on a pasted (masked) key
        # would otherwise cause a baffling probe-retry loop.
        _key="$(printf '%s' "${_key:-$_cur_key}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

        printf '  Checking %s ...\n' "$_url" > /dev/tty
        _models="$(engine_probe_models "$_url" "$_key")"
        [ -n "$_models" ] && break

        # Common gotcha: an OpenAI base URL ends in /v1. If the bare URL found
        # nothing but adding /v1 does, adopt it (capture the list from that probe
        # so we don't re-hit the server).
        case "$_url" in
            */v1 | */v1/) ;;
            *)
                _try="${_url%/}/v1"
                _models="$(engine_probe_models "$_try" "$_key")"
                if [ -n "$_models" ]; then
                    printf '  -> found it at %s (OpenAI base URLs end in /v1); using that.\n' "$_try" > /dev/tty
                    _url="$_try"
                    break
                fi
                ;;
        esac

        # Still nothing. Surface the usual URL mistakes (we found them confusing
        # to debug blind), flagging the ones that look off, before re-prompting.
        # The LLM is optional - the stack boots either way - so "skip" continues.
        printf '  ! no OpenAI-compatible LLM reached at %s. Check:\n' "$_url" > /dev/tty
        case "$_url" in
            http://* | https://*) ;;
            *) printf '      - scheme: prefix it with http:// (or https://)\n' > /dev/tty ;;
        esac
        _auth="${_url#*://}"
        _auth="${_auth%%/*}"
        case "$_auth" in
            *:*) ;;
            *) printf '      - port: add it (e.g. :11434 Ollama, :8000 vLLM, :1234 LM Studio, :8080 llama.cpp)\n' > /dev/tty ;;
        esac
        case "$_url" in
            */v1 | */v1/) ;;
            *) printf '      - path: OpenAI base URLs end in /v1\n' > /dev/tty ;;
        esac
        printf '      - and that the server is running and reachable from this machine\n' > /dev/tty
        printf '    Re-enter the endpoint, or type "skip" to continue without one [retry]: ' > /dev/tty
        IFS= read -r _ans < /dev/tty || _ans="skip"
        case "$_ans" in
            skip | SKIP) _skipped=1; break ;;
            *) _default="$_url" ;;  # re-loop with what they typed pre-filled
        esac
    done

    # Skipped (no LLM): leave .env entirely untouched (writing the last typed,
    # unreachable URL would clobber a returning user's good config), and don't ask
    # for a model. It boots on whatever .env already has (the engine.W001 warning
    # flags a missing model).
    if [ "$_skipped" = 1 ]; then
        printf '  Continuing without configuring an LLM; %s left as-is. Set ENGINE_BASE_URL/ENGINE_MODEL there when ready.\n' "$_envf" > /dev/tty
        return 0
    fi
    _model="$(pick_model "$_models")"

    _save_url="$(engine_url_for_save "$_url")"
    env_set ENGINE_BASE_URL "$_save_url" "$_envf"
    [ -n "$_model" ] && env_set ENGINE_MODEL "$_model" "$_envf"
    # Only write a non-empty key: a blank prompt (e.g. pressing Enter on a re-run)
    # means "keep what's there", not "wipe the existing ENGINE_API_KEY". (env_set
    # chmod 600's the file, so no separate chmod is needed here.)
    [ -n "$_key" ] && env_set ENGINE_API_KEY "$_key" "$_envf"

    # Summarize the STORED config (a blank prompt may have preserved an existing
    # model/key), not just this run's input.
    _stored_model="$(env_get ENGINE_MODEL "$_envf")"
    _masked="(none)"
    [ -n "$(env_get ENGINE_API_KEY "$_envf")" ] && _masked="****"
    printf '  Using LLM: url=%s model=%s key=%s\n' "$_save_url" "${_stored_model:-<unset>}" "$_masked"
}
