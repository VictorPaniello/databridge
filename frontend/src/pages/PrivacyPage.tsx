import { Link } from "react-router-dom";

// Written from what this codebase actually does, verified against the
// source rather than adapted from a generic template - every data
// category, third party, and retention statement below traces back to a
// real field in models.py/auth_models.py, a real integration in
// config.py, or a real endpoint in main.py. Best-effort by the developer,
// not a substitute for independent legal review.
export function PrivacyPage() {
  return (
    <div className="max-w-2xl space-y-8 text-sm leading-relaxed">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Privacy policy</h1>
        <p className="text-muted-foreground">Last updated: September 2026</p>
      </div>

      <Section title="Who's responsible for your data">
        <p>
          databridge is an independent project built and operated by Victor Paniello, based in
          Barcelona, Spain. There is no separate registered company - Victor is the data
          controller (and, for the client data you upload, your data processor - see below) for
          personal data processed through this service.
        </p>
        <p>
          Contact for anything on this page, including exercising the rights described further
          down: <a className="text-ring hover:underline" href="mailto:panivictor14@gmail.com">panivictor14@gmail.com</a>.
        </p>
      </Section>

      <Section title="What data this service collects, and why">
        <p>There are two different kinds of personal data here, and they're treated differently.</p>

        <h3 className="font-medium mt-4 mb-1">1. Your own account data</h3>
        <ul className="list-disc pl-5 space-y-1">
          <li>Email address (required) - to identify your account and log you in.</li>
          <li>
            First and last name (required for email+password signup; a GitHub sign-in only
            provides these once you complete your profile) - shown back to you in the app.
          </li>
          <li>Phone number (optional) - not currently used for anything besides storage on your profile.</li>
          <li>
            A hashed password (email+password accounts only) - your actual password is never
            stored, only a one-way hash of it.
          </li>
          <li>
            If you sign in with GitHub: your GitHub account ID and the email GitHub reports for
            it, plus an OAuth access/refresh token, stored so the linked login keeps working.
          </li>
        </ul>
        <p className="mt-2">
          <strong>Legal basis:</strong> performance of the contract you enter into by creating an
          account (Art. 6(1)(b) GDPR), and your consent for GitHub OAuth specifically, since it's
          an optional login method you choose to use.
        </p>

        <h3 className="font-medium mt-4 mb-1">2. Data you upload about your own clients</h3>
        <p>
          The core purpose of this tool is letting you upload a CSV/Excel export of your own
          clients and have it cleaned and stored. That export can contain your clients' name,
          email, phone, signup date, and a monetary amount - real personal data about real people
          who never interacted with databridge directly and have no account here.
        </p>
        <p className="mt-2">
          <strong>For this data, you are the controller, not databridge.</strong> By uploading a
          file, you confirm you have a lawful basis of your own (a contract with that client, their
          consent, or another valid basis under applicable law) to process and share their data
          this way. databridge acts as your <strong>data processor</strong> for this category only
          - storing and, if you configure a webhook, forwarding it on your behalf, on your
          instructions.
        </p>

        <h3 className="font-medium mt-4 mb-1">3. Technical data</h3>
        <ul className="list-disc pl-5 space-y-1">
          <li>
            A login token and your light/dark theme preference, stored in your browser's
            localStorage - never sent to databridge except as the token itself, on requests you
            make.
          </li>
          <li>
            One cookie, set only if you use "Sign in with GitHub": a short-lived, first-party CSRF
            token needed to complete that login securely. It isn't used for tracking and doesn't
            persist beyond the login flow.
          </li>
        </ul>
        <p className="mt-2">
          Nothing here is used for advertising, analytics, or tracking - there is no analytics
          script, tracking pixel, or third-party embed on this site to opt out of, because none
          exists.
        </p>
      </Section>

      <Section title="Who else sees your data">
        <p>Three services this project depends on, each only for what they're named for:</p>
        <ul className="list-disc pl-5 space-y-1">
          <li>
            <strong>GitHub</strong> - only if you choose "Sign in with GitHub"; see{" "}
            <a
              className="text-ring hover:underline"
              href="https://docs.github.com/en/site-policy/privacy-policies/github-privacy-statement"
              target="_blank"
              rel="noreferrer"
            >
              GitHub's own privacy statement
            </a>.
          </li>
          <li>
            <strong>Resend</strong> - sends the "forgot password" email, if you request one. This
            means your email address, and the reset link, passes through Resend's systems (based
            in the United States) to reach your inbox. See{" "}
            <a
              className="text-ring hover:underline"
              href="https://resend.com/legal/privacy-policy"
              target="_blank"
              rel="noreferrer"
            >
              Resend's privacy policy
            </a>.
          </li>
          <li>
            <strong>Railway</strong> and <strong>Vercel</strong> - host the backend/database and
            the frontend, respectively. Standard infrastructure providers; they don't receive your
            data for any purpose of their own beyond running the service.
          </li>
          <li>
            <strong>A webhook URL you configure yourself</strong> (optional, self-hosted setups
            only) - if set, the data of each newly-ingested client record is sent there. This is
            your own destination, under your own control, not a databridge-operated third party.
          </li>
        </ul>
        <p className="mt-2">
          Nobody else. Your data is not sold, rented, or shared for marketing purposes - there
          isn't a marketing program to share it with.
        </p>
      </Section>

      <Section title="How long data is kept">
        <p>
          <strong>Client data</strong> - what you upload about your own clients - is kept for up to{" "}
          <strong>one year</strong> after it's ingested, then deleted automatically by a scheduled
          job. This isn't just a policy statement; it's a real, tested, scheduled process that
          actually deletes the data - see the project's own README for the technical detail. You
          can also delete an individual client record yourself at any time, sooner than that, from
          the records list.
        </p>
        <p className="mt-2">
          <strong>Your own account</strong> (email, name, phone, password) is different: it's kept
          for as long as you want it, with no automatic expiry, until you delete it yourself from{" "}
          <Link to="/settings" className="text-ring hover:underline">
            Account settings
          </Link>
          . Deleting your account also immediately erases every client record it owns, rather than
          waiting out the one-year window.
        </p>
        <p className="mt-2">
          Database backups are retained separately for up to 30 days for disaster-recovery
          purposes only (protecting against a bad migration or accidental deletion, not a
          substitute for this policy) - see the project's own README for that mechanism.
        </p>
      </Section>

      <Section title="Your rights">
        <p>Under GDPR, you can:</p>
        <ul className="list-disc pl-5 space-y-1">
          <li><strong>Access</strong> the data held about you.</li>
          <li><strong>Rectify</strong> inaccurate data - directly in Account settings for your own profile.</li>
          <li>
            <strong>Erase</strong> your data - self-service, right now: delete individual client
            records from the records list, or your whole account from Account settings.
          </li>
          <li><strong>Export</strong> your data (portability).</li>
          <li><strong>Object to or restrict</strong> processing.</li>
        </ul>
        <p className="mt-2">
          For anything not already self-service in the app, email{" "}
          <a className="text-ring hover:underline" href="mailto:panivictor14@gmail.com">panivictor14@gmail.com</a>.
          If you're not satisfied with the response, you can lodge a complaint with Spain's data
          protection authority, the{" "}
          <a
            className="text-ring hover:underline"
            href="https://www.aepd.es"
            target="_blank"
            rel="noreferrer"
          >
            Agencia Española de Protección de Datos (AEPD)
          </a>
          .
        </p>
      </Section>

      <Section title="Cookies">
        <p>
          One cookie exists on this site: a first-party, session-scoped CSRF token, set only
          during the "Sign in with GitHub" flow, strictly necessary for that login to complete
          securely. Under GDPR/ePrivacy guidance, strictly necessary cookies don't require a
          consent banner - only disclosure, which is this. There are no analytics, advertising, or
          tracking cookies, and therefore nothing else to ask your consent for.
        </p>
      </Section>

      <Section title="Children">
        <p>
          This service isn't directed at, or knowingly used by, anyone under 16. It's a tool for
          managing business client data, not aimed at a general audience.
        </p>
      </Section>

      <Section title="Changes to this policy">
        <p>
          If this policy changes in a way that matters, the "Last updated" date above will change
          along with it. Given the project's own size, expect changes to be occasional and
          substantive, not a routine legal-boilerplate churn.
        </p>
      </Section>

      <p className="text-xs text-muted-foreground pt-4 border-t border-border">
        This policy is a good-faith, technically accurate description of what this specific
        codebase does, written by its developer - not a substitute for independent legal advice
        for your own use of the service.
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
