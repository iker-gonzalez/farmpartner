// Renders video.html to build/frames/*.png (30 fps, 1920×1080), frame by frame.
//   cd docs/video/build && npm i playwright-core && cd .. && node render.mjs [fromSec toSec | t1,t2,…]
import { createRequire } from 'module';
import { mkdirSync } from 'fs';
import { fileURLToPath, pathToFileURL } from 'url';
import path from 'path';
const here = path.dirname(fileURLToPath(import.meta.url));
const { chromium } = createRequire(path.join(here, 'build/'))('playwright-core');
const FPS = 30, DUR = 30;
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
await page.goto(pathToFileURL(path.join(here, 'video.html')).href);
await page.waitForFunction('window.ready === true');
await page.waitForLoadState('networkidle');
const arg = process.argv[2] || '';
if (arg.includes(',')) {                                  // stills for review
  mkdirSync(path.join(here, 'build/stills'), { recursive: true });
  for (const t of arg.split(',').map(Number)) {
    await page.evaluate(t => window.seek(t), t);
    await page.screenshot({ path: path.join(here, `build/stills/t${String(t).padStart(4, '0')}.jpg`), quality: 80 });
  }
} else {
  mkdirSync(path.join(here, 'build/frames'), { recursive: true });
  const from = Math.round((+process.argv[2] || 0) * FPS), to = Math.round((+process.argv[3] || DUR) * FPS);
  for (let f = from; f < to; f++) {
    await page.evaluate(t => window.seek(t), f / FPS);
    await page.screenshot({ path: path.join(here, `build/frames/f${String(f).padStart(4, '0')}.png`) });
  }
}
await browser.close();
