import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

const props = {
  title: "Delete this record?",
  message: "This cannot be undone.",
};

describe("ConfirmDialog", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <ConfirmDialog open={false} {...props} onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the title and message when open", () => {
    render(<ConfirmDialog open {...props} onConfirm={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText("Delete this record?")).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
  });

  it("calls onConfirm when the confirm button is clicked", () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open {...props} onConfirm={onConfirm} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when the cancel button is clicked", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog open {...props} onConfirm={vi.fn()} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when the backdrop is clicked, but not when the dialog body is clicked", () => {
    const onCancel = vi.fn();
    render(<ConfirmDialog open {...props} onConfirm={vi.fn()} onCancel={onCancel} />);

    // The dialog body stops propagation - clicking inside it must not
    // dismiss the dialog the way clicking the backdrop behind it does.
    fireEvent.click(screen.getByRole("alertdialog"));
    expect(onCancel).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Delete this record?").closest("div.fixed")!);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("uses a custom confirm label when given one", () => {
    render(
      <ConfirmDialog open {...props} confirmLabel="Remove" onConfirm={vi.fn()} onCancel={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });
});
