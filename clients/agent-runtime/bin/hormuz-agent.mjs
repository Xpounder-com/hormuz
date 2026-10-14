#!/usr/bin/env node
if (Number(process.versions.node.split('.')[0]) < 24) {
  process.stderr.write('Hormuz Agent requires Node.js 24 or newer.\n');
  process.exitCode = 2;
} else {
  try {
    const { main } = await import('../dist/agent-command.js');
    process.exitCode = await main(process.argv.slice(2));
  } catch {
    process.stderr.write('Unable to load Hormuz Agent. Reinstall the packaged CLI. For a source checkout, run npm ci --prefix clients/agent-runtime, then npm run build --prefix clients/agent-runtime.\n');
    process.exitCode = 2;
  }
}
