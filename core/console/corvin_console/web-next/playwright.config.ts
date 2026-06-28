import { defineConfig, devices } from '@playwright/test';

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  testDir: './tests/e2e',
  /* Log in once and persist the session so specs can reuse it via
     `use.storageState` instead of each spec calling local-login (which
     otherwise trips the 10-logins/60s rate-limit when run in parallel). */
  globalSetup: './tests/e2e/global-setup-adr0124.ts',
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI. */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  reporter: 'html',
  /* Per-test timeout: Firefox in CI is slower than Chromium (cold JIT,
     headless startup); 45 s gives it enough headroom without masking
     real hangs (default is 30 s, retries=2 so total budget = 135 s/test). */
  timeout: 45_000,

  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
  use: {
    /* Base URL to use in actions like `await page.goto('/')`. */
    baseURL: 'http://localhost:5173',
    /* Reuse the session created in globalSetup. Specs that test auth flows
       (security-session-auth.spec.ts) mock the network layer and are
       unaffected; specs that self-login simply start already authenticated. */
    storageState: './tests/e2e/auth-state.json',
    /* Individual action timeout (click, fill, waitFor…). */
    actionTimeout: 15_000,
    /* Navigation timeout for page.goto() and waitForURL(). */
    navigationTimeout: 45_000,
    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  /* Configure projects for major browsers */
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },

    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },

    // WebKit requires additional system deps (libavif16, libwoff1, etc)
    // Enable only when full environment is available
    // {
    //   name: 'webkit',
    //   use: { ...devices['Desktop Safari'] },
    // },

    /* Test against mobile viewports. */
    {
      name: 'Mobile Chrome',
      use: { ...devices['Pixel 5'] },
    },
    // {
    //   name: 'Mobile Safari',
    //   use: { ...devices['iPhone 12'] },
    // },

    /* Test against branded browsers. */
    // {
    //   name: 'Microsoft Edge',
    //   use: { ...devices['Desktop Edge'], channel: 'msedge' },
    // },
    // {
    //   name: 'Google Chrome',
    //   use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    // },
  ],

  /* Run your local dev server before starting the tests */
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
  },
});
