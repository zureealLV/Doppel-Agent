const $=id=>document.getElementById(id);
const REVIEW_PROMPT='先绘制项目结构，再定向搜索高风险代码。只读审查可复现问题，按严重度给出文件、行号、证据和最小修复建议。';
const state={settings:null,conversations:[],groups:[],current:null,runId:null,runConversationId:null,poll:null,archived:false,group:null,events:[],started:0,nextMode:'agent',submitting:false,transition:false};
async function api(path,options={}){const headers={...(options.body?{'Content-Type':'application/json','X-Doppel-UI':'1'}:{}),...(options.headers||{})};const r=await fetch(path,{...options,headers});const data=await r.json().catch(()=>({error:`HTTP ${r.status}`}));if(!r.ok)throw new Error(data.error||`HTTP ${r.status}`);return data}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function markdown(text){let s=esc(text);const blocks=[];s=s.replace(/```([\s\S]*?)```/g,(_,v)=>`@@CODE${blocks.push(`<pre><code>${v.replace(/^\w+\n/,'')}</code></pre>`)-1}@@`);s=s.replace(/^### (.+)$/gm,'<h3>$1</h3>').replace(/^## (.+)$/gm,'<h2>$1</h2>').replace(/^# (.+)$/gm,'<h1>$1</h1>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/^[-*] (.+)$/gm,'<li>$1</li>');s=s.split(/\n{2,}/).map(v=>v.startsWith('<h')||v.startsWith('<li')||v.startsWith('@@')?v:`<p>${v.replace(/\n/g,'<br>')}</p>`).join('');return s.replace(/@@CODE(\d+)@@/g,(_,i)=>blocks[+i])}
function post(path,body){return api(path,{method:'POST',body:JSON.stringify(body)})}
function profile(id){return state.settings?.profiles?.find(x=>x.id===id)||state.settings?.profiles?.[0]}
function currentProfile(){return profile($('composer-model').value)}
function formatTime(iso){if(!iso)return'';return new Date(iso).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}

async function bootstrap(){try{const [health,settings,groups]=await Promise.all([api('/api/health'),api('/api/settings'),api('/api/groups')]);state.settings=settings;state.groups=groups;$('workspace-path').textContent=health.workspace;$('title-workspace').textContent=health.workspace.split(/[\\/]/).pop();$('server-status').textContent='本地核心在线';renderProfiles();renderGroups();await refreshConversations();const remembered=localStorage.getItem('doppel-conversation');if(remembered&&state.conversations.some(x=>x.id===remembered))await openConversation(remembered);else if(state.conversations[0])await openConversation(state.conversations[0].id)}catch(e){$('server-status').textContent=`连接失败：${e.message}`;$('server-dot').style.background='#e98e97'}}
function renderProfiles(){const profiles=state.settings?.profiles||[];$('composer-model').innerHTML=profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} · ${esc(p.model)}</option>`).join('');$('composer-model').value=state.current?.profile_id||state.settings?.active_profile_id||profiles[0]?.id||'';const p=currentProfile();$('provider-summary').textContent=p?`${p.name} · ${p.api_key_saved?'Key 已保存':'无 Key'}`:'尚未配置';$('settings-profile').innerHTML=profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}
function renderGroups(){const all=`<button class="${state.group===null?'active':''}" data-group=""><span>全部对话</span><small>${state.conversations.length}</small></button>`;$('group-list').innerHTML=all+state.groups.map(g=>`<button class="${state.group===g.id?'active':''}" data-group="${g.id}"><span>${esc(g.name)}</span><small>${g.conversation_count}</small></button>`).join('');$('manage-group').innerHTML='<option value="">未分组</option>'+state.groups.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join('')}
async function refreshConversations(){state.conversations=await api(`/api/conversations?archived=${state.archived?1:0}`);renderGroups();renderConversationList()}
function renderConversationList(){const q=$('conversation-search').value.trim().toLowerCase();const rows=state.conversations.filter(c=>(!state.group||c.group_id===state.group)&&(!q||`${c.title} ${c.preview}`.toLowerCase().includes(q)));$('conversation-section-title').textContent=state.archived?'已归档':'最近对话';$('conversation-list').innerHTML=rows.length?rows.map(c=>`<button class="conversation-item ${state.current?.id===c.id?'active':''}" data-id="${c.id}"><b>${esc(c.title)}</b><span>${esc(c.group_name||formatTime(c.updated_at))} · ${c.message_count} 条消息</span></button>`).join(''):'<p class="list-empty">这里还没有对话。</p>'}
async function createConversation(title='新对话'){const c=await post('/api/conversations',{title});state.archived=false;state.current=c;localStorage.setItem('doppel-conversation',c.id);await refreshConversations();renderCurrent();$('prompt').focus();return c}
async function prepareReviewDraft(){
  if(state.runId||state.submitting||state.transition)return;
  state.transition=true;$('start-review').disabled=true;
  try{
    const c=await post('/api/conversations/review-draft',{});
    state.archived=false;state.group=null;state.current=c;state.nextMode='review';
    localStorage.setItem('doppel-conversation',c.id);
    $('show-active').classList.add('active');$('show-archive').classList.remove('active');
    await refreshConversations();renderCurrent();
    $('effort-select').value='quick';$('prompt').value=REVIEW_PROMPT;$('prompt').focus();
  }catch(e){alert(e.message)}finally{state.transition=false;$('start-review').disabled=Boolean(state.runId||state.submitting)}
}
async function openConversation(id){state.current=await api(`/api/conversations/${id}`);localStorage.setItem('doppel-conversation',id);renderCurrent();renderConversationList();if(state.current.profile_id&&profile(state.current.profile_id))$('composer-model').value=state.current.profile_id}
function renderCurrent(){const c=state.current;$('conversation-title').textContent=c?.title||'新对话';$('manage-conversation').disabled=!c;$('welcome').hidden=!!c?.messages?.length;$('message-list').innerHTML=(c?.messages||[]).map(m=>`<article class="message ${m.role}"><div class="message-label">${m.role==='user'?'你':`Doppel${m.model?` · ${esc(m.model)}`:''}`}</div><div class="message-body">${m.role==='assistant'?markdown(m.content):esc(m.content).replace(/\n/g,'<br>')}</div></article>`).join('');requestAnimationFrame(()=>$('message-scroll').scrollTop=$('message-scroll').scrollHeight)}

function runConfig(){return{profile_id:$('composer-model').value}}
async function submitRun(){
  const prompt=$('prompt').value.trim();if(!prompt||state.runId||state.submitting)return;
  state.submitting=true;setRunning(false);
  try{
    if(!state.current)await createConversation();
    const conversationId=state.current.id,mode=state.nextMode;state.nextMode='agent';
    const result=await post('/api/runs',{prompt,mode,conversation_id:conversationId,config:runConfig(),effort:$('effort-select').value,allow_write:$('allow-write').checked,allow_command:$('allow-command').checked,allow_mcp:$('allow-mcp').checked,allow_delegate:$('allow-delegate').checked});
    state.runId=result.run_id;state.runConversationId=conversationId;state.started=Date.now();state.events=[];$('prompt').value='';
    if(state.current?.id===conversationId){state.current.messages.push({role:'user',content:prompt,model:currentProfile()?.model});renderCurrent()}
    setRunning(true);pollRun();
  }catch(e){state.nextMode='agent';alert(e.message)}finally{state.submitting=false;setRunning(Boolean(state.runId))}
}
function setRunning(on){
  const busy=on||state.submitting;
  $('run-button').disabled=busy;$('start-review').disabled=busy||state.transition;
  $('run-status').textContent=on?'运行中':state.submitting?'提交中':'空闲';$('run-status').classList.toggle('running',busy);
  $('trace-indicator').textContent=on?'● 运行中':state.submitting?'● 提交中':'● 空闲';$('active-run-id').textContent=on&&state.runId?state.runId.slice(0,8):'';
}
async function pollRun(){
  clearTimeout(state.poll);const runId=state.runId;if(!runId)return;
  try{
    const [run,events,tasks,approvals]=await Promise.all([api(`/api/runs/${runId}`),api(`/api/runs/${runId}/events`),api(`/api/runs/${runId}/tasks`),api(`/api/runs/${runId}/approvals`)]);
    if(state.runId!==runId)return;state.events=events;renderTrace(events,tasks);renderApprovals(approvals);
    if(['completed','failed'].includes(run.status)){
      const conversationId=state.runConversationId;state.runId=null;state.runConversationId=null;setRunning(false);
      if(conversationId&&state.current?.id===conversationId)await openConversation(conversationId);
      await refreshConversations();return;
    }
  }catch(e){$('trace-indicator').textContent=`● ${e.message}`}
  if(state.runId===runId)state.poll=setTimeout(pollRun,700);
}
function renderTrace(events,tasks){$('event-count').textContent=events.length;$('task-count').textContent=tasks.length;$('task-list').innerHTML=tasks.length?tasks.map(t=>`<li><b>${esc(t.status)}</b> ${esc(t.title||t.description||'任务')}</li>`).join(''):'<li class="trace-empty">没有显式任务计划。</li>';const names={run_started:'开始运行',context_watermark:'整理上下文',context_compacted:'压缩上下文',model_turn:'模型推理',model_usage:'Token 计量',tool_requested:'调用工具',tool_completed:'工具完成',tool_failed:'工具失败',run_completed:'完成',run_failed:'失败'};$('event-list').innerHTML=events.length?events.slice(-80).reverse().map(e=>`<li><b>${esc(names[e.kind]||e.kind)}</b>${e.payload?.name?` · ${esc(e.payload.name)}`:''}</li>`).join(''):'<li class="trace-empty">等待事件…</li>';const usages=events.filter(e=>e.kind==='model_usage').map(e=>e.payload||{});const input=usages.reduce((n,u)=>n+(u.prompt_tokens||u.input_tokens||0),0),output=usages.reduce((n,u)=>n+(u.completion_tokens||u.output_tokens||0),0);const tools=events.filter(e=>e.kind==='tool_requested').length;$('metric-tools').textContent=tools;$('metric-tokens').textContent=(input+output).toLocaleString();$('metric-time').textContent=state.started?`${((Date.now()-state.started)/1000).toFixed(1)}s`:'—';const p=currentProfile(),cost=((input*(p?.input_price||0)+output*(p?.output_price||0))/1e6);$('metric-cost').textContent=p&&(p.input_price||p.output_price)?`¥/${cost.toFixed(4)}`.replace('¥/','¥'):'未设置'}
function renderApprovals(items){$('approval-section').hidden=!items.length;$('approval-list').innerHTML=items.map(a=>`<div><code>${esc(a.tool)}</code> <button data-approval="${a.id}" data-allow="1">允许</button> <button data-approval="${a.id}" data-allow="0">拒绝</button></div>`).join('')}

function fillProfile(id){const p=profile(id);if(!p)return;$('settings-profile').value=p.id;$('profile-name').value=p.name;$('preset').value=p.preset;$('model').value=p.model;$('base-url').value=p.base_url;$('input-price').value=p.input_price;$('output-price').value=p.output_price;$('api-key').value='';$('key-state').textContent=p.api_key_saved?'Key 已安全保存':'尚未保存';$('config-form').dataset.profile=p.id}
function configFromForm(){const preset=$('preset').value;return{provider:preset==='mock'?'mock':'openai',preset,name:$('profile-name').value.trim(),model:$('model').value.trim(),base_url:$('base-url').value.trim(),input_price:Number($('input-price').value||0),output_price:Number($('output-price').value||0),api_key:$('api-key').value}}
async function saveProfile(forget=false){const id=$('config-form').dataset.profile;state.settings=await post('/api/settings',{profile_id:id,config:configFromForm(),forget_key:forget});renderProfiles();fillProfile(state.settings.active_profile_id);$('probe-result').textContent='设置已保存'}
async function testProfile(){try{$('probe-result').textContent='正在测试连接…';const config=configFromForm();if($('config-form').dataset.profile!=='__new__')config.profile_id=$('config-form').dataset.profile;const result=await post('/api/probe',{config});$('probe-result').textContent=result.reply}catch(e){$('probe-result').textContent=`连接失败：${e.message}`}}

function setupSplitters(){
  const bounds={left:[210,390],right:[260,480]};
  function clamp(side,value){const [min,max]=bounds[side];return Math.max(min,Math.min(max,Number(value)))}
  function bind(id,side){const bar=$(id);bar.addEventListener('pointerdown',e=>{bar.setPointerCapture(e.pointerId);bar.classList.add('dragging');const move=ev=>{const raw=side==='left'?ev.clientX:innerWidth-ev.clientX,v=clamp(side,raw);document.documentElement.style.setProperty(`--${side}`,`${v}px`);localStorage.setItem(`doppel-${side}`,String(v))};const stop=()=>{bar.onpointermove=null;bar.onpointerup=null;bar.onpointercancel=null;bar.classList.remove('dragging')};bar.onpointermove=move;bar.onpointerup=stop;bar.onpointercancel=stop})}
  bind('left-splitter','left');bind('right-splitter','right');
  for(const side of ['left','right']){const raw=Number(localStorage.getItem(`doppel-${side}`));if(Number.isFinite(raw)&&raw>0){const v=clamp(side,raw);document.documentElement.style.setProperty(`--${side}`,`${v}px`);localStorage.setItem(`doppel-${side}`,String(v))}}
}

$('run-form').addEventListener('submit',e=>{e.preventDefault();submitRun()});
$('prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();submitRun()}});
$('new-conversation').onclick=()=>{if(state.submitting)return;state.nextMode='agent';createConversation()};
$('start-review').onclick=prepareReviewDraft;
$('conversation-list').onclick=e=>{const b=e.target.closest('[data-id]');if(b){state.nextMode='agent';openConversation(b.dataset.id)}};
$('conversation-search').oninput=renderConversationList;$('group-list').onclick=e=>{const b=e.target.closest('[data-group]');if(!b)return;state.group=b.dataset.group||null;renderGroups();renderConversationList()};
$('show-active').onclick=async()=>{state.archived=false;state.group=null;$('show-active').classList.add('active');$('show-archive').classList.remove('active');await refreshConversations()};$('show-archive').onclick=async()=>{state.archived=true;state.group=null;$('show-archive').classList.add('active');$('show-active').classList.remove('active');await refreshConversations()};
$('add-group').onclick=()=>{$('group-name').value='';$('group-dialog').showModal()};$('save-group').onclick=async()=>{await post('/api/groups',{name:$('group-name').value});$('group-dialog').close();state.groups=await api('/api/groups');renderGroups()};
$('manage-conversation').onclick=()=>{if(!state.current)return;$('manage-title').value=state.current.title;$('manage-group').value=state.current.group_id||'';$('archive-conversation').textContent=state.current.archived?'取消归档':'归档';$('manage-dialog').showModal()};$('save-conversation').onclick=async()=>{await post(`/api/conversations/${state.current.id}/rename`,{title:$('manage-title').value});await post(`/api/conversations/${state.current.id}/group`,{group_id:$('manage-group').value||null});$('manage-dialog').close();await openConversation(state.current.id);await refreshConversations()};$('archive-conversation').onclick=async()=>{await post(`/api/conversations/${state.current.id}/archive`,{archived:!Boolean(state.current.archived)});$('manage-dialog').close();state.current=null;renderCurrent();await refreshConversations()};$('delete-conversation').onclick=async()=>{if(!confirm('永久删除这个对话？'))return;await post(`/api/conversations/${state.current.id}/delete`,{});$('manage-dialog').close();state.current=null;renderCurrent();await refreshConversations()};
$('composer-model').onchange=async()=>{if(state.current)await post(`/api/conversations/${state.current.id}/profile`,{profile_id:$('composer-model').value});const p=currentProfile();$('provider-summary').textContent=`${p.name} · ${p.model}`};$('open-settings').onclick=()=>{fillProfile(state.settings.active_profile_id);$('settings-dialog').showModal()};$('settings-profile').onchange=()=>fillProfile($('settings-profile').value);$('new-profile').onclick=()=>{$('config-form').dataset.profile='__new__';$('profile-name').value='新模型';$('preset').value='openai';$('model').value='';$('base-url').value='';$('input-price').value=0;$('output-price').value=0;$('api-key').value='';$('key-state').textContent='新档案'};$('save-settings').onclick=()=>saveProfile(false);$('forget-key').onclick=()=>saveProfile(true);$('probe-button').onclick=testProfile;$('toggle-key').onclick=()=>{$('api-key').type=$('api-key').type==='password'?'text':'password'};$('delete-profile').onclick=async()=>{const id=$('settings-profile').value;if(!confirm('删除这个模型档案？'))return;state.settings=await post(`/api/settings/profiles/${id}/delete`,{});renderProfiles();fillProfile(state.settings.active_profile_id)};
$('approval-list').onclick=async e=>{const b=e.target.closest('[data-approval]');if(!b)return;await post(`/api/runs/${state.runId}/approvals/${b.dataset.approval}/decision`,{allow:b.dataset.allow==='1'});pollRun()};
document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{state.nextMode=b.dataset.mode||'agent';$('prompt').value=b.dataset.prompt;$('prompt').focus()});
$('toggle-inspector').onclick=()=>document.querySelector('.app-shell').classList.toggle('inspector-hidden');$('collapse-sidebar').onclick=$('toggle-sidebar').onclick=()=>document.querySelector('.app-shell').classList.toggle('sidebar-hidden');
function windowAction(action){if(action==='maximize')document.documentElement.classList.toggle('maximized');window.pywebview?.api?.window_action(action)}
document.querySelectorAll('[data-window-action]').forEach(b=>b.onclick=()=>windowAction(b.dataset.windowAction));
$('titlebar').ondblclick=e=>{if(!e.target.closest('.no-drag'))windowAction('maximize')};
window.addEventListener('pywebviewready',()=>document.documentElement.classList.add('desktop'));setupSplitters();bootstrap();
