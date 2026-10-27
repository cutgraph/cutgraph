import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { base, browserSpec, fixture, layout, state, story } from '../browser.mjs';

const write = `${base}/api/workspaces`;
const upload = `${base}/api/workspaces/${fixture.workspace}/assets`;
const editor = `${base}/editor/`;
const payload = Buffer.from('synthetic upload attempt');
const assetId = createHash('sha256').update(payload).digest('hex').slice(0, 16);
const server = createServer((_request, response) => {
  response.writeHead(200, { 'Content-Type': 'text/html' });
  response.end(`<!doctype html><meta name="viewport" content="width=device-width"><title>Attack fixture</title>
    <p>Cross-origin browser requests</p>
    <iframe name="upload-result" style="width:100%;box-sizing:border-box"></iframe>
    <iframe id="editor-frame" src="${editor}" style="width:100%;box-sizing:border-box"></iframe>
    <form method="POST" enctype="multipart/form-data" action="${upload}" target="upload-result">
      <input type="file" name="files" hidden>
    </form>
    <script>
      fetch(${JSON.stringify(write)}, {mode:'no-cors', method:'POST', body:'text'})
        .then(() => { document.body.dataset.fetchDone = 'resolved'; },
          () => { document.body.dataset.fetchDone = 'blocked'; });
      const transfer = new DataTransfer();
      transfer.items.add(new File(['synthetic upload attempt'], 'attempt.wav', {type:'audio/wav'}));
      document.querySelector('input').files = transfer.files;
      document.querySelector('form').submit();
    </script>`);
});
await new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', resolve);
});
try {
  const attackOrigin = `http://127.0.0.1:${server.address().port}`;
  await browserSpec(async (page) => {
    const network = await page.context().newCDPSession(page);
    await network.send('Network.enable');
    // Chromium omits Playwright response events for documents blocked by framing policy, and may send a
    // request's ExtraInfo status before or after the request itself, so the two are paired by id.
    const urls = new Map();
    const statuses = new Map();
    const failures = new Map();
    const waiters = new Set();
    const settle = (requestId) => {
      const url = urls.get(requestId);
      if (url === undefined || !statuses.has(requestId)) return;
      for (const waiter of waiters) {
        if (waiter.url === url) waiter.resolve(statuses.get(requestId));
      }
    };
    network.on('Network.requestWillBeSent', (event) => {
      urls.set(event.requestId, event.request.url);
      settle(event.requestId);
    });
    network.on('Network.responseReceivedExtraInfo', (event) => {
      statuses.set(event.requestId, event.statusCode);
      settle(event.requestId);
    });
    network.on('Network.loadingFailed', (event) => failures.set(event.requestId, event));
    const responseStatus = (url) => new Promise((resolve, reject) => {
      const waiter = {
        url,
        resolve: (status) => {
          clearTimeout(timeout);
          waiters.delete(waiter);
          resolve(status);
        },
      };
      const timeout = setTimeout(() => {
        waiters.delete(waiter);
        const seen = [...urls].filter(([, seenUrl]) => seenUrl === url).map(([id]) => failures.get(id));
        reject(new Error(`No network status for ${url}; requests seen: ${seen.length}; failures: ${JSON.stringify(
          seen.filter(Boolean).map(({ errorText, blockedReason, corsErrorStatus }) => ({ errorText, blockedReason, corsErrorStatus })))}`));
      }, 15000);
      waiters.add(waiter);
    });
    await story(page);
    const snapshot = async () => ({
      workspaces: await readdir(path.join(state, 'workspaces')),
      workspace: await readFile(path.join(state, 'workspaces', fixture.workspace, 'workspace.json'), 'utf8'),
      library: await readdir(path.join(state, 'workspaces', fixture.workspace, 'library')).catch((error) => {
        if (error.code === 'ENOENT') return [];
        throw error;
      }),
    });
    const before = await snapshot();
    const blockedFrames = [];
    page.on('console', (message) => {
      if (message.type() === 'error' && /frame-ancestors/.test(message.text())) {
        blockedFrames.push(message.text());
      }
    });
    const responses = await Promise.all([
      ...[write, upload, editor].map(responseStatus),
      page.goto(attackOrigin),
    ]);
    for (const [index, status] of responses.slice(0, 3).entries()) {
      assert.equal(status, 403, `Rejected ${[write, upload, editor][index]}`);
    }
    await page.waitForSelector('body[data-fetch-done="blocked"]');
    await layout(page);
    assert.deepEqual(await snapshot(), before, 'Attacks persisted no workspace or upload');
    assert.ok(!(await snapshot()).library.includes(`${assetId}.wav`), 'Upload file does not exist');
    assert.ok(blockedFrames.length > 0, 'Iframe blocked by frame-ancestors');
    assert.ok(!page.frames().some((frame) => frame.url() === editor), 'Editor document did not load in the iframe');
    await story(page);
    const [embedded] = await Promise.all([
      responseStatus(editor),
      page.waitForEvent('console', { predicate: (message) => /frame-ancestors/.test(message.text()) }),
      page.evaluate(() => {
        const iframe = document.createElement('iframe');
        iframe.id = 'same-origin-frame';
        iframe.src = '/editor/';
        document.body.append(iframe);
      }),
    ]);
    assert.equal(embedded, 200, 'Same-origin iframe passes request isolation');
    await page.waitForFunction(() => {
      const iframe = document.querySelector('#same-origin-frame');
      return iframe.contentDocument === null;
    });
    assert.ok(!page.frames().some((frame) => frame.parentFrame() && frame.url() === editor), 'Same-origin editor document did not load');
    await story(page);
  });
} finally {
  server.closeAllConnections();
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}
