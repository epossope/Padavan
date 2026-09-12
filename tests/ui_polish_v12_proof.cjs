// Generates deterministic BEFORE | AFTER sheets from the local visual fixture screenshots.
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
const path=require('path');
const {pathToFileURL}=require('url');

(async()=>{
 const root=path.resolve('ui-proof','ui-polish-v1.2');
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:900,height:1000},deviceScaleFactor:1});
 await page.goto(pathToFileURL(path.join(root,'comparison.html')).href);
 await page.waitForFunction(()=>[...document.images].every(image=>image.complete&&image.naturalWidth));
 for(const screen of ['chat','archive','budget','notes','people']){
  await page.locator(`[data-screen="${screen}"]`).screenshot({path:path.join(root,`comparison-${screen}.png`)});
 }
 await browser.close();
 console.log('Mini App UI Polish V1.2 proof sheets generated');
})().catch(error=>{console.error(error);process.exitCode=1});
