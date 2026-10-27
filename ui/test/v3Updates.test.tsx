// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import Updates from "../src/v3/Updates";
import { ApiError, v3api, type UpdateStatus } from "../src/v3/api";

const status = (state: UpdateStatus["state"]): UpdateStatus => ({ state, reason: "Verification reason", automatic: true,
  currentVersion: "0.2.0", version: "0.3.0", releaseNotesUrl: "https://example.org/release", canInstall: state === "available", lastCheck: null });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each([
  ["up_to_date", "Up to date"], ["checking", "Checking for updates…"], ["available", "Update available: 0.3.0"],
  ["downloading", "Downloading update…"], ["verifying", "Verifying update…"], ["failed", "Update failed"],
  ["not_configured", "Updates not configured"], ["installing", "Installing update…"], ["disabled", "Update checks are off"],
] as const)("presents %s with the reason and default preference", async (state, label) => {
  vi.spyOn(v3api, "updates").mockResolvedValue(status(state));
  render(<Updates />);
  expect(await screen.findByText(label)).toBeTruthy();
  expect(screen.getByText(/Verification reason/)).toBeTruthy();
  expect(screen.getByLabelText("Check for updates automatically")).toHaveProperty("checked", true);
});

it("saves the off switch and disables manual checking", async () => {
  vi.spyOn(v3api, "updates").mockResolvedValue(status("available"));
  const save = vi.spyOn(v3api, "updatePreferences").mockResolvedValue({ ...status("disabled"), automatic: false, canInstall: false });
  render(<Updates />);
  fireEvent.click(await screen.findByLabelText("Check for updates automatically"));
  await screen.findByText("Update checks are off");
  expect(save).toHaveBeenCalledWith(false);
  expect(screen.getByRole("button", { name: "Check for updates" })).toHaveProperty("disabled", true);
});

it("Home offers the verified update, and source checkouts get only the notes link", async () => {
  vi.spyOn(v3api, "updates").mockResolvedValue({ ...status("available"), canInstall: false });
  render(<Updates compact />);
  expect(await screen.findByText("Update available: 0.3.0")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Release notes and download" }).getAttribute("href")).toBe("https://example.org/release");
  expect(screen.queryByRole("button")).toBeNull();
});

it("starts an update and presents its download state", async () => {
  vi.spyOn(v3api, "updates").mockResolvedValue(status("available"));
  const install = vi.spyOn(v3api, "installUpdate").mockResolvedValue({ ...status("downloading"), canInstall: false });
  render(<Updates />);
  fireEvent.click(await screen.findByRole("button", { name: "Update to 0.3.0" }));
  await screen.findByText("Downloading update…");
  expect(install).toHaveBeenCalledOnce();
});

it("updates the switch immediately and restores its saved value when saving fails", async () => {
  vi.spyOn(v3api, "updates").mockResolvedValue(status("available"));
  let reject!: (error: Error) => void;
  vi.spyOn(v3api, "updatePreferences").mockImplementation(() => new Promise((_resolve, fail) => { reject = fail; }));
  render(<Updates />);
  const checkbox = await screen.findByLabelText("Check for updates automatically");
  fireEvent.click(checkbox);
  expect(checkbox).toHaveProperty("checked", false);
  expect(checkbox).toHaveProperty("disabled", true);
  reject(new Error("Couldn't save update preferences."));
  await screen.findByRole("alert");
  expect(checkbox).toHaveProperty("checked", true);
  expect(checkbox).toHaveProperty("disabled", false);
});

it("shows an update refusal without its HTTP status", async () => {
  vi.spyOn(v3api, "updates").mockResolvedValue(status("available"));
  const reason = "Use the release notes to update this installation manually.";
  vi.spyOn(v3api, "installUpdate").mockRejectedValue(new ApiError(409, `409 ${reason}`, undefined, reason));
  render(<Updates />);
  fireEvent.click(await screen.findByRole("button", { name: "Update to 0.3.0" }));
  expect(await screen.findByText(reason)).toBeTruthy();
  expect(screen.queryByText(/409/)).toBeNull();
});
