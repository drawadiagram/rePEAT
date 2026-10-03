import { defineConfig } from "@playwright/test";

/**
 * One real browser against the real dev servers.
 *
 * The default run is deterministic: it replays a canned SSE stream through
 * `page.route`, so it needs no model, no network and nothing from the backend.
 * `E2E_LIVE=1` adds the networked round trip.
 *
 * Both servers are declared here so a cold `npm run test:e2e` works, but
 * `reuseExistingServer` means a dev loop that already has them up is not
 * disturbed — and the backend runs with `cwd: ".."` because `config.yml` is read
 * from the working directory at `import flowgentic` time.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: ".venv/bin/python -m designagent --port 8000",
      cwd: "..",
      url: "http://127.0.0.1:8000/api/health",
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: "npm run dev",
      url: "http://localhost:5173",
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
});
