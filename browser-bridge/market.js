import {extractMarketPage} from "./market-extract.js";

export async function collectMarketTask(task, bridgeId) {
  if (!/^[\p{L}\p{N} ]{1,40}$/u.test(task.query || "")) throw new Error("行情任务关键词无效");
  const pageUrl = `https://www.goofish.com/search?q=${encodeURIComponent(task.query)}`;
  const tab = await chrome.tabs.create({url:pageUrl,active:false});
  let result = {status:"TRANSIENT_ERROR",items:[],message:"搜索页加载超时"};
  try {
    const deadline = Date.now()+25000;
    while (Date.now()<deadline) {
      if ((await chrome.tabs.get(tab.id)).status === "complete") {
        const frames = await chrome.scripting.executeScript({target:{tabId:tab.id},func:extractMarketPage,args:[task.query]});
        result = frames[0]?.result || result;
        if (result.status === "OK" || result.status === "MANUAL_REQUIRED") break;
      }
      await new Promise(resolve=>setTimeout(resolve,1000));
    }
  } catch {
    result = {status:"TRANSIENT_ERROR",items:[],message:"浏览器页面读取失败"};
  } finally {
    // Keep login/risk pages available for human takeover; never attempt a bypass.
    if (result.status === "OK" || result.status === "TRANSIENT_ERROR") await chrome.tabs.remove(tab.id).catch(()=>{});
  }
  return {...task,bridge_id:bridgeId,status:result.status,items:result.items,
    message:result.message,captured_at:new Date().toISOString(),page_url:pageUrl,
    // expires_at is a server lease field, not part of the accepted capture schema.
    expires_at:undefined};
}
