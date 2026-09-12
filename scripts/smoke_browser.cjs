// Run only against a disposable installation; this creates CRM records.
const { chromium, expect } = require('@playwright/test');
(async () => {
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const base = process.env.SMOKE_URL || 'http://localhost:18765';
    await page.goto(base + '/login');
    if (process.env.SMOKE_PASSWORD) {
      await page.getByLabel('Email', {exact:true}).fill(process.env.SMOKE_EMAIL || 'admin@example.test');
      await page.getByLabel('Password', {exact:true}).fill(process.env.SMOKE_PASSWORD);
      await page.getByRole('button', {name:'Sign in', exact:false}).click();
    } else {
      await page.getByRole('button', {name:/Continue as/}).click();
    }
    await expect(page).toHaveURL(/\/deals$/);
    await page.goto(base + '/new/deal');
    const title = 'Compose smoke ' + Date.now();
    await page.getByLabel('Title', {exact:true}).fill(title);
    await page.getByRole('button', {name:'Create deal', exact:true}).click();
    await expect(page.getByRole('link', {name:'Edit Deal name: ' + title, exact:true})).toBeVisible();
    await page.getByRole('link', {name:'Edit Deal name: ' + title, exact:true}).click();
    await page.getByLabel('Title', {exact:true}).fill(title + ' updated');
    await page.getByRole('button', {name:'Save changes', exact:true}).click();
    await expect(page.getByRole('link', {name:'Edit Deal name: ' + title + ' updated', exact:true})).toBeVisible();
    await page.getByRole('link', {name:'Mark won', exact:false}).click();
    await expect(page.getByRole('dialog', {name:'Record editor'})).toBeVisible();
    await expect(page.getByLabel('Outcome', {exact:true})).toHaveValue('won');
    await page.getByRole('button', {name:'Save changes', exact:true}).click();
    await expect(page.getByRole('link', {name:/Reopen deal/})).toBeVisible();
    await page.goto(base + '/directory/contact');
    await page.getByRole('link', {name:'＋ Contact', exact:true}).click();
    await page.getByLabel('Name', {exact:true}).fill('Example Person');
    await page.getByLabel('Email', {exact:true}).fill('person@example.test');
    await page.getByRole('button', {name:'Create contact', exact:true}).click();
    await expect(page.getByRole('heading', {name:'In good company.'})).toBeVisible();
    await page.setViewportSize({width:390,height:844});
    await expect(page.getByRole('link', {name:/Edit Name: Example Person/})).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    expect(errors).toEqual([]);
    console.log('Browser smoke passed: login, create, inline edit, won, contact drawer, mobile profile.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
