const { test } = require('playwright/test');
const fs = require('fs');
const path = require('path');

test('capture frontend pages', async ({ browser, request }) => {
  const sessionId = `sess_shot_${Math.random().toString(16).slice(2, 12)}`;

  const loginResp = await request.post('http://127.0.0.1:8000/api/auth/login', {
    headers: {
      'x-session-id': sessionId,
      'content-type': 'application/json',
    },
    data: {
      username: 'demo',
      password: 'demo123456',
    },
  });

  if (!loginResp.ok()) {
    throw new Error(`login failed: ${loginResp.status()} ${await loginResp.text()}`);
  }

  const login = await loginResp.json();
  const authState = {
    sessionId: login.sessionId,
    accessToken: login.token,
    refreshToken: null,
    mode: 'custom',
    updatedAt: new Date().toISOString(),
    userName: login?.user?.username || 'demo',
    expiresAt: login.expiresAt || null,
  };

  const outDir = path.resolve('screenshots', `pages-${new Date().toISOString().replace(/[:.]/g, '-')}`);
  fs.mkdirSync(outDir, { recursive: true });

  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript((payload) => {
    window.localStorage.setItem('career_hero.client_auth.v3', JSON.stringify(payload));
  }, authState);

  const targets = [
    { route: '/', name: '01-home.png' },
    { route: '/resumes', name: '02-resumes.png' },
    { route: '/rag', name: '03-rag.png' },
    { route: '/interview', name: '04-interview.png' },
    { route: '/interview/summary', name: '05-interview-summary.png' },
    { route: '/login', name: '06-login.png' },
  ];

  for (const item of targets) {
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:3000${item.route}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(outDir, item.name), fullPage: true });
    await page.close();
  }

  console.log(`SCREENSHOT_DIR=${outDir}`);
  await context.close();
});
