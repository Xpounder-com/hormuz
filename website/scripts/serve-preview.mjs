import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BASE_PATH } from '../lib/site.mjs';

const root = path.resolve(fileURLToPath(new URL('../out/', import.meta.url)));
const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.jsonl': 'application/x-ndjson', '.txt': 'text/plain; charset=utf-8', '.cast': 'text/plain; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.xml': 'application/xml', '.pdf': 'application/pdf', '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation' };
http.createServer(async (request, response) => {
  if (!['GET', 'HEAD'].includes(request.method)) { response.writeHead(405); response.end(); return; }
  const url = new URL(request.url, 'http://127.0.0.1:3100');
  if (url.pathname === '/' || url.pathname === BASE_PATH) { response.writeHead(302, { Location: `${BASE_PATH}/` }); response.end(); return; }
  try {
    if (!url.pathname.startsWith(`${BASE_PATH}/`)) throw new Error('Outside site');
    const file = path.resolve(root, `.${decodeURIComponent(url.pathname.slice(BASE_PATH.length))}`);
    if (file !== root && !file.startsWith(root + path.sep)) throw new Error('Outside export');
    const info = await stat(file);
    const target = info.isDirectory() ? path.join(file, 'index.html') : file;
    const bytes = await readFile(target);
    response.writeHead(200, { 'Content-Type': types[path.extname(target)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    response.end(request.method === 'HEAD' ? undefined : bytes);
  } catch {
    response.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(await readFile(path.join(root, '404.html')));
  }
}).listen(3100, '127.0.0.1', () => console.log('Static Pages preview: http://127.0.0.1:3100/hormuz/'));
