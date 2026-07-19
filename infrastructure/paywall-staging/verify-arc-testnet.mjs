#!/usr/bin/env node

const rpcUrl = process.env.ARC_RPC_URL ?? 'https://rpc.testnet.arc.network';
const chainId = '0x4cef52';
const usdc = '0x3600000000000000000000000000000000000000';
const zeroAuthorizationStateCall =
  '0xe94a0102' + '0'.repeat(64) + '0'.repeat(64);

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

const rpc = async (method, params) => {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      const response = await fetch(rpcUrl, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
        signal: AbortSignal.timeout(15_000),
      });
      if (response.ok) {
        const body = await response.json();
        if (body.error) throw new Error(`${method}: ${body.error.message}`);
        return body.result;
      }
      if (response.status !== 429 && response.status < 500) {
        throw new Error(`${method}: HTTP ${response.status}`);
      }
      if (attempt === 3) throw new Error(`${method}: HTTP ${response.status}`);
    } catch (error) {
      if (attempt === 3 || String(error.message).startsWith(`${method}:`)) throw error;
    }
    await sleep(1_000 * 2 ** attempt);
  }
  throw new Error(`${method}: retry budget exhausted`);
};

const call = (data) => rpc('eth_call', [{ to: usdc, data }, 'latest']);

try {
  if ((await rpc('eth_chainId', [])).toLowerCase() !== chainId) {
    throw new Error(`expected Arc Testnet chain ID ${chainId}`);
  }
  if ((await rpc('eth_getCode', [usdc, 'latest'])) === '0x') {
    throw new Error('Arc Testnet USDC ERC-20 interface has no contract code');
  }

  const [domainSeparator, authorizationState] = await Promise.all([
    call('0x3644e515'), // DOMAIN_SEPARATOR()
    call(zeroAuthorizationStateCall), // authorizationState(address,bytes32)
  ]);
  if (!/^0x[0-9a-fA-F]{64}$/.test(domainSeparator) || /^0x0{64}$/i.test(domainSeparator)) {
    throw new Error('USDC EIP-712 domain separator is missing');
  }
  if (authorizationState !== `0x${'0'.repeat(64)}`) {
    throw new Error('USDC EIP-3009 authorizationState returned an unexpected value');
  }

  console.log(`Arc Testnet exact-payment preflight passed (${usdc})`);
} catch (error) {
  console.error(`Arc Testnet exact-payment preflight failed: ${error.message}`);
  process.exitCode = 1;
}
