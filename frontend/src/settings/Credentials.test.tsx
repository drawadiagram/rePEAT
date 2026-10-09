/**
 * The sign-in gate and the panel where a user types their own key.
 *
 * Three things are pinned. With logins off, the app renders with no sign-in
 * page and no request it did not make before — every old test's assumption.
 * With logins on and no session, the sign-in page and nothing else. And the
 * credentials panel keeps SettingsPanel's two rules: a secret is never rendered,
 * and an untouched field is never sent back as the hint it displayed.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import App from "../App";
import * as api from "../lib/api";
import CredentialsPanel from "./CredentialsPanel";

const HINT = "sk-ant-…9f3c";

// The chat pane scrolls its last message into view; jsdom has no layout, so no
// scrollIntoView. These are the first tests to mount the whole App.
beforeAll(() => {
  Element.prototype.scrollIntoView = () => {};
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function stubWorkspace() {
  vi.spyOn(api, "fetchHealth").mockResolvedValue({ ok: true, llm: false, hpc: false });
  vi.spyOn(api, "fetchSession").mockResolvedValue({ state: {}, messages: [], tasks: [] });
}

describe("sign-in gate", () => {
  it("with logins off, renders the app and never shows a sign-in page", async () => {
    vi.spyOn(api, "fetchMe").mockResolvedValue({ auth: false });
    stubWorkspace();
    render(<App />);
    await waitFor(() => screen.getByText("New session"));
    expect(screen.queryByRole("form", { name: "Sign in" })).toBeNull();
    expect(screen.queryByText("Sign out")).toBeNull();
    expect(screen.getByText("Settings")).toBeTruthy();
  });

  it("with logins on and no session, shows only the sign-in page", async () => {
    vi.spyOn(api, "fetchMe").mockResolvedValue({ auth: true });
    render(<App />);
    await waitFor(() => screen.getByRole("form", { name: "Sign in" }));
    expect(screen.queryByText("New session")).toBeNull();
  });

  it("a plain user gets their own credentials and not the server's settings", async () => {
    vi.spyOn(api, "fetchMe").mockResolvedValue({
      auth: true,
      user: { id: "u-1", username: "alice", role: "user" },
      llm: false,
      hpc: false,
    });
    stubWorkspace();
    render(<App />);
    await waitFor(() => screen.getByText("My credentials"));
    expect(screen.queryByText("Settings")).toBeNull();
    expect(screen.getByText("Sign out")).toBeTruthy();
  });

  it("returns to the sign-in page on any 401", async () => {
    vi.spyOn(api, "fetchMe").mockResolvedValue({
      auth: true,
      user: { id: "u-1", username: "alice", role: "user" },
    });
    stubWorkspace();
    render(<App />);
    await waitFor(() => screen.getByText("Sign out"));
    window.dispatchEvent(new Event(api.UNAUTHORIZED));
    await waitFor(() => screen.getByRole("form", { name: "Sign in" }));
  });
});

describe("CredentialsPanel", () => {
  const view = {
    credentials: {
      anthropic_api_key: { present: true, hint: HINT, source: "user" as const },
      model: { value: "", source: "user" as const },
      orbit_broker_url: { value: "https://broker:8443", source: "user" as const },
      orbit_broker_token: { present: false, hint: "", source: "user" as const },
      orbit_broker_cert: { value: "(certificate set)", source: "user" as const },
      orbit_endpoint: { value: "amarel3", source: "user" as const },
      orbit_account: { value: "", source: "user" as const },
      orbit_queue: { value: "", source: "user" as const },
    },
    fields: [],
    can_store: true,
    allowed_brokers: ["https://broker:8443"],
  };

  it("shows a key only as a placeholder, never as a value", async () => {
    vi.spyOn(api, "fetchCredentials").mockResolvedValue(view);
    render(<CredentialsPanel onClose={() => {}} onChanged={() => {}} />);
    const key = (await waitFor(() =>
      screen.getByLabelText("Anthropic API key"),
    )) as HTMLInputElement;
    expect(key.value).toBe("");
    expect(key.placeholder).toBe(HINT);
    expect(key.type).toBe("password");
    expect(document.body.innerHTML).not.toContain("sk-ant-alice");
  });

  it("sends only the fields that were touched", async () => {
    vi.spyOn(api, "fetchCredentials").mockResolvedValue(view);
    const save = vi
      .spyOn(api, "saveCredentials")
      .mockResolvedValue({ ok: true, status: 200, view });
    render(<CredentialsPanel onClose={() => {}} onChanged={() => {}} />);
    const queue = await waitFor(() => screen.getByLabelText("Queue"));
    await userEvent.type(queue, "main");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(save).toHaveBeenCalledWith({ orbit_queue: "main" });
  });
});
