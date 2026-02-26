const fs = require('fs');
const path = require('path');

async function main() {
  const baseUrl = 'http://127.0.0.1:3000';
  const apiUrl = 'http://127.0.0.1:8000/api/auth/login';

  const sessionId = `sess_shot_${Math.random().toString(16).slice(2, 12)}`;
  const loginRes = await fetch(apiUrl, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-session-id': sessionId,
    },
    body: JSON.stringify({ username: 'demo', password: 'demo123456' }),
  });

  if (!loginRes.ok) {
    const txt = await loginRes.text();
    throw new Error(`login failed ${loginRes.status}: ${txt}`);
  }

  const login = await loginRes.json();
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

  const { chromium } = require('playwright');
  const browser = await chromium.launch({ headless: true });
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

  const outputs = [];
  for (const item of targets) {
    const page = await context.newPage();
    const url = `${baseUrl}${item.route}`;
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(1500);
    const filePath = path.join(outDir, item.name);
    await page.screenshot({ path: filePath, fullPage: true });
    outputs.push({ route: item.route, file: filePath });
    await page.close();
  }

  await browser.close();

  console.log(JSON.stringify({ outDir, outputs }, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
