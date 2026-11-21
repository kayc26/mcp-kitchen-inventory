# auth-client: the vendored Neon Auth browser client

`/login` and `/consent` load `static/neon-auth-client.js`, a bundled copy of
[`@neon/auth`](https://www.npmjs.com/package/@neon/auth) that the server serves
itself. It's committed so a deploy never depends on a CDN or on dependency
versions resolved at load time. `package-lock.json` pins every version exactly
(`@neon/auth` 0.8.2, `better-auth` 1.7.27, ...).

Rebuild it (Node 18+), only when upgrading on purpose:

```bash
cd auth-client
npm ci              # installs exactly what package-lock.json pins
npm run build       # writes ../static/neon-auth-client.js
```

To upgrade, change the `@neon/auth` version in `package.json`, run
`npm install --save-exact`, then `npm run build`, and commit `package.json`,
`package-lock.json` and the new `static/neon-auth-client.js` together. Then sign
in again from the Claude iPhone app before deploying (see
`docs/agent-tasks/15-oauth-sign-in.md`).
