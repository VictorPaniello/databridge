import { COUNTRY_CODES } from "../data/countryCodes";

// Shared by RegisterPage, CompleteProfilePage, and SettingsPage - all three
// collect the same "dial code + local number" pair the same way, so this
// used to be copy-pasted three times.
export function PhoneInput({
  dialCode,
  phoneNumber,
  onDialCodeChange,
  onPhoneNumberChange,
}: {
  dialCode: string;
  phoneNumber: string;
  onDialCodeChange: (dialCode: string) => void;
  onPhoneNumberChange: (phoneNumber: string) => void;
}) {
  return (
    <div>
      <label className="block text-sm font-medium mb-1" htmlFor="phone">
        Phone <span className="text-muted-foreground font-normal">(optional)</span>
      </label>
      <div className="flex gap-2">
        <select
          id="phone-country"
          aria-label="Country code"
          value={dialCode}
          onChange={(e) => onDialCodeChange(e.target.value)}
          // bg-background/text-foreground (not bg-transparent) here because
          // the dropdown's own open-list popup is native chrome the page
          // can't reach with Tailwind classes - only color-scheme and an
          // explicit background/color on <option> (below) reliably keep it
          // from falling back to barely-readable default styling in dark
          // mode.
          className="w-28 shrink-0 rounded-md border border-input bg-background text-foreground px-2 py-2 outline-none focus:ring-2 focus:ring-ring"
        >
          {COUNTRY_CODES.map((c) => (
            <option
              key={c.iso2}
              value={c.dialCode}
              style={{ backgroundColor: "var(--background)", color: "var(--foreground)" }}
            >
              {c.iso2} {c.dialCode}
            </option>
          ))}
        </select>
        <input
          id="phone"
          type="tel"
          autoComplete="tel-national"
          value={phoneNumber}
          onChange={(e) => onPhoneNumberChange(e.target.value)}
          className="w-full rounded-md border border-input bg-transparent px-3 py-2 outline-none focus:ring-2 focus:ring-ring"
        />
      </div>
    </div>
  );
}
