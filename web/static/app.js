import { formatTTL } from './ttl.js';

const button = document.getElementById('verify');
const status = document.getElementById('status');
const results = document.getElementById('results');
button.addEventListener('click', async () => {
  button.disabled = true;
  results.hidden = true;
  status.textContent = 'Verifying workload identity and encrypted access…';
  try {
    const response = await fetch('/api/demo', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error('Verification unavailable');
    const data = await response.json();
    if (data.verified !== true || data.tokenRevoked !== true) throw new Error('Verification incomplete');
    document.getElementById('algorithm').textContent = data.algorithm;
    document.getElementById('ttl').textContent = formatTTL(data.tokenTTLSeconds);
    results.hidden = false;
    status.textContent = 'Verified. The workload authenticated, completed encryption and revoked its token.';
  } catch {
    status.textContent = 'Verification unavailable. Check that the vault is unsealed and try again.';
  } finally {
    button.disabled = false;
  }
});
