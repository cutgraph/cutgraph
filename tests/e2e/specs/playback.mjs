import assert from 'node:assert/strict';
import { browserSpec, cut, document as videoDocument, fixture, layout, story, undo } from '../browser.mjs';

async function setSeek(page, frame) {
  await page.getByRole('slider', { name: 'Seek' }).evaluate((element, frame) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(element, String(frame));
    element.dispatchEvent(new Event('input', { bubbles: true }));
  }, frame);
}

async function seek(page, frame, sourceFrame = frame) {
  await setSeek(page, frame);
  await page.waitForFunction(({ frame, sourceFrame }) => {
    const slider = document.querySelector('[aria-label="Seek"]');
    const video = [...document.querySelectorAll('.frame video')]
      .find((element) => getComputedStyle(element).visibility === 'visible');
    return Number(slider.value) === frame && video && !video.seeking && video.readyState >= 2
      && Math.abs(video.currentTime - sourceFrame / 30) < 0.001;
  }, { frame, sourceFrame });
}

async function picture(page) {
  return page.evaluate(() => {
    const video = [...document.querySelectorAll('.frame video')]
      .find((element) => getComputedStyle(element).visibility === 'visible');
    const canvas = document.createElement('canvas');
    canvas.width = 64;
    canvas.height = 36;
    const context = canvas.getContext('2d');
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return [...context.getImageData(0, 0, canvas.width, canvas.height).data];
  });
}

await browserSpec(async (page) => {
  await story(page);
  await seek(page, 15);
  const before = await picture(page);
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="Seek"]').value) >= 25);
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  assert.notDeepEqual(await picture(page), before, 'Playback advances the decoded picture');
  assert.ok(Number(await page.getByRole('slider', { name: 'Seek' }).inputValue()) >= 25, 'Playback advances the playhead');

  await seek(page, 17);
  assert.equal(await page.locator('.caption-word-active').innerText(), 'Twinkle,', 'Seek selects the exact caption word');
  assert.equal(await page.getByRole('slider', { name: 'Seek' }).inputValue(), '17', 'Seek lands on the exact frame');

  await cut(page);
  try {
    const clips = (await videoDocument(page)).project.tracks.find((track) => track.kind === 'speech').clips;
    assert.equal(clips.length, 2, 'Removing a word splits the speech');
    const next = clips[1];
    const removedStart = clips[0].srcOutF;
    assert.ok(next.srcInF > removedStart, 'The cut removes source frames');
    await seek(page, next.tlStartF - 1);
    const preCut = await picture(page);
    await page.evaluate(({ removedStart, removedEnd }) => {
      window.cutSamples = [];
      for (const video of document.querySelectorAll('.frame video')) {
        const sample = (_now, metadata) => {
          if (getComputedStyle(video).visibility === 'visible') {
            window.cutSamples.push({ time: metadata.mediaTime, removedStart, removedEnd,
              caption: document.querySelector('.caption-word-active')?.textContent });
          }
          video.requestVideoFrameCallback(sample);
        };
        video.requestVideoFrameCallback(sample);
      }
    }, { removedStart: removedStart / 30, removedEnd: next.srcInF / 30 });
    await page.getByRole('button', { name: 'Play', exact: true }).click();
    await page.waitForFunction((boundary) => Number(document.querySelector('[aria-label="Seek"]').value) >= boundary + 3, next.tlStartF);
    // The seek across the cut presents its first frame a decode later than the playhead moves.
    await page.waitForFunction(() => window.cutSamples.some((sample) => sample.time >= sample.removedEnd));
    await page.getByRole('button', { name: 'Pause', exact: true }).click();
    const samples = await page.evaluate(() => window.cutSamples);
    assert.ok(samples.some((sample) => sample.time >= sample.removedEnd), 'Playback jumps to the source after the cut');
    assert.ok(samples.every((sample) => sample.time < sample.removedStart || sample.time >= sample.removedEnd), 'No decoded cut frames are displayed');
    assert.ok(samples.every((sample) => sample.caption !== 'I'), 'The cut word is never highlighted');
    assert.notDeepEqual(await picture(page), preCut, 'The picture jumps across the cut');
    await seek(page, next.tlStartF + 1, next.srcInF + 1);
    assert.equal(await page.locator('.caption-word-active').innerText(), 'wonder', 'Captions use the post-cut source word');
  } finally {
    await undo(page);
  }
  await layout(page);

  await story(page, fixture.script);
  await setSeek(page, 30);
  const canvas = page.locator('canvas[data-scene-layer]').first();
  await canvas.waitFor({ state: 'visible' });
  await page.waitForFunction(() => {
    const canvas = document.querySelector('canvas[data-scene-layer]');
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    return pixels.some((value, index) => index % 4 === 3 && value > 0);
  });
  const firstSceneFrame = await canvas.evaluate((element) => element.toDataURL());
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.waitForFunction(() => Number(document.querySelector('[aria-label="Seek"]').value) >= 45);
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  assert.notEqual(await canvas.evaluate((element) => element.toDataURL()), firstSceneFrame, 'The scene layer animates with the player clock');
  await layout(page);
});
