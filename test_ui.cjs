const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');

async function testServer(stateDirectory) {
  const python = process.env.SPOTIFY_TRANSFER_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const child = spawn(python, [path.join(__dirname, 'server.py'), '--port', '0', '--state-dir', stateDirectory], {windowsHide: true});
  try {
    const origin = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Test server startup timed out')), 15000);
      let output = '';
      child.once('error', error => {clearTimeout(timer); reject(error);});
      child.once('exit', code => {clearTimeout(timer); reject(new Error('Test server exited: ' + code));});
      child.stdout.on('data', data => {
        output += data;
        const match = output.match(/Spotify transfer tool: (http:\/\/127\.0\.0\.1:\d+)/);
        if (match) {clearTimeout(timer); resolve(match[1]);}
      });
      child.stderr.on('data', data => process.stderr.write(data));
    });
    return {child, origin};
  } catch (error) { child.kill(); throw error; }
}

(async () => {
  const tempRoot = fs.realpathSync(os.tmpdir());
  const stateDirectory = fs.mkdtempSync(path.join(tempRoot, 'spotify-transfer-test-'));
  const shots = path.join(__dirname, 'test-results');
  fs.mkdirSync(shots, {recursive: true});
  let browser, child;
  try {
    const server = await testServer(stateDirectory);
    child = server.child;
    browser = await chromium.launch({headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {})});
    const page = await browser.newPage({viewport: {width: 1280, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(server.origin);
    await page.getByRole('heading', {name: 'Spotify Account Transfer', exact: true}).waitFor();
    await page.waitForFunction(() => document.getElementById('message').textContent !== 'Connecting to local service...');
    assert.equal(await page.locator('#transfer').isDisabled(), true);
    assert.equal(await page.locator('#connect-source').isDisabled(), true);
    await page.locator('#client').fill('not-a-client-id');
    await page.locator('#save').click();
    await page.locator('#error').filter({hasText: '32-character'}).waitFor();
    await page.waitForTimeout(2300);
    assert.match(await page.locator('#error').textContent(), /32-character/);
    await page.reload();
    await page.waitForFunction(() => document.getElementById('message').textContent === 'Connect your accounts');
    await page.screenshot({path: path.join(shots, 'ui-desktop.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path: path.join(shots, 'ui-mobile.png'), fullPage: true});

    const status = {client_id: 'a'.repeat(32), csrf:'fake',busy:false,message:'Backup saved',error:'',result:null,
      accounts:{source:{id:'old123',name:'Old Account'},target:{id:'new456',name:'New Account'}},
      inventory:{source:{id:'old123'},created:'test',library:{tracks:1234,albums:48,artists:95},warnings:[],playlists:[
        {name:'Road trips',action:'copy',items:142}, {name:'<img src=x onerror=alert(1)>',action:'copy',items:20},
        {name:'Discover Weekly',action:'follow',items:0}]}};
    await page.route('**/api/status', route => route.fulfill({json: status}));
    let payload;
    await page.route('**/api/transfer', async route => { payload = route.request().postDataJSON(); await route.fulfill({json:{ok:true}}); });
    await page.reload();
    await page.locator('#source-name').filter({hasText:'Old Account'}).waitFor();
    assert.equal(await page.locator('#playlists img').count(), 0);
    assert.equal(await page.locator('#transfer').isDisabled(), true);
    await page.locator('#confirm').fill('new456');
    await page.locator('#transfer').click();
    assert.deepEqual(payload, {confirm_target:'new456',preserve_public:false});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({path:path.join(shots,'ui-preview-mobile.png'),fullPage:true});
    await page.setViewportSize({width:1280,height:1000});
    await page.screenshot({path:path.join(shots,'ui-preview-desktop.png'),fullPage:true});
    assert.deepEqual(errors, []);
    console.log('Browser checks passed: real local page, persistent validation errors, desktop/mobile overflow, escaped playlist names, destination confirmation, private default. No Spotify requests or account writes.');
  } finally {
    if (browser) await browser.close();
    if (child && child.exitCode === null) {
      const exited = once(child, 'exit');
      child.kill();
      await exited;
    }
    const cleanupPath = fs.realpathSync(stateDirectory);
    assert.equal(cleanupPath, stateDirectory);
    assert.equal(path.dirname(cleanupPath), tempRoot);
    assert.ok(path.basename(cleanupPath).startsWith('spotify-transfer-test-'));
    fs.rmSync(cleanupPath, {recursive: true, force: true});
  }
})().catch(error => {console.error(error);process.exitCode=1;});
