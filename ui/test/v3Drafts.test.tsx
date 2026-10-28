// @vitest-environment jsdom
// An editable draft follows its saved value, and an edit made right after the field mounts survives.
// The race needs a commit that React's scheduler leaves before running the mount's passive effects:
// a slow layout effect forces that yield, and a mutation observer types in the gap, outside act().
import { afterEach, describe, expect, it, vi } from "vitest";
import { useLayoutEffect, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { fireEvent as domEvent } from "@testing-library/dom";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import BrandField from "../src/v3/BrandField";
import { useDraft } from "../src/v3/common";

const SAVE = vi.fn(async () => true);

function Draft({ value, draftKey }: { value: string; draftKey?: string }) {
  const [draft, setDraft] = useDraft(value, draftKey);
  return <input aria-label="Draft" value={draft} onChange={(e) => setDraft(e.target.value)} />;
}

function SlowCommit() {
  useLayoutEffect(() => {
    const end = performance.now() + 20;
    while (performance.now() < end) { /* outlast the scheduler's frame so it yields before passive effects */ }
  }, []);
  return null;
}

const actEnvironment = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
let root: Root | null = null;

afterEach(() => {
  root?.unmount();
  root = null;
  actEnvironment.IS_REACT_ACT_ENVIRONMENT = true;
  cleanup();
  document.body.innerHTML = "";
});

/** Mounts `field` and types `text` into the input named `label` as soon as it appears; returns its value once settled. */
async function editOnMount(field: ReactElement, label: string, text: string): Promise<string> {
  actEnvironment.IS_REACT_ACT_ENVIRONMENT = false;
  const container = document.body.appendChild(document.createElement("div"));
  const typed = new Promise<HTMLInputElement>((resolve) => {
    const observer = new MutationObserver(() => {
      const input = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`);
      if (!input) return;
      observer.disconnect();
      domEvent.change(input, { target: { value: text } });
      resolve(input);
    });
    observer.observe(container, { childList: true, subtree: true });
  });
  root = createRoot(container);
  root.render(<><SlowCommit />{field}</>);
  const input = await typed;
  await new Promise((resolve) => setTimeout(resolve, 0));
  return input.value;
}

describe("useDraft", () => {
  it("keeps an edit made before the mount's effects have run", async () => {
    expect(await editOnMount(<Draft value="Saved" />, "Draft", "Edited")).toBe("Edited");
  });

  it("takes a new saved value or key over a pending edit, and keeps the edit otherwise", () => {
    const { rerender } = render(<Draft value="Saved" draftKey="a" />);
    const input = screen.getByRole("textbox", { name: "Draft" }) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Edited" } });
    rerender(<Draft value="Saved" draftKey="a" />);
    expect(input.value).toBe("Edited");
    rerender(<Draft value="Saved again" draftKey="a" />);
    expect(input.value).toBe("Saved again");
    fireEvent.change(input, { target: { value: "Edited" } });
    rerender(<Draft value="Saved again" draftKey="b" />);
    expect(input.value).toBe("Saved again");
  });
});

describe("the brand field", () => {
  const brand = { name: "Weeknight", accent: "#336699" };

  it("keeps an eyebrow or accent edit made before the mount's effects have run", async () => {
    expect(await editOnMount(<BrandField brand={brand} editable onSave={SAVE} />, "Brand name", "Kitchen")).toBe("Kitchen");
    root?.unmount();
    root = null;
    expect(await editOnMount(<BrandField brand={brand} editable onSave={SAVE} />, "Brand accent", "#aa5500")).toBe("#aa5500");
  });

  it("takes a newly saved brand over a pending edit", () => {
    const { rerender } = render(<BrandField brand={brand} editable onSave={SAVE} />);
    const name = screen.getByRole("textbox", { name: "Brand name" }) as HTMLInputElement;
    const accent = screen.getByLabelText("Brand accent") as HTMLInputElement;
    fireEvent.change(name, { target: { value: "Pending" } });
    rerender(<BrandField brand={{ name: "Renamed", accent: "#112233" }} editable onSave={SAVE} />);
    expect(name.value).toBe("Renamed");
    expect(accent.value).toBe("#112233");
  });
});
