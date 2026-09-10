import { describe, expect, it } from "vitest";
import { parsePhone } from "./phone";

describe("parsePhone", () => {
  it("returns an empty number with the default dial code for null", () => {
    const result = parsePhone(null);
    expect(result.number).toBe("");
    expect(result.dialCode).toBe("+34"); // Spain - COUNTRY_CODES[0], see profile
  });

  it("splits a stored '<dial code> <number>' string back into its parts", () => {
    expect(parsePhone("+34 611223344")).toEqual({ dialCode: "+34", number: "611223344" });
  });

  it("doesn't mistake a longer dial code that shares a prefix for a shorter one", () => {
    // Real regression case from the dataset: "+1" (US/Canada) is a
    // literal prefix of "+1242" (Bahamas). Without the trailing-space
    // boundary check in parsePhone(), "+1242 5551234" would match "+1"
    // first and mis-parse as dial code "+1", number "242 5551234".
    expect(parsePhone("+1242 5551234")).toEqual({ dialCode: "+1242", number: "5551234" });
    // The shorter code still resolves correctly on its own, unaffected.
    expect(parsePhone("+1 5551234")).toEqual({ dialCode: "+1", number: "5551234" });
  });

  it("keeps the full string as the number when no known dial code matches", () => {
    expect(parsePhone("not-a-real-number")).toEqual({
      dialCode: "+34",
      number: "not-a-real-number",
    });
  });
});
