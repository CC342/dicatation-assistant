export default {
  /**
   * @param {{ url: string | URL; headers: HeadersInit; method: any; body: any; }} request
   * @param {any} env
   */
  async fetch(request, env) {
    const upstream = '*.hf.space'; // 你的 HF 域名
    const url = new URL(request.url);
  
    // 🔴 【超级外挂】定点拦截域名验证请求！直接在这里返回暗号，不惊动后端
    if (url.pathname === '/微信给的名称.txt') {
      return new Response('内容', {
        status: 200,
        headers: { 
          'Content-Type': 'text/plain; charset=utf-8',
          'Access-Control-Allow-Origin': '*'
        }
      });
    }
    // 替换域名
    url.host = upstream;
    url.protocol = 'https:'; // 强制 HTTPS

    // 复制原始请求的 Headers，防止只读限制
    const newHeaders = new Headers(request.headers);
    
    // 注入关键 Header，让 HF 以为是直连访问
    newHeaders.set('Host', upstream);
    newHeaders.set('Origin', `https://${upstream}`);
    newHeaders.set('Referer', `https://${upstream}`);
    
    // 构造新的请求
    const newRequest = new Request(url.toString(), {
      method: request.method,
      headers: newHeaders,
      body: request.body,
      redirect: 'follow'
    });

    try {
      const response = await fetch(newRequest);
      
      // 处理跨域，方便 Webdav 客户端握手
      const newResponseHeaders = new Headers(response.headers);
      newResponseHeaders.set('Access-Control-Allow-Origin', '*');
      newResponseHeaders.set('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS, PROPFIND, PROPPATCH, MKCOL, COPY, MOVE, LOCK, UNLOCK');
      newResponseHeaders.set('Access-Control-Allow-Headers', '*');

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: newResponseHeaders
      });
    } catch (e) {
      return new Response('Error: Upstream connection failed. ' + e.message, { status: 502 });
    }
  }
};
