# Self-hosted runner + Cloudflare relay — why, and how to finish it

## Why this exists

The nightly `crawl.yml` workflow, run on GitHub's own `ubuntu-latest` runners, failed with:

```
httpx.ConnectTimeout: timed out
crawler.fetch.FetchError: exhausted 3 retries for https://soxaydung-demo.hanoi.gov.vn/api/NewsZone/NewsZone: timed out
```

DNS resolved fine; the TCP handshake to the resolved IP never completed — packets silently
dropped, not an HTTP-level rejection. Moving execution to a self-hosted runner on a Hetzner VPS
(so far so good, that part's done) surfaced two more layers of the same underlying problem:

1. **DNS**, initially: the VPS's default resolver (via Hetzner's DHCP-provided nameservers) got
   `SERVFAIL` trying to reach `hanoi.gov.vn`'s authoritative nameservers — so did `1.1.1.1`
   (Cloudflare's own resolver, with an explicit "No Reachable Authority" error). `8.8.8.8`
   (Google) resolved it fine. **Fixed** by pinning `systemd-resolved` to Google's DNS via a
   drop-in config:
   ```bash
   sudo mkdir -p /etc/systemd/resolved.conf.d
   sudo tee /etc/systemd/resolved.conf.d/dns-override.conf > /dev/null <<'EOF'
   [Resolve]
   DNS=8.8.8.8 8.8.4.4
   FallbackDNS=1.1.1.1
   EOF
   sudo systemctl restart systemd-resolved
   ```
2. **TCP**, once DNS was fixed: `curl` to the resolved IP on port 443 timed out — the exact same
   failure mode as the GitHub-hosted runner. Confirmed (by testing from a third, unrelated
   network) that the site is reachable and serving normally elsewhere. **Conclusion:** TCP is
   blocked from at least two independent hosting-provider ASNs (GitHub Actions' and Hetzner's,
   AS24940) — a blanket "block generic datacenter ASN traffic" policy, not something specific to
   GitHub, and not something a different mainstream cloud VPS provider would likely dodge either.

**The fix for #2:** relay just the two outbound calls the crawler makes (`POST` the listing page,
`GET` detail pages) through a Cloudflare Worker. Cloudflare's edge network fronts too much of the
legitimate web to be on the same blocklists as generic VPS ASNs. The Hetzner box still does
everything else — scheduling (via the runner), extraction, the git commit. See
`infra/relay-worker.js`.

## Steps to finish setup

### 1. Deploy the Cloudflare Worker

Needs a free Cloudflare account and Node.js on your local machine (or the VPS) to run `wrangler`,
Cloudflare's CLI:

```bash
cd infra
npx wrangler login          # opens a browser to authorize once
npx wrangler secret put RELAY_SECRET
# paste a random value when prompted, e.g. generated with: openssl rand -hex 32
# keep this value — you'll need it again in step 2
npx wrangler deploy
```

`wrangler deploy` prints the Worker's URL, something like
`https://noxh-relay.<your-subdomain>.workers.dev`. That's `NOXH_RELAY_URL` for the next step.

**Verify the Worker on its own**, before touching the crawler:

```bash
# without the secret — should be 403
curl -s -o /dev/null -w '%{http_code}\n' https://noxh-relay.<your-subdomain>.workers.dev/api/NewsZone/NewsZone

# with the secret — should be 200 and a real HTML fragment
curl -s -X POST 'https://noxh-relay.<your-subdomain>.workers.dev/api/NewsZone/NewsZone' \
  -H 'X-Relay-Secret: <your-RELAY_SECRET-value>' \
  -H 'X-Requested-With: XMLHttpRequest' \
  -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' \
  --data-raw 'PageIndex=0&PageSize=DxZD1w2i%2B8E%3D&Catname=tcSrMdFFelkh9g86xUeP49VtbNmplxo1&LanguageId=jM2HDDVEz40%3D&Site=t6dK76xLcwQ%3D'
```

### 2. Point the crawler at the relay, from the Hetzner VPS

`crawler/config.py` reads `NOXH_RELAY_URL` / `NOXH_RELAY_SECRET` from the environment — unset
(the default) means connect directly, unchanged. On the VPS:

```bash
export NOXH_RELAY_URL="https://noxh-relay.<your-subdomain>.workers.dev"
export NOXH_RELAY_SECRET="<your-RELAY_SECRET-value>"
cd ~/actions-runner/_work/spider/spider   # or wherever the repo checkout is
python3 -m crawler.crawl
```

This is the real end-to-end proof: if it fetches and parses a real listing page instead of
timing out, the block is bypassed.

### 3. Make the runner permanent (it's currently a manual foreground process)

Today the runner was started with `RUNNER_ALLOW_RUNASROOT=1 ./run.sh` directly in an SSH session
— that stops the moment the session closes or the VPS reboots. Install it as a systemd service
instead, from the runner's install directory:

```bash
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status
```

The two relay env vars need to reach the service too — add them to the runner's service
environment (e.g. `~/actions-runner/.env`, which the runner's systemd service loads
automatically):

```bash
echo "NOXH_RELAY_URL=https://noxh-relay.<your-subdomain>.workers.dev" >> ~/actions-runner/.env
echo "NOXH_RELAY_SECRET=<your-RELAY_SECRET-value>" >> ~/actions-runner/.env
sudo ./svc.sh stop && sudo ./svc.sh start
```

### 4. Point the workflow at the self-hosted runner

In `.github/workflows/crawl.yml`:

```diff
-    runs-on: ubuntu-latest
+    runs-on: self-hosted
```

Commit, push, then trigger `workflow_dispatch` manually from the Actions tab and confirm a fully
green run — that's the real, final confirmation the whole chain works end to end.

## Not done here (by design)

- No switch to a different VPS provider — staying on Hetzner.
- No residential/commercial proxy service — the Worker is the free, lower-friction equivalent for
  this specific problem.
- No changes to the crawler's retry/error-handling behavior — out of scope for this fix.
