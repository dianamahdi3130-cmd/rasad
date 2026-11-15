# Subscriber access keys (the private app's door)

Status: **built and tested, not yet wired into the page** (15 Nov 2026). Server: `worker/src/index.js`.
Client library: `web/access.js` (`RasadAccess`). Tests: `tests/test_worker_access.js`,
`tests/test_access_client.js` (no network). The page (`web/index.html`) still has the old staff
login and must switch to `RasadAccess`; nothing was ever deployed, so there is nothing to migrate.

## What the manager gets
One key per subscriber, under the subscriber's name, that works on **one device**; a second device
needs the manager's approval; changing network changes nothing; the manager can sign anyone out,
stop a key, give a new key, untie a device, set or extend a validity period that ends by itself;
and nothing (data, clips, archive, the app's tools) is served without a valid, active subscription.

## How it works
- **Key**: `RSD-XXXXX-XXXXX-XXXXX-XXXXX`, 20 symbols from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (100
  bits, no 0/O/1/I). Shown once (create, rekey); stored only as SHA-256. Typed any way: lower case,
  spaces, no dashes, no prefix, Arabic digits, invisible marks from copy and paste.
- **Device = a key pair the browser cannot export.** `web/access.js` makes an ECDSA P-256 pair
  with `extractable: false` and keeps it in IndexedDB (`rasad-access` / `keys` / `device`). The
  device's name is the SHA-256 (hex) of its canonical public JWK (`crv, kty, x, y`). To sign in or to
  prove itself the device signs a fresh number from the door (`POST /api/nonce`: 32 hex, good once,
  two minutes): text `rasad-login:<nonce>` or `rasad-prove:<nonce>`, ECDSA/SHA-256, raw `r||s`,
  base64url. The access key itself is never stored in the browser. No IP address is involved.
- **Session**: cookie `fs` (64 hex; `HttpOnly; Secure; SameSite=Strict; Path=/`; 400 days), stored
  hashed as `sess:<sha256>` → `{sub, role, device, epoch, t, proof_until, prev}`. The row is only a
  pointer: **every request re-reads the subscriber** and the session counts only while the subscriber
  exists, is not stopped, not expired, the device is still bound and `epoch` matches. So expiry,
  stop, forced sign-out (`epoch + 1`), untie and removal work at once.
- **Proof window, 15 minutes**: after `proof_until` every answer is `401 {"error":"proof","nonce"}`
  until the device signs (`POST /api/prove`). Each proof also **replaces the cookie**: the old one
  works 60 more seconds (requests on their way), then only for proving again (in case the answer was
  lost on a bad network) and is deleted at the following proof. Answers carry `x-proof-left`
  (seconds); the library proves early under 300 s, and asks the door before locking the screen.
- **Manager**: types the manager's code (secret `ADMIN_CODE`) in the same field; role `admin`; one
  device at a time (a new device signs the old one out and is reported on Telegram); same proofs.
  Changing the code ends every manager session. `code_weak` in the panel's state warns under 12 characters.
- **Wrong keys**: 5 per network (IPv4 address, IPv6 /64) per 15 minutes, then `429 wait`; a device
  already bound to the key it shows is never held up by this. Telegram warning at the third.
- **Other device**: `403 {"error":"device","requested":true}`, one request per device kept for the
  manager (newest 3, one week), Telegram note. Approve: the device is bound; it **replaces** the old
  device(s) when `replace` is sent or the allowed number is reached. Reject: that device gets
  `requested:false` for a day. After approval the subscriber types the key again on the new device.
- **Files**: `run_worker_first`. Public to anyone: `/`, `/index.html`, `/access.js`, `/sw.js`,
  `/manifest.webmanifest`, `/favicon.ico`, `/icons/*`, `/brand-*` (`isPublicPath`). Every other
  file needs the session of an active subscription on its bound device (`401` JSON otherwise), but
  not the 15-minute proof: the files hold no data, and a page cannot sign before it has loaded.
  Headers: `nosniff`, `no-referrer`, `X-Frame-Options: DENY`, HSTS; protected files
  `cache-control: private, no-cache`.

## API (JSON, `cache-control: no-store`)
| Route | Who | Answer |
|---|---|---|
| `GET /api/ping` | anyone | `ok` |
| `POST /api/nonce` | anyone (20 a minute per network) | `{nonce}` |
| `POST /api/login` `{key, pub, nonce, sig, label?}` | anyone | `200 {ok, me:{id,name,role,expires}, server_time}` + cookie · `400 missing` · `400 proof` · `401 wrong` · `403 disabled` · `403 expired` · `403 {error:"device", requested}` · `429 wait` |
| `GET /api/me` | session | `{id,name,role,expires,server_time}` or `401 {error: login / proof / expired / disabled / logout, nonce?}` |
| `POST /api/prove` `{nonce, sig}` | session (also unproven) | `200 {ok}` + new cookie · `400 proof` · `401` as above |
| `POST /api/logout` | anyone | `{ok}`, cookie cleared, session deleted |
| `GET /api/data` | proven session | the incidents; records seen / open |
| `GET /api/archive` · `GET /api/archive/YYYY-MM` | proven session | `{months:[{month,size,t}]}` newest first · that month's JSON (`404` when missing) |
| `GET / HEAD /clips/<file>` | proven session | the clip (byte ranges) |
| `GET /api/admin/state` | manager | `{subs:[…], online:[{id,name,role,t,since,city,country}], log:[{t,event,id,name,city,country,device}], telegram, now, code_weak}` |
| `POST /api/admin/sub/create` `{name, days? / expires?, note?, max_devices?}` | manager | `{ok, id, key, sub}` · `400 name / days / expires` · `409 exists` |
| `POST /api/admin/sub/update` `{id, name?, note?, expires?, add_days?, max_devices?}` | manager | `{ok, sub}` (`add_days` counts from the end of the running period, or from now when it is over) |
| `POST /api/admin/sub/disable` `{id, disabled}` · `logout` `{id}` · `rekey` `{id}` → `{ok, key, sub}` · `unbind` `{id, device_id?}` · `approve` `{id, rid, replace?}` · `reject` `{id, rid}` · `delete` `{id}` | manager | `{ok, sub}`; `404` unknown id, device or request; `403 forbidden` for a subscriber, `401` without session |
| `POST /api/admin/telegram` | manager | `{link}` (unchanged) |
| `POST /api/ingest/data` · `PUT /api/ingest/clip/<file>` · `GET /api/ingest/clips` · `POST /api/ingest/keep` · `PUT /api/ingest/archive/YYYY-MM` | header `x-ingest-key` | as before; archive: `{ok, size}`, `400` for a bad month or body that is not JSON |

A subscriber in `subs`: `{id, name, note, status, disabled, created, expires, key_hint, max_devices,
devices:[{id,label,bound_at,last_seen}], pending:[{rid,label,t,city,country}], last_login, last_seen,
last_open, city, country, online}`; `status` is `disabled`, `expired`, `waiting` (no device yet) or
`active`. Log events: `login open failed blocked requested approved rejected created rekeyed updated
disabled enabled expired refused forced_logout unbound deleted` (45 days).
Stored rows: `sub:<id>`, `key:<sha256>`, `sess:<sha256>`, `nonce:<n>`, `fail:<network>`, `log:…`,
`cfg:manager`, `cfg:archives`, and as before `data#…`, `clip:…`, `archive:YYYY-MM#…`.

## Threat model, honestly
Stops: a key used on a second device; guessing keys; a session cookie copied to another browser
(dead within 15 minutes at most, and a minute after the real device's next proof, even while that
device keeps the app open); reading the key pair with a script or an extension's page script; keys
that ran out, were stopped or removed (checked on every request); another site posting requests or
framing the app; a leaked copy of the store (it holds fingerprints of keys and cookies, not the keys).

Does not stop:
- someone **lending the unlocked device** itself, or a subscriber **screenshotting** or retyping data;
- a script the subscriber runs inside their own signed-in page: it can use the session while the
  page is open (it still cannot take the key pair away);
- **copying the whole browser profile** off a computer or a rooted phone: "cannot be exported"
  holds against scripts, not against someone who can copy the browser's files. The copy is the same
  device to the door; the two then keep ending each other's session and the log shows sign-ins from
  two places. Remedy: untie the device and give a new key;
- a copied cookie **inside** its 15 minutes;
- a **short manager's code**: whoever knows it is the manager. Use a long one;
- a flood of requests: the free plan allows 100,000 requests a day in total;
- whoever controls the Cloudflare account or the repository's secrets.

## Known behaviours
- **iPhone**: an app added to the Home Screen has its own storage, so it is a different device from
  Safari. Add it to the Home Screen first and sign in there, or the manager approves the request.
  The same holds for a private window, another browser, and a link opened inside Telegram's browser.
- **Safari clears a site's storage after 7 days without a visit** (not for Home Screen apps), and
  any browser does when the user clears site data: the device's identity is gone, the key asks for
  approval again. The library asks the browser to keep the storage (`navigator.storage.persist`).
- The manager's panel must show a key **once** and offer copying: it cannot be shown again.
- Stop → start: the old session does not come back, the subscriber signs in again (no approval).
  Expired → renewed: the same device carries on without typing the key.
- A lost phone: untie it (or stop the key); a leaked key: `rekey`.
- "Who has the app open" lives in the Worker's memory and restarts empty after a deploy.
- An open app makes about two requests a minute (`RasadAccess.watch` asks only when nothing else
  confirmed the session for half a minute, and never in the background).
- The service worker must not pre-cache protected files: they answer `401` before sign-in.
- Tested in Chrome and in the Workers runtime (`wrangler dev`); not yet on a real iPhone.

## Wiring the page (`RasadAccess`, `web/access.js`)
```js
const st = await RasadAccess.boot();
// {mode:"public"}                    the open site, as before
// {mode:"private", me}               signed in: me = {id, name, role: "admin" | "subscriber", expires}
// {mode:"private", me:null, reason}  show the gate: login, logout, expired, disabled, proof → errorText(reason);
//                                    network, server → the door is out of reach: offer "try again", not the key field
const r = await RasadAccess.login(field.value);       // {ok:true, me} or {ok:false, error} → errorText(r.error)
RasadAccess.onLocked = reason => { /* hide the data, show the gate with errorText(reason) */ };
RasadAccess.watch();                                  // once: closes the app within 30 s of a sign-out, stop or expiry
const res = await RasadAccess.fetch("api/data");      // every call to the door; 401 = locked, or no proof possible right now
const st2 = await RasadAccess.admin("state");         // the panel: GET without a body, POST with one
```
Also: `logout()` → `{ok}`; `prove()`; `me` (read it fresh: a renewal changes `expires`); `errorText`,
`eventText`, `statusText`, `normalizeKey`, `deviceLabel`. Load `access.js` first and the protected
scripts (`imagery.js`, `beforeafter.js`) only on the open site or once somebody is signed in (after a
sign-in: add the script tags, or reload). Escape every name, note and label when showing them. On an
iPhone, tell people to add the app to the Home Screen **before** typing the key.

## Names and deployment
Worker `rasad` → `https://rasad.<name>.workers.dev` (Durable Object class `Hub`, migration `v1`).
Optional repository variable `RASAD_DOMAIN` (for example `app.example.com`, on the same Cloudflare
account): `deploy-worker.yml` adds it as a custom domain, and `rasad.yml` uses it for the links
people open once it answers, while the monitor sends through whichever address answers.
