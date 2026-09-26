// Capture des maquettes pour l'aperçu dans l'email : node screenshot.js '<json [[html, png], ...]>'
const { chromium } = require('playwright');
(async () => {
  const jobs = JSON.parse(process.argv[2]);
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 390, height: 720 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  for (const [html, png] of jobs) {
    try {
      await page.goto('file://' + html, { waitUntil: 'load', timeout: 15000 });
      // Le bandeau "maquette de démonstration" n'a pas sa place dans l'aperçu
      await page.evaluate(() => { document.querySelectorAll('.banner, .wa').forEach(el => el.remove()); });
      await page.screenshot({ path: png, type: 'jpeg', quality: 82 });
      console.log('ok ' + png);
    } catch (e) {
      console.log('ko ' + png + ' ' + e.message);
    }
  }
  await browser.close();
})();
