#!/usr/bin/env node
// test_teams_e2e.js — integration/E2E tests for the Teams bridge.
//
// Tests the full message flow without real Azure credentials:
//   1. Auth gate: whitelisted / denied / rate-limited users
//   2. Inbox write: correct JSON shape, channel='teams'
//   3. In-chat commands: /stop, /btw, /new
//   4. Outbox → sendTeams: card delivered via mock adapter
//   5. ConversationRef store: missing ref → drop (not throw)
//
// No botbuilder package needed — the handler accepts any object that
// implements { activity, sendActivity() }. The mock adapter below fakes
// the continueConversation() surface.

'use strict';

const assert = require('assert');
const fs     = require('fs');
const os     = require('os');
const path   = require('path');

// ── Shim shared modules that need real filesystem paths ───────────────────────
// We point SETTINGS_FILE to a temp file, INBOX/OUTBOX to temp dirs.

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'teams-e2e-'));
const INBOX_DIR       = path.join(tmp, 'inbox');
const OUTBOX_DIR      = path.join(tmp, 'outbox');
const SETTINGS_FILE   = path.join(tmp, 'settings.json');

fs.mkdirSync(INBOX_DIR,  { recursive: true });
fs.mkdirSync(OUTBOX_DIR, { recursive: true });

// ── Helpers ───────────────────────────────────────────────────────────────────

function writeSettings(obj) {
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(obj, null, 2));
}

function currentSettings() {
  return JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8'));
}

function readInboxFiles() {
  return fs.readdirSync(INBOX_DIR)
    .filter((f) => f.endsWith('.json'))
    .map((f) => JSON.parse(fs.readFileSync(path.join(INBOX_DIR, f), 'utf8')));
}

function clearInbox() {
  for (const f of fs.readdirSync(INBOX_DIR)) {
    fs.unlinkSync(path.join(INBOX_DIR, f));
  }
}

function writeOutbox(payload) {
  const name = `${Date.now()}_${Math.random().toString(36).slice(2)}.json`;
  fs.writeFileSync(path.join(OUTBOX_DIR, name), JSON.stringify(payload, null, 2));
  return name;
}

// Mock TurnContext — minimal shape expected by handler.js.
function mockCtx(overrides = {}) {
  const replies = [];
  return {
    activity: {
      type: 'message',
      text: 'Hello',
      from: {
        userPrincipalName: 'alice@company.com',
        aadObjectId: 'aad-alice',
        id: 'teams-alice',
      },
      conversation: { id: 'conv-123' },
      ...overrides.activity,
    },
    sendActivity: async (msg) => { replies.push(msg); },
    _replies: replies,
    ...overrides.ctx,
  };
}

// ── Load shared modules & makeHandler ─────────────────────────────────────────
const { makeAuth }    = require('../shared/js/auth');
const { makeHandler } = require('./handler');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    const r = fn();
    if (r && typeof r.then === 'function') {
      return r.then(() => { console.log(`  ✓ ${name}`); passed++; })
              .catch((e) => { console.error(`  ✗ ${name}\n    ${e.message}`); failed++; });
    }
    console.log(`  ✓ ${name}`);
    passed++;
    return Promise.resolve();
  } catch (e) {
    console.error(`  ✗ ${name}\n    ${e.message}`);
    failed++;
    return Promise.resolve();
  }
}

// Run all tests sequentially (each is async).
async function run() {

  // ── 1. Auth gate ───────────────────────────────────────────────────────────
  console.log('\nAuth gate');

  await test('DEV mode (empty whitelist): message written to inbox', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'teams' });
    const { handleActivity } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, conversationRefs: new Map(),
    });
    const ctx = mockCtx();
    const id = await handleActivity(ctx);
    assert.ok(id, 'must return a message id');
    const msgs = readInboxFiles();
    assert.strictEqual(msgs.length, 1);
    assert.strictEqual(msgs[0].channel, 'teams');
    assert.strictEqual(msgs[0].from, 'alice@company.com');
    assert.strictEqual(msgs[0].text, 'Hello');
    assert.strictEqual(msgs[0].chat_id, 'conv-123');
  });

  await test('Whitelisted user: message written to inbox', async () => {
    writeSettings({ whitelist: ['alice@company.com'], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'teams' });
    const { handleActivity } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, conversationRefs: new Map(),
    });
    const id = await handleActivity(mockCtx());
    assert.ok(id);
    assert.strictEqual(readInboxFiles().length, 1);
  });

  await test('Non-whitelisted user: denied, no inbox write', async () => {
    writeSettings({ whitelist: ['bob@company.com'], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'teams' });
    const { handleActivity } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, conversationRefs: new Map(),
    });
    const ctx = mockCtx();
    const id = await handleActivity(ctx);
    assert.strictEqual(id, null, 'must be null for denied user');
    assert.strictEqual(readInboxFiles().length, 0, 'no inbox write');
    assert.ok(ctx._replies.length > 0, 'must send auth-deny reply');
    assert.ok(ctx._replies[0].includes('alice@company.com'), 'reply must include the user identity');
  });

  await test('Rate-limited user: dropped, no inbox write', async () => {
    writeSettings({ whitelist: ['alice@company.com'], rate_limit_per_hour: 1 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'teams' });
    const { handleActivity } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, conversationRefs: new Map(),
    });
    // First message OK
    await handleActivity(mockCtx());
    clearInbox();
    // Second message hits rate limit
    const ctx = mockCtx();
    const id = await handleActivity(ctx);
    assert.strictEqual(id, null, 'rate-limited message must be dropped');
    assert.strictEqual(readInboxFiles().length, 0, 'no inbox write on rate limit');
    assert.ok(ctx._replies.some((r) => /rate/i.test(r)), 'must send rate-limit reply');
  });

  // ── 2. In-chat commands ────────────────────────────────────────────────────
  console.log('\nIn-chat commands');

  await test('/stop writes cancel envelope', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'teams' });
    const { handleActivity } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, conversationRefs: new Map(),
    });
    const ctx = mockCtx({ activity: { text: '/stop' } });
    await handleActivity(ctx);
    const msgs = readInboxFiles();
    assert.strictEqual(msgs.length, 1);
    assert.strictEqual(msgs[0]._cancel, true);
  });

  await test('/btw writes btw envelope', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'teams' });
    const { handleActivity } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, conversationRefs: new Map(),
    });
    const ctx = mockCtx({ activity: { text: '/btw also consider this' } });
    await handleActivity(ctx);
    const msgs = readInboxFiles();
    assert.strictEqual(msgs.length, 1);
    assert.strictEqual(msgs[0]._btw, true);
    assert.strictEqual(msgs[0].text, 'also consider this');
  });

  await test('empty text after trim: no inbox write', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'teams' });
    const { handleActivity } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, conversationRefs: new Map(),
    });
    const ctx = mockCtx({ activity: { text: '   ' } });
    const id = await handleActivity(ctx);
    assert.strictEqual(id, null);
    assert.strictEqual(readInboxFiles().length, 0);
  });

  await test('non-message activity type: ignored', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'teams' });
    const { handleActivity } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, conversationRefs: new Map(),
    });
    const ctx = mockCtx({ activity: { type: 'typing', text: '' } });
    const id = await handleActivity(ctx);
    assert.strictEqual(id, null);
    assert.strictEqual(readInboxFiles().length, 0);
  });

  // ── 3. Outbox → sendTeams ──────────────────────────────────────────────────
  console.log('\nOutbox → sendTeams');

  await test('plain text: sendActivity called with Adaptive Card', async () => {
    const conversationRefs = new Map();
    const chatKey = 'conv-abc';
    conversationRefs.set(chatKey, { conversation: { id: chatKey } });

    const sentActivities = [];
    const mockAdapter = {
      continueConversation: async (_ref, _appId, fn) => {
        const mockTurnCtx = {
          sendActivity: async (a) => { sentActivities.push(a); },
        };
        await fn(mockTurnCtx);
      },
    };

    const { sendTeams } = require('./handler').makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings,
      auth: { authOk: () => true, readOnlyOk: () => ({ isReadOnly: false }), rateAllow: () => true },
      logger: null, conversationRefs,
    });

    const payload = { channel: 'teams', chat_id: chatKey, text: 'Hello from outbox!' };
    const ok = await sendTeams(payload, mockAdapter, 'fake-app-id');
    assert.strictEqual(ok, true);
    assert.strictEqual(sentActivities.length, 1);
    const card = sentActivities[0];
    assert.strictEqual(card.type, 'message');
    assert.ok(card.attachments[0].contentType.includes('adaptive'));
    assert.ok(card.attachments[0].content.body[0].text.includes('Hello from outbox!'));
  });

  await test('code reply: dispatches to codeCard (Monospace)', async () => {
    const conversationRefs = new Map();
    const chatKey = 'conv-code';
    conversationRefs.set(chatKey, { conversation: { id: chatKey } });

    const sentActivities = [];
    const mockAdapter = {
      continueConversation: async (_ref, _appId, fn) => {
        await fn({ sendActivity: async (a) => sentActivities.push(a) });
      },
    };

    const { sendTeams } = require('./handler').makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings,
      auth: { authOk: () => true, readOnlyOk: () => ({ isReadOnly: false }), rateAllow: () => true },
      logger: null, conversationRefs,
    });

    const payload = { channel: 'teams', chat_id: chatKey, text: '```python\nprint("hi")\n```' };
    await sendTeams(payload, mockAdapter, 'fake-app-id');
    const card = sentActivities[0];
    assert.ok(card.attachments[0].content.body.some((b) => b.fontType === 'Monospace'));
  });

  await test('missing conversationRef: returns false, does not throw', async () => {
    const conversationRefs = new Map(); // empty — no ref registered
    const { sendTeams } = require('./handler').makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings,
      auth: { authOk: () => true, readOnlyOk: () => ({ isReadOnly: false }), rateAllow: () => true },
      logger: null, conversationRefs,
    });

    const payload = { channel: 'teams', chat_id: 'no-such-chat', text: 'hi' };
    const ok = await sendTeams(payload, {}, 'fake-app-id');
    assert.strictEqual(ok, false, 'must return false for missing ref');
  });

  await test('missing chat_id: returns false', async () => {
    const { sendTeams } = require('./handler').makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings,
      auth: { authOk: () => true, readOnlyOk: () => ({ isReadOnly: false }), rateAllow: () => true },
      logger: null, conversationRefs: new Map(),
    });

    const payload = { channel: 'teams', text: 'hi' }; // no chat_id
    const ok = await sendTeams(payload, {}, 'fake-app-id');
    assert.strictEqual(ok, false);
  });

  // ── 4. Inbox envelope shape ────────────────────────────────────────────────
  console.log('\nInbox envelope shape');

  await test('inbox JSON has required fields', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'teams' });
    const { handleActivity } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, conversationRefs: new Map(),
    });
    await handleActivity(mockCtx({ activity: { text: 'Test message' } }));
    const [msg] = readInboxFiles();
    assert.ok(msg.id, 'must have id');
    assert.strictEqual(msg.channel, 'teams');
    assert.ok(msg.from, 'must have from');
    assert.ok(msg.chat_id, 'must have chat_id');
    assert.ok(typeof msg.ts === 'number', 'ts must be a number');
    assert.strictEqual(msg.text, 'Test message');
  });

  await test('UPN preferred over aadObjectId for from field', async () => {
    writeSettings({ whitelist: [], rate_limit_per_hour: 60 });
    clearInbox();
    const auth = makeAuth({ settingsFile: SETTINGS_FILE, currentSettings, loadSettings: currentSettings, channel: 'teams' });
    const { handleActivity } = makeHandler({
      inboxDir: INBOX_DIR, settingsFile: SETTINGS_FILE, currentSettings, auth,
      logger: null, conversationRefs: new Map(),
    });
    const ctx = mockCtx({
      activity: {
        text: 'hi',
        from: { userPrincipalName: 'alice@company.com', aadObjectId: 'aad-xyz', id: 'teams-xyz' },
      },
    });
    await handleActivity(ctx);
    const [msg] = readInboxFiles();
    assert.strictEqual(msg.from, 'alice@company.com', 'UPN must take priority');
  });

  // ── Cleanup ────────────────────────────────────────────────────────────────
  fs.rmSync(tmp, { recursive: true, force: true });

  console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed\n`);
  if (failed > 0) process.exit(1);
}

run().catch((e) => { console.error(e); process.exit(1); });
