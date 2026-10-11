// Copyright (C) 2026 - Joseph M Wilcox
'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="tripoforge-token"]').content;
const images = {}, previewURLs = {}, thumbnailURLs = new Map(), costDetails = new Map();
let loadedProject = '', state = null, busy = false, projectRequest = 0, materialControls = {pbr:true,delight:true};
async function api(path, data) {
  const response = await fetch(path, {method: data === undefined ? 'GET' : 'POST', headers: {'X-TripoForge-Token': token, 'Content-Type':'application/json'}, ...(data === undefined ? {} : {body:JSON.stringify(data)})});
  const result = await response.json(); if (!response.ok) throw Error(result.error || 'Request failed'); return result;
}
function notice(message = '') { $('notice').textContent = message; $('notice').hidden = !message; }
function canAdd() { $('add').disabled = busy || loadedProject !== $('project').value.trim() || Object.keys(images).length !== 4; }
function setImage(view, file) {
  if (!file || !/\.(png|jpe?g)$/i.test(file.name)) { notice('Use PNG or JPEG images.'); return; }
  if (file.size > 20 * 1024 * 1024) { notice('Each image must be at most 20 MiB.'); return; }
  if (previewURLs[view]) URL.revokeObjectURL(previewURLs[view]);
  images[view] = file; previewURLs[view] = URL.createObjectURL(file);
  const slot = document.querySelector(`[data-view="${view}"]`), img = slot.querySelector('img');
  img.src = previewURLs[view]; img.hidden = false; slot.querySelector('.drop').classList.add('has-image'); slot.querySelector('.clear').hidden = false; slot.querySelector('.filename').textContent = file.name;
  notice(); canAdd();
}
document.querySelectorAll('.slot').forEach(slot => {
  const view = slot.dataset.view, input = slot.querySelector('input'), drop = slot.querySelector('.drop');
  drop.onclick = () => input.click(); input.onchange = () => setImage(view, input.files[0]);
  drop.ondragover = event => {event.preventDefault();slot.classList.add('dragging');};
  drop.ondragleave = () => slot.classList.remove('dragging');
  drop.ondrop = event => {event.preventDefault();slot.classList.remove('dragging');if(event.dataTransfer.files.length !== 1)notice('Drop one image into each slot.');else setImage(view,event.dataTransfer.files[0]);};
  slot.querySelector('.clear').onclick = () => {delete images[view]; URL.revokeObjectURL(previewURLs[view]); delete previewURLs[view]; slot.querySelector('img').hidden=true;drop.classList.remove('has-image');slot.querySelector('.clear').hidden=true;slot.querySelector('.filename').textContent='No image assigned';input.value='';canAdd();};
});
window.addEventListener('dragover', e => e.preventDefault()); window.addEventListener('drop', e => e.preventDefault());
function faces(value) {const clamped=Math.max(Number($('faces').min),Math.min(Number($('faces').max),Math.round(Number(value))));if(!Number.isFinite(clamped))return;$('faces').value=clamped;$('face-number').value=clamped;$('face-label').textContent=clamped.toLocaleString();}
$('faces').oninput = () => faces($('faces').value); $('face-number').oninput = () => faces($('face-number').value);
function updateMaterialControls() {
  for(const id of ['pbr','delight']) $(id).disabled=$('notexture').checked || !materialControls[id];
}
$('notexture').onchange=updateMaterialControls;
function updateRigControls() {
  $('rig-options').hidden=!$('rig').checked;
  const ue5=$('rig-spec').value==='ue5';
  if(ue5)$('rig-type').value='biped';
  for(const option of $('rig-type').options)option.disabled=ue5 && option.value!=='biped';
}
$('rig').onchange=updateRigControls;$('rig-spec').onchange=updateRigControls;
async function loadProject(reset = true) {
  const sequence = ++projectRequest, project = $('project').value.trim();
  loadedProject='';canAdd();
  try {
    const data = await api('/api/project',{project,...(reset ? {} : {topology:$('topology').value})});
    if(sequence !== projectRequest)return;
    if(reset)$('topology').value=data.topology;
    materialControls=data.controls;
    if(reset){$('pbr').checked=data.pbr;$('delight').checked=data.delight && data.controls.delight;$('rig-spec').value=data.rig_spec;$('rig-type').value=data.rig_type;}
    updateMaterialControls();
    updateRigControls();
    $('topology').querySelector('[value="quad"]').disabled=!data.quad_supported;
    for(const id of ['faces','face-number']) {$(id).min=data.minimum;$(id).max=data.maximum;}
    $('minimum').textContent=data.minimum.toLocaleString();$('maximum').textContent=data.maximum.toLocaleString();
    faces(reset ? data.faces : $('faces').value);$('output').placeholder=data.output;
    $('project-info').textContent=`${data.model} · ${data.minimum.toLocaleString()}–${data.maximum.toLocaleString()} faces`;
    loadedProject=project;notice();canAdd();if(reset)refreshBalance();
  } catch(e) {notice(e.message);$('project-info').textContent='Project not loaded';}
}
$('project').oninput=()=>{loadedProject='';canAdd();};$('project').onkeydown=e=>{if(e.key==='Enter')loadProject();};$('load').onclick=()=>loadProject();$('topology').onchange=()=>loadProject(false);
$('find').onclick=async()=>{try{$('find').disabled=true;const data=await api('/api/pick',{});if(data.project){$('project').value=data.project;await loadProject();}}catch(e){notice(e.message);}finally{$('find').disabled=false;}};
async function refreshBalance() {try{$('refresh').disabled=true;const d=await api('/api/balance',{project:$('project').value.trim()});$('balance').textContent=Math.floor(d.balance).toLocaleString();$('frozen').textContent=`${d.frozen.toLocaleString()} frozen`;}catch(e){$('balance').textContent='—';$('frozen').textContent='Balance unavailable';notice(e.message);}finally{$('refresh').disabled=false;}}
$('refresh').onclick=refreshBalance;
async function fileData(file) {return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve({name:file.name,data:r.result.split(',')[1]});r.onerror=()=>reject(Error(`Cannot read ${file.name}`));r.readAsDataURL(file);});}
$('add').onclick=async()=>{
  busy=true;canAdd();$('add').textContent='Saving task…';
  try {const assigned={};await Promise.all(Object.entries(images).map(async([v,f])=>{assigned[v]=await fileData(f);}));
    await api('/api/add',{project:loadedProject,name:$('name').value.trim(),output:$('output').value.trim(),topology:$('topology').value,faces:Number($('face-number').value),rig:$('rig').checked,rig_spec:$('rig-spec').value,rig_type:$('rig-type').value,notexture:$('notexture').checked,pbr:!$('pbr').disabled && $('pbr').checked,delight:!$('delight').disabled && $('delight').checked,messy:$('messy').checked,subfolders:$('subfolders').checked,images:assigned});
    const number=/^(.*?)(\d+)$/.exec($('name').value);if(number)$('name').value=number[1]+String(Number(number[2])+1).padStart(number[2].length,'0');
    notice('Task added. No credits used.');await refreshState();
  } catch(e){notice(e.message);}finally{busy=false;canAdd();$('add').textContent='＋ Add task to queue';}
};
function element(tag, className, text) {const n=document.createElement(tag);if(className)n.className=className;if(text!==undefined)n.textContent=text;return n;}
function money(cost) {return cost.min === cost.max ? `${cost.max.toLocaleString()} credits · $${(cost.max/100).toFixed(2)}` : `${cost.min}–${cost.max} credits · $${(cost.min/100).toFixed(2)}–$${(cost.max/100).toFixed(2)}`;}
async function thumbnail(path, img) {try{let url=thumbnailURLs.get(path);if(!url){const r=await fetch(path,{headers:{'X-TripoForge-Token':token}});if(!r.ok)return;url=URL.createObjectURL(await r.blob());thumbnailURLs.set(path,url);}img.src=url;}catch{}}
function render() {
  const pending=state.tasks.filter(t=>t.status==='pending'),completed=state.tasks.filter(t=>t.status==='complete').length;
  $('count').textContent=state.tasks.length;$('run').disabled=state.running || !pending.length;$('run').textContent=state.running ? 'Running queue…' : '▶ Run all tasks';
  $('queue-state').textContent=state.running ? 'GENERATING' : 'READY';$('queue-summary').textContent=`${pending.length} pending · ${completed} complete`;
  $('pricing-date').textContent=`Pricing snapshot ${state.pricing_date} · Estimates only`;
  if(!state.tasks.length){$('tasks').innerHTML='<div class="empty"><span class="empty-icon">▧</span><h3>Your queue is empty</h3><p>Assign four views on the left, choose your mesh settings, and add your first task.</p><span class="empty-line">COMPOSE → QUEUE → GENERATE</span></div>';return;}
  $('tasks').replaceChildren();
  for(const task of state.tasks){
    const card=element('article','task'),top=element('div','task-top');top.append(element('h3','',task.name),element('span','status '+task.status,task.status));card.append(top);
    const thumbs=element('div','thumbs');for(const view of ['front','left','right','back']){const box=element('div'),img=element('img');img.alt=`${task.name} ${view}`;thumbnail(task.images[view],img);box.append(img,element('span','',view.toUpperCase()));thumbs.append(box);}card.append(thumbs);
    card.append(element('div','task-options',`${task.topology==='quad'?'Quads':'Tris'} · ${task.faces.toLocaleString()} faces · ${task.texture?'Textures + UVs':'No texture / UVs'} · ${task.rig?`Rig: ${task.rig_spec} / ${task.rig_type}`:'No rig'}${task.texture?` · ${task.pbr?'PBR':'No PBR'} · ${task.delight?'No Lighting':'Keep lighting'}`:''}`),element('div','task-path',task.output));
    if(task.progress)card.append(element('div','task-progress',task.progress));if(task.error)card.append(element('div','task-error',task.error));
    const actions=element('div','task-actions'),cost=element('button','secondary','Estimate cost');cost.onclick=async()=>{try{const result=await api('/api/cost',{id:task.id});costDetails.set(task.id,result);render();}catch(e){notice(e.message);}};actions.append(cost,element('span','estimate',costDetails.has(task.id)?money(costDetails.get(task.id)):''));
    if(task.status==='failed'){const retry=element('button','secondary','Resume');retry.onclick=async()=>{try{await api('/api/retry',{id:task.id});await refreshState();}catch(e){notice(e.message);}};actions.append(retry);}
    const remove=element('button','remove','Remove');remove.disabled=task.status==='running';remove.onclick=async()=>{try{await api('/api/remove',{id:task.id});costDetails.delete(task.id);await refreshState();}catch(e){notice(e.message);}};actions.append(remove);card.append(actions);
    if(costDetails.has(task.id))card.append(element('div','cost-detail',costDetails.get(task.id).parts.map(p=>`${p.label}: ${p.min===p.max?p.max:`${p.min}–${p.max}`} credits`).join('\n')));
    $('tasks').append(card);
  }
}
let polling=false;
async function refreshState() {if(polling)return;polling=true;try{const next=await api('/api/state');const changed=JSON.stringify(next)!==JSON.stringify(state);const wasRunning=state?.running;state=next;if(changed)render();if(wasRunning&&!state.running)refreshBalance();}catch(e){notice(e.message);}finally{polling=false;}}
$('run').onclick=async()=>{try{$('run').disabled=true;await api('/api/run',{});notice('Queue started.');await refreshState();}catch(e){notice(e.message);$('run').disabled=false;}};
(async()=>{await refreshState();if(state){$('project').value=state.default_project;await loadProject();}setInterval(refreshState,2000);})();
