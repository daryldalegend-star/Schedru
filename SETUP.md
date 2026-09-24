# Google Calendar setup

This walks you through everything in Google Cloud Console needed for
`python -m syllabus_cal auth` to work. You only do this once. It's free —
you're just registering an app with your own Google account so it's allowed
to talk to your Calendar.

## 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and
   sign in with the Google account whose Calendar you want to write to.
2. In the top blue bar, click the project dropdown just right of the "Google
   Cloud" logo (it may say "Select a project").
3. Click **New Project** (top right of the dialog that opens).
4. **Project name**: anything, e.g. `syllabus-cal`. Leave "Location" as is.
5. Click **Create**. Wait a few seconds — a bell icon in the top bar will
   notify you when it's done.
6. Open the project dropdown again and select the project you just created
   (it won't switch to it automatically).

## 2. Enable the Google Calendar API

1. In the left sidebar (click the ☰ menu if you don't see it), go to
   **APIs & Services → Library**.
2. In the search bar, type `Google Calendar API` and click the result.
3. Click the blue **Enable** button.

## 3. Configure the consent screen ("Google Auth Platform")

This is the screen your own browser will show you when you sign in — Google
requires every app to declare one, even one only you will ever use.

All you're doing here is four things: name the app, set it to **External**,
add your own Gmail as a **test user**, and leave it in **Testing**. The
steps below are just those four, in order.

1. Left sidebar: **APIs & Services → OAuth consent screen**. This will land
   you on **Google Auth Platform**.
2. Click **Get started** (first time only).
3. **App Information** — App name: anything, e.g. `syllabus_cal`. User
   support email: pick your own address from the dropdown. **Next**.
4. **Audience** — choose **External**. (Internal only exists if you have a
   Google Workspace organization; a personal Gmail account won't see it.)
   **Next**.
5. **Contact Information** — your email again. **Next**.
6. Agree to the user data policy checkbox, then **Create**.
7. Now add yourself as a test user: go to the **Audience** page in the left
   sidebar, find the **Test users** section, click **+ Add users**, enter
   your own Gmail address, and **Save**.
   *(Old UI: this was a "Test users" step inside the setup wizard.)*
8. Important: leave **Publishing status** as **Testing**. Do **not** click
   "Publish app" — that triggers a Google verification review meant for
   public apps, which you don't need and don't want for a personal tool.
   Testing mode works indefinitely for accounts on the test users list.

You do **not** need to touch the **Data Access** page (old UI: "Scopes").
The app requests the Calendar scope directly from the code.

*If your screen doesn't match the wording above:* Google renamed this area
from "OAuth consent screen" to **Google Auth Platform** and replaced the old
multi-page "Save and Continue" wizard with a **Get started** flow plus
permanent sub-pages (Overview, Branding, Audience, Clients, Data Access).
The rollout has been uneven, so you may see either. The old equivalents are
noted in italics where they differ — the four things you're doing are the
same either way. And if you can't find this section at all, it's because
it only appears once an API is enabled: go back and do step 2 first.

## 4. Create OAuth Desktop App credentials

1. Left sidebar: **Google Auth Platform → Clients**.
   *(Old UI: **APIs & Services → Credentials**.)*
2. Click **+ Create client**.
   *(Old UI: **+ Create Credentials → OAuth client ID**.)*
3. **Application type**: select **Desktop app**. This matters — picking
   "Web application" gives you a client that expects a hosted redirect URL
   and the local sign-in flow will fail.
4. **Name**: anything, e.g. `syllabus_cal CLI`.
5. Click **Create**. A dialog pops up showing your client ID/secret.
6. Click **Download JSON** (also available later via the ⬇ download icon
   next to the client in the list).
7. Rename the downloaded file to exactly `credentials.json` and move it into
   the project root — the same folder as `requirements.txt`. It's already
   listed in `.gitignore`, so it won't get committed.

## 5. Run the sign-in flow

```
python -m syllabus_cal auth
```

This opens your default browser. You'll likely see a warning screen that
says **"Google hasn't verified this app"** — that's expected for an app in
Testing mode, not a problem. Click **Advanced**, then **Go to syllabus_cal
(unsafe)**, then **Continue**/**Allow** to grant Calendar access. The CLI
will print `Signed in. token.json saved to the project root.` — that file
(also gitignored) is what lets future runs skip sign-in.

To also confirm your Calendar can actually be written to, add
`--test-event`:

```
python -m syllabus_cal auth --test-event
```

This writes one event titled "syllabus_cal test event" tomorrow at
9:00–9:30 AM and prints a link to it. It's tagged so it's easy to find and
delete — safe to remove once you've confirmed it showed up.

## Troubleshooting

**"Access blocked: syllabus_cal has not completed the Google verification
process"** — Your Google account isn't in the test users list. Go back to
step 3.7 and add your email under **Google Auth Platform → Audience → Test
users**.

**"Error 400: invalid_request" or "invalid_client"** — Usually means
`credentials.json` is missing/wrong, or wasn't created as a **Desktop app**
client. Re-check step 4.

**Browser doesn't open / you're on a remote machine with no browser** — The
desktop OAuth flow needs a real browser on the same machine running the
command. Run `auth` on your laptop, not over SSH into a headless server.

**Token expired / `auth` says to re-run `auth`** — Just run
`python -m syllabus_cal auth` again; it overwrites `token.json`.
