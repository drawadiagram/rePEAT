/**
 * The panel where someone types a real API key.
 *
 * Two properties are stated in the component's own docstring and were not
 * enforced anywhere: it never renders a secret, and a field the user did not
 * touch is left out of the request rather than sent back as the masked hint it
 * displayed. Sending the hint back would write the literal string
 * "sk-ant-…4f2a" into the running settings.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import * as api from "../lib/api";
import SettingsPanel from "./SettingsPanel";

const HINT = "sk-ant-…4f2a";

function view(overrides: string[] = ["anthropic_api_key"]) {
  return {
    credentials: {
      llm: {
        anthropic_api_key: { present: true, hint: HINT, source: "override" as const },
        model: { value: "claude-sonnet-5-5", source: "env" as const },
        max_tokens: { value: 2048, source: "default" as const },
      },
      orbit: {
        orbit_enabled: { value: false, source: "default" as const },
        orbit_broker_url: { value: "https://broker:8443", source: "env" as const },
        orbit_broker_token: { present: false, hint: "", source: "default" as const },
        orbit_broker_cert: { value: "", source: "default" as const },
        orbit_endpoint: { value: "", source: "default" as const },
        orbit_local_stack: { value: false, source: "default" as const },
        orbit_rhapsody_backends: { value: "", source: "default" as const },
        orbit_psij_executor: { value: "local", source: "default" as const },
        orbit_account: { value: "", source: "default" as const },
        orbit_queue: { value: "", source: "default" as const },
        orbit_job_duration_sec: { value: 1800, source: "default" as const },
      },
      globus: {
        globus_enabled: { value: false, source: "default" as const },
        globus_endpoint_id: { value: "", source: "default" as const },
      },
      fold: { fold_backend: { value: "esmatlas", source: "default" as const } },
    },
    overrides,
    hpc_available: false,
    llm_available: true,
    persistent_sessions: true,
  };
}

// Typed from the real function, so `tsc -b` checks the stub's shape against the
// module it replaces — a drifting mock is a test that proves nothing.
let save: MockInstance<typeof api.saveSettings>;

beforeEach(() => {
  vi.spyOn(api, "fetchSettings").mockResolvedValue(view());
  save = vi.spyOn(api, "saveSettings").mockResolvedValue({
    ok: true,
    status: 200,
    body: { ...view(), applied: true, restarted: ["pool", "graph"], sessions_preserved: true },
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const noop = () => {};

describe("SettingsPanel", () => {
  it("shows a secret only as a placeholder, never as a value", async () => {
    render(<SettingsPanel onClose={noop} onChanged={noop} />);
    const key = await waitFor(() => screen.getByLabelText(/Anthropic API key/));

    expect((key as HTMLInputElement).value).toBe("");
    expect((key as HTMLInputElement).placeholder).toBe(HINT);
    expect((key as HTMLInputElement).type).toBe("password");
  });

  it("sends only the fields the user touched", async () => {
    render(<SettingsPanel onClose={noop} onChanged={noop} />);
    const account = await waitFor(() => screen.getByLabelText(/Account/));
    await userEvent.type(account, "proj-42");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(save).toHaveBeenCalled());
    const [values] = save.mock.calls[0];
    expect(values).toEqual({ orbit_account: "proj-42" });
    // The masked hint must never travel back as if it were the key.
    expect(JSON.stringify(values)).not.toContain("…");
  });

  it("cannot apply with nothing changed", async () => {
    render(<SettingsPanel onClose={noop} onChanged={noop} />);
    const apply = await waitFor(() => screen.getByRole("button", { name: "Apply" }));
    expect((apply as HTMLButtonElement).disabled).toBe(true);
  });

  it("says where each value came from", async () => {
    render(<SettingsPanel onClose={noop} onChanged={noop} />);
    await waitFor(() => screen.getByLabelText(/Anthropic API key/));
    expect(screen.getByText("set here")).toBeTruthy(); // the override
    expect(screen.getAllByText("from .env").length).toBeGreaterThan(0);
  });

  it("reports what the apply restarted, including the pool", async () => {
    render(<SettingsPanel onClose={noop} onChanged={noop} />);
    const model = await waitFor(() => screen.getByLabelText("Model"));
    await userEvent.clear(model);
    await userEvent.type(model, "claude-opus-5");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    expect(await waitFor(() => screen.getByText(/Applied\./))).toBeTruthy();
    expect(screen.getByText(/pool, graph/)).toBeTruthy();
  });

  it("offers to force past running tasks rather than silently abandoning them", async () => {
    // Exactly what app.py returns on a 409, down to the field names.
    save.mockResolvedValue({
      ok: false,
      status: 409,
      body: {
        applied: false,
        reason: "tasks are still running; retry with force=true",
        running: ["fold-1", "fold-2"],
      },
    });
    render(<SettingsPanel onClose={noop} onChanged={noop} />);
    const account = await waitFor(() => screen.getByLabelText(/Account/));
    await userEvent.type(account, "proj-42");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    expect(await waitFor(() => screen.getByText(/2 task\(s\) are still running/))).toBeTruthy();
    const forced = screen.getByRole("button", { name: "Apply anyway" });
    await userEvent.click(forced);
    await waitFor(() => expect(save.mock.calls.length).toBe(2));
    expect(save.mock.calls[1][1]).toMatchObject({ force: true });
  });
});
