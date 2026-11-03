import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import next from 'next';

// Exercise the real production Next request handler, on loopback only.
// No malformed images or exploit payloads are sent.
test('Vercel/Next target disables the unused optimizer and preserves public assets', async (t) => {
  const directory = fileURLToPath(new URL('..', import.meta.url));
  const imageManifest = JSON.parse(await readFile(new URL('../.next/images-manifest.json', import.meta.url), 'utf8'));
  assert.equal(imageManifest.images.unoptimized, true);
  assert.deepEqual(imageManifest.images.remotePatterns, []);
  const app = next({ dev: false, dir: directory, quiet: true });
  await app.prepare();
  const server = createServer(app.getRequestHandler());
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
    await app.close();
  });
  const base = `http://127.0.0.1:${server.address().port}`;

  const optimizer = await fetch(`${base}/_next/image?url=%2Fog.png&w=640&q=75`);
  assert.equal(optimizer.status, 404, 'The unused Next optimizer must not process images');

  for (const [pathname, expectedType] of [
    ['/og.png', 'image/png'],
    ['/og-fischer.png', 'image/png'],
    ['/favicon.svg', 'image/svg+xml'],
    ['/fonts/geist-latin.woff2', 'font/woff2'],
    ['/media/aurora-portfolio-walkthrough-297271fb.wav', 'audio/wav'],
    ['/data/manifest.json', 'application/json'],
  ]) {
    const response = await fetch(`${base}${pathname}`);
    assert.equal(response.status, 200, pathname);
    assert.ok(response.headers.get('content-type')?.startsWith(expectedType), pathname);
    const body = Buffer.from(await response.arrayBuffer());
    assert.deepEqual(body, await readFile(new URL(`../public${pathname}`, import.meta.url)), pathname);
  }

  for (const pathname of ['/', '/demo']) {
    const response = await fetch(`${base}${pathname}`);
    assert.equal(response.status, 200, pathname);
    assert.match(response.headers.get('content-security-policy') ?? '', /default-src 'self'/);
    const body = await response.text();
    assert.match(body, pathname === '/' ? /When the specialist fails/ : /Aurora Run Explorer/);
  }
  const deepLink = await fetch(`${base}/?run=worker-fault-with-fallback`, { redirect: 'manual' });
  assert.equal(deepLink.status, 307);
  assert.match(deepLink.headers.get('location') ?? '', /^\/demo\?run=worker-fault-with-fallback$/);
});

// Vercel's image pipeline is separate from the local Next request handler.
// Keep an explicit platform rule and verify it against a hosted preview at release.
test('Vercel routing blocks only the unused image optimizer', async () => {
  const config = JSON.parse(await readFile(new URL('../vercel.json', import.meta.url), 'utf8'));
  const rule = config.routes[0];
  assert.equal(rule.status, 404);
  assert.notEqual(rule.continue, true);
  assert.equal(rule.headers['Cache-Control'], 'no-store');
  assert.equal(rule.methods, undefined, 'The rule must cover every HTTP method');
  const match = new RegExp(rule.src);
  for (const pathname of ['/_next/image', '/_next/image/', '/_next/image/unused']) {
    assert.ok(match.test(pathname), pathname);
  }
  for (const pathname of ['/', '/demo', '/og.png', '/_next/static/chunks/app.js', '/_next/image-other']) {
    assert.equal(match.test(pathname), false, pathname);
  }
});
