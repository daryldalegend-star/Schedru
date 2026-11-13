# Schedru launch plan

> **Status: draft, waiting for your approval.** Nothing in this plan has been built yet.
> Each phase ends with a plain-English summary of what changed and why, steps to test it
> yourself, and a git commit. Then work stops until you approve the next phase.

## 1. Where Schedru stands today

| | Today |
|---|---|
| Languages | Python 3.11+ for the app and its engine; HTML, CSS and plain JavaScript (no framework) for what's inside the window |
| Window | **pywebview**: a native Mac window that shows the interface using Apple's WebKit. The interface talks to a small server inside the app that only this Mac can reach (127.0.0.1, a random port, and a password made fresh at each launch) |
| ⌘⇧2 | Registered system-wide through Apple's Carbon hotkey API (the `quickmachotkey` library). The capture itself is macOS's own `screencapture -i`, the same crosshair as ⌘⇧4 |
| AI | Claude (`claude-sonnet-5`), called **directly from the app** using **each user's own Anthropic key** |
| AI key storage | A `.env` file in `~/Library/Application Support/Schedru/` that only your Mac account can read (the project folder when run from source), or the `ANTHROPIC_API_KEY` environment variable |
| Google sign-in | The standard desktop sign-in: your normal browser, PKCE (S256), and a redirect back to `http://localhost:<random port>/`. Each user supplies their **own** Google Cloud credentials file |
| Google scope | `https://www.googleapis.com/auth/calendar.events` only. No `openid` or `email`, so Google issues no ID token |
| Google tokens | `token.json`, next to the key, readable only by your Mac account. They are **not** in the Keychain |
| Calendar | Events go into your main calendar, tagged `extendedProperties.private.created_by = "syllabus_cal"`. Times are written in New York time, and Claude is told every user is at Rutgers–New Brunswick |
| Build | PyInstaller turns the app into `Schedru.app`, then a DMG. Both are built on GitHub's Apple Silicon and Intel Macs. The app is **ad-hoc signed**: it has no Developer ID signature and is not notarized |
| Tests | 428 unit tests, plus a walk-through on real Macs on every push. It presses ⌘⇧2, drags a region, uses the Capture button, Esc and ⌘V, plays the tutorial video, closes and reopens the window, and quits |

## 2. How it fits together after Phases 1–2

```
Schedru.app (your Mac)               Your server (Cloudflare Worker + D1)     Outside
──────────────────────               ────────────────────────────────────     ───────
⌘⇧2 → screenshot ── image + ID token ──▶ check the token against Google's keys
                                        find the user; check trial or plan
                                        check limits and the daily budget
                                        ask Claude ──────────────────────────▶ Anthropic
                                        log tokens + cost; drop the image ◀──
review ◀──────────── events (JSON) ─────
Add ─────────────── straight from your Mac, as today ────────────────────────▶ Google Calendar
Upgrade ──▶ server creates a checkout ──▶ opens in your browser ─────────────▶ Paddle
                                        ◀── signed Paddle webhooks update the plan
```

Google Calendar access stays entirely on the user's Mac. The server only learns **who** the user is,
from the ID token. It never sees their calendar or the tokens that unlock it.

## 3. Where the brief doesn't fit how Schedru is built

1. **No AI key ships in the app today.** Users paste their own. Phase 1 is still the right move, for
   a different reason: users will no longer need an Anthropic account, so that setup screen goes away,
   and **you** start paying for every screenshot. Until Phase 2's trial limits exist, the server will
   only serve accounts you have invited.
2. **Every user currently creates their own Google Cloud project** (SETUP.md). Paying customers can't
   be asked to do that. Phase 1 switches to a single sign-in client that you own, and the
   "credentials file" step goes away.

   Google treats desktop apps as public clients that "cannot keep secrets", so that client's "secret"
   isn't truly confidential. It still won't be committed: it is injected at build time from a GitHub
   secret. I'll also check whether sign-in works without it at all.
3. **The app has no Google ID token today.** It only asks for Calendar access. Phase 1 adds the
   `openid` and `email` scopes, which aren't sensitive, so that Google issues one. You'll sign in once
   more after that update.
4. **"Verify the token with Google"** is done by checking the token's signature against Google's
   published keys, which the server keeps cached. That is just as secure, and it saves a round trip to
   Google on every screenshot.
5. **Rutgers and New York time are hard-coded.** Claude is told every user is at Rutgers–New
   Brunswick, and every event is written in New York time. A 9 AM class in Chicago would land on the
   calendar at 8 AM Chicago time. Phase 1 makes the school a setting, and Phase 3 writes events in the
   time zone of the user's own Google Calendar.
6. **Sparkle is meant for native apps, but it still fits.** Schedru is Python inside an app bundle,
   and Sparkle replaces the whole bundle, so what's inside doesn't matter. Python can drive it through
   PyObjC, which the app already uses. If it fights the build, the fallback is a small "new version
   available" check against a file on your website.
7. **The Keychain and Screen Recording permission both follow the app's signature.** Until Phase 4's
   Developer ID signing, macOS treats every new build as a new app. Expect a Keychain prompt and a
   fresh Screen Recording request after each test build. Both stop once builds are properly signed.
8. **The current app has a license problem.** iPhone photo (HEIC) support comes from `pillow-heif`,
   whose prebuilt packages are licensed GPLv2 because they bundle the x265 video encoder. That
   shouldn't ship inside a paid, closed-source app. Its decode-only sibling, `pi-heif`, is discontinued
   and has a published security bug. The fix, in Phase 4: decode HEIC with the Mac's built-in image
   support (ImageIO, reachable through PyObjC) and drop pillow-heif. Phase 4 also adds a third-party
   license notice.
9. **The repository is public, so this plan is public too.** That's why the Mac builds cost nothing:
   GitHub doesn't charge for standard runners on public repositories. Nothing secret is in the repo or
   will be. Customers pay for the server, which holds your AI key and checks payments, so public code
   doesn't give the service away. But anyone can read or copy the code.

   Going private would mean the Mac builds count against GitHub's free allowance for private repos
   (2,000 minutes a month), and Mac minutes cost more than Linux ones. Your call. I'd stay public until
   launch and decide then.
10. **Some outside steps are slow.** Google's review of sensitive scopes can take weeks, and it needs a
    website and a privacy policy before it starts. See [Suggested order](#suggested-order).

## 4. Decisions I need from you

**Before Phase 1**

- **D1. School context.** Either (a) keep Rutgers–New Brunswick for everyone, or (b) let each user set
  their school, optionally, with Rutgers preselected. I suggest (b) if you'll sell beyond Rutgers.
- **D2. Repository visibility.** I suggest staying public for now (see item 9 above).
- **D3. AI model.** Stay on `claude-sonnet-5`, your earlier choice. Changing it later is a one-line
  server setting, and Phase 1 measures the real cost per screenshot before anything else is decided.

**Before Phase 2** (your brief left these as placeholders)

- **D4. Trial.** Your example, 7 days or 20 screenshots (whichever comes first), is a good default. At
  an estimated 1–3¢ per screenshot (to be measured in Phase 1), each trial costs you at most about
  20–60¢.
- **D5. Plan and price.** Paddle takes 5% + 50¢ of each sale, which eats a large share of small monthly
  charges:

  | Price | Paddle's cut | You keep (before tax and AI costs) |
  |---|---|---|
  | $2.99 / month | $0.65 (22%) | $2.34 |
  | $4.99 / month | $0.75 (15%) | $4.24 |
  | $9.99 / semester | $1.00 (10%) | $8.99 |
  | $14.99 / semester | $1.25 (8%) | $13.74 |
  | $24.99 / year | $1.75 (7%) | $23.24 |

  Also decide how the semester price is sold:
  - **A subscription that renews each term.** You get recurring revenue. It needs a manage/cancel page,
    which Paddle provides.
  - **A one-time semester pass.** No surprise renewals, so fewer refunds and chargebacks (Paddle charges
    $20 per chargeback), but you have to win the sale again every term.

  Both are equally easy to build. My lean is a semester price sold as a renewing subscription, with the
  renewal date shown clearly in the app.
- **D6. When the trial ends**, I suggest that screenshots already read can still be added to the
  calendar. Only new reads would be blocked.

**Later**

- **D7. Calendar scope.** The options are in Phase 3.
- **D8. Intel Macs.** Keep building for them (a second download and update feed), or support Apple
  Silicon only. Apple has announced macOS 26 is the last major version for Intel Macs. I suggest keeping
  them for now.

## 5. Start these now

Only you can do these, and they take days to weeks.

| What | Needed for | Notes |
|---|---|---|
| A domain and a simple website: home, pricing, privacy policy, terms, refund policy, support email | Google's review, Paddle's approval, update downloads | Phase 5 drafts the privacy policy. I can draft terms and a refund policy too |
| Apple Developer Program | Phase 4 signing | $99/year. You must be of legal age (18 in most US states). Approval can take a few days |
| Paddle **sandbox** account | Phase 2 | Free. Apply for the **live** account once your website is up, because Paddle reviews it |
| Cloudflare account | Phase 1 | Free to create; you'll need the Workers Paid plan (see costs) |
| A new Google Cloud project named "Schedru", for production | Phase 1 | Separate from your current personal test project |
| A separate Anthropic API key for the server, with a monthly spend limit set in the Anthropic Console | Phase 1 | The spend limit is your last line of defense against a surprise bill |

## 6. What it costs (approximate; prices change)

| Item | Cost | Needed from |
|---|---|---|
| Cloudflare Workers Paid | about $5/month | Phase 1. The free plan allows only 10 ms of CPU time per request, too little for multi-megabyte images |
| Anthropic API | per screenshot; estimated 1–3¢, measured in Phase 1 | Phase 1 |
| Paddle | 5% + 50¢ per sale, no monthly fee, $20 per chargeback | Phase 2 |
| Domain | about $10–20/year | before the Google submission |
| Apple Developer Program | $99/year | Phase 4 |
| Google verification | free for Calendar scopes. Paid security assessments only apply to restricted scopes such as Gmail and Drive | Phase 3 |
| GitHub | free while the repository is public | — |

---

## Phase 1 — Backend server

**Goal:** Claude runs on your server, with your key. The app only talks to your server. Every request
is tied to a Google account, held to limits, and logged.

### Server

The server goes in a new `backend/` folder, written in TypeScript (the language Workers are built for)
and running on Cloudflare Workers with D1. Cloudflare's Python Workers exist but are less mature, which
isn't worth the risk for code that handles money.

- **Endpoints**
  - `POST /v1/extract` takes an image (PNG, JPEG, WebP or GIF, up to 5 MB) **or** plain text, so the
    file-upload feature can reuse it later. It also takes the user's local date, time zone and
    optional school. It returns the same event data the app already checks.
  - `GET /v1/me` returns the account, its plan or trial status, and today's usage.
- **Sign-in check on every request.** The app sends `Authorization: Bearer <Google ID token>`. The
  server checks the signature against Google's published keys, which it caches, then checks the issuer,
  the audience (which must be Schedru's client ID), the expiry and that the email is verified. A new
  account is created on first use.
- **Database (D1)**
  - `users`: Google account ID (unique), email, created date, status (`invited`, `waitlisted` or
    `blocked`), trial start and usage, plan and plan end date, Paddle IDs (Phase 2), school.
  - `ai_calls`: one row per Claude call, with the user, time, model, input/output/cached tokens, cost
    in micro-dollars, outcome and duration. **No image or text content, ever.**
  - `daily_usage`: per-user, per-day counters, so limit checks are fast.
- **Usage is counted in credits.** One screenshot is one credit, so a 12-page PDF can count for more
  later.
- **Limits.** These are defaults that can be changed without a code change:
  - 6 requests a minute per user, to stop bursts;
  - 40 credits a day per user;
  - a daily budget across everyone (for example $5/day). Once it's reached, the server answers "busy,
    try later" instead of spending more;
  - in Phase 1 only, just the accounts you invite can read screenshots.

  Credits are reserved **before** Claude is called, so 20 requests sent at once can't sneak past a
  limit. They're given back if the call fails on the server's side.
- **The Claude call** uses Anthropic's official TypeScript library, with the same model and
  instructions as today.
  - The instructions move into one shared prompt file that both the server and your local developer
    tool read, so the two can't drift apart.
  - The instructions go in a system prompt marked for caching. The same ~2,000 tokens accompany every
    screenshot, and if that's long enough to qualify, repeats are billed at a tenth of the price.
  - A reply that isn't valid JSON gets one retry, as today. Refusals and over-long replies are turned
    into clear error messages.
- **Seeing your costs.** `npm run usage-report` prints each user's requests, tokens and cost for the
  last 30 days.
- **Nothing is kept.** The image exists only in memory for the length of one request. It is never
  written to the database, logs or storage, and request bodies are never logged.
- **Secrets.** `ANTHROPIC_API_KEY` is stored with `wrangler secret put`, which Cloudflare keeps
  encrypted. For local development it goes in `backend/.dev.vars`, which git ignores. The Google client
  ID is not a secret, so it lives in `wrangler.toml`.
- **Tests** run inside Cloudflare's local runtime, with a real local D1 database and a stand-in for
  Claude. They cover:
  - token checks: wrong audience, expired, forged;
  - every limit, and many requests arriving at once;
  - cost logging;
  - a check that no content ever reaches the database.

### App

- **Sign-in** uses Schedru's own Google client. Its ID and secret are injected at build time from
  environment variables and GitHub secrets, never committed. It asks for `openid email calendar.events`,
  and the credentials-file screen goes away. A saved sign-in without an ID token asks you to sign in
  once more.
- **No more Claude key in the app.** The "Add your Claude key" screen and the app's direct calls to
  Claude go away. Your terminal developer tool keeps working with a key in your local, git-ignored
  `.env`.
- **A new engine** sends the prepared image and a fresh ID token to the server. It renews the token when
  it is about to expire, and it turns the server's answers into plain messages:
  - signed out;
  - not invited, or trial over;
  - slow down;
  - busy;
  - offline.
- **Image preparation stays in the app** (HEIC, rotation, the 5 MB limit), so uploads stay small.
- **The server address** is set at build time (`SCHEDRU_API_URL`) and can be changed for development.
- **Unchanged:** ⌘⇧2, capture, review, adding to Calendar and the tutorial video. The demo engine and the
  Mac walk-through keep running on every push, now with a stand-in server as well.

### What you'll do

I'll give exact, click-by-click steps for each.

1. Cloudflare: sign up and choose Workers Paid, then run three commands (create the database, store the
   key, deploy).
2. Google Cloud: create the "Schedru" project, turn on the Calendar API, set up the consent screen and
   create a "Desktop app" client. Add yourself as a test user.
3. GitHub: add the client ID and secret as repository secrets.
4. Invite yourself, with one command.

### How you'll test it

- Sign in, capture a flyer and check the events.
- Run the usage report: your request should appear with its tokens and cost, and no image.
- Capture 7 times within a minute: you should see "slow down".
- Un-invite yourself: you should see the "private testing" message.
- The usage report also gives the first **real** cost per screenshot, which has never been measured.

---

## Phase 2 — Free trial and payments

### Server

- **Trial:** tracked per Google account and stored on the server, so reinstalling doesn't reset it. It
  follows your answer to D4.
- **One rule decides access:** `can_extract(user, now)`. It returns allowed, trial ended, plan lapsed or
  blocked, each with a reason the app can show. It is small and self-contained, so it can be tested
  exhaustively.
- **Paddle Billing, sandbox first**
  - `POST /v1/billing/checkout` creates a Paddle checkout for your price. It attaches the user's
    Schedru ID (`custom_data`) and fills in their email, and the app opens it in the browser. Paddle
    needs a small `/pay` page on your website for this, which I'll write.
  - `POST /v1/billing/manage` returns a link to Paddle's customer portal, where users cancel, change
    card and get receipts.
  - `POST /paddle/webhook` receives Paddle's notifications:
    - It checks the `Paddle-Signature` header: an HMAC over the timestamp and the raw body, using your
      webhook secret. Old timestamps are rejected.
    - It records each event once, because Paddle can resend, and copes with events arriving out of order.
    - It updates the plan for each kind of event:
      - a purchase or renewal turns access on;
      - a cancellation keeps access until the paid period ends;
      - a failed payment gets a short grace period while Paddle retries;
      - a refund ends access.
  - Payments are matched by Schedru user ID, not email, so paying with a different email still works.
- **Tests** cover:
  - trial math: days vs. credits, whichever comes first, across time zones and midnight;
  - every webhook type;
  - signatures: valid, wrong secret, altered body, old timestamp, replayed;
  - events arriving out of order;
  - a refund removing access.

### App

- A trial line, for example "12 screenshots · 5 days left", from `/v1/me`.
- A clear trial-ended screen with **Upgrade**. Upgrade is also in the ⋯ menu, and paying users get
  **Manage subscription** there instead.
- After paying, the app notices by itself: it re-checks whenever its window comes back to the front.

### What you'll do

In the Paddle sandbox: create the product and price, add the webhook address, and set the default
payment link. Then copy two keys into `wrangler secret put`.

### How you'll test it

1. With a fresh test account, watch the trial count down, then let it run out.
2. Press Upgrade and pay with Paddle's test card. The app should unlock within seconds.
3. Cancel in the portal. Access should stay until the paid period ends.
4. Refund the payment in Paddle's dashboard. Access should end.

---

## Phase 3 — Google sign-in readiness

- **D7. Calendar scope.** These are the narrowest options, using the descriptions from Google's Calendar
  scope list:

  | Scope | What Schedru can do | Trade-off |
  |---|---|---|
  | `calendar.events` (today) | See and edit events on every calendar you can edit, including shared ones | Broader than Schedru needs |
  | `calendar.events.owned` | See, create, change and delete events on calendars you own, including your main one | **Nothing lost.** Recommended |
  | `calendar.app.created` | Only calendars Schedru creates itself, so events go into a separate "Schedru" calendar | The narrowest. Events don't land in your main calendar (they show in a separate color, but are easy to hide or delete all at once), and Schedru can't see your main calendar at all, so it can't read its time zone either |

  Google marks each scope as sensitive or not in the Cloud console, and I'll confirm the
  classifications there before you choose. If `calendar.app.created` turns out not to be sensitive,
  Google's review could be lighter, which is the main reason to consider it.
- **Desktop best practice.** The system browser and PKCE are already in place and tested. For the
  redirect back to the app, switch from `localhost` to `127.0.0.1`: Google recommends this because
  `localhost` can trip some firewalls.
- **Tokens move to the macOS Keychain**, through PyObjC's Security framework (already in the app). It
  is a one-time move out of `token.json`, which is then deleted.
- **Expired or revoked sign-ins** are already handled when writing to Calendar. Phase 3 extends that to
  the server: the app renews the token once, and if that fails it asks you to sign in again, keeping
  your current review. Tests cover revoked access, expired tokens, Google's `invalid_grant` error and a
  Mac clock that's wrong.
- **Your calendar's own time zone** is used for every event, instead of New York.
- **`docs/GOOGLE_VERIFICATION.md`** will hold everything needed for Google's review:
  - a checklist: domain verified in Search Console, homepage, privacy policy including Google's
    "Limited Use" statement, an app name and logo that match the consent screen, a reason for each
    scope, a support email;
  - a scene-by-scene script for the demo video.

**How you'll test it:**

- Sign in fresh: the browser's address bar should show `127.0.0.1`.
- In Keychain Access you should see a Schedru item, and `token.json` should be gone.
- Revoke Schedru at myaccount.google.com/permissions: the app should ask you to sign in again, not fail
  silently.

---

## Phase 4 — Packaging and distribution

- **One script, `packaging/build_release.sh`, run by CI**, does the whole release:
  1. Build the app.
  2. Sign every binary inside it, innermost first, with Developer ID, hardened runtime and a secure
     timestamp.
  3. Notarize the app with `notarytool`, then staple the result to it.
  4. Build the DMG, then sign, notarize and staple the DMG.
  5. Confirm Gatekeeper accepts it (`spctl`).

  The certificate and Apple's API key live only in GitHub secrets. During the build they are loaded
  into a throwaway keychain.
- **Hardened runtime entitlements** start at none. I'll add only what the Mac walk-through shows is
  needed. Python's compiled extensions sometimes need `allow-unsigned-executable-memory`.
- **Screen Recording**
  - A Schedru screen explains *why* it needs the permission before macOS asks.
  - After you allow it, the app explains that macOS needs it reopened.
  - The walk-through checks that the permission survives an update.
- **Sparkle 2 auto-updates**
  - The framework is bundled into the app.
  - "Check for Updates…" goes in the app menu, plus a daily automatic check.
  - Updates are signed with an EdDSA key whose private half lives only in GitHub secrets.
  - The update feed and DMGs are hosted on Cloudflare R2 (no download fees) or your website, with one
    feed per architecture (see D8).
- **License cleanup:** HEIC is decoded with macOS ImageIO instead of pillow-heif, and a third-party
  notices file covers everything bundled, including the Fraunces font.
- **`docs/APPLE_SETUP.md`**, step by step: enroll, create the Developer ID Application certificate,
  create the notarization key, find your Team ID, and add the GitHub secrets.

**How you'll test it:**

- Download the DMG from CI. It should open with no "Open Anyway" step.
- Press ⌘⇧2: Schedru's explanation should appear first, then macOS's own request.
- Install an older build and accept the update. The permission should still be granted afterwards.

---

## Phase 5 — Privacy policy draft

`docs/PRIVACY_POLICY.md` is written from what the code actually does.

- **What Schedru collects:** the Google account ID and email, trial and plan status, usage counts, the
  AI token counts and cost of each request, and Paddle customer and subscription IDs.
- **What it doesn't keep:** screenshots and file contents go to the server and on to the AI provider,
  are processed, and are discarded. Schedru never stores them.
- **Google Calendar:** only events you approve are written. Calendar data never goes to the server or
  the AI. The policy includes Google's "Limited Use" statement.
- **No training:** Schedru doesn't use your data to train AI models. The AI provider's own commitment
  is flagged for you to confirm.
- **Other companies involved:** Anthropic (AI), Paddle (merchant of record: card details never reach
  Schedru), Cloudflare (hosting) and Google (sign-in and Calendar).
- **On the user's Mac:** the sign-in is in the Keychain, and settings are in Application Support.
- The policy also covers how long data is kept, deletion requests, children, changes to the policy, and
  how to contact you.

**Flagged for you to confirm:**

- how long Anthropic currently keeps API inputs, and its commitment not to train on them;
- whether you need EU/UK wording, since Paddle sells worldwide;
- your contact address;
- the governing law.

This is a draft, not legal advice.

**Optional, with your OK (because it's code):** a "Delete my account" button that removes your record
from the server.

---

## Suggested order

```
Phase 1 → Phase 2 → Phase 3 → Phase 5 → submit to Google ──(Google reviews: often weeks)──▶ launch
                                                  └──── Phase 4 while Google reviews ────┘
```

Phase 5 is writing only, and Google won't start its review without a privacy policy. Doing Phase 5
right after Phase 3 starts the slowest clock sooner. If you prefer your original order, that only
delays the Google submission.

## Later: file upload (your second request)

It is on hold, as you asked. It depends on Phase 1's server. It should also come after Phase 2, because
uploads count against trials and limits, and after Phase 3, because of the time zone and because the
duplicate check depends on the Calendar scope.

Phase 1's server already accepts text and counts usage in credits, so uploads will fit without a
redesign. A few parts of that request don't match the app yet (for example, Schedru has no menu-bar
icon), and I'll raise them in that plan.

## How each phase is delivered

- **One phase at a time.** Each phase ends with what changed and why, in plain English, how to test it
  yourself, and a git commit. Then work stops until you approve the next phase.
- **No secrets in the code or the repository.** They go in Cloudflare secrets (`wrangler secret put`),
  GitHub Actions secrets, or local files that git ignores. `.gitignore` gains `.dev.vars`, `.wrangler/`,
  `*.p12`, `*.p8`, `*.pem` and `secrets/`.
- **What works keeps working.** The Mac walk-through runs on every push and must stay green. It covers
  ⌘⇧2, capture, sign-in with stand-ins, the tutorial video, review and adding.
- **Anything that doesn't fit gets raised, not forced.**

## Sources for outside facts

These were checked on 13 November 2026. Prices and policies change, so confirm them before relying on
them. Items not confirmed are marked "to confirm" above.

- Google: [Choose Google Calendar API scopes](https://developers.google.com/workspace/calendar/api/auth),
  [Sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification),
  [OAuth 2.0 for Desktop Apps](https://developers.google.com/identity/protocols/oauth2/native-app)
- Cloudflare: [Workers limits](https://developers.cloudflare.com/workers/platform/limits/),
  [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- Paddle: [Paddle vs Stripe (fees)](https://www.paddle.com/compare/stripe),
  [Chargebacks](https://www.paddle.com/help/manage/risk-prevention/understanding-chargebacks-with-paddle)
- GitHub: [Actions billing](https://docs.github.com/en/actions/concepts/billing-and-usage),
  [2026 Actions pricing changes](https://github.com/resources/insights/2026-pricing-changes-for-github-actions)
- HEIC libraries: [pillow-heif bundled licenses](https://github.com/bigcat88/pillow_heif/blob/master/LICENSES_bundled.txt),
  [pi-heif](https://pypi.org/project/pi-heif/)
