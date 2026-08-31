import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { BASE_PATH, LEGACY_BASE_PATH } from '../lib/site.mjs';

const root = path.resolve(fileURLToPath(new URL('../out/', import.meta.url)));
const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.jsonl': 'application/x-ndjson', '.txt': 'text/plain; charset=utf-8', '.cast': 'text/plain; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.xml': 'application/xml', '.pdf': 'application/pdf', '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation' };

export function createPreviewServer({ rootPath = root, basePath = BASE_PATH } = {}) {
  const exportRoot = path.resolve(rootPath);
  return http.createServer(async (request, response) => {
    if (!['GET', 'HEAD'].includes(request.method)) { response.writeHead(405, { Allow: 'GET, HEAD' }); response.end(); return; }
    try {
      const url = new URL(request.url, 'http://127.0.0.1:3100');
      // A root site must serve / directly, not redirect it back to itself.
      if (basePath && (url.pathname === '/' || url.pathname === basePath)) {
        response.writeHead(302, { Location: `${basePath}/` }); response.end(); return;
      }
      if (!url.pathname.startsWith(`${basePath}/`)) throw new Error('Outside site');
      const file = path.resolve(exportRoot, `.${decodeURIComponent(url.pathname.slice(basePath.length))}`);
      if (file !== exportRoot && !file.startsWith(exportRoot + path.sep)) throw new Error('Outside export');
      const info = await stat(file);
      const target = info.isDirectory() ? path.join(file, 'index.html') : file;
      const bytes = await readFile(target);
      response.writeHead(200, { 'Content-Type': types[path.extname(target)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
      response.end(request.method === 'HEAD' ? undefined : bytes);
    } catch {
      const fallback = await readFile(path.join(exportRoot, '404.html')).catch(() => 'Not found');
      response.writeHead(404, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      response.end(request.method === 'HEAD' ? undefined : fallback);
    }
  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const basePath = process.argv.includes('--legacy') ? LEGACY_BASE_PATH : BASE_PATH;
  createPreviewServer({ basePath }).listen(3100, '127.0.0.1', () => console.log(`Static Pages preview: http://127.0.0.1:3100${basePath}/`));
}
