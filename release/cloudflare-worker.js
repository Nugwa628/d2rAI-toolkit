// cloudflare-worker.js: serve the download site from an S3-compatible bucket, through Cloudflare.
//
// Players' downloaders ask https://files.example.com/mymod/... . This Worker answers by fetching the same path from the
// bucket and handing the answer back, so the bucket can stay private-looking (no listing, no custom domain on the
// bucket) and Cloudflare's cache sits in front of it. See release/CDN.md for the whole setup.
//
// What it does:
//   - answers GET and HEAD only; everything else gets 405;
//   - serves only paths under SITE_PREFIX (for example "/mymod/") and refuses dot segments and encoded tricks;
//   - passes the Range and conditional (If-None-Match, If-Modified-Since) request headers through, for resumed
//     downloads and cache revalidation;
//   - lets Cloudflare cache every answer for as long as the bucket's own Cache-Control says (the sync tool sets
//     "immutable" on objects/ and "no-cache" on channels/), so channel files are always revalidated;
//   - turns the bucket's 403 (a missing key on a private-listing bucket) into 404, and drops its x-amz-* headers;
//   - holds no secrets: it only reads public objects. The bucket URL is a plain-text variable.
//
// Settings (Workers & Pages > your Worker > Settings > Variables, plain text, not secrets):
//   BUCKET_URL   the bucket's public endpoint without a trailing slash, for example
//                https://my-mod-files.s3.example.com   (virtual-host style) or https://s3.example.com/my-mod-files
//   SITE_PREFIX  the path the site is served under, with slashes at both ends, for example /mymod/
//
// Deploy: create a Worker, paste this file in, set the two variables, add the custom domain files.example.com to the Worker.

const FORWARD_REQUEST_HEADERS = ["range", "if-none-match", "if-modified-since", "accept-encoding"];
const DROP_RESPONSE_PREFIXES = ["x-amz-", "x-guploader", "server-timing"];
const DROP_RESPONSE_HEADERS = ["set-cookie", "server", "x-request-id"];

function notFound() {
  return new Response("Not found\n", { status: 404, headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" } });
}

// True when the path is safe to ask the bucket for: under the prefix, no empty, dot or encoded-dot segments.
function allowedPath(pathname, prefix) {
  if (!pathname.startsWith(prefix) || pathname.length === prefix.length) return false;
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch (e) {
    return false;
  }
  if (decoded.includes("\\") || decoded.includes("\0")) return false;
  const rest = decoded.slice(prefix.length);
  if (rest.endsWith("/")) return false; // folders have no listing
  return rest.split("/").every((seg) => seg !== "" && !seg.startsWith("."));
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed\n", { status: 405, headers: { allow: "GET, HEAD" } });
    }
    const bucket = (env.BUCKET_URL || "").replace(/\/+$/, "");
    const prefix = env.SITE_PREFIX || "/";
    if (!bucket) return new Response("BUCKET_URL is not set\n", { status: 500 });

    const url = new URL(request.url);
    if (!allowedPath(url.pathname, prefix)) return notFound();

    // The same path on the bucket. The query string is dropped: nothing on the site uses one.
    const upstream = new Request(bucket + url.pathname, { method: request.method, headers: new Headers() });
    for (const name of FORWARD_REQUEST_HEADERS) {
      const v = request.headers.get(name);
      if (v) upstream.headers.set(name, v);
    }

    // cacheEverything: cache objects of any type (the objects have no file extension). The edge keeps each answer for
    // as long as the bucket's Cache-Control allows, and revalidates "no-cache" ones on every request.
    const answer = await fetch(upstream, { cf: { cacheEverything: true } });

    if (answer.status === 403 || answer.status === 404) return notFound();

    const headers = new Headers(answer.headers);
    for (const name of [...headers.keys()]) {
      const lower = name.toLowerCase();
      if (DROP_RESPONSE_HEADERS.includes(lower) || DROP_RESPONSE_PREFIXES.some((p) => lower.startsWith(p))) headers.delete(name);
    }
    headers.set("accept-ranges", "bytes");
    headers.set("x-content-type-options", "nosniff");
    if (!headers.has("cache-control")) headers.set("cache-control", "public, max-age=3600");

    return new Response(request.method === "HEAD" ? null : answer.body, {
      status: answer.status, // 200, 206 for a Range request, 304 for a revalidation, 416 for a bad range
      statusText: answer.statusText,
      headers,
    });
  },
};
