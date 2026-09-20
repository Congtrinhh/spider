// Relay for the NOXH crawler — forwards to the demo host only, so this can
// never be used as an open proxy to anywhere else. created by tqcong, 18/09/2026
//
// Why this exists: TCP connections to the demo host are blocked from at
// least two independent hosting-provider ASNs (a GitHub-hosted Actions
// runner, and this project's own Hetzner VPS), while working fine from
// non-datacenter-flagged networks. Cloudflare's edge network isn't on that
// kind of blocklist, so the crawler calls this Worker instead of the site
// directly — the Worker makes the real request from Cloudflare's network
// and hands the response straight back. See ../SELF_HOSTED_RUNNER.md.
const UPSTREAM = "https://soxaydung-demo.hanoi.gov.vn";
const SECRET_HEADER = "X-Relay-Secret";

export default {
  async fetch(request, env) {
    if (request.headers.get(SECRET_HEADER) !== env.RELAY_SECRET) {
      return new Response("Forbidden", { status: 403 });
    }

    const url = new URL(request.url);
    const upstreamUrl = UPSTREAM + url.pathname + url.search;

    const headers = new Headers(request.headers);
    headers.delete(SECRET_HEADER);
    headers.delete("host");

    const upstreamResp = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
    });

    return new Response(upstreamResp.body, {
      status: upstreamResp.status,
      headers: upstreamResp.headers,
    });
  },
};
