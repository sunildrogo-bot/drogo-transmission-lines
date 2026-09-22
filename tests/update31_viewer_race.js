const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync(require('path').join(__dirname, '../templates/project_map.html'), 'utf8');
function extract(name) {
  const start = source.search(new RegExp('(?:async )?function '+name+'\\('));
  const tail = source.slice(start);
  const end = tail.slice(1).search(/\n(?:async )?function /);
  return end < 0 ? tail : tail.slice(0, end+1);
}
const pending = [];
const context = vm.createContext({
  currentFilteredPhotos:[{id:1},{id:2}], currentPhotoIndex:0, photoViewSerial:1,
  defectLoadSerial:0, thermalLoadSerial:0, currentPhotoDefects:[], currentThermalPoints:[],
  open:true, document:{getElementById:()=>({classList:{contains:()=>context.open}})},
  fetch:()=>new Promise((resolve,reject)=>pending.push({resolve,reject})),
  renderDefectShapes(){}, renderDefectChips(){}, renderThermalPoints(){},
  updateThermalColorbar(){}, prefillThermalParams(){},
});
vm.runInContext(['photoViewIsCurrent','loadPhotoDefects','loadThermalPoints'].map(extract).join('\n'), context);
(async()=>{
  for (const [fn,field,key] of [['loadPhotoDefects','currentPhotoDefects','defects'],['loadThermalPoints','currentThermalPoints','points']]) {
    context.currentPhotoIndex=0; context.photoViewSerial++; context.open=true;
    const old=context[fn](1); const oldRequest=pending.shift();
    context.currentPhotoIndex=1; context.photoViewSerial++;
    const active=context[fn](2); pending.shift().resolve({ok:true,json:async()=>({[key]:[{id:22}]})});
    await active;
    oldRequest.resolve({ok:true,json:async()=>({[key]:[{id:11}]})}); await old;
    assert.equal(context[field][0].id,22,'late previous photo response');
    const first=context[fn](2); const request1=pending.shift();
    const second=context[fn](2); pending.shift().resolve({ok:true,json:async()=>({[key]:[{id:33}]})}); await second;
    request1.reject(new Error('late failure')); await first;
    assert.equal(context[field][0].id,33,'late failure must not clear newer result');
    const closing=context[fn](2); const request=pending.shift(); context.open=false; context.photoViewSerial++;
    request.resolve({ok:true,json:async()=>({[key]:[{id:44}]})}); await closing;
    assert.equal(context[field][0].id,33,'closed viewer must ignore results');
  }
  console.log('RGB and thermal response-order tests passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
