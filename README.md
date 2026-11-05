# work-pixelclock – Pixel Panel for Windows

A Windows app (`PixelPanel.exe`) that controls an **iPixel Color LED panel over Bluetooth** and
replaces the "iPixels" phone app entirely. Besides text, emojis, images and clocks it shows your
**Microsoft Teams status as a status light**, your **Claude usage** (session and week) and your
**next meeting** right on the panel.

![Panel pages](docs/images/pages.png)

**Download:** [latest release](https://github.com/Nipo-98/work-pixelclock/releases/latest) –
a single `PixelPanel.exe`, no installation needed.

---

## Contents

- [Features](#features)
- [The hardware](#the-hardware)
- [Quick start](#quick-start)
- [Using the app](#using-the-app)
- [Teams status](#teams-status)
- [Claude usage](#claude-usage)
- [Work features](#work-features)
- [Claude session alerts](#claude-session-alerts)
- [Calendar (next meeting)](#calendar-next-meeting)
- [Weather](#weather)
- [Updates](#updates)
- [Settings and files](#settings-and-files)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [How it works](#how-it-works)
- [Credits](#credits)

---

## Features

| Area | What it does |
|---|---|
| **Show now** | Text with emoji or icon, your own colour, background effect, for X seconds or until you go back; an image or animated GIF; the panel's 9 built-in clock designs |
| **Page rotation** | Any number of pages shown one after another: clock, date, text/emoji, image/GIF, weather, Teams status, Claude usage, next meeting – each with its own duration, tick to enable, sortable |
| **Transitions** | Page changes jump, push, shoot in, fade, wipe, dissolve – or arrive with **Nyan Cat** |
| **Teams status** | A stripe in the Teams colour on both sides of every page; for "Busy", "In a call", "Don't disturb" … the whole panel lights up in the status colour – automatically, no sign-in |
| **Claude usage** | Percent of the 5-hour session and of the weekly limit of your Claude plan, coloured green/yellow/red, with the Claude logo or **Clawd** |
| **Work** | On air light, focus timer, stopwatch, countdowns, door sign, break and limit reminders, unread mail and Teams, day overview |
| **Connections** | GitHub builds, issues and reviews, service monitor, team message, local interface for your own scripts |
| **Organise** | Profiles (also by time of day), a schedule per page, global keyboard shortcuts |
| **Claude session alerts** | Optional: Clawd pulls an alert onto the panel when a Claude Code session needs you |
| **Calendar** | Next meeting from Outlook (no sign-in), a calendar link or Microsoft 365, with a countdown reminder shortly before it starts |
| **Emojis** | Real colour emojis (Windows emoji font) at 16 × 16 pixels, ~50 built-in pixel icons, thousands of LaMetric icons (`lm:<id>`) or your own image files |
| **Effects** | Rainbow text, matrix, twinkle, fire, plasma, snow, rain, pulse |
| **Panel** | On/off, brightness, night mode (dimmer at set times), orientation incl. portrait |
| **App** | Runs in the background (tray icon), starts with Windows, reconnects on its own |

<p>
  <img src="docs/images/effect-matrix.gif" alt="Matrix effect" width="32%">
  <img src="docs/images/effect-fire.gif" alt="Fire effect" width="32%">
  <img src="docs/images/effect-snow.gif" alt="Snow effect" width="32%">
</p>

---

## The hardware

Built and tested with the **"LED flexible fun screen"** (64×16 RGB LEDs, USB powered) from
Action: [Briloner LED-Zeichentablet, article 3226274](https://www.action.com/de-de/p/3226274/briloner-led-zeichentablet/).
It is an iPixel Color panel: Bluetooth name `LED_BLE_…`, vendor app *iPixels*. Other iPixel Color
panels (32×16 … 448×32, e.g. BGLight) speak the same protocol; the size is read from the panel.

> **USB is power only.** The panel does not show up as a USB device on the PC – it is controlled
> exclusively over **Bluetooth Low Energy**. The PC needs Bluetooth (built in or a dongle).

---

## Quick start

1. **Plug in the panel** (USB power) and switch it on.
2. **Close the phone app** or turn off Bluetooth on the phone – the panel accepts only *one*
   connection.
3. Start `PixelPanel.exe` (from the [releases](https://github.com/Nipo-98/work-pixelclock/releases)
   or [build it](#build-the-exe)).
4. On first start the app searches for panels. If it finds exactly one, it connects right away;
   otherwise pick the panel under *Settings › Panel* and click **Connect**.
5. Done – the rotation starts with clock and Claude usage, and the Teams status light is on.

The panel does **not** need to be paired in the Windows Bluetooth settings.

> Windows SmartScreen may warn on first start because the exe is not signed:
> *More info › Run anyway*.

---

## Using the app

On the left is the navigation (**Show now**, **Pages**, **Work**, **Connections**, **Settings**) with the
connection status below it. At the top there is always the **live preview** – it looks like the
panel itself (round LEDs, animations included) – with **Next page**, **brightness** and **On/Off**.

### Show now

![Show now](docs/images/app-show-now.png)

- Type a **text**, pick an **icon / emoji** (open the list or paste an emoji), choose colour and
  effect, then **Show** (or press Enter).
- An emoji *in the text* automatically becomes the icon on the left, e.g. `Back in 5 min ☕`.
- Long text scrolls.
- **Duration** `0` = stays until you click **Back to rotation**; otherwise e.g. `60 s`.
- **Add to rotation** keeps the text as a page.
- **Show image / GIF…** – scaled down to the panel size, GIFs play animated.
- **Show built-in clock** – one of the 9 clock designs the panel has built in.

### Pages (rotation)

![Pages](docs/images/app-pages.png)

On the left the list of pages: **ticked** pages are in the rotation, order with **▲ / ▼**, new
pages with **+ Page**. On the right the settings of the selected page:

| Page | Settings |
|---|---|
| **Clock** | Colour, effect, seconds, weekday bar |
| **Clock (built into the panel)** | Design 0–8, date on/off – runs on the panel itself |
| **Date** | Colour, icon, effect |
| **Text / Emoji** | Text, icon/emoji, colour, effect |
| **Image / GIF** | File |
| **Weather** | Colour, effect (location under *Settings*) |
| **Teams status** | Coloured dot + status |
| **Claude usage** | Session + week, session only or week only (with bar); icon: Claude logo or **Clawd** |
| **Next meeting** | Time and title of your next meeting (source under *Connections*) |

Scrolling text is always shown at least once completely, even if the duration is shorter.

**Transitions:** At the top of the page list you choose how the next page comes onto the panel –
*Instant (jump)*, *Push left / up*, *Shoot in*, *Fade*, *Wipe*, *Dissolve*, **Nyan Cat** or
*Random*. With Nyan Cat the cat flies across the panel, pulls its rainbow over the old page, the
rainbow fades out and the new page is underneath. Transitions apply to every page change (also
status light and meeting reminder); a page's duration only starts once it is fully on the panel.

<p>
  <img src="docs/images/transition-nyan.gif" alt="Nyan Cat transition" width="32%">
  <img src="docs/images/transition-shoot.gif" alt="Shoot in" width="32%">
  <img src="docs/images/transition-push.gif" alt="Push" width="32%">
</p>

**Icons:** the *Icon / Emoji* field accepts

- an emoji, e.g. `🚀`
- a built-in name, e.g. `heart`, `house`, `coffee`, `bell`, `car`, `music`, `star` …
- a LaMetric icon: `lm:2867` (number from [developer.lametric.com/icons](https://developer.lametric.com/icons))
- `claude` (Claude logo) or `clawd` (Clawd, the Claude Code mascot)
- an image file (button **…**)

### Connections

![Connections](docs/images/app-connections.png)

Everything that fetches data from outside: **Microsoft Teams**, **Claude usage** and **Calendar** –
more services will be added here.

### Settings

![Settings](docs/images/app-settings-1.3.png)

Panel (search/connect device, orientation), night mode (time range + brightness), weather location,
**Start with Windows**, **Start minimised**, **Install updates automatically** and **Check for New
Version** (see [Updates](#updates)).

### Tray icon

The window's **X** only closes the window – the panel keeps being controlled. From the tray icon
(right-click): Open, Next page, Back to rotation, Panel on/off, Teams status light on/off, **Quit**.

---

## Teams status

![Teams states](docs/images/teams-states.png)

*Left: Teams page in the rotation. Right: status light (the whole panel lights up).*

- The status is read from the **log file of the new Teams client**
  (`%LOCALAPPDATA%\Packages\MSTeams_8wekyb3d8bbwe\LocalCache\Microsoft\MSTeams\Logs\MSTeams_*.log`,
  lines `UserPresenceAction … availability: Busy`). No sign-in, no Azure app, no admin rights;
  checked every 2 seconds.
- **Status bar:** while the rotation runs, every page has a stripe in the Teams colour on the left
  and right – the status is always visible without stopping the rotation. Width **1, 2 or 3
  pixels** (default 2), can be switched off under *Connections*. The page is drawn narrower with a
  1-pixel gap, so the stripes never cover anything and icons don't touch them.

  ![Status bar](docs/images/status-bar.png)

- **Status light:** under *Connections* you choose **for which states** the whole panel lights up
  and the rotation pauses (default: Busy, In a call, In a meeting, On the phone, Presenting, Don't
  disturb). **Only tick states that should really stop the rotation** – ticking "Available" means a
  permanent status light and no rotation.
- Priority: **Show now** > **meeting reminder** > **status light** > **rotation**. If no page is
  ticked, the panel shows the clock.
- Limits: works only with the new Teams ("Teams 2.0") on the same PC. During a call Teams usually
  just writes "Busy" to the log. As this is not an official interface, a Teams update may change the
  format.

---

## Claude usage

- Shows how much of your **Claude plan** you have used: **session** (5-hour limit) and **week** –
  the same numbers as claude.ai › Settings › Usage.
- Requirement: **Claude Code is signed in on this PC** (`claude` → `/login`). The app reads the
  token from `%USERPROFILE%\.claude\.credentials.json` and queries
  `api.anthropic.com/api/oauth/usage` (every 120 s by default). The token is only read – never
  changed or passed on.
- The plan (Pro, Max (5x), Max (20x), Team …) is read live from your Claude profile once an hour – so an upgrade shows up without signing in to Claude Code again.
- If the sign-in has expired, the panel says so – opening Claude Code once is enough.
- Colours: green < 50 %, yellow < 80 %, red from 80 %.
- Icon: Claude logo or **Clawd**, the mascot from Claude Code. Clawd is animated: he blinks, looks around, waves, hops and sits down (an ~8 s loop that runs on the panel itself; switch it off with *Animate Clawd*).

  ![Clawd waving, hopping and sitting down](docs/images/clawd.gif)

---

## Work features

![Work pages](docs/images/work-pages.png)

All of these are off until you switch them on or tick their page, and only what is on gets fetched.

**Who gets the panel**, from highest to lowest:

1. Short notes from Clawd (Claude session alerts, build failed, break …)
2. **On air**
3. The stopwatch for 5 seconds when you start or stop it
4. Your **door sign** or message from *Show now* or the local interface
5. Meeting reminder
6. **Focus timer**
7. Teams status light
8. Page rotation (a running timer is always part of it, even without a ticked page)

The last thing you do wins: starting the focus timer or pressing **Next page** takes a door sign down. The line
under the preview says what is showing, and **Back to rotation** appears while a sign or message holds the panel.

**Under *Work***

| Feature | What it does |
|---|---|
| **On air** | Red **ON AIR** while any app uses the microphone or camera (Teams, Zoom, browser …), read from Windows' privacy data; the app shows which one. Windows only says "in use", not whether you are muted. |
| **Focus timer** | Countdown with a bar, then a break, with a note from Clawd at the end of each. Can take over the panel while it runs. Does not change your Teams status. |
| **Stopwatch** | Time on a task with your own label. Shows up when started or stopped and stays in the rotation while it runs; resets when the app restarts. |
| **Door sign** | One click: *Do not disturb*, *Back soon*, *Come in* (also in the tray menu). Stays until you take it down. |
| **Break reminder** | After 90 minutes at the PC (adjustable); 5 minutes away counts as a break. |
| **Claude limit warning** | Note at 80 % and 90 % of the session or week (adjustable), with the time it resets. |

**New pages** (tick them under *Pages*): countdown or end of work (`Home in 23 min`, `Vacation 45 days`), unread mail (classic Outlook), unread in Teams (from its log file, no sign-in), day overview (`3 left`, `60% busy`), GitHub, service monitor, PC values (CPU, memory, battery), open Claude sessions, team message.

**Under *Connections***

| Feature | What it does |
|---|---|
| **GitHub** | Build status, pull requests waiting for your review, open issues; notes when a build fails or a new issue arrives. Public repositories need no token; a token for private ones is stored encrypted for your Windows user. |
| **Service monitor** | Checks a list of addresses (`API = https://…`) and alerts when one goes down. |
| **Team message** | Shows the first line of a shared text file or web address. Whoever can change that file writes on everyone's panel; nothing is sent back. |
| **Local interface** | Lets scripts on this PC write to the panel, see below. |

**Under *Settings***: **profiles** remember ticked pages, brightness and transition, can switch by time of day and are in the tray menu. **Shortcuts** (`Ctrl+Alt+N` next page, `F` focus timer, `W` stopwatch, `B` back to rotation) work everywhere in Windows. **Night mode** can switch the panel off completely. Every page has its own **schedule** in the page editor.

### Local interface

Switch it on under *Connections › Local interface* to get your address with its secret, then:

```powershell
curl -X POST http://127.0.0.1:47814/api/<secret>/message -d "Backup done"
curl -X POST http://127.0.0.1:47814/api/<secret>/message -d "{\"text\": \"Build\", \"progress\": 42, \"seconds\": 30}"
curl -X POST http://127.0.0.1:47814/api/<secret>/clear -d ""
curl http://127.0.0.1:47814/api/<secret>/status
```

JSON fields: `text`, `color` (`#rrggbb`), `icon` (emoji or built-in name), `effect`, `seconds` (0 = until cleared, default 10), `progress` (0–100). It only listens on this PC, and the secret keeps web pages out.

---

## Claude session alerts

Optional, off by default: **Clawd walks onto the panel and pulls in an alert** when a Claude Code
session on this PC (terminal or VS Code) needs you, then waves next to the reason and the project.
The alert stays in the rotation until the session goes on.

| On the panel | When |
|---|---|
| **Attention** | Claude waits for a permission or an answer |
| **Finished** | Claude is done and has been waiting for you for about a minute |
| **Error** | The turn ended with an API error (rate limit, overload …) |
| **2 waiting** | Several sessions wait; the most urgent one sets colour and project |

Switch it on under *Connections › Claude sessions*. You choose which of the three cases alert you and
for how long the alert takes over the panel; **Send test alert** tries it out.

- Switching it on adds [hooks](https://code.claude.com/docs/en/hooks) to
  `%USERPROFILE%\.claude\settings.json` (after asking, with a backup next to the file). Your other
  settings and hooks stay untouched; switching it off removes only Pixel Panel's entries.
- Claude Code reports to `127.0.0.1` only, to a URL with a random token, so a website cannot raise
  alerts. Only session ID, project folder name and event type are used – prompts, answers and tool
  input are dropped on arrival.
- If Pixel Panel is not running, Claude Code keeps working normally; it may show a short hook error.
  A session that disappears without notice is cleared after an hour.

---

## Calendar (next meeting)

![Calendar](docs/images/calendar.png)

*Next meeting · reminder shortly before · meeting in progress · no meetings*

- Page **Next meeting**: time (or weekday + time if not today) and title. During a meeting:
  "till 11:00".
- **Show on the panel** (under *Connections › Calendar*) sets what the page and the reminder show:

| Setting | Before a meeting | During a meeting |
|---|---|---|
| **Title and time** | `10:30` + title | `till 11:00` + title |
| **Time only** (no titles) | `10:30` | `till 11:00` |
| **Free till / Busy till** | `Free till` + `10:30` | `Busy till` + `11:00` |

  "Busy till" counts meetings that follow without a gap as one block. The calendar icon can be
  switched off for more room.
- **Reminder:** X minutes before the start (default 5) a countdown "in 4 min" appears – with priority
  over status light and rotation.
- Cancelled, declined, all-day and "free" meetings are skipped.
- Checked every 5 minutes. Under *Connections › Calendar* you choose the **source**:

| Source | Sign-in | Requirement |
|---|---|---|
| **Outlook on this PC** (default) | none | Classic Outlook is installed and set up (even if you normally use the new Outlook). It is started hidden when needed and only read. |
| **Calendar link (ICS)** | none | In Outlook on the web › Settings › Calendar › Shared calendars › *Publish a calendar*, create an ICS link (e.g. "Can view titles and locations"). Anyone with the link can see the meetings; some organisations switch this off. |
| **Microsoft 365 (sign-in)** | once in the browser | App registration, see below |

### Microsoft 365: app registration (once, possibly by IT)

Microsoft only lets apps sign in with a registered app ID – a plain "Sign in" button without a
registration is not possible. If you may not register apps in your organisation, ask IT with exactly
these details – or use one of the sources without sign-in:

1. [Entra admin center](https://entra.microsoft.com) › **Applications › App registrations › New
   registration**
   - Name: `Pixel Panel`
   - Account types: **Accounts in this organizational directory only**
   - Redirect URI: platform **Public client/native (mobile & desktop)**, URI `http://localhost`
2. **Authentication** › *Allow public client flows*: **Yes**
3. **API permissions** › Add a permission › Microsoft Graph › **Delegated permissions** ›
   `Calendars.Read` (`User.Read` is already there). If users in your tenant may not consent
   themselves: **Grant admin consent**.
4. On the overview page copy the **Application (client) ID** and **Directory (tenant) ID**.

In Pixel Panel › *Connections › Calendar*: choose the source **Microsoft 365**, enter both IDs, tick
*Fetch next meeting*, click **Sign in** – the browser opens the normal Microsoft sign-in. You stay
signed in afterwards (token encrypted with Windows DPAPI in `%APPDATA%\PixelPanel\m365_token.bin`;
**Sign out** deletes it). The app can only read, not change or send anything.

---

## Weather

Under *Settings › Weather* enter a location and click **Set location**. Data from
[Open-Meteo](https://open-meteo.com) (free, no API key), updated every 15 minutes. The weather page
shows an icon, the current temperature and today's low/high.

---

## Updates

Pixel Panel (from 1.3.0) keeps itself up to date from the
[GitHub releases](https://github.com/Nipo-98/work-pixelclock/releases):

1. Shortly after start and then every 6 hours it asks GitHub for the latest release (public API,
   no sign-in). **Settings › Check for New Version** checks right away.
2. If there is a newer version, it downloads `PixelPanel.exe` next to the running one and checks
   its **SHA-256 checksum** against the one GitHub publishes – a damaged or altered download is
   never installed.
3. Windows does not let a running program be overwritten, but it can be renamed: the running exe
   becomes `PixelPanel.exe.old`, the new one takes its place, starts, and the old app quits. The new
   version deletes the `.old` file. Path, settings and autostart stay the same.

With **Install updates automatically** (default) this happens on its own, with a tray notice. Switched
off, you get a notice instead and install via **Check for New Version**. Running from the source
code, the app only reports new versions.

> Anyone who can publish releases in this repository can deliver code to every installed copy –
> keep write access to the repository tight.

---

## Settings and files

| What | Where |
|---|---|
| Settings | `%APPDATA%\PixelPanel\config.json` (saved automatically on every change) |
| Log | `%APPDATA%\PixelPanel\pixelpanel.log` |
| LaMetric icon cache | `%APPDATA%\PixelPanel\icons\` |
| Autostart | Registry `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\PixelPanel` |

To reset, quit the app and delete `config.json`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| **No panel found** | Panel on? Phone app closed / phone Bluetooth off? (A connected panel is invisible to others.) Bluetooth on the PC on? Move closer. |
| **"Panel not reachable – retrying"** | The app retries every 15 s. Unplugging the panel briefly usually helps. |
| **Phone app can't connect anymore** | **Quit** Pixel Panel from the tray icon – then the panel is free again. |
| **Rotation doesn't run / panel stuck on a Teams status** | Under *Connections*, tick only "busy"-like states for the status light (not "Available"), and tick at least two pages under *Pages*. |
| **Teams status stays "no status found"** | Only the new Teams client is supported. Change your status in Teams once, then it shows up in the log. |
| **Claude: "Not signed in to Claude Code" / "Sign-in expired"** | Start Claude Code and `/login` if needed. |
| **Calendar: "Outlook is not responding"** | Classic Outlook must be installed and have a profile; open it once manually. Otherwise use the ICS link. |
| **Claude alerts don't appear** | Check *Connections › Claude sessions* (status line, **Send test alert**). Restart a session that was already open if it doesn't pick up the hooks. |
| **"Pixel Panel is already running"** | An instance is already running – tray icon (maybe under ^). |
| **"The panel control does not respond"** | The cause and position are in the log. Restart the app; please report the log lines if it happens again. |
| Anything else | Check `%APPDATA%\PixelPanel\pixelpanel.log` – errors of buttons and background work are written there too. |

---

## Development

Requirements: Windows 10/11 with Bluetooth LE, [uv](https://docs.astral.sh/uv/) (or Python 3.13 + pip).

```powershell
git clone https://github.com/Nipo-98/work-pixelclock.git
cd work-pixelclock
uv venv --python 3.13 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt

.venv\Scripts\python.exe -m pixelpanel      # run
.venv\Scripts\python.exe -m pytest tests    # tests
```

### Build the exe

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
# -> dist\PixelPanel.exe  (single file, ~60 MB, no installation needed)
```

`build.ps1` creates the virtual environment if needed, generates the app icon and builds with
PyInstaller (`--onefile --windowed`). It stops with an error if Pixel Panel is still running or the
exe was not rewritten.

### Project layout

```
pixelpanel/
  __main__.py     start, logging
  gui.py          window, sections, preview, tray icon (PySide6)
  theme.py        colours and stylesheet of the window
  engine.py       background thread: connection, rotation, priorities, sending
  ble.py          Bluetooth LE connection to the panel (bleak), upload with ACKs
  pages.py        page + live data -> picture/GIF (emojis, icons, Teams, Claude, weather …)
  transitions.py  transitions between pages (incl. Nyan Cat)
  sources.py      Teams log, Claude usage, weather (Open-Meteo)
  work.py         on air, timers, reminders, profiles, schedules, GitHub, monitors, team message
  api.py          local interface for scripts
  hotkeys.py      global keyboard shortcuts
  gui_work.py     window sections of the work features
  claude_hooks.py Claude session alerts: tracker, local server, Claude Code settings
  calendars.py    calendar without sign-in: classic Outlook (COM) and ICS link
  m365.py         Microsoft 365 calendar (MSAL sign-in, Graph calendarView)
  config.py       settings (JSON in %APPDATA%\PixelPanel)
  autostart.py    start with Windows (registry, no admin)
  updater.py      self-update from GitHub releases (check, download + SHA-256, swap, restart)
  core/           protocol, pixel font, icons, effects, drawing – from ha-pixelclock
tests/            pytest
build.ps1         builds dist\PixelPanel.exe
run.py            entry point for PyInstaller
```

---

## How it works

- **Protocol:** the panel offers the BLE service `0x00FA`. Commands go to `FA02`, answers arrive as
  notifications on `FA03`. Every command is `<length u16 LE><cmd><type><data>`. Supported directly:
  power, brightness, time, built-in clocks, orientation – and uploading a PNG/GIF in 12 KB windows
  that the panel acknowledges one by one.
- **Everything else is a picture:** text, scrolling text, emojis, effects, Teams and Claude pages are
  drawn pixel-exact on the PC with Pillow and sent as PNG (still) or GIF (animated). An animated GIF
  of ~20 KB is transferred in under a second; the animation then keeps running on the panel itself.
- **Transitions:** on a page change a short GIF from the old to the new picture is sent first; its
  last frame (the new page) is held for 5 s, and after it has played the real page follows. So the
  panel never jumps back to the start of the transition.
- **Only send changes:** every page has a "signature" (e.g. time to the minute, Teams status, Claude
  percentages). It is only sent again when that changes.
- **Connection:** stays open; after a drop it reconnects every 15 s. The panel clock is set on
  connect and once a day.

---

## Credits

Protocol, pixel font, icons, effects and drawing functions (`pixelpanel/core/`) come – with the
author's permission – from [dominguez-posh/ha-pixelclock](https://github.com/dominguez-posh/ha-pixelclock),
a Home Assistant integration for the same panels. Its protocol in turn is based on the reverse
engineering in [pypixelcolor](https://github.com/lucagoc/pypixelcolor) (MIT).

Weather data: [Open-Meteo](https://open-meteo.com) (CC BY 4.0). Optional icons from
[LaMetric](https://developer.lametric.com/icons). Emojis: Windows font "Segoe UI Emoji" (used from
the system at runtime, not bundled).

Not affiliated with Action, Briloner, iPixels, Microsoft or Anthropic.
