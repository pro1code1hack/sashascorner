# Wallet passes: setting up Apple and Google

Sasha's Corner Rewards works without either wallet: the web card at `/c/<id>` is a
complete card, and the join page only offers "Add to Apple Wallet" / "Add to Google
Wallet" once that wallet is configured. So these can be done one at a time, in any order.

At any point, `uv run cafeops wallet doctor` says what is configured, which environment
variables are still missing, when the certificates expire, and whether pushes are
failing. `--online` also checks the Google credentials against Google.

Allow **two to three weeks** end to end: most of it is waiting for D-U-N-S and Apple's
organisation check, and for Google's review of the pass class.

---

## Apple Wallet

### 1. D-U-N-S number (free, about 5 working days)

Apple only enrols a company as an organisation if it has a D-U-N-S number, and the pass
must be issued by the café's company (the name on the pass is the Apple account's legal
name, and an individual account would show a person's name instead).

1. Check first whether the company already has one: Apple's lookup tool at
   <https://developer.apple.com/enroll/duns-lookup/>.
2. If not, request it through the same tool. Use the company's exact legal name and
   registered address as on Companies House.
3. Wait for the email from Dun & Bradstreet. Apple may take a further day or two to see
   the new number.

### 2. Apple Developer Program, as an organisation (99 USD a year)

1. Create (or use) an Apple Account for the business, with two-factor authentication,
   ideally on a shared business email rather than a person's.
2. Enrol at <https://developer.apple.com/programs/enroll/> choosing **Organization**.
   You need: the D-U-N-S number, the legal entity name, a company website on the
   company's domain (sashascorner.co.uk), and the authority to bind the company.
3. Apple phones to verify. Pay the fee. Note the **Team ID** (10 characters) shown under
   Membership details: that is `CAFEOPS_WALLET_APPLE_TEAM_ID`.

### 3. Pass Type ID

1. Certificates, Identifiers & Profiles → Identifiers → **+** → **Pass Type IDs**.
2. Description "Sasha's Corner Rewards", identifier `pass.uk.co.sashascorner.rewards`
   (any reverse-domain name starting `pass.`; it cannot be changed later without
   re-issuing every customer's pass). That is `CAFEOPS_WALLET_APPLE_PASS_TYPE_ID`.

### 4. The Pass Type ID certificate

The same certificate signs every pass **and** authenticates the push notifications that
update them, so there is only one to manage. It lasts a year.

On any Mac or Linux machine:

```bash
mkdir -p ~/wallet-certs && cd ~/wallet-certs
openssl genrsa -out pass.key 2048
openssl req -new -key pass.key -out pass.csr \
  -subj "/emailAddress=you@sashascorner.co.uk/CN=Sasha's Corner Rewards/C=GB"
```

1. In the developer portal open the Pass Type ID → **Create Certificate** → upload
   `pass.csr` → download `pass.cer`.
2. Convert it to PEM:
   ```bash
   openssl x509 -inform DER -in pass.cer -out pass.pem
   ```
   (DER works too -- the code accepts both -- but PEM is easier to inspect.)
3. Keep `pass.key` secret. It never leaves the server and is not in git.

If you created the CSR with Keychain Access instead, export the certificate *and its
key* as a `.p12` and split it:

```bash
openssl pkcs12 -in pass.p12 -clcerts -nokeys -out pass.pem -legacy
openssl pkcs12 -in pass.p12 -nocerts -nodes -out pass.key -legacy
```

### 5. Apple WWDR intermediate certificate

Wallet rejects a pass whose signature does not include Apple's intermediate. Download
**Worldwide Developer Relations - G4** from <https://www.apple.com/certificateauthority/>
and convert it:

```bash
curl -O https://www.apple.com/certificateauthority/AppleWWDRCAG4.cer
openssl x509 -inform DER -in AppleWWDRCAG4.cer -out wwdr.pem
```

### 6. Put the files on the server and set the variables

**Docker (the normal path):** copy the three files into `secrets/wallet/` in the repo on
the server with exactly these names, `sudo chown -R 1000:1000 secrets/wallet` and
`chmod 600 secrets/wallet/*`. `docker-compose.yml` already mounts that folder read-only
into `api` and `scheduler` and points the three path variables at it, so in `.env` you
set only `CAFEOPS_WALLET_APPLE_PASS_TYPE_ID` and `CAFEOPS_WALLET_APPLE_TEAM_ID` (and the
key password if there is one). See [`GO-LIVE.md`](GO-LIVE.md) section 2.

**Without docker:** copy them somewhere the app can read and nobody else can (e.g.
`/etc/cafeops/wallet/`, mode 600, owned by the app user). Then in `.env`:

```
CAFEOPS_WALLET_APPLE_PASS_TYPE_ID=pass.uk.co.sashascorner.rewards
CAFEOPS_WALLET_APPLE_TEAM_ID=XXXXXXXXXX
CAFEOPS_WALLET_APPLE_CERT_PATH=/etc/cafeops/wallet/pass.pem
CAFEOPS_WALLET_APPLE_KEY_PATH=/etc/cafeops/wallet/pass.key
# CAFEOPS_WALLET_APPLE_KEY_PASSWORD=...   only if pass.key is encrypted
CAFEOPS_WALLET_APPLE_WWDR_PATH=/etc/cafeops/wallet/wwdr.pem
# CAFEOPS_WALLET_APPLE_APNS_USE_SANDBOX=false   (default; keep false in production)
```

### 7. Check it

```bash
uv run cafeops wallet doctor               # Apple: configured, cert for the right Pass Type ID and team
uv run cafeops wallet preview --demo 5 --out /tmp/pass
```

Open `/tmp/pass/<id>.pkpass` on an iPhone (AirDrop or email it to yourself): it should
add to Wallet. Then join on the real site from an iPhone, add the pass, and stamp it from
the scanner -- the pass should update within a few seconds with "You've got 1 stamps"
on the lock screen.

Updates need the site to be reachable on **HTTPS** at the public URL: Wallet calls
`https://sashascorner.co.uk/wallet/apple/v1/...` (Caddy forwards `/wallet/apple/*` to the
API). If passes add but never update, `cafeops wallet doctor` shows the latest push
failure, and the API log shows lines starting `wallet device log:` -- that is Wallet
itself explaining what went wrong.

**Renewal:** the pass certificate expires after a year. `doctor` turns yellow 30 days
before. Make a new one (steps 4 and 6) and restart the app; existing passes keep working,
because they are re-signed with whatever certificate is current when they update.

---

## Google Wallet

### 1. Issuer account

1. Go to the Google Pay & Wallet Console, <https://pay.google.com/business/console>, with
   a Google account for the business.
2. Create the business profile (legal name, address).
3. Open **Google Wallet API** and sign up as an issuer. Note the **Issuer ID** (a long
   number): that is `CAFEOPS_WALLET_GOOGLE_ISSUER_ID`.

### 2. Service account

1. In Google Cloud Console (<https://console.cloud.google.com/>), create a project, e.g.
   "sashas-corner-wallet".
2. APIs & Services → Library → enable **Google Wallet API**.
3. IAM & Admin → Service accounts → create one, e.g. `wallet@...`. No project roles are
   needed.
4. On the service account → Keys → Add key → JSON. Download it. Keep it secret.
5. Back in the Pay & Wallet Console → Users → invite the service account's email with
   the **Developer** access level. This is what lets it create passes for the issuer.

Copy the JSON to the server -- under docker as `secrets/wallet/google-sa.json` (then only
the issuer id is needed in `.env`), otherwise e.g. `/etc/cafeops/wallet/google-sa.json`,
mode 600 -- and set:

```
CAFEOPS_WALLET_GOOGLE_ISSUER_ID=3388000000000000000
CAFEOPS_WALLET_GOOGLE_SERVICE_ACCOUNT_PATH=/etc/cafeops/wallet/google-sa.json
# CAFEOPS_WALLET_GOOGLE_CLASS_SUFFIX=stamp   (default; do not change once approved)
```

### 3. Create the pass class and get it reviewed

```bash
uv run cafeops wallet doctor --online     # token ok?
uv run cafeops wallet google-class-sync   # creates <issuer>.stamp
```

Then in the Pay & Wallet Console → Google Wallet API → the class → **Request publishing
access** / submit for review. Until Google approves it (typically a few working days),
only accounts added as test users in the console can save the pass -- which is exactly
right for trying it out. Google shows the programme logo from
`https://sashascorner.co.uk/api/loyalty/pass-assets/google-logo.png` and the stamp strip
from `/api/loyalty/strip/...`, so the site must be live on its domain before review.

Running `google-class-sync` again after changing the programme's rules or logo updates
the class; an already-approved class may go back to review.

### 4. Check it

From an Android phone signed in to a test account: join on the site, tap "Add to Google
Wallet", then stamp it from the scanner. The pass's stamp count and strip should update
within a minute, with a notification for "You've got 1 stamps". Google limits notifying
messages to a few per pass per day; beyond that the pass still updates, silently.

---

## All the variables

| Variable | Needed for | What |
|---|---|---|
| `CAFEOPS_WALLET_APPLE_PASS_TYPE_ID` | Apple | e.g. `pass.uk.co.sashascorner.rewards` |
| `CAFEOPS_WALLET_APPLE_TEAM_ID` | Apple | 10-character Team ID |
| `CAFEOPS_WALLET_APPLE_CERT_PATH` | Apple | Pass Type ID certificate, PEM or DER |
| `CAFEOPS_WALLET_APPLE_KEY_PATH` | Apple | its private key, PEM |
| `CAFEOPS_WALLET_APPLE_KEY_PASSWORD` | Apple, optional | if the key is encrypted |
| `CAFEOPS_WALLET_APPLE_WWDR_PATH` | Apple | Apple WWDR G4 intermediate |
| `CAFEOPS_WALLET_APPLE_APNS_USE_SANDBOX` | Apple, optional | `true` only for development iPhones |
| `CAFEOPS_WALLET_GOOGLE_ISSUER_ID` | Google | numeric issuer ID |
| `CAFEOPS_WALLET_GOOGLE_SERVICE_ACCOUNT_PATH` | Google | service-account JSON key |
| `CAFEOPS_WALLET_GOOGLE_CLASS_SUFFIX` | Google, optional | default `stamp` |
| `CAFEOPS_WALLET_PUBLIC_URL` | optional | defaults to `CAFEOPS_LOYALTY_PUBLIC_URL`, then `https://sashascorner.co.uk` |

The API **and** the scheduler both need the wallet variables and files: the API builds
passes and pushes right after a stamp, the scheduler retries pushes that failed.

## Artwork

The icon, logo and Google programme logo are generated from the brand line-art
(`site/web/public/brand/lineart.svg`) into `assets/pass/` and committed:

```bash
uv run cafeops wallet assets            # regenerate after a brand change
uv run cafeops wallet strips --out /tmp/strips   # look at every stamp strip
```

The stamp itself (a cup in the logo's line style) is defined in
`cafeops/integrations/wallet/strips.py`; `assets/pass/stamp.svg` is the same drawing for
the web card.
