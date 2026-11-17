# Changelog

What changed in each version of Audible Watcher, newest first. Upgrading
always keeps your watches and settings: the database is updated
automatically the first time a new version starts.

Downloads for every version are on the
[releases page](https://github.com/Andrew911tt/audible-watcher/releases).

## 1.2.0 — 2026-11-16

Wishlist price alerts, mail relays, Last released sort.

### Wishlist price alerts (needs your Audible account connected)
- A new Wishlist tab lists every title on your Audible wishlist with its
  price now, list price and lowest price seen.
- Get an email when a title is at or below your alert price: set a
  default in Settings > Monitoring, or a price per title on the tab.
- Prices are checked with the daily check and again 12 hours later, plus
  a Refresh prices now button. One email per drop: you hear again only
  if it gets cheaper still, and the alert re-arms once the price goes
  back up. Titles you own are skipped.
- Disconnecting your Audible account deletes the stored wishlist.

### Mail relays
- Email can go through a mail relay on your own network: leave the
  username and password blank and choose Security "None" (plain SMTP,
  port 25) or STARTTLS.
- A login always needs SSL or STARTTLS: Settings won't save a username
  and password with Security "None", so a password is never sent
  unencrypted.
- Clearer hints when a server refuses the From address, and for reaching
  a relay from a Docker container.

### Also
- "Last released" sort on the Authors, Series and Narrators tabs: the
  most recent release first (pre-orders don't count), so a book you may
  have missed rises to the top.
- The app has a favicon.

Wishlist alerts, mail-relay support, the favicon and the Series sort were
contributed in the [mylacc/audible-watcher](https://github.com/mylacc/audible-watcher)
fork.

## 1.1.0 — 2026-11-16

Narrator watches, pre-order filters, update check.

### Narrators
- Watch narrators as well as authors and series: search for one under
  Add favorites and click Watch narrator. You get an email for each new
  narration, full-cast productions included. Narrators are only watched
  when you add them yourself.
- One narrator credited two ways ("R. C. Bray" / "R.C. Bray",
  "Kevin R. Free" / "Kevin Free") counts as one person.
- New Narrators tab on the dashboard, and a Library "By narrator" view.
- Book cards show "Narrated by" when a book has one narrator; watch pages
  list every narrator. Each name links to that narrator's page or a search.
- A book that matches several watches is credited to the series, then the
  author, then the narrator. A book that's already been announced isn't
  announced again when another watch finds it later.

### Pre-orders filters
- Narrow the Pre-orders tab by genre, length, or whether the book comes
  from a series, author or narrator you follow. Filters only change what
  you see, never what's watched or emailed. Genres fill in for existing
  books with the next check.

### Updates
- Settings has a new About & updates card with your version and a Check
  for updates button, plus how to update for your kind of install.
- A daily check shows a dismissable notice on the dashboard when there's
  a new version. It asks GitHub for the latest release number only; turn
  it off under Settings > Monitoring.
- The Windows tray menu has Check for updates (can be hidden in Settings).

## 1.0.6 — 2026-11-16

Podcasts and old wrong-author matches cleaned up.

- Watches skip podcasts: Audible's author search also returns podcasts
  that share a name (e.g. "Scott Meyers" or "Ashley Scott Meyers" for a
  "Scott Meyer" watch). Add favorites no longer offers them either.
- Each check hides an author watch's entries that aren't that author's
  audiobooks: podcasts, and titles from before exact author matching
  (other authors, or only a foreword, introduction, translation or
  editing credit). They move to Hidden entries with the reason shown;
  Unhide works and sticks, and nothing is emailed. Series watches are
  left alone.

## 1.0.5 — 2026-11-16

Series pages in series order.

- Series pages list their books in series order (book 1 first, 2.5
  between 2 and 3, the versions of one book together, oldest listing
  first), with a Publication order option (oldest release first).
- The All books / By series toggle is gone from series pages; author
  pages keep it, and their By series groups are now in series order too.

## 1.0.4 — 2026-11-16

Safer series lookup, automatic placeholder cleanup.

### Fixes
- A series is only looked up again when its stored ID finds nothing in the
  store, and then by matching authors; same-name series are never guessed.
  (A series added while Audible was down could switch to another series
  with the same name and add that series' author.)
- Library sync keeps the last known pre-orders when Amazon's order list
  fails to load, instead of dropping them for a day.
- Sign-in lockout counts each visitor separately (no shared limit anyone
  could trigger). `AW_PROXY_COUNT` trusts that many reverse proxies, so
  visitors behind one are told apart.
- Watch names match ignoring case for accented letters too.

### Placeholders instead of "Clean up duplicates"
- The Clean up duplicates button is gone. Instead, each check hides a
  no-date placeholder when the same series and book number has a dated
  listing of the same kind on that watch. It's hidden the normal way
  (Unhide works and sticks), and never emailed.
- Dramatized adaptations, other narrators' editions and re-releases are
  never hidden.

## 1.0.3 — 2026-11-15

Search fixes, safer updates, library and settings polish.

### Search and watches
- Series and author search use Audible's relevance order, so popular
  series (Dungeon Crawler Carl, Harry Potter) and heavily translated
  authors (Stephen King) are found again. Credits like "X - introduction"
  aren't offered as authors.
- Author watches fetch all of an author's titles, so translated authors
  show their whole own-language catalogue.
- Re-adding a watch you already have no longer resets it; search results
  show "Watching ✓" for existing watches.
- Names with commas ("Martin Luther King, Jr.") stay whole everywhere.

### No more floods of "new" books
- After an update that changes what the app fetches, titles that only
  turn up because of the change are recorded quietly. Recent and upcoming
  books still alert, and books that are genuinely new on Audible always do.
- Email safety net: more than 10 new books from one watch in one check are
  summed up in a single email line (they're all on the dashboard).

### Store and availability
- Changing marketplace looks series up in the new store, hides the old
  store's editions (they come back if you switch back), and warns about a
  series the new store doesn't carry.
- Books hidden as "not sold in your store" are checked again weekly and
  come back as new once they go on sale. Your own Hide/Unhide is never
  undone, and watch pages say why each book is hidden.

### Security
- Sign-in checks where it sends you afterwards, and wrong passwords lead
  to growing waits without slowing the server down.
- An out-of-date or forged form returns you to the page with a note.
  Pages no longer send their address to Audible or Amazon.

### Library and pages
- Library: upcoming pre-orders first; All books sorts by recent purchase,
  release date, title, author or length. The library re-syncs about once
  a day.
- Series names on book cards link to the series page (or a search), and
  series pages link to the series on Audible.
- Settings: the Audible account moved here (out of the top bar), and the
  page is one set of matching cards with a single Save row.

### Other fixes
- The retry after an Audible outage also works with 3 or fewer watches; a
  series added during an outage gets its author watch later; a clear
  message when the Docker data folder isn't writable.
- Releases wait for the full test suite and publish the Docker image to
  ghcr.io.

## 1.0.2 — 2026-11-15

Fixes from a full review, access protection, email setup guide.

### Correctness
- A watch whose first fetch failed, or every watch after a marketplace
  change, is quietly re-recorded instead of flagging and emailing its
  whole back catalogue.
- Author watches match the exact credited name (ignoring punctuation),
  including co-written books; forewords and translations don't count.
- Long series are fetched in full; series pages show each book's number
  in that series.
- PRE-ORDERED only for the exact edition you ordered; other regions'
  editions count as owned once released. Undated books get a TBA badge.
- Email: nothing is saved up while email is off, books you've marked seen
  are never emailed, and SMTP errors explain what to fix.
- Audible outages: temporary errors are retried (honouring Retry-After);
  after 3 unreachable watches the check stops and tries again an hour
  later.
- Bad input no longer causes server errors; removing a watch during a
  check isn't an error.

### Security and access
- Unrecognised hostnames are refused (protection against DNS rebinding);
  `AW_ALLOWED_HOSTS` adds your own names. Optional `AW_PASSWORD` sign-in.
  A read-only Access panel in Settings, and an "Address not recognised"
  page with exact steps.
- Audible tokens are saved with owner-only permissions, and Disconnect
  removes the app's device from your Amazon account.
- The Docker image runs as a non-root user, with a health check.

### Usability
- Step-by-step email setup on the Settings page, with provider presets and
  a troubleshooting list.
- "Check Audible now" runs in the background; times show in local time;
  print publish-date lookups can be switched off.
- The Windows app tells its own copy apart from another program on the
  port, and keeps a log file.

## 1.0.1 — 2026-11-15

Fixes for four serious issues found in a code review.

- Settings: a daily check hour outside 0–23 is refused instead of saved,
  and a bad stored value falls back to 08:00, so the app always starts.
- Security: every form carries a per-session token (CSRF protection), the
  session key is random per install, and the saved email password is
  cleared when the mail server or username changes, so it can't be sent
  anywhere else.
- Duplicate cleanup no longer hides book 1 of one series because of
  book 1 of another on author watches.
- Email: each new book is checked as sold in your Audible store before
  it's emailed; other regions' editions are hidden, and undated books or
  failed lookups wait for the next check.

## 1.0.0 — 2026-11-15

First public release: a self-hosted app that watches Audible for new
releases from your favorite authors and series and emails you when
something new appears.

- Windows tray app and installer, built automatically for each release.
- Docker image and a compose example for servers.
- Installed builds keep their data in a per-user folder (`AW_DATA_DIR`
  overrides it).
