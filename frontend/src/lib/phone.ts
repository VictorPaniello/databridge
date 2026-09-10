import { COUNTRY_CODES } from "../data/countryCodes";

// Phone is stored as a single "<dial code> <number>" string (see
// RegisterPage/CompleteProfilePage) - this splits it back into the two
// pieces SettingsPage's form needs to pre-fill. Matches on "<dial code> "
// with the trailing space so "+1 5551234" (US) can't be mistaken for a
// prefix of "+1242 5551234" (Bahamas) - dial codes that share a prefix
// only collide without that boundary check.
export function parsePhone(phone: string | null): { dialCode: string; number: string } {
  const fallbackDialCode = COUNTRY_CODES[0].dialCode;
  if (!phone) return { dialCode: fallbackDialCode, number: "" };

  const match = COUNTRY_CODES.find((c) => phone.startsWith(`${c.dialCode} `));
  if (!match) return { dialCode: fallbackDialCode, number: phone };

  return { dialCode: match.dialCode, number: phone.slice(match.dialCode.length).trim() };
}
