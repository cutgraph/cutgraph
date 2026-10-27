// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../src/v3/story/ask/markdown";

function html(text: string): string {
  const { container } = render(<>{renderMarkdown(text)}</>);
  return container.innerHTML;
}

describe("renderMarkdown", () => {
  it("renders bold, italic and inline code as real elements, not literal asterisks or backticks", () => {
    expect(html("**Blindspot pass** (short clip) — `channel_profile` is unset, so treat it *conservatively*."))
      .toBe("<p><strong>Blindspot pass</strong> (short clip) — <code>channel_profile</code> is unset, so treat it <em>conservatively</em>.</p>");
  });

  it("turns a blank-line-separated paragraph into its own <p>, and single newlines into <br>", () => {
    expect(html("First line.\nSecond line.\n\nA new paragraph."))
      .toBe("<p>First line.<br>Second line.</p><p>A new paragraph.</p>");
  });

  it("renders a run of - or * lines as a list", () => {
    expect(html("Here's what I found:\n- um at word 4\n- a repeat at word 9"))
      .toBe("<p>Here's what I found:</p><ul><li>um at word 4</li><li>a repeat at word 9</li></ul>");
  });

  it("never turns the model's own literal HTML into markup (no raw HTML injection)", () => {
    const out = html("<script>alert(1)</script> and <img src=x onerror=alert(1)>");
    expect(out).not.toContain("<script>");
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;script&gt;");
  });

  it("is empty for blank text", () => {
    expect(html("   \n\n  ")).toBe("");
  });
});
