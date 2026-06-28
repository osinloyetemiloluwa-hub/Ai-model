#!/usr/bin/env bash
# LDD_STATUSLINE_v1 — auto-installed by the LDD host-statusline skill.
# Renders a single-line permanent LDD status: task · loop · loss · sparkline.
# Reads .ldd/trace.log (Tier 0) or falls back to current session JSONL
# grepped for ⟪LDD-TRACE-v1⟫ markers (Tier 2).
#
# v0.10.4 adds:
#   * Budget-burn label   — k=N/K_MAX (K_MAX from $LDD_K_MAX, default 5)
#   * Elapsed label       — wall time from meta-line to last iter-line
#   * ⚠ plateau / ⚠ regression warnings — derived from last 2-3 Δloss values
#
# Do not edit in place — the skill detects this marker and reinstalls.
# Customise the single project's statusline by overriding .ldd/statusline.sh
# AND removing the marker line, so the skill leaves your copy alone.

set -uo pipefail
export LC_ALL=C  # force '.' as decimal separator for awk + printf

BLOCKS=('▁' '▂' '▃' '▄' '▅' '▆' '▇' '█')

input=$(cat)
cwd=$(jq -r '.cwd // .workspace.current_dir // "."' <<<"$input" 2>/dev/null || echo ".")
transcript=$(jq -r '.transcript_path // empty' <<<"$input" 2>/dev/null || echo "")
hook_sid=$(jq -r '.session_id // empty' <<<"$input" 2>/dev/null || echo "")

trace_file="${cwd}/.ldd/trace.log"
session_marker="${cwd}/.ldd/sessions/${hook_sid}"   # v0.13.1+ per-session
legacy_marker="${cwd}/.ldd/session_active"          # pre-v0.13.1 singular
task=""; loop=""; last_k=""; losses=""; source_tag=""
level=""; level_name=""; creativity=""
terminal=""  # v0.12.0 — show ✓/⚠/✗ when the current task closed

k_max="${LDD_K_MAX:-5}"
elapsed_label=""
warning_label=""

# Session gate — render the current LDD state only if LDD was actually used
# in THIS Claude-Code session. Mirror of scripts/ldd_trace/session_gate.py.
#
# v0.13.1+ multi-clauding layout: existence of .ldd/sessions/<hook_sid>.
# Legacy v0.13.0 fallback: singular .ldd/session_active with id equality.
gate_allows=0
if [[ -n "$hook_sid" && -f "$session_marker" ]]; then
    gate_allows=1
elif [[ -f "$legacy_marker" ]]; then
    marker_sid=$(head -1 "$legacy_marker" 2>/dev/null | grep -oP 'session_id=\K.*' || true)
    if [[ -z "${marker_sid:-}" || -z "$hook_sid" ]]; then
        gate_allows=1
    elif [[ "$marker_sid" == "$hook_sid" ]]; then
        gate_allows=1
    fi
fi

# Heartbeat file selection — prefer per-session (v0.13.1+), fall back to
# singular for projects still on the legacy layout.
hb_file="${cwd}/.ldd/heartbeat"
if [[ -n "$hook_sid" && -f "${cwd}/.ldd/heartbeats/${hook_sid}" ]]; then
    hb_file="${cwd}/.ldd/heartbeats/${hook_sid}"
fi

if [[ -f "$trace_file" && "$gate_allows" == "1" ]]; then
    # Extract just the CURRENT task section: everything from the last `meta`
    # line onward. trace.log is append-only and accumulates tasks; reading
    # the whole file mixes sparklines / task titles / losses across runs.
    current_section=$(awk '
        $2 == "meta" { buf = $0 ORS; next }
        { buf = buf $0 ORS }
        END { printf "%s", buf }
    ' "$trace_file")

    # Multi-clauding: prefer the per-session task pointer so each concurrent
    # session sees ITS own task. Fall back to scanning the current section of
    # trace.log for projects / sessions that predate the per-session marker.
    task=""
    if [[ -n "$hook_sid" && -f "$session_marker" ]]; then
        task=$(grep -oP '^task=\K.*' "$session_marker" | head -1)
    fi
    if [[ -z "$task" ]]; then
        # trace.log writes `task=<name>` (unquoted) from init; accept the
        # legacy `task="<name>"` form too.
        task=$(grep -m1 -oP 'task="?\K[^"\s]+' <<<"$current_section" 2>/dev/null || true)
    fi
    # v0.11.0: loss field is `loss=`; accept legacy `loss_norm=` too for trace
    # files written by pre-v0.11.0 agents that still co-exist during rollout.
    losses=$(grep -oE 'loss(_norm)?=[0-9.]+' <<<"$current_section" 2>/dev/null | sed -E 's/loss(_norm)?=//' | tail -30)
    # v0.11.0: `design` replaces `architect` on iter lines; accept both.
    # Filter out `close` lines — those carry `terminal=` but no `k=N`, so the
    # second grep for `k=[0-9]` keeps only iteration lines (including
    # `baseline` iters which still carry `k=0`).
    last_line=$(grep -E '^\S+[[:space:]]+(design|architect|inner|refine|outer|cot)[[:space:]]' <<<"$current_section" 2>/dev/null | grep -E ' k=[0-9]+' | tail -1)
    loop=$(awk '{print $2}' <<<"$last_line")
    # Normalize legacy `architect` loop name to `design` for display.
    [[ "$loop" == "architect" ]] && loop="design"
    last_k=$(grep -oP 'k=\K[0-9]+' <<<"$last_line" | head -1)

    # Terminal state — render ✓/⚠/✗ when the current task has closed AND no
    # further iteration followed. The close line is the last non-blank line
    # of the current section in the common case.
    last_content=$(grep -vE '^[[:space:]]*$' <<<"$current_section" | tail -1)
    if grep -qE '[[:space:]]close[[:space:]]' <<<"$last_content"; then
        terminal=$(grep -oP 'terminal=\K\w+' <<<"$last_content" | head -1)
    fi

    # Pull the level + name + creativity from the current section's meta
    # (which is its first line by construction — it is the latest meta in
    # the whole trace.log).
    meta_line=$(grep -E '^\S+[[:space:]]+meta[[:space:]]' <<<"$current_section" 2>/dev/null | tail -1)
    level=$(grep -oE 'L[0-4]/[a-z]+' <<<"$meta_line" | head -1)
    if [[ -n "$level" ]]; then
        level_name="${level#*/}"
    else
        level=$(grep -oP 'level_chosen=\K[^ ]+' <<<"$meta_line" | head -1)
    fi
    creativity=$(grep -oP 'creativity=\K[a-z]+' <<<"$meta_line" | head -1)
    source_tag=".ldd"

    # --- Elapsed: wall time from the latest meta-line to the latest iter-line ---
    first_ts_iso=$(awk '{print $1}' <<<"$meta_line")
    last_ts_iso=$(awk '{print $1}' <<<"$last_line")
    if [[ -n "$first_ts_iso" && -n "$last_ts_iso" ]]; then
        first_ts=$(date -u -d "$first_ts_iso" +%s 2>/dev/null || echo "")
        last_ts=$(date -u -d "$last_ts_iso" +%s 2>/dev/null || echo "")
        if [[ -n "$first_ts" && -n "$last_ts" && "$last_ts" -ge "$first_ts" ]]; then
            elapsed=$((last_ts - first_ts))
            if (( elapsed < 60 )); then
                elapsed_label="${elapsed}s"
            elif (( elapsed < 3600 )); then
                elapsed_label="$((elapsed / 60))m"
            elif (( elapsed < 86400 )); then
                elapsed_label=$(printf "%dh%dm" "$((elapsed / 3600))" "$(((elapsed % 3600) / 60))")
            else
                elapsed_label="$((elapsed / 86400))d"
            fi
        fi
    fi
elif [[ -n "$transcript" && -f "$transcript" ]]; then
    markers=$(jq -r 'select(.type=="assistant") | (.message.content[]?.text // empty)' "$transcript" 2>/dev/null \
        | grep '⟪LDD-TRACE-v1⟫' || true)
    if [[ -n "$markers" ]]; then
        task=$(grep -oP 'task="\K[^"]+' <<<"$markers" | head -1)
        losses=$(grep -oE 'loss(_norm)?=[0-9.]+' <<<"$markers" | sed -E 's/loss(_norm)?=//' | tail -30)
        last_line=$(tail -1 <<<"$markers")
        loop=$(grep -oE '(design|architect|inner|refine|outer|cot)' <<<"$last_line" | head -1)
        [[ "$loop" == "architect" ]] && loop="design"
        last_k=$(grep -oP 'k=\K[0-9]+' <<<"$last_line" | head -1)
        meta_line_m=$(grep 'meta' <<<"$markers" | head -1)
        level=$(grep -oE 'L[0-4]/[a-z]+' <<<"$meta_line_m" | head -1)
        if [[ -n "$level" ]]; then
            level_name="${level#*/}"
        else
            level=$(grep -oP 'level_chosen=\K[^ ]+' <<<"$meta_line_m" | head -1)
        fi
        creativity=$(grep -oP 'creativity=\K[a-z]+' <<<"$meta_line_m" | head -1)
        source_tag="jsonl"
    fi
fi

if [[ -z "$losses" ]]; then
    # No current-session LDD trace. Two sub-states so the display can tell
    # the user WHY there is no active task, not just "something is missing":
    #
    #   idle     — no `.ldd/trace.log` yet OR it is empty.  LDD was never
    #              used in this project.
    #   standby  — `.ldd/trace.log` has prior-session history but the
    #              session gate blocks the current session (no matching
    #              `.ldd/session_active` marker).  LDD is installed and
    #              has been used before; agent just hasn't registered a
    #              fresh task via `ldd_trace init` yet.
    #
    # Heartbeat suffix (⚡Ns Tool) appends in both sub-states when a
    # PreToolUse fired within the last 60s — shows the project is live
    # regardless of which sub-state applies.
    idle_hb=""
    if [[ -f "$hb_file" ]]; then
        hb_line=$(cat "$hb_file" 2>/dev/null || echo "")
        hb_ts=$(awk '{print $1}' <<<"$hb_line")
        hb_tool=$(awk '{print $2}' <<<"$hb_line")
        if [[ -n "$hb_ts" ]]; then
            now=$(date -u +%s)
            age=$((now - hb_ts))
            if (( age >= 0 && age < 60 )); then
                idle_hb=$(printf " · ⚡%ss %s" "$age" "${hb_tool:-?}")
            fi
        fi
    fi
    state="idle"
    if [[ -s "$trace_file" ]]; then
        state="standby"
    fi
    printf "LDD · %s%s" "$state" "$idle_hb"
    exit 0
fi

max=$(awk 'BEGIN{m=0}{v=$1+0; if(v>m)m=v}END{print m}' <<<"$losses")

sparkline=""
while IFS= read -r v; do
    [[ -z "$v" ]] && continue
    if awk -v v="$v" 'BEGIN{exit !(v+0 <= 0)}'; then
        sparkline+="·"
    elif awk -v m="$max" 'BEGIN{exit !(m+0 <= 0)}'; then
        sparkline+="·"
    else
        idx=$(awk -v v="$v" -v m="$max" 'BEGIN{printf "%d", (v/m)*7 + 0.5}')
        (( idx > 7 )) && idx=7
        (( idx < 0 )) && idx=0
        sparkline+="${BLOCKS[idx]}"
    fi
done <<<"$losses"

last=$(tail -1 <<<"$losses")
prev=$(tail -2 <<<"$losses" | head -1)
prev_prev=$(tail -3 <<<"$losses" | head -1)
first=$(head -1 <<<"$losses")

delta=""
if [[ -n "$prev" && "$prev" != "$last" ]]; then
    delta=$(awk -v a="$last" -v b="$prev" 'BEGIN{
        d = a - b
        if (d < -0.005) arrow = "↓"
        else if (d > 0.005) arrow = "↑"
        else arrow = "→"
        printf " %s%+.3f", arrow, d
    }')
fi

trend="→"
if [[ -n "$first" && "$first" != "$last" ]]; then
    trend=$(awk -v a="$last" -v b="$first" 'BEGIN{
        d = a - b
        if (d < -0.005) print "↓"
        else if (d > 0.005) print "↑"
        else print "→"
    }')
fi

# --- Plateau / regression warning (based on last 2-3 losses) ---
# regression: last - prev > 0.005 (needs ≥ 2 samples)
# plateau   : |last-prev| < 0.015 AND |prev-prev_prev| < 0.015 (needs ≥ 3 samples)
# Thresholds are slightly above the obvious 0.005/0.01 so IEEE-754 drift on
# subtractions like 0.45-0.44 = 0.010000000000000009 doesn't miss the boundary.
# `tail -N | head -1` on fewer-than-N-line input returns the single line
# repeatedly, so we gate on an explicit sample count before comparing.
num_losses=$(awk 'NF{n++}END{print n+0}' <<<"$losses")
if (( num_losses >= 2 )); then
    if awk -v a="$last" -v b="$prev" 'BEGIN{exit !((a-b) > 0.005)}'; then
        warning_label="⚠ regression"
    elif (( num_losses >= 3 )); then
        if awk -v a="$last" -v b="$prev" -v c="$prev_prev" 'BEGIN{
            d1 = a - b; if (d1 < 0) d1 = -d1
            d2 = b - c; if (d2 < 0) d2 = -d2
            exit !(d1 < 0.015 && d2 < 0.015)
        }'; then
            warning_label="⚠ plateau"
        fi
    fi
fi

task_short="${task:-untitled}"
(( ${#task_short} > 40 )) && task_short="${task_short:0:37}..."

loop_label="${loop:-?}"
k_label=""
if [[ -n "$last_k" ]]; then
    case "$loop" in
        design|architect) k_prefix="p" ;;  # architect kept for pre-v0.11.0 traces
        refine)           k_prefix="r" ;;
        outer)            k_prefix="o" ;;
        cot)              k_prefix="c" ;;
        *)                k_prefix="i" ;;
    esac
    k_label=" ${k_prefix}${last_k}/${k_max}"
fi

# v0.12.0 — when the current task has closed, replace the live "loop k=N/K_MAX"
# label with a terminal marker so the statusline does not keep pretending the
# run is still progressing.
if [[ -n "$terminal" ]]; then
    case "$terminal" in
        complete) loop_label="✓ complete" ;;
        partial)  loop_label="⚠ partial"  ;;
        failed)   loop_label="✗ failed"   ;;
        aborted)  loop_label="✗ aborted"  ;;
        handoff)  loop_label="→ handoff"  ;;
        *)        loop_label="· ${terminal}" ;;
    esac
    k_label=""  # iteration counter loses meaning once closed
fi

last_fmt=$(awk -v v="$last" 'BEGIN{printf "%.3f", v+0}')

# Heartbeat — shows "⚡ <age>s <tool>" if a tool fired in the last 60s.
# hb_file was resolved at top (per-session → singular fallback).
hb_suffix=""
if [[ -f "$hb_file" ]]; then
    hb_line=$(cat "$hb_file" 2>/dev/null || echo "")
    hb_ts=$(awk '{print $1}' <<<"$hb_line")
    hb_tool=$(awk '{print $2}' <<<"$hb_line")
    if [[ -n "$hb_ts" ]]; then
        now=$(date -u +%s)
        age=$((now - hb_ts))
        if (( age >= 0 && age < 60 )); then
            hb_suffix=$(printf " · ⚡%ss %s" "$age" "${hb_tool:-?}")
        fi
    fi
fi

# Optional trailing segments (only render when populated).
elapsed_tail=""
[[ -n "$elapsed_label" ]] && elapsed_tail=" · ${elapsed_label}"
warning_tail=""
[[ -n "$warning_label" ]] && warning_tail=" · ${warning_label}"

# v0.11.0 statusline format:
#   Idle          : LDD · idle     (no .ldd/trace.log history ever)
#   Standby       : LDD · standby  (prior task(s) in trace.log, session gate
#                                   blocks current session — awaiting
#                                   `ldd_trace init` for a fresh task)
#   L0..L2 active : LDD · L2/deliberate · inner k=1/5 · loss=0.167
#   L3/L4 active  : LDD · L3/structural · creativity=standard · design k=2/5 · loss=0.286
level_label=""
creativity_label=""
if [[ -n "$level" ]]; then
    level_label="$level"
fi
if [[ -n "$creativity" ]]; then
    creativity_label=" · creativity=${creativity}"
fi

if [[ -n "$level_label" ]]; then
    # When terminal is set, the "loop k=N/K_MAX" segment becomes just
    # "loop_label" (already rewritten to ✓/⚠/✗ above); skip the k counters.
    if [[ -n "$terminal" ]]; then
        printf "LDD · %s%s · %s · loss=%s · %s · %s %s%s%s · %s%s" \
            "$level_label" \
            "$creativity_label" \
            "$loop_label" \
            "$last_fmt" \
            "$task_short" \
            "$sparkline" \
            "$trend" \
            "$elapsed_tail" \
            "$warning_tail" \
            "$source_tag" \
            "$hb_suffix"
        exit 0
    fi
    printf "LDD · %s%s · %s k=%s/%s · loss=%s%s · %s · %s %s%s%s · %s%s" \
        "$level_label" \
        "$creativity_label" \
        "$loop_label" \
        "${last_k:-0}" \
        "$k_max" \
        "$last_fmt" \
        "$delta" \
        "$task_short" \
        "$sparkline" \
        "$trend" \
        "$elapsed_tail" \
        "$warning_tail" \
        "$source_tag" \
        "$hb_suffix"
else
    printf "LDD · %s · %s%s · loss %s%s · %s %s%s%s · %s%s" \
        "$task_short" \
        "$loop_label" \
        "$k_label" \
        "$last_fmt" \
        "$delta" \
        "$sparkline" \
        "$trend" \
        "$elapsed_tail" \
        "$warning_tail" \
        "$source_tag" \
        "$hb_suffix"
fi
