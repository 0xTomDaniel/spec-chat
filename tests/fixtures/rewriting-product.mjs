import { createServer, request } from 'node:http';

// The product as a lane runs it, for the browser tests of a prototype mockup: the QA fixture's running prototype
// (tests/fixtures/qa/prototype.py) at `productPort`, answered at `port` as it serves it, its route's page rewritten while
// `product.rewrite` is set, as another product could serve it: rewrite(text, headers) returns the page's text and may
// change its headers, each named in lower case. Every other answer passes as the fixture's.
export async function rewritingProduct(port, productPort, route = '/reports/weekly') {
  const product = { rewrite: null };
  product.server = createServer((asked, answer) => {
    const forward = request({ host: '127.0.0.1', port: productPort, path: asked.url, method: asked.method, headers: asked.headers }, served => {
      const chunks = [];
      served.on('data', chunk => chunks.push(chunk));
      served.on('end', () => {
        const headers = { ...served.headers };
        let body = Buffer.concat(chunks);
        if (product.rewrite && asked.method === 'GET' && asked.url.split('?')[0] === route) {
          body = Buffer.from(product.rewrite(body.toString('utf8'), headers));
          headers['content-length'] = String(body.length);
        }
        answer.writeHead(served.statusCode, headers);
        answer.end(asked.method === 'HEAD' ? undefined : body);
      });
    });
    forward.on('error', () => { answer.writeHead(502); answer.end(); });
    asked.pipe(forward);
  });
  await new Promise(done => product.server.listen(port, '127.0.0.1', done));
  return product;
}
