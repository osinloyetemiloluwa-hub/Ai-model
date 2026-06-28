// bridge_paths.js — Node mirror of paths.py bridge_runtime_dir().
//
// ADR-0008: All bridge runtime state (inbox/outbox/processed/attachments
// queues, settings.json with credentials, auth/, voice.log) lives under
// <corvin_home>/bridges/<channel>/<kind>/ so the repo tree contains zero
// user-private data. Identity-only — no FS side effects. The Phase 8.2
// migration helper is the single owner of mkdir for this tree.

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const BRIDGE_CHANNEL_RE = /^[a-z][a-z0-9_-]{0,31}$/;
const BRIDGE_KINDS = new Set([
  'inbox', 'outbox', 'processed', 'attachments', 'auth', 'log',
  'settings', 'root',
]);

function validateBridgeChannel(channel) {
  if (typeof channel !== 'string') {
    throw new TypeError(`bridge channel must be string, got ${typeof channel}`);
  }
  if (!BRIDGE_CHANNEL_RE.test(channel)) {
    throw new RangeError(
      `bridge channel ${JSON.stringify(channel)} fails charset rule [a-z][a-z0-9_-]{0,31}`
    );
  }
  return channel;
}

function validateBridgeKind(kind) {
  if (!BRIDGE_KINDS.has(kind)) {
    throw new RangeError(
      `bridge kind ${JSON.stringify(kind)} not in ${JSON.stringify([...BRIDGE_KINDS].sort())}`
    );
  }
  return kind;
}

function corvinHome() {
  // Mirrors shared/paths.py::corvin_home(); kept in sync with
  // shared/js/auth_elevation.js::corvinHome().
  const env = process.env.CORVIN_HOME;
  if (env) {
    return path.resolve(env);
  }
  let cur = path.resolve(__dirname);
  while (true) {
    if (fs.existsSync(path.join(cur, '.corvin_repo')) || fs.existsSync(path.join(cur, 'plugins'))) {
      return path.join(cur, '.corvin');
    }
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  return path.join(os.homedir(), '.corvin');
}

function bridgesHome() {
  return path.join(corvinHome(), 'bridges');
}

function bridgeChannelDir(channel) {
  return path.join(bridgesHome(), validateBridgeChannel(channel));
}

function bridgeRuntimeDir(channel, kind) {
  validateBridgeChannel(channel);
  validateBridgeKind(kind);
  const envKey = `CORVIN_BRIDGE_${channel.toUpperCase()}_${kind.toUpperCase()}`;
  const envOverride = process.env[envKey];
  if (envOverride) {
    return path.resolve(envOverride);
  }
  const rootOverride = process.env.CORVIN_BRIDGES_HOME;
  const base = rootOverride ? path.resolve(rootOverride) : bridgesHome();
  const channelDir = path.join(base, channel);
  if (kind === 'settings' || kind === 'root') {
    return channelDir;
  }
  return path.join(channelDir, kind);
}

function bridgeSettingsPath(channel) {
  return path.join(bridgeRuntimeDir(channel, 'root'), 'settings.json');
}

function bridgeLogPath(channel) {
  return path.join(bridgeRuntimeDir(channel, 'log'), 'voice.log');
}

function legacyBridgeRuntimeDir(channel, kind) {
  validateBridgeChannel(channel);
  validateBridgeKind(kind);
  let cur = path.resolve(__dirname);
  let repo = null;
  while (true) {
    if (fs.existsSync(path.join(cur, '.corvin_repo')) || fs.existsSync(path.join(cur, 'plugins'))) {
      repo = cur;
      break;
    }
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  if (repo === null) return null;
  // Try new operator/bridges layout first, fall back to legacy plugins/ location
  const newChannelDir = path.join(repo, 'operator', 'bridges', channel);
  const channelDir = fs.existsSync(newChannelDir) ? newChannelDir : path.join(repo, 'plugins', 'voice', 'bridges', channel);
  if (kind === 'settings' || kind === 'root') return channelDir;
  return path.join(channelDir, kind);
}

module.exports = {
  corvinHome,
  bridgesHome,
  bridgeChannelDir,
  bridgeRuntimeDir,
  bridgeSettingsPath,
  bridgeLogPath,
  legacyBridgeRuntimeDir,
  validateBridgeChannel,
  validateBridgeKind,
};
