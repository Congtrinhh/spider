// Relay for the NOXH crawler — forwards to the demo host only, so this can
// never be used as an open proxy to anywhere else. created by tqcong, 20/09/2026
//
// Why this exists: TCP connections to the demo host are blocked from at
// least two independent hosting-provider ASNs (a GitHub-hosted Actions
// runner, and this project's own Hetzner VPS), while working fine from
// non-datacenter-flagged networks. Cloudflare's edge network isn't on that
// kind of blocklist, so the crawler calls this Worker instead of the site
// directly — the Worker makes the real request from Cloudflare's network
// and hands the response straight back. See ../SELF_HOSTED_RUNNER.md.
const UPSTREAM_HOST = "soxaydung-demo.hanoi.gov.vn";
const UPSTREAM = `https://${UPSTREAM_HOST}`;
const SECRET_HEADER = "X-Relay-Secret";

export default {
  async fetch(request, env) {
    if (request.headers.get(SECRET_HEADER) !== env.RELAY_SECRET) {
      return new Response("Forbidden", { status: 403 });
    }

    // Cloudflare's own DNS resolver can't reach hanoi.gov.vn's authoritative
    // nameservers (confirmed independently: `dig @1.1.1.1` for this domain
    // returns SERVFAIL / "No Reachable Authority") — so a plain fetch() to
    // the hostname fails at Cloudflare's edge with a 530 Origin DNS Error,
    // before our own code below even runs. Work around it: resolve via
    // Google's DNS-over-HTTPS (an unrelated, definitely-reachable domain)
    // and force the connection to that IP with cf.resolveOverride, while
    // keeping the Host header + TLS SNI/cert validation on the real
    // hostname — resolveOverride is documented to preserve exactly that.
    //
    // Both fetches are wrapped so a failure returns the real JS error text
    // instead of an opaque platform-level 5xx — needed while we're still
    // narrowing down exactly which step this particular block affects.
    let ip;
    try {
      const dohResp = await fetch(
        `https://dns.google/resolve?type=A&name=${UPSTREAM_HOST}`,
        { headers: { Accept: "application/dns-json" } }
      );
      const dohData = await dohResp.json();
      ip = dohData.Answer?.find((a) => a.type === 1)?.data;
      if (!ip) {
        return new Response(
          `Relay: DoH returned no A record. Raw: ${JSON.stringify(dohData)}`,
          { status: 502 }
        );
      }
    } catch (err) {
      return new Response(
        `Relay: DoH lookup threw: ${err.stack || err.message || err}`,
        { status: 502 }
      );
    }

    const url = new URL(request.url);
    const upstreamUrl = UPSTREAM + url.pathname + url.search;

    const headers = new Headers(request.headers);
    headers.delete(SECRET_HEADER);
    headers.delete("host");

    try {
      const upstreamResp = await fetch(upstreamUrl, {
        method: request.method,
        headers,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
        cf: { resolveOverride: ip },
      });

      const respHeaders = new Headers(upstreamResp.headers);
      respHeaders.set("X-Relay-Resolved-IP", ip);
      return new Response(upstreamResp.body, {
        status: upstreamResp.status,
        headers: respHeaders,
      });
    } catch (err) {
      return new Response(
        `Relay: upstream fetch (resolveOverride=${ip}) threw: ${err.stack || err.message || err}`,
        { status: 502 }
      );
    }
  },
};
