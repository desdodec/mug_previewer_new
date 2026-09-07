// Controller unit regression: actual async file/hash/export code, minimal DOM.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
let downloaded;
class Element {
  constructor() { this.listeners = {}; this.style = {}; this.dataset = {}; this.value = ''; this.children = []; }
  addEventListener(type, cb) { (this.listeners[type] ||= []).push(cb); }
  appendChild(child) { this.children.push(child); }
  remove() {}
  click() { if (this.href) downloaded = JSON.parse(decodeURIComponent(this.href.split(',').slice(1).join(','))); }
}
globalThis.window = globalThis;
globalThis.alert = () => {};
globalThis.document = {
  baseURI: 'file:///prepared/', body: new Element(),
  getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
  createElement() { return new Element(); }
};
globalThis.DOMParser = class {
  parseFromString(text) { return {querySelector: () => !text.includes('<svg'), documentElement: {localName: 'svg'}}; }
};
const html = fs.readFileSync('tools/svg_reviewer/previewer.html', 'utf8');
vm.runInThisContext(fs.readFileSync('tools/svg_reviewer/qa_hash.js', 'utf8'));
for (const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) vm.runInThisContext(match[1]);
(async () => {
  await vm.runInThisContext(String.raw`(async () => {
    const file = new File(['<svg>\r\n<!-- Café -->\r\n</svg>'], '0001_test.svg');
    Object.defineProperty(file, 'webkitRelativePath', {value: 'faces/dataset/0001_test.svg'});
    selectedSvgFiles = [file];
    savedReviews = [{svg_name:file.name, svg_path:file.webkitRelativePath, status:'pass'}];
    restoreSavedReviews();
    await Promise.all(reviewItems.map(i => i.ready));
    if (reviewItems[0].status !== '') throw new Error('Filename-only pass transferred');
    reviewItems[0].select.value = 'pass';
    reviewItems[0].select.listeners.change[0]();
    await exportJSON();
  })()`);
  assert.equal(downloaded[0].dataset_id, 'dataset');
  assert.equal(downloaded[0].street_id, '0001');
  assert.match(downloaded[0].reviewed_svg_sha256, /^[0-9a-f]{64}$/);
  globalThis.roundTrip = downloaded;
  await vm.runInThisContext(String.raw`(async () => {
    savedReviews = roundTrip;
    restoreSavedReviews();
    await Promise.all(reviewItems.map(i => i.ready));
    if (reviewItems[0].status !== 'pass') throw new Error('Exact saved pass lost');
    const changed = new File(['<svg><!-- changed --></svg>'], '0001_test.svg');
    Object.defineProperty(changed, 'webkitRelativePath', {value: 'faces/dataset/0001_test.svg'});
    selectedSvgFiles = [changed];
    restoreSavedReviews();
    await Promise.all(reviewItems.map(i => i.ready));
    if (reviewItems[0].status !== '') throw new Error('Stale pass transferred');
  })()`);
  console.log('reviewer controller: filename-only blocked, explicit pass saved, identity/hash retained, stale pass reset');
})().catch(error => { console.error(error); process.exitCode = 1; });
