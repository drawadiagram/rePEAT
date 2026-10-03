/**
 * Sanitization of agent prose.
 *
 * This component is the only `dangerouslySetInnerHTML` in the app, and its input
 * is model-influenced: a session summary, an artifact, a chat answer. `marked`
 * passes raw HTML through untouched, so DOMPurify is the only thing between a
 * model's output and the DOM. These tests exist so that stays true.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import MarkdownView from "./MarkdownView";

afterEach(cleanup);

function html(text: string): string {
  const { container } = render(<MarkdownView text={text} />);
  return container.innerHTML;
}

describe("MarkdownView", () => {
  it("renders ordinary markdown", () => {
    const out = html("## Interpretation\n\nThe lead is **d-3**.");
    expect(out).toContain("<h2");
    expect(out).toContain("<strong>d-3</strong>");
  });

  it("drops a script tag", () => {
    const out = html("Summary.\n\n<script>window.stolen = 1</script>");
    expect(out).not.toContain("<script");
    expect(out).not.toContain("window.stolen");
  });

  it("drops an inline event handler", () => {
    const out = html('<img src="x" onerror="window.stolen = 1">');
    expect(out).not.toContain("onerror");
  });

  it("drops a javascript: href but keeps the link text", () => {
    const out = html("[click me](javascript:window.stolen=1)");
    expect(out).not.toContain("javascript:");
    expect(screen.getByText("click me")).toBeTruthy();
  });

  it("drops an iframe", () => {
    expect(html('<iframe src="https://example.com"></iframe>')).not.toContain("<iframe");
  });

  it("says so when there is nothing to show", () => {
    expect(screen.queryByText("Empty document.")).toBeNull();
    render(<MarkdownView text="" />);
    expect(screen.getByText("Empty document.")).toBeTruthy();
  });
});
