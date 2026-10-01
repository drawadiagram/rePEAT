import { useMemo } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";

/**
 * Render Markdown from the agent.
 *
 * The text is model-influenced, so it is sanitized before it reaches the DOM:
 * marked does not escape raw HTML on its own.
 */
export default function MarkdownView({ text }: { text: string }) {
  const html = useMemo(() => {
    const parsed = marked.parse(text, { async: false, gfm: true }) as string;
    return DOMPurify.sanitize(parsed, { USE_PROFILES: { html: true } });
  }, [text]);

  if (!text) return <p className="muted">Empty document.</p>;
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}
