import { Link } from "react-router-dom";

export function TermsPage() {
  return (
    <div className="max-w-2xl space-y-8 text-sm leading-relaxed">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Terms of service</h1>
        <p className="text-muted-foreground">Last updated: September 2026</p>
      </div>

      <Section title="What this is">
        <p>
          tidybridge is a client-data ingestion service: you upload a CSV/Excel file, it's
          cleaned and validated, persisted, and (if you've configured one) forwarded to a webhook
          you control. It's operated by an individual developer (Victor Paniello), not a
          company, and run as a single instance with no uptime guarantee or service-level
          agreement - see the project's own README for the full, honest list of what it doesn't
          do yet (no horizontal scaling, no failover, in-memory rate limiting). By using it, you
          accept that trade-off.
        </p>
      </Section>

      <Section title="Your account">
        <ul className="list-disc pl-5 space-y-1">
          <li>You must provide accurate information when registering (or completing your profile after a GitHub sign-in).</li>
          <li>You're responsible for keeping your password confidential and for activity under your account.</li>
          <li>One account per person; don't share credentials.</li>
        </ul>
      </Section>

      <Section title="Data you upload about your own clients">
        <p>
          You may only upload data you have a lawful right to process and share - your own
          clients' or customers' data, under a lawful basis you already have with them (a
          contract, their consent, or another valid basis). You're the data controller for
          anything you upload; tidybridge acts only as your processor, on your instructions - see
          the{" "}
          <Link to="/privacy" className="text-ring hover:underline">
            Privacy policy
          </Link>{" "}
          for what that means in practice.
        </p>
        <p className="mt-2">
          Don't upload data you don't have the right to share - someone else's client list taken
          without authorization, data obtained unlawfully, or special-category data (health,
          biometric, etc.) this service isn't built to handle securely.
        </p>
      </Section>

      <Section title="Acceptable use">
        <p>Don't use tidybridge to:</p>
        <ul className="list-disc pl-5 space-y-1">
          <li>Attack, overload, or attempt to gain unauthorized access to the service or other accounts.</li>
          <li>Upload malicious files, or data intended to exploit the ingestion pipeline.</li>
          <li>Violate any applicable law, or the rights of the people whose data you upload.</li>
        </ul>
        <p className="mt-2">
          Reasonable rate limits apply to registration and login specifically (5 attempts/minute)
          to deter abuse - see the README's Security section for the exact mechanism.
        </p>
      </Section>

      <Section title="Deleting your data">
        <p>
          You can delete an individual client record at any time from the records list, and your
          entire account - along with every client record, upload history entry, and webhook
          delivery it owns - from{" "}
          <Link to="/settings" className="text-ring hover:underline">
            Account settings
          </Link>
          . This is real, permanent deletion, not a deactivation; there's no way to undo it once
          confirmed, and no way for tidybridge to recover it for you afterward.
        </p>
      </Section>

      <Section title="No warranty">
        <p>
          tidybridge is provided "as is," without warranty of any kind, express or implied,
          including merchantability or fitness for a particular purpose. It's a single-engineer
          project, not a commercial product backed by a support team - see the README for the
          explicit, current list of what it doesn't (yet) do.
        </p>
      </Section>

      <Section title="Limitation of liability">
        <p>
          To the maximum extent permitted by law, Victor Paniello isn't liable for any indirect,
          incidental, or consequential damages arising from your use of (or inability to use) this
          service, including loss of data beyond the backup measures described in the README.
          Nothing here limits liability where the law doesn't allow it to be limited (e.g., for
          gross negligence or willful misconduct).
        </p>
      </Section>

      <Section title="Termination">
        <p>
          You can stop using the service and delete your account at any time. Access may be
          suspended or terminated for a violation of these terms (e.g., abuse, unlawful data
          uploads), with notice where reasonably possible.
        </p>
      </Section>

      <Section title="Changes to these terms">
        <p>
          If these terms change in a way that matters, the "Last updated" date above will move
          with them. Continuing to use the service after a change means you accept the updated
          terms.
        </p>
      </Section>

      <Section title="Governing law">
        <p>
          These terms are governed by the laws of Spain. Any dispute not resolved informally is
          subject to the courts of Barcelona, Spain, without prejudice to any mandatory consumer-
          protection rights you may have in your own country of residence under EU law.
        </p>
      </Section>

      <Section title="Contact">
        <p>
          <a className="text-ring hover:underline" href="mailto:panivictor14@gmail.com">panivictor14@gmail.com</a>
        </p>
      </Section>

      <p className="text-xs text-muted-foreground pt-4 border-t border-border">
        Written by the developer as a good-faith, accurate description of this specific service -
        not a substitute for independent legal advice for your own use of it.
      </p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="text-lg font-semibold tracking-tight mb-2">{title}</h2>
      {children}
    </section>
  );
}
