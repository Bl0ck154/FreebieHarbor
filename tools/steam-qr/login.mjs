#!/usr/bin/env node
/** One-shot SteamClient QR login for one verified account; never print tokens. */
import fs from 'node:fs';
import process from 'node:process';
import { LoginSession, EAuthTokenPlatformType } from 'steam-session';
import QRCode from 'qrcode';

function option(name) {
  const index = process.argv.indexOf(name);
  return index < 0 ? null : process.argv[index + 1];
}
const expectedId = option('--expected-id');
const expectedLogin = option('--expected-login');
const outputPath = option('--output');
if (!/^7656119[0-9]{10}$/.test(expectedId || '') ||
    !/^[A-Za-z0-9_]{3,64}$/.test(expectedLogin || '') ||
    !outputPath || !outputPath.startsWith('/')) {
  console.error('Invalid verified account parameters.');
  process.exit(2);
}
const session = new LoginSession(EAuthTokenPlatformType.SteamClient, {
  machineId: true,
  machineFriendlyName: 'FreebieHarbor-Friends',
});
session.loginTimeout = 120000;
let finished = false;
const finish = (status, message) => {
  if (finished) return;
  finished = true;
  console.log(message);
  process.exit(status);
};
session.on('authenticated', () => {
  try {
    const actualId = session.steamID?.getSteamID64();
    const actualLogin = session.accountName;
    if (actualId !== expectedId ||
        actualLogin?.toLowerCase() !== expectedLogin.toLowerCase()) {
      finish(3, 'Steam account mismatch. No session has been stored.');
      return;
    }
    if (typeof session.refreshToken !== 'string' || session.refreshToken.length < 80) {
      finish(4, 'Steam did not provide a reusable SteamClient session.');
      return;
    }
    // Python created a private 0700 temporary directory. This file is 0600,
    // is never printed and is only consumed by the local admin handoff.
    fs.writeFileSync(outputPath, session.refreshToken, {
      encoding: 'utf8', flag: 'wx', mode: 0o600,
    });
    finish(0, 'Steam identity verified. Private handoff prepared.');
  } catch {
    finish(5, 'Unable to store the verified Steam session.');
  }
});
session.on('timeout', () => finish(6, 'Steam QR approval timed out. No session stored.'));
session.on('error', () => finish(7, 'Steam login failed. No session stored.'));
try {
  const attempt = await session.startWithQR();
  console.log('\nOpen Steam Mobile → Steam Guard → Scan QR code, then approve.');
  console.log('Only approve if the intended Steam account is selected.');
  console.log(await QRCode.toString(attempt.qrChallengeUrl, { type: 'terminal' }));
} catch {
  finish(8, 'Unable to start Steam QR login.');
}
