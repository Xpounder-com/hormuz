import assert from 'node:assert/strict';
import { once } from 'node:events';
import http from 'node:http';
import https from 'node:https';
import type { AddressInfo } from 'node:net';
import { test } from 'node:test';
import { InferenceProxy } from '../src/admission.ts';
import type { InferenceProxyOptions } from '../src/admission.ts';

const base = {
  credential: 'synthetic-provider-key', model: 'test-model', allowanceMicrousd: 1000000,
  inputMicrousdPerMillion: 500001, outputMicrousdPerMillion: 1500001,
  maxInputTokens: 100000, maxOutputTokens: 10, requestLimit: 2, timeoutMs: 1000,
};
const invalidRates = [
  ['zero', 0], ['negative zero', -0], ['negative', -1], ['fraction', 0.5],
  ['NaN', NaN], ['positive infinity', Infinity], ['negative infinity', -Infinity],
  ['unsafe integer', Number.MAX_SAFE_INTEGER + 1],
] as const;

for (const provider of ['openai', 'anthropic'] as const) {
  for (const field of ['inputMicrousdPerMillion', 'outputMicrousdPerMillion'] as const) {
    test(provider + ' rejects invalid ' + field + ' before resource allocation or dispatch', async (t) => {
      // Reaching any network allocation would fail this regression immediately;
      // the counters also show that the rejected constructor/start chain never
      // acquires a server, connection pool or outbound request.
      const noAllocation = (): never => { throw new Error('unexpected_network_allocation'); };
      const spies = [
        t.mock.method(http, 'createServer', noAllocation),
        t.mock.method(http, 'Agent', noAllocation),
        t.mock.method(https, 'Agent', noAllocation),
        t.mock.method(http, 'request', noAllocation),
        t.mock.method(https, 'request', noAllocation),
      ];
      for (const [label, value] of invalidRates) {
        const options: InferenceProxyOptions = {
          ...base, provider, upstream: provider === 'openai' ? 'https://api.openai.com' : 'https://api.anthropic.com',
          [field]: value,
        };
        assert.throws(() => new InferenceProxy(options), { message: 'invalid_proxy_configuration' }, label);
        await assert.rejects(async () => {
          const proxy = new InferenceProxy(options);
          try { await proxy.start(); }
          finally { await proxy.stop(); }
        }, { message: 'invalid_proxy_configuration' }, label + ' start chain');
      }
      for (const spy of spies) assert.equal(spy.mock.callCount(), 0);
    });
  }

  for (const allowanceMicrousd of [1000000, 0]) {
    test(provider + (allowanceMicrousd ? ' positive rates settle exact rounded costs' : ' accepts zero allowance and denies paid calls before egress'), async () => {
      let requests = 0;
      const server = http.createServer(async (req, res) => {
        for await (const _chunk of req) { /* Drain the bounded synthetic request. */ }
        requests += 1;
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ usage: { input_tokens: 2, output_tokens: 3 } }));
      });
      let proxy: InferenceProxy | undefined;
      try {
        server.listen(0, '127.0.0.1');
        await once(server, 'listening');
        const upstream = 'http://127.0.0.1:' + (server.address() as AddressInfo).port;
        proxy = new InferenceProxy({ ...base, provider, upstream, allowanceMicrousd, allowLoopbackUpstream: true });
        const started = await proxy.start();
        const body = provider === 'openai' ? { model: base.model, input: 'hello' } : { model: base.model, messages: [{ role: 'user', content: 'hello' }] };
        const result = await call(started.endpoint + (provider === 'openai' ? '/responses' : '/messages'), started.credential, body, provider);
        assert.equal(result.status, allowanceMicrousd ? 200 : 402);
        assert.equal(requests, allowanceMicrousd ? 1 : 0);
        if (!allowanceMicrousd) assert.deepEqual(result.body, { error: { code: 'allowance_exhausted', message: 'allowance_exhausted' } });
        assert.deepEqual(proxy.summary(), {
          spentMicrousd: allowanceMicrousd ? 7 : 0, heldMicrousd: 0, unknownMicrousd: 0,
          requestCount: allowanceMicrousd ? 1 : 0, coverage: 'enforced',
        });
      } finally {
        await proxy?.stop();
        server.closeAllConnections();
        if (server.listening) await new Promise<void>((resolve) => server.close(() => resolve()));
      }
    });
  }
}

async function call(url: string, credential: string, body: unknown, provider: InferenceProxyOptions['provider']): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const request = http.request(url, {
      method: 'POST', agent: false, timeout: 2000,
      headers: { 'content-type': 'application/json', ...(provider === 'openai' ? { authorization: 'Bearer ' + credential } : { 'x-api-key': credential }) },
    }, (response) => {
      const chunks: Buffer[] = [];
      response.on('data', (chunk: Buffer) => chunks.push(chunk));
      response.on('error', reject);
      response.on('end', () => {
        try { resolve({ status: response.statusCode!, body: JSON.parse(Buffer.concat(chunks).toString()) }); }
        catch (error) { reject(error); }
      });
    });
    request.on('timeout', () => request.destroy(new Error('test_request_timeout')));
    request.on('error', reject);
    request.end(JSON.stringify(body));
  });
}
