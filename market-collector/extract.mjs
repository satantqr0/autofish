// Executed in the rendered public search page. No private protocol or credentials.
export function extractSearch() {
  const text = document.body?.innerText || "";
  if (/验证码|安全验证|异常访问|非法访问|正常浏览器访问|访问受限|滑动验证/.test(text)) {
    return {status:"MANUAL_REQUIRED",items:[],message:"闲鱼搜索页出现验证提示"};
  }
  const items = Array.from(document.querySelectorAll('a[href*="/item?id="]')).slice(0,50).map(card => {
    const url = new URL(card.href);
    const title = card.querySelector('[class*="row1-wrap-title"]');
    const price = card.querySelector('[class*="price-wrap"]');
    return {
      item_id: url.searchParams.get('id'),
      title: (title?.getAttribute('title') || title?.textContent || '').replace(/\s+/g,' ').trim(),
      price: (price?.querySelector('[class*="number"]')?.textContent || '').trim() + (price?.querySelector('[class*="decimal"]')?.textContent || '').trim(),
      category_id: url.searchParams.get('categoryId') || '',
      url: `https://www.goofish.com/item?id=${url.searchParams.get('id')}`,
    };
  }).filter(x => /^\d+$/.test(x.item_id || '') && x.title && /^\d+(\.\d{1,2})?$/.test(x.price));
  if (items.length) return {status:"OK",items};
  if (/暂无相关宝贝|未找到相关宝贝|没有找到/.test(text)) return {status:"OK",items:[]};
  return {status: /登录/.test(text) ? "AUTH_REQUIRED" : "TRANSIENT_ERROR",items:[],message:"未能读取公开搜索结果"};
}

export function validQuery(query) {
  return typeof query === 'string' && query.length > 0 && query.length <= 40 && /^[\p{L}\p{N} ]+$/u.test(query);
}
