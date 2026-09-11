import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CopyButton } from "./CopyButton";

describe("CopyButton", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("writes the given value to the clipboard when clicked", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<CopyButton value="https://example.com/webhook" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));

    expect(writeText).toHaveBeenCalledWith("https://example.com/webhook");
    // Confirms the icon swap, not the timed revert back - that's an
    // implementation timing detail not worth a flaky fake-timer test.
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("doesn't throw, and leaves the default state, if the clipboard write fails", async () => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });

    render(<CopyButton value="secret" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));

    // Give the rejected promise a tick to settle, then confirm it never
    // flipped to "Copied" and nothing threw past the click handler.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  });
});
