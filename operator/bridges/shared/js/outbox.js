// outbox.js — generischer Outbox-Polling-Loop.
//
// Vor dem Refactor hatte jeder daemon seine eigene processOutbox() mit dem
// gleichen Boilerplate (read dir → parse JSON → channel filter → send →
// unlink). Hier zentralisiert, inklusive des in Phase 1 gefixten
// Strict-Channel-Checks: payload.channel MUSS set sein UND === channel
// — kein silent default auf 'whatsapp'.

const fs = require('fs');
const path = require('path');

/**
 * @param {object} cfg
 * @param {string} cfg.outboxDir        — path zum gemeinsamen outbox/-directory
 * @param {string} cfg.channel          — eigener Channel-Name (z.B. 'telegram')
 * @param {function} cfg.sendFn         — async (payload, fpath) => void
 *                                        Bei success: returnt normal, file wed gedeletes.
 *                                        Bei Throw: file bleibt, Tick versucht es erneut.
 * @param {function} [cfg.preCheck]     — sync () => boolean. Wenn false, Tick bricht ab
 *                                        bevor das next File angefasst wed (z.B.
 *                                        WhatsApp-Socket nicht ready).
 * @param {function} [cfg.logger]
 * @param {number}   [cfg.intervalMs=500]
 * @returns {{stop: function}}          — handle mit stop() zum Cleanup
 */
function startOutboxPoller({
  outboxDir, channel, sendFn, preCheck, logger, intervalMs = 500,
}) {
  let running = false;
  // Sent-once guard: track files that were successfully sent but whose
  // unlink() failed. On the next tick we delete them instead of re-sending,
  // which would duplicate voice notes / messages.
  const _sentOnce = new Set();

  async function tick() {
    let files;
    try {
      files = fs.readdirSync(outboxDir).filter((f) => f.endsWith('.json')).sort();
    } catch {
      return;
    }
    for (const f of files) {
      if (preCheck && !preCheck()) return;
      const fpath = path.join(outboxDir, f);
      // Already sent in a previous tick but unlink() failed — retry the
      // unlink only; do NOT re-send. Only clear the guard when the file is
      // actually gone so a second failed unlink doesn't reopen the send path.
      if (_sentOnce.has(fpath)) {
        let retryUnlinked = false;
        try { fs.unlinkSync(fpath); retryUnlinked = true; } catch {}
        if (retryUnlinked) _sentOnce.delete(fpath);
        continue;
      }
      let payload;
      try {
        payload = JSON.parse(fs.readFileSync(fpath, 'utf8'));
      } catch (e) {
        if (logger) logger(`outbox: bad JSON in ${f}: ${e.message}`);
        try { fs.unlinkSync(fpath); } catch {}
        continue;
      }
      // Strict: missing `channel` is a writer bug — drop instead of
      // silently routing to a default channel (the old `|| 'whatsapp'`
      // fallback could deliver Telegram-bound messages to a WhatsApp
      // account if channel was forgotten somewhere).
      if (!payload.channel) {
        if (logger) logger(`outbox: missing 'channel' field in ${f}, dropping`);
        try { fs.unlinkSync(fpath); } catch {}
        continue;
      }
      if (payload.channel !== channel) continue;
      try {
        await sendFn(payload, fpath);
        _sentOnce.add(fpath);         // mark before unlink so a failed unlink is detected next tick
        let unlinked = false;
        try { fs.unlinkSync(fpath); unlinked = true; } catch {}
        if (unlinked) _sentOnce.delete(fpath); // only clear guard when file is actually gone
        // If unlink failed the file stays in the outbox; _sentOnce keeps the
        // entry so the next tick knows the message was already sent and only
        // retries the unlink (no re-send → prevents duplicate Discord messages).
      } catch (e) {
        if (logger) logger(`outbox: send failed for ${f}: ${e.message}`);
        // File bleibt → nextr Tick versucht es erneut.
      }
    }
  }

  const handle = setInterval(() => {
    if (running) return;
    running = true;
    Promise.resolve().then(tick)
      .catch((e) => { if (logger) logger(`outbox tick error: ${e.message}`); })
      .finally(() => { running = false; });
  }, intervalMs);

  return { stop: () => clearInterval(handle) };
}

module.exports = { startOutboxPoller };
