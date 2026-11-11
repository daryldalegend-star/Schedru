# Google Calendar setup

This walks you through making the **Google credentials file** that Schedru
asks for the first time you press **Continue with Google**. You only do this
once. It's free — you're registering your own copy of the app with your own
Google account, so that it's allowed to talk to your Calendar.

If you set up an earlier version of this project, you've already done all of
this: the file is the `credentials.json` in that folder. Drop it onto
Schedru when it asks and skip the rest of this page.

## 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and
   sign in with the Google account whose Calendar you want to write to.
2. In the top blue bar, click the project dropdown just right of the "Google
   Cloud" logo (it may say "Select a project").
3. Click **New Project** (top right of the dialog that opens).
4. **Project name**: anything, e.g. `schedru`. Leave "Location" as is.
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
3. **App Information** — App name: `Schedru` (this is the name Google will
   show you when you sign in). User support email: pick your own address
   from the dropdown. **Next**.
4. **Audience** — choose **External**. (Internal only exists if you have a
   Google Workspace organization; a personal Gmail account won't see it.)
   **Next**.
5. **Contact Information** — your email again. **Next**.
6. Agree to the user data policy checkbox, then **Create**.
7. Now add yourself as a test user: go to the **Audience** page in the left
   sidebar, find the **Test users** section, click **+ Add users**, enter
   the Gmail address **whose calendar you want events on**, and **Save**.
   If you have more than one Google account, add each one you might sign in
   with — only listed accounts are allowed in.
   *(Old UI: this was a "Test users" step inside the setup wizard.)*
8. Leave **Publishing status** as **Testing**. Do **not** click "Publish
   app" — for an app using your calendar that can start a Google
   verification review meant for public apps.

   The one cost of Testing mode: Google ends sign-ins after **7 days**. The
   app handles that for you — when it happens, it says your sign-in expired
   and shows the **Continue with Google** button again; press it, pick your
   account in the browser, and you're back in. Expect that roughly once a
   week.

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
4. **Name**: anything, e.g. `Schedru on my Mac`.
5. Click **Create**. A dialog pops up showing your client ID/secret.
6. Click **Download JSON** (also available later via the ⬇ download icon
   next to the client in the list).
7. The file lands in your Downloads folder with a long name starting
   `client_secret_`. That's the credentials file. You don't need to rename
   or move it for the app: Schedru will ask for it in the next step.

   (Only if you run the terminal version: rename it to exactly
   `credentials.json` and put it in the project folder, next to
   `requirements.txt`. It's listed in `.gitignore`, so it won't be
   committed.)

## 5. Sign in

Open Schedru and press **Continue with Google**. The first time, it asks for
the credentials file: drag the file from step 4 onto the window, or press
**Choose the file…** and pick it. Schedru checks it's the right kind of file
and keeps its own copy, so you can delete the download afterwards.

Your browser then opens to Google's account picker: choose the account whose
calendar you want events on. You'll then see **"Google hasn't verified this
app"** — that's expected for your own private copy in Testing mode, not a
problem. Press **Continue**. (If you see a small **Advanced** link instead,
click it, then **Go to Schedru (unsafe)**.) If Google lists permissions with
checkboxes, tick the one for **Google Calendar**, then **Continue** again.

The browser says **Schedru is connected**; go back to the app, which has
already moved on. The account you're connected as is shown in the top-right
corner of the window. To use a different one, open the **⋯** menu and
choose **Use a different Google account…**.

Schedru never adds anything by itself, so the simplest way to confirm your
calendar can be written to is to capture something and press Add. If you'd
rather test without a screenshot, the terminal version can write one
throwaway event (tomorrow, 9:00–9:30 AM, safe to delete):

```
python -m syllabus_cal auth --test-event
```

## Troubleshooting

**"Access blocked: Schedru has not completed the Google verification
process"** — The Google account you picked isn't in the test users list.
Either pick the account that is, or add this one in step 3.7 under
**Google Auth Platform → Audience → Test users**.

**"Google Calendar refused the connection ... has not been used in project
... or it is disabled"** — The Calendar API isn't switched on for your Cloud
project. Redo step 2.

**"Error 400: invalid_request" or "invalid_client"** — Usually means the
credentials file wasn't created as a **Desktop app** client. Re-check step 4
and give Schedru the new file (sign out from the **⋯** menu first).

**Schedru says the file "is for a Web application client"** — Same cause:
in step 4.3, the application type must be **Desktop app**. Create another
client with that type and use its file instead.

**Schedru says Google signed you in "without permission to add events"** —
On Google's permissions screen the Calendar checkbox was left unticked.
Press **Continue with Google** again and tick it.

**The browser doesn't open** — Press **Open Google's page again** in
Schedru. Sign-in needs a browser on the same computer as the app.

**"Your Google sign-in has expired"** — Normal, about once a week (see step
3.8). Press **Continue with Google** and pick your account again.
