const {chromium}=require(process.env.BOOKANALYST_PLAYWRIGHT||'playwright');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    const base=process.env.BOOKANALYST_URL||'http://127.0.0.1:8766';
    const run=process.env.BOOKANALYST_RUN;
    await page.goto(base+(run?'/?run='+run:''),{waitUntil:'networkidle'});
    if(run){
      await page.getByRole('heading',{name:'内容对照',exact:true}).waitFor();
      await page.getByText('每批 2 页',{exact:false}).waitFor();
      await page.screenshot({path:'.bookanalyst/workflow-panel.png',fullPage:true});
    }
    await page.goto(base,{waitUntil:'networkidle'});
    await page.locator('[data-book="wang-2022-g-invariant-min-max"]').click();
    const form=page.locator('#run-form');
    if(await form.locator('[name="pages_per_task"]').inputValue()!=='2')throw Error('Expected two-page default');
    if(await form.locator('[name="review_mode"]').inputValue()!=='on_demand')throw Error('Expected on-demand review');
    if(await form.locator('[name="end_page"]').inputValue()!=='45')throw Error('Expected full Wang paper scope');
    if(await form.locator('[name="max_llm_requests"]').inputValue()!=='')throw Error('Expected no request cap');
    if(await form.locator('[name$="_context"], [name$="_output"]').count())throw Error('Token limit controls must be absent');
    await form.locator('[name="pages_per_task"]').fill('3');
    await form.locator('[name="review_mode"]').selectOption('all');
    await page.screenshot({path:'.bookanalyst/workflow-settings.png',fullPage:true});
    if(errors.length)throw Error(errors.join('\n'));
    console.log('Read-only browser checks passed: complete paper scope, page size, optional review, uncapped requests, live panel.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
