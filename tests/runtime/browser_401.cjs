// Read-only UI checks against an accepted, retained 4.0.1 upgrade fixture.
// Run inside Docker with the QEMU container's isolated network namespace.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const tls = require('node:tls');
const crypto = require('node:crypto');
const puppeteer = require('puppeteer-core');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const base = 'https://127.0.0.1:8444';

async function peer(expected) {
  return new Promise((resolve, reject) => {
    // Inspect only this local fixture, then require the exact certificate
    // fingerprint already authenticated by the SSH/HTTPS acceptance runner.
    const socket = tls.connect({host: '127.0.0.1', port: 8444, rejectUnauthorized: false}, () => {
      try {
        const raw = socket.getPeerCertificate(true).raw;
        assert.equal(hash(raw), expected, 'fixture TLS certificate changed');
        const spki = new crypto.X509Certificate(raw).publicKey.export({type: 'spki', format: 'der'});
        resolve(crypto.createHash('sha256').update(spki).digest('base64'));
      } catch (error) { reject(error); }
      finally { socket.end(); }
    });
    socket.setTimeout(8000, () => socket.destroy(new Error('fixture TLS inspection timed out')));
    socket.on('error', reject);
  });
}

async function main() {
  const [caseDirectory, parentEvidence, output] = process.argv.slice(2);
  assert(caseDirectory && parentEvidence && output, 'provide case, parent evidence and new private output directories');
  process.umask(0o077);
  await fs.mkdir(output, {mode: 0o700});
  const parentBytes = await fs.readFile(path.join(parentEvidence, 'result.json'));
  const parent = JSON.parse(parentBytes);
  const credentials = JSON.parse(await fs.readFile(path.join(caseDirectory, 'credentials.json')));
  assert.equal(parent.status, 'passed', 'use a completed accepted upgrade fixture');
  const result = {status: 'running', parent_result_sha256: hash(parentBytes),
    runner_sha256: hash(await fs.readFile(__filename)), node: process.version,
    puppeteer: require('puppeteer-core/package.json').version,
    scope: 'Docker Chromium rendering and JavaScript checks; no configuration saves',
    pages: [], page_errors: [], local_asset_failures: []};
  let browser;
  let profile;
  let phase = 'certificate';
  try {
    const spki = await peer(parent.served_certificate_sha256);
    result.certificate_sha256 = parent.served_certificate_sha256;
    result.fixture_spki_sha256 = spki;
    profile = await fs.mkdtemp(path.join(os.tmpdir(), 'boltwrt-browser-401-'));
    browser = await puppeteer.launch({executablePath: '/usr/bin/chromium', headless: true,
      acceptInsecureCerts: false, userDataDir: profile,
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking',
        '--no-first-run', '--lang=en-US', '--ignore-certificate-errors-spki-list=' + spki]});
    result.chromium = await browser.version();
    const page = await browser.newPage();
    await page.setViewport({width: 1360, height: 1000, deviceScaleFactor: 1});
    page.setDefaultTimeout(45000);
    page.on('pageerror', error => result.page_errors.push({phase, error: String(error)}));
    page.on('response', response => {
      if (response.url().startsWith(base + '/luci-static/') && response.status() >= 400)
        result.local_asset_failures.push({phase, url: response.url(), status: response.status()});
    });
    await page.setRequestInterception(true);
    page.on('request', request => {
      const url = request.url();
      if (url.startsWith(base + '/') || url.startsWith('data:') || url.startsWith('blob:'))
        void request.continue();
      else void request.abort();
    });
    phase = 'login';
    await page.goto(base + '/cgi-bin/luci/', {waitUntil: 'domcontentloaded', timeout: 60000});
    await page.waitForSelector('[name="luci_password"]');
    await page.$eval('[name="luci_username"]', (element, value) => { element.value = value; }, credentials.username);
    await page.type('[name="luci_password"]', credentials.password);
    await Promise.all([page.waitForNavigation({waitUntil: 'domcontentloaded', timeout: 60000}),
      page.click('input[type="submit"], button[type="submit"]')]);
    assert.equal(await page.$('[name="luci_password"]'), null, 'browser login failed');
    const service = '/cgi-bin/luci/admin/services/passwall2/';
    const pages = [
      ['overview', '/cgi-bin/luci/admin/status/overview'],
      ['settings', service + 'settings'], ['app-update', service + 'app_update'],
      ['native-hysteria2', service + 'node_config/bw401_hysteria'],
      ['native-ss-libev', service + 'node_config/bw401_ss'],
      ['migrated-xray-tls', service + 'node_config/bw401_xray_tls'],
      ['migrated-server', service + 'server_config/bw401_vless'],
      ['shunt-rule-node', service + 'node_config/rulenode'],
    ];
    for (const [label, route] of pages) {
      phase = label;
      const response = await page.goto(base + route, {waitUntil: 'domcontentloaded', timeout: 60000});
      assert.equal(response.status(), 200, label + ' did not return HTTP 200');
      await page.waitForSelector('#maincontent', {visible: true});
      await page.waitForNetworkIdle({idleTime: 500, timeout: 8000}).catch(() => {});
      const rendered = await page.evaluate(() => {
        const content = document.querySelector('#maincontent');
        const rectangle = content.getBoundingClientRect();
        return {text_length: content.innerText.trim().length, width: rectangle.width, height: rectangle.height,
          forms: content.querySelectorAll('form').length, controls: content.querySelectorAll('input,select,button').length,
          argon_stylesheets: [...document.styleSheets].filter(sheet => (sheet.href || '').includes('/luci-static/argon/')).length};
      });
      assert(rendered.text_length > 20 && rendered.width > 200 && rendered.height > 20, label + ' rendered empty content');
      assert(rendered.argon_stylesheets > 0, 'Argon stylesheet missing');
      if (label === 'overview') assert((await page.$eval('body', element => element.innerText)).includes('4.0.1'));
      if (['overview', 'app-update', 'native-hysteria2'].includes(label))
        await page.screenshot({path: path.join(output, label + '.png'), fullPage: true});
      result.pages.push({label, route, ...rendered});
      await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(result, null, 2) + '\n');
    }
    assert.equal(result.page_errors.length, 0, 'uncaught page JavaScript errors');
    assert.equal(result.local_asset_failures.length, 0, 'local UI assets failed to load');
    result.status = 'passed';
    console.log('Chromium: root login and ' + result.pages.length + ' Argon/PassWall2 pages passed');
  } catch (error) {
    result.status = 'failed'; result.phase = phase; result.error = String(error);
    console.error('Chromium check failed at ' + phase + ': ' + error.name);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    if (profile) await fs.rm(profile, {recursive: true, force: true});
    await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(result, null, 2) + '\n');
  }
}
main().catch(error => { console.error('Browser setup failed: ' + error.name); process.exitCode = 1; });
