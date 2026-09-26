import {test} from 'node:test';
import assert from 'node:assert/strict';
import {extractMarketPage} from '../browser-bridge/market-extract.js';

function setup(text='') {
  global.location={origin:'https://www.goofish.com',pathname:'/search',href:'https://www.goofish.com/search?q=手机'};
  global.document={body:{innerText:text},querySelector:()=>null,querySelectorAll:()=>[]};
}
test('risk and illegal access always stop; no card extraction',()=>{
  for(const text of ['非法访问','请使用正常浏览器访问','安全验证','滑动验证']) {
    setup(text);
    global.document.querySelectorAll=()=>{throw Error('must stop');};
    assert.equal(extractMarketPage('手机').status,'MANUAL_REQUIRED');
  }
});
test('search query binding',()=>{
  setup(); assert.equal(extractMarketPage('平板电脑').status,'TRANSIENT_ERROR');
});
test('real card extraction uses current price number plus decimal only',()=>{
  setup();
  global.document.querySelectorAll=()=>[{
    href:'https://www.goofish.com/item?id=1084580796858&categoryId=126862528',
    querySelector:selector=>selector.includes('title') ? {getAttribute:()=> '测试手机',textContent:'测试手机'} :
      {querySelector:inner=>({textContent:inner.includes('number')?'3400':'.50'})}
  }];
  const result=extractMarketPage('手机');
  assert.equal(result.items[0].price,'3400.50');
  assert.equal(result.items[0].category_id,'126862528');
});
