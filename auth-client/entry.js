// The only export the pages use. Bundled by `npm run build` into
// static/neon-auth-client.js so the server serves a fixed copy, not a CDN URL.
export { createAuthClient } from "@neon/auth";
