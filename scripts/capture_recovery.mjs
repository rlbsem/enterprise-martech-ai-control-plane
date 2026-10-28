// Genuine Chromium captures of the source-bound, read-only operator report.
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import puppeteer from 'puppeteer';

const [reportArg, recordArg, outputArg] = process.argv.slice(2);
if (!reportArg || !recordArg || !outputArg) throw Error('report, record and fresh output directory required');
const report = await readFile(resolve(reportArg));
const record = await readFile(resolve(recordArg));
const output = resolve(outputArg);
await mkdir(output); // existing captures must never be overwritten
const sha = (raw) => createHash('sha256').update(raw).digest('hex');
const server = createServer((req, res) => {
  if (req.method !== 'GET') { res.writeHead(405).end(); return; }
  if (req.url !== '/') { res.writeHead(404).end(); return; }
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(report);
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
let browser;
try {
  browser = await puppeteer.launch({ headless: true,
    ...(process.env.PUPPETEER_EXECUTABLE_PATH ? { executablePath: process.env.PUPPETEER_EXECUTABLE_PATH } : {}) });
  const base = `http://127.0.0.1:${server.address().port}/`;
  const observations = [];
  for (const [name, width, height] of [['desktop', 1440, 1120], ['mobile', 390, 1000]]) {
    const page = await browser.newPage();
    await page.setViewport({ width, height, deviceScaleFactor: 1 });
    const external = [], errors = [];
    page.on('request', (r) => { if (!r.url().startsWith(base)) external.push(r.url()); });
    page.on('pageerror', (e) => errors.push(e.message));
    const response = await page.goto(base, { waitUntil: 'networkidle0' });
    const observation = await page.evaluate(() => ({
      title: document.title,
      articles: document.querySelectorAll('article').length,
      details: document.querySelectorAll('details').length,
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
      mainLandmarks: document.querySelectorAll('main').length,
      headings: document.querySelectorAll('h1').length,
      text: document.body.innerText,
    }));
    if (response.status() !== 200 || observation.articles !== 8 || observation.details !== 8
      || observation.horizontalOverflow || observation.mainLandmarks !== 1 || observation.headings !== 1
      || !observation.text.includes('Synthetic records only') || !observation.text.includes('Recorded local')
      || errors.length || external.length) throw Error('Rendered report contract failed');
    await page.click('article:first-child summary');
    if (!await page.$eval('details', (el) => el.open)) throw Error('Evidence details did not open');
    if (!await page.$eval('details pre', (el) => el.textContent.includes('remote_ahead_missing_intent')))
      throw Error('Actual negative evidence missing');
    await page.click('article:first-child summary');
    await page.keyboard.press('Tab');
    if (!await page.evaluate(() => document.activeElement.tagName === 'SUMMARY'))
      throw Error('Keyboard evidence access failed');
    await page.screenshot({ path: resolve(output, name + '.png'), fullPage: true });
    const raw = await readFile(resolve(output, name + '.png'));
    delete observation.text;
    observations.push({ viewport: { width, height }, ...observation, status: 'passed',
      evidenceDetailsOpened: true, keyboardSummaryAccess: true, externalRequests: external.length,
      pageErrors: errors, screenshot: name + '.png', screenshot_sha256: sha(raw) });
    await page.close();
  }
  const provenance = { schema: 'recovery-browser-proof.v1', observed_at: new Date().toISOString(),
    browser: await browser.version(), renderer: 'actual Chromium over loopback HTTP; no image editing',
    source_report: reportArg.replaceAll('\\', '/'), source_report_sha256: sha(report),
    source_record: recordArg.replaceAll('\\', '/'), source_record_sha256: sha(record),
    capture_script_sha256: sha(await readFile(new URL(import.meta.url))), observations,
    boundary: 'Recorded local recovery evidence; not a live AWS application or new cloud execution' };
  await writeFile(resolve(output, 'provenance.json'), JSON.stringify(provenance, null, 2) + '\n');
  console.log(JSON.stringify({ status: 'passed', captures: observations.length, browser: provenance.browser }));
} finally {
  if (browser) await browser.close();
  await new Promise((done) => server.close(done));
}
