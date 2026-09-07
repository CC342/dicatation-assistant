export default {
  async fetch(request, env) {
    const upstream = 'cody89-dictation-assistant.hf.space'; // HF 真实域名
    const customDomain = new URL(request.url).hostname; // 你的自定义域名 (tx.imeet.eu.cc)
    const url = new URL(request.url);

    // 1. 保留微信验证
    if (url.pathname === '/微信给的名称.txt') {
      return new Response('内容', { status: 200, headers: { 'Content-Type': 'text/plain' } });
    }

    url.hostname = upstream;
    url.protocol = 'https:';

    // 2. 伪装请求来源，骗过 Hugging Face 门禁
    const newHeaders = new Headers(request.headers);
    newHeaders.set('Host', upstream);
    newHeaders.set('Origin', `https://${upstream}`);
    newHeaders.set('Referer', `https://${upstream}/`);

    const init = {
      method: request.method,
      headers: newHeaders,
      redirect: 'follow'
    };
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      init.body = request.body;
    }

    try {
      const response = await fetch(new Request(url.toString(), init));

      // 3. WebSocket 实时通道：原样放行，绝对不拆包
      if (request.headers.get('Upgrade')?.toLowerCase() === 'websocket' || response.status === 101) {
        return response;
      }

      // 4. 🚀 核心大招：拦截网页源码，做“移花接木”文本替换！
      const contentType = response.headers.get('content-type') || '';
      // 只拦截网页和配置文件，绝不碰音频文件
      if (contentType.includes('text/html') || contentType.includes('application/json') || contentType.includes('application/javascript')) {
        
        let text = await response.text();
        // 魔法替换：把网页源码里的官方域名，全部强行替换成你的自定义域名
        text = text.split(upstream).join(customDomain);
        
        const resHeaders = new Headers(response.headers);
        resHeaders.delete('Content-Encoding'); // 已经解压成文本了，必须删掉压缩头防止浏览器死锁
        resHeaders.delete('Content-Length');
        resHeaders.delete('X-Frame-Options');
        resHeaders.delete('Content-Security-Policy');

        return new Response(text, {
          status: response.status,
          headers: resHeaders
        });
      }

      // 5. 其他静态文件（音频流、图片），原样透传
      const resHeaders = new Headers(response.headers);
      resHeaders.set('Access-Control-Allow-Origin', '*');
      return new Response(response.body, {
        status: response.status,
        headers: resHeaders
      });

    } catch (e) {
      return new Response('Error: ' + e.message, { status: 502 });
    }
  }
};
