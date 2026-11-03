import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { createRequire } from 'node:module';
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
  assert.deepEqual(imageManifest.images.domains, []);
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

// The hosted Vercel adapter consumes Next config and permits static-image imports.
test('Next image policy rejects public and remote sources with no generated media', async () => {
  const { images } = JSON.parse(await readFile(new URL('../.next/images-manifest.json', import.meta.url), 'utf8'));
  assert.deepEqual(images.remotePatterns, []);
  assert.deepEqual(images.domains, []);
  const require = createRequire(import.meta.url);
  const { makeRe } = require('next/dist/compiled/picomatch');
  const implicitPatterns = ['/_next/static/media/**', '/_next/static/immutable/media/**'];
  assert.deepEqual(images.localPatterns, implicitPatterns.map((pathname) => ({
    pathname: makeRe(pathname, { dot: true }).source, search: '',
  })));
  for (const pathname of ['/og.png', '/og-fischer.png', '/favicon.svg', '/data/manifest.json']) {
    assert.equal(images.localPatterns.some((p) => new RegExp(p.pathname).test(pathname)), false, pathname);
  }
  const staticFiles = await readdir(new URL('../.next/static/', import.meta.url), { recursive: true });
  assert.deepEqual(staticFiles.filter((p) => /^(?:immutable\/)?media(?:\/|$)/.test(p)), [],
    'Adding generated static media requires an explicit optimizer-policy review');
});

// npm audit may lag maintainer advisories. Guard the known fixed RSC release too.
test('React runtime packages meet the reviewed security patch baseline', async () => {
  for (const name of ['react', 'react-dom', 'react-server-dom-webpack']) {
    const { version } = JSON.parse(await readFile(new URL(`../node_modules/${name}/package.json`, import.meta.url), 'utf8'));
    const [major, minor, patch] = version.split('.').map(Number);
    assert.ok(major === 19 && minor === 2 && patch >= 9, `${name}: review security advisories before changing the release line`);
  }
});
