import { describe, expect, it } from "vitest";
import { greeting } from "./greeting";

describe("greeting", () => {
  it("greets by first name when it's set", () => {
    expect(greeting({ email: "ada@shop.com", first_name: "Ada" })).toBe("Hello, Ada");
  });

  it("falls back to the email when first_name is null", () => {
    // Every GitHub OAuth signup, and any account that registered before
    // first_name existed - see UserRead's docstring in auth.py.
    expect(greeting({ email: "ada@shop.com", first_name: null })).toBe("ada@shop.com");
  });
});
