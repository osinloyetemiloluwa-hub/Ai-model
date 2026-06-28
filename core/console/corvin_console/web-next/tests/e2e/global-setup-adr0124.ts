/**
 * Global setup for ADR-0124 E2E tests.
 * Logs in ONCE via local-login and saves the session cookies to auth-state.json.
 * All subsequent tests reuse those cookies — no more repeated login calls.
 *
 * Rate-limit guard: if auth-state.json already contains a valid session,
 * skip the local-login call entirely to avoid burning the rate-limit budget
 * (10 logins / IP / 60 s).
 */
import { chromium, request } from "@playwright/test";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const _dirname = path.dirname(fileURLToPath(import.meta.url));

export const AUTH_STATE_PATH = path.join(_dirname, "auth-state.json");

/** Check if the existing auth-state is still valid (whoami returns 200). */
async function isSessionValid(): Promise<boolean> {
  if (!fs.existsSync(AUTH_STATE_PATH)) return false;
  try {
    const ctx = await request.newContext({ baseURL: "http://localhost:5173" });
    // Replay cookies from the stored state
    const raw = JSON.parse(fs.readFileSync(AUTH_STATE_PATH, "utf-8"));
    const cookies: Array<{
      name: string; value: string; domain: string; path: string;
      expires?: number; httpOnly?: boolean; secure?: boolean; sameSite?: "Strict"|"Lax"|"None";
    }> = raw.cookies ?? [];
    // Build a cookie header manually and check whoami
    const cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    const r = await ctx.get("http://localhost:5173/v1/console/auth/whoami", {
      headers: { Cookie: cookieHeader },
    });
    await ctx.dispose();
    return r.status() === 200;
  } catch {
    return false;
  }
}

export default async function globalSetup() {
  // Fast path: reuse the existing valid session
  if (await isSessionValid()) {
    console.log("[global-setup] Existing auth-state.json is valid — skipping local-login");
    return;
  }

  const browser = await chromium.launch();
  const context = await browser.newContext({ baseURL: "http://localhost:5173" });
  const page = await context.newPage();

  await page.goto("http://localhost:5173/v1/console/auth/local-login", {
    waitUntil: "load",
    timeout: 20_000,
  }).catch(() => null);
  // After redirect we should land on the SPA — wait for React to boot
  await page.waitForURL(/\/console\//, { timeout: 10_000 }).catch(() => null);
  await page.waitForTimeout(1000);

  // Verify we're actually logged in
  const ok = await page.evaluate(async () => {
    try {
      const r = await fetch("/v1/console/auth/whoami", { credentials: "include" });
      return r.ok;
    } catch {
      return false;
    }
  });

  if (!ok) {
    await browser.close();
    throw new Error("Global setup: login failed — whoami returned non-ok");
  }

  await context.storageState({ path: AUTH_STATE_PATH });
  await browser.close();
  console.log("[global-setup] New auth-state.json saved");
}
