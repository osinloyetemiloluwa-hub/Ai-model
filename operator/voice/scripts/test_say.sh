#!/usr/bin/env bash
# test_say.sh — smoke-test for the standalone TTS helper used by the
# WhatsApp /welcome path.
#
# Without OPENAI_API_KEY: must exit 0 with EMPTY stdout and a diagnostic
# on stderr. Caller (whatsapp/daemon.js) treats empty stdout as "voice
# disabled, fall through to text-only".
#
# Usage args: <out_path> <text> [<lang>]. Missing args → exit 2.
#
# Run: bash operator/voice/scripts/test_say.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAY="$SCRIPT_DIR/say.py"

if [[ ! -x "$SAY" ]]; then
  echo "FAIL: $SAY missing or not executable"
  exit 1
fi

PASS=0
FAIL=0
ok()  { echo "PASS: $*"; PASS=$((PASS+1)); }
bad() { echo "FAIL: $*"; FAIL=$((FAIL+1)); }

TMP=$(mktemp -d -t say-test-XXXXXX)
trap 'rm -rf "$TMP"' EXIT

# Sandbox VOICE_CONFIG_DIR so a real ~/.config/corvin-voice/.env on the
# host machine cannot leak in and turn the no-key test into a real
# OpenAI call (and silently burn quota).
export VOICE_CONFIG_DIR="$TMP/no-config"
mkdir -p "$VOICE_CONFIG_DIR"
unset OPENAI_API_KEY OPENAI_APIKEY

# ── 1. Missing args → exit 2 ─────────────────────────────────────────────
echo "=== say.py: arg validation ==="
"$SAY" >/dev/null 2>&1
[[ $? -eq 2 ]] && ok "no args → exit 2" || bad "no args → expected exit 2"

"$SAY" /tmp/x.ogg >/dev/null 2>&1
[[ $? -eq 2 ]] && ok "one arg → exit 2" || bad "one arg → expected exit 2"

# ── 2. No OPENAI key → exit 0, empty stdout, diagnostic on stderr ────────
echo "=== say.py: silent-skip when no API key ==="
OUT_PATH="$TMP/out.ogg"
STDOUT=$("$SAY" "$OUT_PATH" "Hallo Welt" de 2>"$TMP/err.log")
RC=$?
[[ $RC -eq 0 ]] && ok "no key → exit 0" || bad "no key → expected exit 0, got $RC"
[[ -z "$STDOUT" ]] && ok "no key → empty stdout" || bad "no key → stdout was '$STDOUT'"
grep -q "no OPENAI_API_KEY" "$TMP/err.log" \
  && ok "no key → diagnostic on stderr" \
  || bad "no key → expected stderr diagnostic, got: $(cat "$TMP/err.log")"
[[ ! -e "$OUT_PATH" ]] && ok "no key → no file written" || bad "no key → unexpectedly wrote $OUT_PATH"

# ── 3. Empty text → exit 0, empty stdout, no file ────────────────────────
echo "=== say.py: empty text early-out ==="
STDOUT=$("$SAY" "$TMP/empty.ogg" "   " 2>/dev/null)
RC=$?
[[ $RC -eq 0 && -z "$STDOUT" ]] && ok "empty text → exit 0 + empty stdout" \
  || bad "empty text → expected silent skip, got rc=$RC stdout='$STDOUT'"
[[ ! -e "$TMP/empty.ogg" ]] && ok "empty text → no file" || bad "empty text → wrote file"

# ── 4. .env lookup precedence (sandboxed) ────────────────────────────────
# A bogus key in the sandboxed VOICE_CONFIG_DIR/.env must be picked up by
# _load_key_from_env_files. We can't actually call OpenAI, but we can
# verify the resolver reaches the openai-import branch instead of the
# "no key" branch.
echo "=== say.py: .env precedence ==="
echo 'OPENAI_API_KEY=sk-test-bogus-key-for-resolver-test' >"$VOICE_CONFIG_DIR/.env"
chmod 600 "$VOICE_CONFIG_DIR/.env"
"$SAY" "$TMP/probe.ogg" "Probe" de >/dev/null 2>"$TMP/err2.log"
RC=$?
[[ $RC -eq 0 ]] && ok ".env path: still exit 0 (silent skip on bad key)" \
  || bad ".env path: expected exit 0, got $RC"
# Should NOT carry the "no OPENAI_API_KEY" diagnostic — the resolver
# found the key and went past it. Either openai is missing (different
# diagnostic) or the call failed (different diagnostic).
if grep -q "no OPENAI_API_KEY" "$TMP/err2.log"; then
  bad ".env-resolved key was ignored (still hit no-key branch)"
else
  ok ".env-resolved key was used (no-key branch skipped)"
fi

echo ""
echo "say.py: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]] || exit 1
