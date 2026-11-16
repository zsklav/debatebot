const {test} = require('node:test');
const assert = require('node:assert/strict');

// Minimal DOM contract for pure renderer checks. Browser fixtures verify actual DOM behavior.
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.value = ''; this.handlers = {}; this.dataset = {}; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  querySelectorAll() { return this.children; }
  set textContent(value) { this.value = String(value); this.children = []; }
  get textContent() { return this.value + this.children.map(x => x.textContent).join(' '); }
  set innerHTML(value) { throw new Error('Renderer must not interpret HTML'); }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren() { this.children = []; this.value = ''; }
}
global.document = {createElement: tag => new Element(tag), getElementById: () => null};
const {safeUrl, renderResult} = require('../frontend/evidence-lab.js');

test('source URLs allow only credential-free HTTP(S)', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,test', 'file:///tmp/test',
                     'https://user:password@example.org', '/relative', null]) {
    assert.equal(safeUrl(url), null);
  }
  assert.equal(safeUrl('https://example.org/study'), 'https://example.org/study');
});

test('untrusted result strings remain text and unsafe source URLs are omitted', () => {
  const root = new Element('main');
  renderResult({weakness: {severity: 7, weakest_claim: '<img src=x onerror=alert(1)>',
    why_weak: 'A reason', the_attack: 'A question', how_to_defend: 'A repair'},
    evidence: {verdict: 'supported', explanation: '<script>alert(1)</script>',
      suggested_revision: 'A narrower claim', citations: [{relation: 'supports', quote: '<b>source</b>',
        source: {title: 'Source', source_url: 'javascript:alert(1)'}}]}}, 'evidence', root);
  assert.match(root.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(root.textContent, /<script>alert\(1\)<\/script>/);
  const all = node => [node, ...node.children.flatMap(all)];
  assert.equal(all(root).filter(node => node.tagName === 'a').length, 0);
});

test('partial research renders termination and inspected passages without assessment', () => {
  const root = new Element('main');
  renderResult({status: 'partial', termination_reason: 'step_limit', tool_calls: 4,
    searches: 2, elapsed_seconds: 2, events: [], assessment: null,
    read_passages: [{chunk_id: 'c1', text: 'Unassessed evidence'}]}, 'research', root);
  assert.match(root.textContent, /partial results/);
  assert.match(root.textContent, /not a factual verdict/);
  assert.match(root.textContent, /Unassessed evidence/);
  assert.doesNotMatch(root.textContent, /Evidence assessment/);
});

test('rerender replaces previous result instead of accumulating stale conclusions', () => {
  const root = new Element('main');
  root.append(new Element('old'));
  renderResult({status: 'complete', termination_reason: 'finished', tool_calls: 0,
    searches: 0, elapsed_seconds: 0, events: [], assessment: {
      verdict: 'insufficient_evidence', explanation: 'No evidence',
      suggested_revision: 'Provide sources', citations: []}}, 'research', root);
  assert.equal(root.children.some(node => node.tagName === 'old'), false);
  assert.match(root.textContent, /insufficient evidence/);
  assert.match(root.textContent, /Research finished — evidence insufficient/);
});

test('completed runs remember their ID and reopening uses GET without generation', async () => {
  const names = ['lab-form','lab-input','lab-mode','lab-submit','lab-status','lab-result',
                 'lab-run-id','lab-reopen','lab-mode-help','lab-count'];
  const nodes = Object.fromEntries(names.map(name => [name,new Element('div')]));
  const previousGetter = document.getElementById;
  document.getElementById = id => nodes[id] || null;
  const saved = new Map();
  global.localStorage = {getItem: key => saved.get(key), setItem: (key,value) => saved.set(key,value)};
  const calls = [];
  const result = {status:'partial',termination_reason:'step_limit',tool_calls:1,searches:1,
                  elapsed_seconds:1,events:[],assessment:null,run_id:'saved-id',storage:'postgresql'};
  global.fetch = async (url,options) => {
    calls.push({url,options});
    return {ok:true,json:async () => options ? result : {id:'saved-id',kind:'research',input_text:'Saved claim',result}};
  };
  try {
    const {init} = require('../frontend/evidence-lab.js');
    init(); nodes['lab-mode'].value='research'; nodes['lab-input'].value='Original claim';
    await nodes['lab-form'].handlers.submit({preventDefault(){}});
    assert.equal(saved.get('debatebot.run'),'saved-id');
    assert.equal(nodes['lab-run-id'].value,'saved-id');
    await nodes['lab-reopen'].handlers.click();
    assert.equal(calls[1].url,'/api/runs/saved-id');
    assert.equal(calls[1].options,undefined);
    assert.equal(nodes['lab-input'].value,'Saved claim');
    assert.match(nodes['lab-status'].textContent,/No new model call/);
    assert.equal(nodes['lab-submit'].disabled,false);
  } finally {
    document.getElementById=previousGetter; delete global.localStorage; delete global.fetch;
  }
});

test('web mode sends an explicit web source and shows provider errors clearly', async () => {
  const names=['lab-form','lab-input','lab-mode','lab-submit','lab-status','lab-result','lab-run-id','lab-reopen','lab-mode-help','lab-count'];
  const nodes=Object.fromEntries(names.map(id=>[id,new Element('div')]));
  const previous=document.getElementById;
  document.getElementById=id=>nodes[id]||null;
  let request;
  global.fetch=async (url,options)=>{request={url,options};return {ok:false,status:503,json:async()=>({code:'web_unavailable'})};};
  try {
    require('../frontend/evidence-lab.js').init();
    nodes['lab-mode'].value='web_research';nodes['lab-input'].value='A public research claim';
    await nodes['lab-form'].handlers.submit({preventDefault(){}});
    assert.equal(request.url,'/api/research');
    assert.equal(JSON.parse(request.options.body).source,'web');
    assert.match(nodes['lab-status'].textContent,/Web search is unavailable/);
    assert.equal(nodes['lab-input'].value,'A public research claim');
  } finally {document.getElementById=previous;delete global.fetch;}
});

test('sign-out invalidates late private results from the old account', async () => {
  const names=['lab-form','lab-input','lab-mode','lab-submit','lab-status','lab-result','lab-run-id','lab-reopen','lab-mode-help','lab-count'];
  const nodes=Object.fromEntries(names.map(id=>[id,new Element('div')]));
  const previous=document.getElementById;
  document.getElementById=id=>nodes[id]||null;
  const events={};
  global.window={addEventListener:(name,handler)=>events[name]=handler};
  let resolveRequest;
  global.fetch=()=>new Promise(resolve=>{resolveRequest=resolve;});
  try {
    require('../frontend/evidence-lab.js').init();
    nodes['lab-input'].value='Private old claim';nodes['lab-mode'].value='research';
    const request=nodes['lab-form'].handlers.submit({preventDefault(){}});
    events['debatebot:authchanged']({detail:{reset:true}});
    resolveRequest({ok:true,json:async()=>({status:'complete',run_id:'old-private-id'})});
    await request;
    assert.equal(nodes['lab-result'].children.length,0);
    assert.equal(nodes['lab-input'].value,'');
    assert.equal(nodes['lab-run-id'].value,'');
    assert.equal(nodes['lab-submit'].disabled,false);
  } finally {document.getElementById=previous;delete global.window;delete global.fetch;}
});

test('login UI clears passwords and gates the private workspace', async () => {
  const names=['auth-form','auth-status','auth-user','auth-logout','app-workspace','auth-username','auth-password'];
  const nodes=Object.fromEntries(names.map(id=>[id,new Element('div')]));
  const previous=document.getElementById;
  document.getElementById=id=>nodes[id];
  const events=[];
  global.window={dispatchEvent:event=>events.push(event)};
  global.CustomEvent=class {constructor(type,options){this.type=type;this.detail=options.detail;}};
  let authenticated=false;
  global.fetch=async url=>({ok:true,json:async()=>({authenticated,required:true,user:authenticated?{username:'alice'}:null})});
  try {
    require('../frontend/auth-ui.js');
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(nodes['app-workspace'].hidden,true);
    nodes['auth-username'].value='alice';nodes['auth-password'].value='private-password-42';
    authenticated=true;
    await nodes['auth-form'].handlers.submit({preventDefault(){},submitter:{value:'login'}});
    assert.equal(nodes['app-workspace'].hidden,false);
    assert.equal(nodes['auth-password'].value,'');
    assert.match(nodes['auth-user'].textContent,/alice/);
    assert.equal(events.at(-1).detail.reset,true);
    authenticated=false;
    await nodes['auth-logout'].handlers.click();
    assert.equal(nodes['app-workspace'].hidden,true);
  } finally {document.getElementById=previous;delete global.window;delete global.CustomEvent;delete global.fetch;}
});

// Exercise actual debate handlers with a deferred provider response.
test('debate blocks duplicate turns and preserves an unsent argument after failure', async () => {
  const vm = require('node:vm');
  const fs = require('node:fs');
  class Node {
    constructor() { this.handlers={}; this.children=[]; this.value=''; this.textContent=''; this.classList={add(){},remove(){}}; }
    addEventListener(event, handler) { this.handlers[event]=handler; }
    querySelectorAll() { return this.children; }
    querySelector() { return this.sendButton ||= new Node(); }
    appendChild(node) { this.children.push(node); }
    replaceChildren() { this.children=[]; }
    focus() {}
    scrollIntoView() {}
    set innerHTML(value) { this.children=[]; this.html=value; }
  }
  const nodes=new Map(), byId=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
  let rejectTurn, turnCalls=0;
  const context={document:{getElementById:byId,createElement:()=>new Node()},window:{addEventListener(){}},
    localStorage:{getItem(){},setItem(){}},fetch:async(path)=>{
      if(path==='/api/personas')return {ok:true,json:async()=>({personas:[{key:'socratic',name:'Professor',tagline:'Ask why'}]})};
      if(path==='/api/debate')return {ok:true,json:async()=>({session_id:'test',persona_name:'Professor'})};
      turnCalls++; return new Promise((resolve,reject)=>{rejectTurn=reject;});
    }};
  vm.runInNewContext(fs.readFileSync(require.resolve('../frontend/debate-ui.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  await byId('persona-buttons').children[0].onclick();
  byId('turn-input').value='My argument';
  const event={preventDefault(){}};
  const first=byId('turn-form').handlers.submit(event);
  await byId('turn-form').handlers.submit(event);
  assert.equal(turnCalls,1);
  assert.equal(byId('turn-input').disabled,true);
  rejectTurn(new Error('Offline'));
  await first;
  assert.equal(byId('turn-input').value,'My argument');
  assert.equal(byId('messages').children.length,0);
  assert.equal(byId('turn-input').disabled,false);
  assert.match(byId('turn-meta').textContent,/Offline/);
});

test('research activity keeps raw tool data inside collapsed disclosures', () => {
  const root = new Element('main');
  renderResult({status:'complete',termination_reason:'finished',tool_calls:1,searches:1,
    elapsed_seconds:1,events:[{tool:'search_evidence',arguments:{query:'hybrid retention'},
    output:{results:[{title:'A study',chunk_id:'c1'}]}}],assessment:null}, 'research', root);
  const activity=root.children.find(node=>node.tagName==='details');
  assert.ok(activity);
  assert.ok(!activity.open);
  assert.match(activity.textContent,/Search evidence/);
  assert.match(activity.textContent,/hybrid retention/);
  const debug=activity.children.find(node=>node.tagName==='details');
  assert.ok(debug);
  assert.ok(!debug.open);
});

test('workspace tabs support keyboard navigation without clearing draft input', () => {
  const vm=require('node:vm'), fs=require('node:fs');
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,{handlers:{},value:'draft',attributes:{},
    addEventListener(name,fn){this.handlers[name]=fn;},setAttribute(name,value){this.attributes[name]=value;},focus(){this.focused=true;},scrollIntoView(){}}); return nodes.get(id);};
  vm.runInNewContext(fs.readFileSync(require.resolve('../frontend/workspace-ui.js'),'utf8'),
    {document:{getElementById:get},window:{addEventListener(){}}});
  get('tab-evidence').handlers.click();
  assert.equal(get('view-debate').hidden,true);
  assert.equal(get('view-evidence').hidden,false);
  assert.equal(get('tab-evidence').attributes['aria-selected'],'true');
  get('tab-evidence').handlers.keydown({key:'ArrowRight',preventDefault(){}});
  assert.equal(get('tab-critique').focused,true);
  assert.equal(get('view-critique').hidden,false);
  assert.equal(get('tab-evidence').tabIndex,-1);
  get('tab-critique').handlers.keydown({key:'Home',preventDefault(){}});
  assert.equal(get('view-debate').hidden,false);
  assert.equal(get('lab-input').value,'draft');
});

test('local preflight abstention shows zero model calls and screened candidates', () => {
  const root=new Element('main');
  renderResult({status:'complete',termination_reason:'no_relevant_evidence',model_calls:0,
    tool_calls:0,searches:1,elapsed_seconds:.1,candidate_count:0,inspected_count:0,
    events:[{tool:'local_relevance_preflight',arguments:{query:'CEO not smart'},output:{}}],
    assessment:{verdict:'insufficient_evidence',explanation:'No relevant local passages.',
      suggested_revision:'Provide relevant sources.',citations:[],
      relevance_screen:{rejected:3,warning:'May miss paraphrases.'}}},'research',root);
  assert.match(root.textContent,/0 LLM calls; no web search/);
  assert.match(root.textContent,/3 candidate passages excluded/);
  assert.match(root.textContent,/evidence insufficient/);
  assert.match(root.textContent,/Local relevance check/);
});

test('chat formats headings, emphasis and lists without interpreting model HTML', () => {
  const {render}=require('../frontend/message-format.js');
  const root=new Element('div');
  render(root,'**ACKNOWLEDGE**\nYour **strongest point** matters.\n\n**THE OPPOSING CASE**\n- First reason\n- Second reason with `code`\n\n<img src=x onerror=alert(1)>\n<script>alert(1)</script>');
  const all=node=>[node,...node.children.flatMap(all)];
  const nodes=all(root);
  assert.equal(nodes.filter(n=>n.tagName==='h4').length,2);
  assert.equal(nodes.filter(n=>n.tagName==='li').length,2);
  assert.equal(nodes.filter(n=>n.tagName==='strong').length,1);
  assert.equal(nodes.filter(n=>['img','script','a'].includes(n.tagName)).length,0);
  assert.match(root.textContent,/<img src=x onerror/);
  assert.doesNotMatch(root.textContent,/\*\*ACKNOWLEDGE\*\*/);
});

test('chat renders ordered lists and unfinished code fences safely', () => {
  const {render}=require('../frontend/message-format.js');
  const root=new Element('div');
  render(root,'1. First\n2. Second\n\n```html\n<script>example</script>');
  assert.equal(root.children[0].tagName,'ol');
  assert.equal(root.children[0].children.length,2);
  assert.equal(root.children.at(-1).tagName,'pre');
  assert.match(root.textContent,/<script>example<\/script>/);
});

test('post-interview reports keep model text inert and label synthetic results', () => {
  const {renderReport}=require('../frontend/post-review.js');
  const root=new Element('main');
  renderReport({demo:true,status:'complete',criteria:[{id:'c1',label:'Caching'}],
    final:{summary:'<script>unsafe</script>',findings:[{criterion_id:'c1',status:'needs_verification',
      explanation:'<img src=x onerror=alert(1)>',resolution:'unresolved',
      evidence:[{source_id:'transcript-1',quote:'Exact candidate text'}]}]},
    reviews:[],sources:[{id:'transcript-1',speaker:'Candidate',kind:'transcript',start_line:2,end_line:2,text:'Exact candidate text'}],
    audit:{model_requests:0,elapsed_seconds:1},scope:'Human review required.'}, root);
  assert.match(root.textContent,/outputs are scripted/);
  assert.match(root.textContent,/<script>unsafe<\/script>/);
  assert.match(root.textContent,/<img src=x/);
  assert.match(root.textContent,/Exact candidate text/);
  const all=n=>[n,...n.children.flatMap(all)];
  assert.equal(all(root).filter(n=>['img','script'].includes(n.tagName)).length,0);
});

test('partial post-interview report never presents a consolidated assessment', () => {
  const {renderReport}=require('../frontend/post-review.js');
  const root=new Element('main');
  renderReport({demo:false,status:'partial',criteria:[],final:null,reviews:[],sources:[],
    audit:{model_requests:2,elapsed_seconds:1},scope:'Human review required.'},root);
  assert.match(root.textContent,/No consolidated assessment/);
  assert.doesNotMatch(root.textContent,/Review summary/);
});

test('notebook timeline preserves timestamps, inert text and unanswered prompts', () => {
  const {renderTimeline, stamp}=require('../frontend/notebook.js');
  const root=new Element('main');
  renderTimeline({sources:[
    {id:'s1',speaker:'Interviewer',text:'<script>Question?</script>',start_time:'00:12'},
    {id:'s2',speaker:'Candidate',text:'<img src=x onerror=alert(1)>',start_time:'00:19',end_time:'00:31'},
    {id:'s3',speaker:'Interviewer',text:'Unanswered follow-up.'}],
    notes:[{id:'note-001',body:'A human note',kind:'human_note',source_ids:['s1','s2']},
      {id:'note-002',body:'',kind:'source_excerpt',source_ids:['s3']}],
    timeline:[{note_id:'note-001',question_source_ids:['s1'],answer_source_ids:['s2']},
      {note_id:'note-002',question_source_ids:['s3'],answer_source_ids:[]}]},root);
  assert.match(root.textContent,/00:19/);
  assert.match(root.textContent,/No candidate answer captured/);
  assert.match(root.textContent,/Human-edited note/);
  assert.equal(stamp({}), 'Time unavailable');
  const all=n=>[n,...n.children.flatMap(all)];
  assert.equal(all(root).filter(n=>['script','img'].includes(n.tagName)).length,0);
  assert.equal(all(root).filter(n=>n.tagName==='textarea').length,2);
  assert.match(root.textContent,/<img src=x/);
});

test('notebook edit history renders prior and corrected notes as inert text', () => {
  const {renderHistory}=require('../frontend/notebook.js');
  const root=new Element('main');
  renderHistory([{revision:1,created_at:'2026-11-15T10:00:00Z',changes:[
    {field:'note-001',before:'Original excerpt.',after:'<script>Edited note</script>'}]}],root);
  assert.match(root.textContent,/Revision 1/);
  assert.match(root.textContent,/Original excerpt/);
  assert.match(root.textContent,/<script>Edited note/);
  assert.equal(root.children[0].tagName,'details');
});

test('automation jobs identify synthetic capture and keep provider text inert', () => {
  const {renderJobs}=require('../frontend/meet-automation.js');
  const root=new Element('main');
  renderJobs([{id:'job-1',provider:'demo',status:'needs_speakers',attempts:2,google_requests:0,
    message:'<script>Provider text</script>'}],root,{prepare(){},change(){}});
  assert.match(root.textContent,/Synthetic automation demo/);
  assert.match(root.textContent,/No live Google account/);
  assert.match(root.textContent,/0 recorded Meet API reads/);
  assert.match(root.textContent,/Confirm speakers/);
  assert.match(root.textContent,/<script>Provider text/);
  const all=n=>[n,...n.children.flatMap(all)];
  assert.equal(all(root).filter(n=>n.tagName==='script').length,0);
});

test('AI notes show source quotes safely without replacing human notes', () => {
  const {renderDigest}=require('../frontend/notebook.js');
  const root=new Element('main');
  renderDigest({sources:[{id:'s2',start_time:'00:19'}],ai_digest:{model:'test-model',scope:'Human review required.',
    points:[{text:'<img src=x>Summary',quote:'Exact source text.',source_id:'s2'}]}},root);
  assert.match(root.textContent,/Human review required/);
  assert.match(root.textContent,/Exact source text/);
  assert.match(root.textContent,/00:19/);
  const all=n=>[n,...n.children.flatMap(all)];
  assert.equal(all(root).filter(n=>n.tagName==='img').length,0);
  assert.equal(all(root).find(n=>n.tagName==='a').href,'#notebook-source-s2');
});
