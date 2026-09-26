import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validQuery,extractSearch} from './extract.mjs';

test('query cannot become a URL or script',()=>{
  assert.equal(validQuery('手机'),true);
  for(const q of ['http://127.0.0.1','../etc/passwd','<script>','',null,'a'.repeat(41)]) assert.equal(validQuery(q),false);
});
test('verification takes precedence over item cards',()=>{
  global.document={body:{innerText:'请完成安全验证'},querySelectorAll:()=>{throw Error('must not scrape during challenge');}};
  assert.equal(extractSearch().status,'MANUAL_REQUIRED');
  delete global.document;
});
test('empty search is distinct from missing/authenticated content',()=>{
  global.document={body:{innerText:'暂无相关宝贝'},querySelectorAll:()=>[]};
  assert.equal(extractSearch().status,'OK');
  global.document.body.innerText='请先登录';
  assert.equal(extractSearch().status,'AUTH_REQUIRED');
  delete global.document;
});
