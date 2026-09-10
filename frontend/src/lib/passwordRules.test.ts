import { describe, expect, it } from "vitest";
import { PASSWORD_RULES } from "./passwordRules";

// Mirrors the backend's real policy (auth.py's UserManager.validate_
// password) - these are the client-side hints shown while typing, so
// every rule needs to accept/reject the same passwords the backend
// would, or a user sees "all green" here and still gets rejected by the
// API.
function passes(password: string): boolean[] {
  return PASSWORD_RULES.map((rule) => rule.test(password));
}

describe("PASSWORD_RULES", () => {
  it("rejects a password missing every requirement", () => {
    expect(passes("")).toEqual([false, false, false, false, false]);
  });

  it("flags each requirement independently, not just overall pass/fail", () => {
    // Exactly one rule failing at a time - proves each test() function
    // checks only its own requirement, not some combined regex where a
    // bug in one rule could silently mask another.
    expect(passes("short1A!")).toEqual([true, true, true, true, true]);
    expect(passes("nouppercase1!")).toEqual([true, false, true, true, true]);
    expect(passes("NOLOWERCASE1!")).toEqual([true, true, false, true, true]);
    expect(passes("NoDigitHere!")).toEqual([true, true, true, false, true]);
    expect(passes("NoSpecial123")).toEqual([true, true, true, true, false]);
    expect(passes("Sh0rt!")).toEqual([false, true, true, true, true]);
  });

  it("accepts a password satisfying every rule", () => {
    expect(passes("Valid1Password!")).toEqual([true, true, true, true, true]);
  });
});
