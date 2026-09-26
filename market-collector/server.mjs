import http from 'node:http';
import {timingSafeEqual} from 'node:crypto';
import {chromium} from 'playwright';
import {extractSearch, validQuery} from './extract.mjs';

const token = process.env.MARKET_COLLECTOR_TOKEN || '';
if (token.length < 32) throw new Error('MARKET_COLLECTOR_TOKEN must be configured');
let browser;
let context;
let busy = false;

async function startBrowser() {
  browser = await chromium.launch({headless:true, chromiumSandbox:true});
  context = await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'});
}
await startBrowser();
const send = (res, code, data) => {res.writeHead(code,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
function authorized(value) {
  const given = Buffer.from(value || ''); const expected = Buffer.from(`Bearer ${token}`);
  return given.length === expected.length && timingSafeEqual(given, expected);
}

const server = http.createServer(async (req,res) => {
  if (req.method === 'GET' && req.url === '/health') {
    return send(res,browser?.isConnected() ? 200 : 503,{status:browser?.isConnected()?'ok':'unavailable',busy});
  }
  if (!authorized(req.headers.authorization)) return send(res,401,{error:'unauthorized'});
  if (req.method !== 'POST' || req.url !== '/search') return send(res,404,{error:'not_found'});
  if (busy) return send(res,429,{error:'collector_busy'});
  busy=true;
  let page;
  let timer;
  try {
    let body = '';
    for await (const chunk of req) {body += chunk; if (Buffer.byteLength(body)>2048) throw new Error('invalid_body');}
    const {query} = JSON.parse(body);
    if (!validQuery(query)) return send(res,400,{error:'invalid_query'});
    if (!browser.isConnected()) await startBrowser();
    page = await context.newPage();
    // Closing the page bounds navigation AND frame extraction even if upstream stalls.
    timer = setTimeout(()=>void page.close().catch(()=>{}),22000);
    await page.goto(`https://www.goofish.com/search?q=${encodeURIComponent(query)}`,{waitUntil:'domcontentloaded',timeout:15000});
    await page.waitForFunction(()=>document.querySelector('a[href*="/item?id="]') || /验证码|安全验证|异常访问|非法访问|正常浏览器访问|暂无相关宝贝|未找到相关宝贝/.test(document.body?.innerText || ''),{},{timeout:12000}).catch(()=>{});
    for (const frame of page.frames()) {
      const text = await frame.locator('body').innerText({timeout:1000}).catch(()=>'');
      if (/验证码|安全验证|异常访问|非法访问|正常浏览器访问|滑动验证/.test(text)) {
        return send(res,200,{status:'MANUAL_REQUIRED',items:[],source:'goofish-public-browser',message:'搜索页面出现验证提示，停止采集'});
      }
    }
    const result = await page.evaluate(extractSearch);
    send(res,200,{...result,source:'goofish-public-browser',query,captured_at:new Date().toISOString(),page_url:page.url()});
  } catch {
    if (!res.writableEnded) send(res,200,{status:'TRANSIENT_ERROR',items:[],source:'goofish-public-browser',message:'公开搜索页读取失败或超时'});
  } finally {
    clearTimeout(timer);
    await page?.close().catch(()=>{});
    busy=false;
  }
});
server.requestTimeout=25000;
server.headersTimeout=5000;
server.listen(8080,'0.0.0.0');
for (const signal of ['SIGTERM','SIGINT']) process.on(signal,async()=>{server.close();await browser?.close();process.exit(0);});
