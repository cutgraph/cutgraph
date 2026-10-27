// @vitest-environment jsdom
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { toast as sharedToast } from "../src/toast";
import ToastHost, { toast } from "../src/v3/Toast";

afterEach(cleanup);

describe("v3 toast host", () => {
  it("shows its own toasts", () => {
    render(<ToastHost />);
    act(() => toast("Saved"));
    expect(screen.getByRole("status").textContent).toBe("Saved");
    expect(screen.getByRole("status").className).toContain("show");
  });

  it("shows toasts raised on the shared store/save-path bus, including repeats and actions", () => {
    render(<ToastHost />);
    act(() => sharedToast("Remote edit dropped your redo history"));
    expect(screen.getByRole("status").textContent).toBe("Remote edit dropped your redo history");

    const run = vi.fn();
    act(() => sharedToast("Conflict", { label: "Reload", run }));
    act(() => sharedToast("Conflict", { label: "Reload", run }));
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(run).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status").className).not.toContain("show");
  });
});
