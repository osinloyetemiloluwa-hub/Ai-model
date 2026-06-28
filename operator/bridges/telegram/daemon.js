#!/usr/bin/env node
// telegram_daemon.js — Telegram-Bot-API frontend, drop-in replacement for
// the Baileys-based daemon.js. Same inbox/outbox JSON contract, so adapter.py
// keeps working unchanged.
//
// Setup:
//   1. Talk to @BotFather, /newbot, get an HTTP API token.
//   2. Put the token in settings.json under "telegram_token".
//   3. Add your numeric Telegram user ID to "whitelist" (or leave empty for
//      DEV mode — accepts everyone). The bot will tell you your ID via
//      /start when you write to it the first time.
//   4. Run: node telegram_daemon.js  (or via systemd).

const fs = require('fs');
const path = require('path');

require('../shared/js/bridge_state').exitIfDisabled('telegram');

const TelegramBot = require('node-telegram-bot-api');

// ── Shared bridge runtime (Phase 2 refactor) ────────────────────────────────
const { makeLogger }            = require('../shared/js/logger');
const { makeSettingsAccessor }  = require('../shared/js/settings');
const { makeAuth }              = require('../shared/js/auth');
const { startOutboxPoller }     = require('../shared/js/outbox');
const { startHealthServer }     = require('../shared/js/health-server');
const { makeAnnouncer }         = require('../shared/js/local-announce');
const { newMsgId }              = require('../shared/js/msg-id');
const inChatCmds                = require('../shared/js/in_chat_commands');
const chatToggle                = require('../shared/js/chat_toggle');
const { bridgeSettingsPath }    = require('../shared/js/bridge_paths');

const ROOT = __dirname;
const PLUGIN_ROOT = path.resolve(ROOT, '..', '..');
const SHARED = path.resolve(ROOT, '..', 'shared');
const INBOX  = path.join(SHARED, 'inbox');
const OUTBOX = path.join(SHARED, 'outbox');
// ADR-0008 §8.3: settings live in <corvin_home>/bridges/telegram/.
const SETTINGS_FILE = (ch => {
  const can = bridgeSettingsPath(ch);
  const leg = path.join(ROOT, 'settings.json');
  if (!fs.existsSync(can) && fs.existsSync(leg)) {
    try { fs.mkdirSync(path.dirname(can), { recursive: true }); fs.copyFileSync(leg, can); } catch {}
  }
  return fs.existsSync(can) ? can : leg;
})('telegram');
const CHANNEL = 'telegram';
for (const d of [INBOX, OUTBOX]) fs.mkdirSync(d, { recursive: true });

const HTTP_PORT = parseInt(process.env.TELEGRAM_HTTP_PORT || '7892', 10);

const log = makeLogger('tg-daemon');
const { loadSettings, currentSettings } = makeSettingsAccessor(SETTINGS_FILE, log);
const settings = loadSettings(); // boot-time snapshot for token resolution
const { rateAllow, authOk, readOnlyOk } = makeAuth({
  settingsFile: SETTINGS_FILE, currentSettings, loadSettings, logger: log,
  channel: CHANNEL,
});

const READ_ONLY_ACK = '🔒 You are read-only in this chat — you can read along, but you cannot drive the bot. Ask the owner to add you to the whitelist if that is wrong.';

function maybeForwardAsObserver(uid, text, chatKey, base) {
  if (!text || !String(text).trim()) return false;
  let mode = 'off';
  try { mode = inChatCmds.getObserverVisibility(SETTINGS_FILE, String(chatKey)) || 'off'; }
  catch { mode = 'off'; }
  if (mode !== 'transcript') return false;
  try {
    writeInbox({ ...base, _observer: true, text: String(text).slice(0, 2000) });
  } catch (e) {
    log(`observer-forward failed: ${e && e.message}`);
    return false;
  }
  return true;
}
const announce = makeAnnouncer({
  pluginRoot: PLUGIN_ROOT, channelLabel: 'Telegram', currentSettings, logger: log,
});

const TOKEN = process.env.TELEGRAM_TOKEN || settings.telegram_token;
if (!TOKEN) {
  log('FATAL: TELEGRAM_TOKEN not set (env or settings.json telegram_token)');
  process.exit(1);
}

const activeChats = new Map(); // chatId -> Date.now() for typing-indicator refresh

function writeInbox(payload) {
  const id = newMsgId();
  fs.writeFileSync(path.join(INBOX, `${id}.json`),
    JSON.stringify({ id, channel: CHANNEL, ...payload }, null, 2));
  const kind = payload.audio_path ? 'voice'
             : payload.image_path ? 'image'
             : payload.document_path ? 'document'
             : payload.video_path ? 'video' : 'text';
  log(`inbox: ${id} from=${payload.from} kind=${kind}`);
  announce(payload, kind);
  return id;
}

// ─── Bot weing ──────────────────────────────────────────────────────────────
const bot = new TelegramBot(TOKEN, { polling: true });

let botInfo = null;
bot.getMe().then(me => {
  botInfo = me;
  log(`bot ready: @${me.username} id=${me.id}`);
}).catch(e => { log(`getMe failed: ${e.message}`); process.exit(1); });

bot.on('polling_error', err => log(`polling_error: ${err.code} ${err.message}`));
bot.on('webhook_error', err => log(`webhook_error: ${err.message}`));

bot.on('message', async (msg) => {
  try {
    const chatId = msg.chat.id;
    const userId = msg.from.id;
    const userIdStr = String(userId);
    const text = msg.text || msg.caption || '';
    const id = newMsgId();

    {
      const ro = readOnlyOk(userIdStr, text, String(chatId));
      if (ro.isReadOnly) {
        const base = { from: userIdStr, chat_id: chatId, ts: Date.now() };
        // Layer-17: read-only senders may run /consent on/off/<ttl>/status
        // and /share <text> BEFORE the observer-buffer path. dispatch returns
        // non-null only for those two prefixes; everything else falls through.
        const cc = inChatCmds.dispatchReadOnlyConsent({
          text, channel: CHANNEL, chatKey: String(chatId), uid: userIdStr,
          settingsFile: SETTINGS_FILE,
        });
        if (cc) {
          if (cc.admitShare && cc.sharePayload) {
            try {
              writeInbox({ ...base, _observer: true, _share: true,
                           text: String(cc.sharePayload).slice(0, 2000) });
            } catch (e) { log(`/share inbox-write failed: ${e.message}`); }
          }
          if (cc.reply) {
            try { await bot.sendMessage(chatId, cc.reply); } catch {}
          }
          log(`read-only consent: ${cc.kind} from=${userIdStr} chat=${chatId}`);
          return;
        }
        // Layer-19: /join /pass for read-only senders register them as
        // observers (or simply ack the disclosure card). Mirrors the
        // consent gate above; sits BEFORE maybeForwardAsObserver so an
        // observer who just typed /pass doesn't also flow into the
        // transcript buffer for that same message.
        const dd = inChatCmds.dispatchReadOnlyDisclosure({
          text, channel: CHANNEL, chatKey: String(chatId), uid: userIdStr,
          settingsFile: SETTINGS_FILE,
        });
        if (dd) {
          if (dd.reply) {
            try { await bot.sendMessage(chatId, dd.reply); } catch {}
          }
          log(`read-only disclosure: ${dd.kind} from=${userIdStr} chat=${chatId}`);
          return;
        }
        // Layer-21: /propose <text> from a read-only sender adds the
        // proposal to the chat's stack without triggering the bot.
        const pp = inChatCmds.dispatchReadOnlyProposal({
          text, channel: CHANNEL, chatKey: String(chatId), uid: userIdStr,
          settingsFile: SETTINGS_FILE,
        });
        if (pp) {
          if (pp.reply) {
            try { await bot.sendMessage(chatId, pp.reply); } catch {}
          }
          log(`read-only proposal: ${pp.kind} from=${userIdStr} chat=${chatId}`);
          return;
        }
        // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure for read-only
        // OBSERVERS too. Their message is forwarded to the LLM (observer transcript),
        // so they are interacting with the AI and must be told — not only reactively
        // via /join. Shown once per (chat, uid), same ledger as the owner path.
        if (!inChatCmds.disclosureHasSeen({ channel: CHANNEL, chatKey: String(chatId), uid: userIdStr })) {
          const ocard = inChatCmds.disclosureCardText({
            channel: CHANNEL, ownerLabel: currentSettings().operator_name || '(owner)',
            hasObserverTranscript: true,
            lang: currentSettings().lang || 'en',
          });
          if (ocard) {
            try { await bot.sendMessage(chatId, ocard); } catch {}
            const oseen = inChatCmds.disclosureMarkSeen({ channel: CHANNEL, chatKey: String(chatId), uid: userIdStr, action: 'pending' });
            if (!oseen.ok) log(`[disclosure] observer mark_seen failed — ${oseen.error}`);
            log(`disclosure shown (observer) uid=${userIdStr} ch=${chatId}`);
          }
        }
        const forwarded = maybeForwardAsObserver(userIdStr, text, chatId, base);
        if (forwarded) {
          // Silent forward — the LLM will see the line on the next owner turn.
        } else if (ro.firstDrop) {
          try { await bot.sendMessage(chatId, READ_ONLY_ACK); } catch {}
        }
        return;
      }
    }
    if (!authOk(userIdStr, text, chatId)) {
      await bot.sendMessage(chatId, `You are not authorized. Your user-id: ${userId}\nAdd it to the whitelist in settings.json (or send "/auth <pin>").\nThe owner can also open this chat with /all on.`);
      return;
    }
    // Layer-19 — EU AI Act Art. 50: proactive bot-disclosure on first encounter
    // for whitelisted (owner) senders. Shown once per (chat, uid).
    if (!inChatCmds.disclosureHasSeen({ channel: CHANNEL, chatKey: String(chatId), uid: userIdStr })) {
      const card = inChatCmds.disclosureCardText({
        channel: CHANNEL, ownerLabel: '(owner)',
        hasObserverTranscript: false,
        lang: currentSettings().lang || 'en',
      });
      if (card) {
        try { await bot.sendMessage(chatId, card); } catch {}
        inChatCmds.disclosureMarkSeen({ channel: CHANNEL, chatKey: String(chatId), uid: userIdStr, action: 'pending' });
        log(`disclosure shown uid=${userIdStr} chat=${chatId}`);
      }
    }
    // Owner-side /on /off /status — opt-in toggle. settings.json without
    // an `enabled_chats` field stays in legacy default-on mode, so this
    // is a no-op for existing deployments. Add `"enabled_chats": []` to
    // flip into opt-in mode and use /on per chat.
    {
      const tog = chatToggle.handleToggleCommand({
        text, chatKey: String(chatId), isOwner: true,
        settingsFile: SETTINGS_FILE,
      });
      if (tog) {
        try { await bot.sendMessage(chatId, tog.reply); } catch {}
        log(`toggle ${tog.kind} chat=${chatId}`);
        return;
      }
    }
    // Default-off gate when opt-in mode is active. Legacy mode (no
    // `enabled_chats` field) returns true for every chat — backwards-
    // compat preserved.
    if (!chatToggle.isChatEnabled(currentSettings(), String(chatId))) {
      log(`chat ${chatId} not enabled, ignoring`);
      return;
    }
    if (!rateAllow(userIdStr, currentSettings().rate_limit_per_hour || 30)) {
      await bot.sendMessage(chatId, 'Rate limit reached. Please try again later.');
      return;
    }

    // /start → tell user their ID and bridge status, no inbox write.
    if (text === '/start') {
      await bot.sendMessage(chatId,
        `Hi! I'm the Claude bridge.\n\nYour Telegram user-id: ${userId}\n` +
        `Add this id to settings.json -> whitelist, then you can send me tasks (text, voice, images, documents).\n\n` +
        `Tip: /help shows all in-chat commands.`);
      return;
    }

    // Cowork in-chat commands: /help, /personas, /persona <name>, /whoami, /skills.
    // Whitelisted senders count as "owner" for persona switches — die
    // the whitelist IS the security model of the bridge.
    {
      const cwk = inChatCmds.dispatch({
        text, channel: CHANNEL, chatKey: String(chatId),
        isOwner: true,  // authOk has already checked the whitelist
        settingsFile: SETTINGS_FILE,
      });
      if (cwk) {
        await bot.sendMessage(chatId, cwk.reply);
        log(`in-chat-cmd ${cwk.kind} → ${chatId}`);
        return;
      }
    }

    // /stop /cancel: SIGTERM the currently running claude subprocess for this
    // chat. Adapter writes a short ACK; conversation history is untouched.
    {
      const cmdLower = (text || '').trim().toLowerCase();
      if (cmdLower === '/stop' || cmdLower === '/cancel' || cmdLower === '/abbruch' || cmdLower === '/halt') {
        log(`cancel cmd from ${userId} in chat ${chatId}`);
        writeInbox({ from: userIdStr, chat_id: chatId, _cancel: true, ts: Date.now() });
        return;
      }
    }

    // /btw <text> — Layer 13 mid-stream injection. Pushes the text as an
    // extra user-message into the live claude subprocess for this chat,
    // bypassing the per-chat queue. If nothing is running the adapter
    // writes a friendly "send it as a normal message" ACK.
    {
      const btwMatch = (text || '').match(/^\/btw(?:\s+([\s\S]+))?$/i);
      if (btwMatch) {
        const btwText = (btwMatch[1] || '').trim();
        log(`btw cmd from ${userId} in chat ${chatId} (len=${btwText.length})`);
        writeInbox({ from: userIdStr, chat_id: chatId, _btw: true, text: btwText, ts: Date.now() });
        return;
      }
    }

    // Build inbox payload depending on media type.
    const base = { from: userIdStr, chat_id: chatId, ts: Date.now() };

    if (msg.voice || msg.audio) {
      const fileId = (msg.voice || msg.audio).file_id;
      const stream = bot.getFileStream(fileId);
      const ext = msg.voice ? '.ogg' : '.mp3';
      const p = path.join(INBOX, `${id}${ext}`);
      const out = fs.createWriteStream(p);
      stream.pipe(out);
      await new Promise(res => out.on('finish', res));
      writeInbox({ ...base, audio_path: p });
    } else if (msg.photo) {
      const photo = msg.photo[msg.photo.length - 1]; // largest size
      const fileLink = await bot.getFileLink(photo.file_id);
      const ext = '.jpg';
      const p = path.join(INBOX, `${id}${ext}`);
      const buf = await (await fetch(fileLink)).arrayBuffer();
      fs.writeFileSync(p, Buffer.from(buf));
      writeInbox({ ...base, image_path: p, caption: msg.caption || '' });
    } else if (msg.document) {
      const fileLink = await bot.getFileLink(msg.document.file_id);
      const fname = (msg.document.file_name || 'document.bin').replace(/[^a-zA-Z0-9._-]/g, '_');
      const p = path.join(INBOX, `${id}_${fname}`);
      const buf = await (await fetch(fileLink)).arrayBuffer();
      fs.writeFileSync(p, Buffer.from(buf));
      writeInbox({ ...base, document_path: p, document_name: msg.document.file_name, mimetype: msg.document.mime_type, caption: msg.caption || '' });
    } else if (msg.video) {
      const fileLink = await bot.getFileLink(msg.video.file_id);
      const p = path.join(INBOX, `${id}.mp4`);
      const buf = await (await fetch(fileLink)).arrayBuffer();
      fs.writeFileSync(p, Buffer.from(buf));
      writeInbox({ ...base, video_path: p, caption: msg.caption || '' });
    } else if (msg.sticker) {
      const fileLink = await bot.getFileLink(msg.sticker.file_id);
      const p = path.join(INBOX, `${id}.webp`);
      const buf = await (await fetch(fileLink)).arrayBuffer();
      fs.writeFileSync(p, Buffer.from(buf));
      writeInbox({ ...base, image_path: p, is_sticker: true });
    } else if (text) {
      writeInbox({ ...base, text });
    } else {
      log(`unknown message shape from ${userId}, ignoring`);
      return;
    }

    // Typing-indicator until adapter responds.
    activeChats.set(chatId, Date.now());
    try { await bot.sendChatAction(chatId, 'typing'); } catch {}
  } catch (e) {
    log(`message handler error: ${e.message}`);
  }
});

// Refresh typing every 4s for chats we're actively processing
// (Telegram's typing-indicator expires after ~5s).
setInterval(async () => {
  const now = Date.now();
  for (const [chatId, ts] of activeChats.entries()) {
    if (now - ts > 60000) { activeChats.delete(chatId); continue; }
    try { await bot.sendChatAction(chatId, 'typing'); } catch {}
  }
}, 4000);

// ─── Outbox processing ──────────────────────────────────────────────────────
async function sendTelegram(payload, _fpath) {
  const chatId = payload.chat_id;
  if (!chatId) {
    log(`no chat_id, skipping`);
    return; // returnt → poller deletes das File (analog zu vorherigem unlink)
  }
  if (payload.text) {
    await bot.sendMessage(chatId, payload.text);
  }
  if (payload.voice_path && fs.existsSync(payload.voice_path)) {
    await bot.sendVoice(chatId, payload.voice_path);
  }
  if (payload.image_path && fs.existsSync(payload.image_path)) {
    await bot.sendPhoto(chatId, payload.image_path, { caption: payload.image_caption || '' });
  }
  if (payload.document_path && fs.existsSync(payload.document_path)) {
    await bot.sendDocument(chatId, payload.document_path, { caption: payload.document_caption || '' });
  }
  if (payload.video_path && fs.existsSync(payload.video_path)) {
    await bot.sendVideo(chatId, payload.video_path, { caption: payload.video_caption || '' });
  }
  activeChats.delete(chatId);
}

startOutboxPoller({
  outboxDir: OUTBOX, channel: CHANNEL, sendFn: sendTelegram, logger: log,
});

process.on('unhandledRejection', r => log(`unhandledRejection: ${r && r.message || r}`));

// ─── Health HTTP server ─────────────────────────────────────────────────────
startHealthServer({
  port: HTTP_PORT, kind: 'telegram', logger: log,
  getStatus: () => ({
    paired: !!botInfo,
    bot_username: botInfo?.username || null,
    whitelist_size: (currentSettings().whitelist || []).length,
    pending_outbox: fs.readdirSync(OUTBOX).filter(f => f.endsWith('.json')).length,
  }),
});

process.on('SIGINT',  () => { log('shutting down'); process.exit(0); });
process.on('SIGTERM', () => { log('shutting down'); process.exit(0); });
