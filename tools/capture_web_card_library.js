#!/usr/bin/env node
/** Capture full and focused web-config previews for every card fixture. */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const base = path.join(root, 'artifacts', 'card-library');
const fixture = JSON.parse(fs.readFileSync(path.join(base, 'fixture-layout.json'), 'utf8'));
const manifest = JSON.parse(fs.readFileSync(path.join(base, 'manifest.json'), 'utf8'));
const url = process.env.AURORA_BUILDER_URL || 'http://localhost:8766/builder';
const password = process.env.AURORA_PASSWORD || 'Admin';

(async () => {
  for (const dir of ['web-config', 'web-config-focused']) {
    const target = path.join(base, dir);
    fs.mkdirSync(target, {recursive: true});
    for (const name of fs.readdirSync(target)) {
      if (name.endsWith('.png')) fs.unlinkSync(path.join(target, name));
    }
  }
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 900}, deviceScaleFactor: 1});
  await page.goto(url, {waitUntil: 'networkidle'});
  if (await page.locator('#pw').count()) {
    await page.locator('#pw').fill(password);
    await Promise.all([
      page.waitForNavigation({waitUntil: 'networkidle'}),
      page.locator('#go').click(),
    ]);
  }
  await page.waitForTimeout(300);
  for (const record of manifest.records) {
    const name = String(record.index).padStart(4, '0') + '_' + record.ck + '_' + record.w + 'x' + record.h + '.png';
    await page.evaluate(({layout, key}) => {
      L = layout;
      curPage = key;
      curSub = 0;
      sel = null;
      renderAll();
    }, {layout: fixture, key: record.page});
    await page.waitForTimeout(30);
    await page.screenshot({path: path.join(base, 'web-config', name), fullPage: false});
    await page.locator('.pc[data-id="' + record.id + '"]').screenshot({
      path: path.join(base, 'web-config-focused', name),
    });
    console.log('[' + String(record.index).padStart(3, '0') + '/' + manifest.records.length + '] ' + name);
  }
  await browser.close();
})().catch(error => {
  console.error(error);
  process.exit(1);
});
