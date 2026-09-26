// Only the rendered search DOM is read. Never call private APIs or read cookies.
export function extractMarketPage(expectedQuery) {
  if (location.origin !== "https://www.goofish.com" || location.pathname !== "/search"
      || new URL(location.href).searchParams.get("q") !== expectedQuery) {
    return {status:"TRANSIENT_ERROR",items:[],message:"搜索页与任务不匹配"};
  }
  const text = document.body?.innerText || "";
  if (/验证码|安全验证|异常访问|非法访问|正常浏览器访问|访问受限|滑动验证/.test(text)
      || document.querySelector('iframe[src*="captcha"],iframe[src*="punish"],iframe[src*="verify"]')) {
    return {status:"MANUAL_REQUIRED",items:[],message:"搜索页出现验证或访问限制，请人工处理"};
  }
  const items = Array.from(document.querySelectorAll('a[href*="/item?id="]')).slice(0,50).map(card => {
    const url = new URL(card.href);
    const title = card.querySelector('[class*="row1-wrap-title"]');
    const price = card.querySelector('[class*="price-wrap"]');
    return {item_id:url.searchParams.get('id'),
      title:(title?.getAttribute('title') || title?.textContent || '').replace(/\s+/g,' ').trim().slice(0,2000),
      price:(price?.querySelector('[class*="number"]')?.textContent || '').trim() + (price?.querySelector('[class*="decimal"]')?.textContent || '').trim(),
      category_id:url.searchParams.get('categoryId') || '',
      url:`https://www.goofish.com/item?id=${url.searchParams.get('id')}`};
  }).filter(x=>/^\d{5,30}$/.test(x.item_id || '') && x.title && /^\d{1,7}(\.\d{1,2})?$/.test(x.price));
  if (items.length) return {status:"OK",items,message:""};
  if (/暂无相关宝贝|未找到相关宝贝|没有找到/.test(text)) return {status:"OK",items:[],message:"搜索无结果"};
  return {status:/登录/.test(text)?"AUTH_REQUIRED":"TRANSIENT_ERROR",items:[],message:"未读取到商品卡片"};
}
