// Generates local, deterministic REFERENCE | CURRENT c5fca87 | V1.1 proof sheets.
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'C:/Users/tanke/Desktop/NoemaDiz/node_modules/playwright');
const path=require('path');
const {pathToFileURL}=require('url');

(async()=>{
 const root=path.resolve('ui-proof','design-match-v1.1');
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1200,height:820},deviceScaleFactor:1});
 await page.goto(pathToFileURL(path.join(root,'comparison.html')).href);
 await page.waitForFunction(()=>[...document.images].every(image=>image.complete&&image.naturalWidth));
 for(const screen of ['home','tasks','notes','archive','people','budget','settings']){
  await page.locator('[data-screen="'+screen+'"]').screenshot({path:path.join(root,'comparison-'+screen+'.png')});
 }
 await browser.close();
 console.log('Design Match V1.1 proof sheets generated');
})().catch(error=>{console.error(error);process.exitCode=1});
