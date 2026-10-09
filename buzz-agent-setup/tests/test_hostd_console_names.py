"""Local display projection only: real gateway guard and actual UI functions."""
import asyncio
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / 'scripts'))
from hostd.console_metadata import DisplayMetadata, MAX_AGE
import test_hostd_console_web as web_fixture

CHANNEL = '12345678-1234-1234-1234-123456789abc'
CHAT = 'oc_alpha'
REF = hashlib.sha256(('buzz-feishu-chat:v1:' + CHAT).encode()).hexdigest()
PUB = 'a' * 64


def document():
    return {'version': 1, 'observed_at': int(time.time()),
            'bindings': [{'binding_id': 'alpha', 'channel_id': CHANNEL, 'chat_ref': REF,
                          'chat_id': CHAT, 'chat_name': '实际测试群', 'channel_name': '实际频道'}],
            'agents': [{'pubkey': PUB, 'app_id': 'cli_alpha', 'name': 'local-agent'}]}


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root / 'names.json'
        self.write(document())

    def write(self, doc):
        self.path.write_text(json.dumps(doc)); self.path.chmod(0o600)

    def test_exact_current_metadata_and_missing_unknown_names(self):
        doc = document(); doc['bindings'][0]['chat_name'] = ''; self.write(doc)
        data = DisplayMetadata.load(self.path).public()
        self.assertEqual(data['status'], 'available')
        self.assertEqual(data['bindings'][0]['chat_name'], '')
        self.assertEqual(DisplayMetadata.load(None).public()['status'], 'unavailable')

    def test_unsafe_path_mode_hardlink_and_symlink_fail_optional(self):
        self.path.chmod(0o644)
        self.assertEqual(DisplayMetadata.load(self.path).public()['status'], 'unavailable')
        self.path.chmod(0o600); other = self.root / 'link'; other.symlink_to(self.path)
        self.assertEqual(DisplayMetadata.load(other).public()['status'], 'unavailable')
        other.unlink(); os.link(self.path, other)
        self.assertEqual(DisplayMetadata.load(self.path).public()['status'], 'unavailable')

    def test_bad_fields_mismatched_ref_duplicates_controls_and_secrets_not_projected(self):
        cases = []
        bad = document(); bad['credential'] = 'never-visible'; cases.append(bad)
        bad = document(); bad['bindings'][0]['chat_ref'] = '0' * 64; cases.append(bad)
        bad = document(); bad['bindings'][0]['chat_name'] = 'name\nheader'; cases.append(bad)
        bad = document(); bad['agents'] *= 2; cases.append(bad)
        bad = document(); bad['version'] = True; cases.append(bad)
        bad = document(); bad['agents'][0]['name'] = 'x' * 257; cases.append(bad)
        for doc in cases:
            with self.subTest(doc=cases.index(doc)):
                self.write(doc); result = DisplayMetadata.load(self.path).public()
                self.assertEqual(result['status'], 'unavailable'); self.assertEqual(result['bindings'], [])
        self.path.write_text('{"version":1,"version":1}')
        self.assertEqual(DisplayMetadata.load(self.path).public()['status'], 'unavailable')

    def test_expired_future_and_deep_json_do_not_break_console(self):
        doc = document(); snapshot = DisplayMetadata.load(self.path)
        self.assertEqual(snapshot.public(now=doc['observed_at'] - 1)['status'], 'unavailable')
        stale = snapshot.public(now=doc['observed_at'] + MAX_AGE + 1)
        self.assertEqual(stale['status'], 'stale')
        self.assertEqual(stale['bindings'][0]['chat_name'], doc['bindings'][0]['chat_name'])
        self.assertEqual(stale['agents'], doc['agents'])
        self.path.write_text('[' * 4000 + ']' * 4000)
        self.assertEqual(DisplayMetadata.load(self.path).public()['status'], 'unavailable')

    def test_snapshot_is_local_once_and_names_are_not_html_decoded(self):
        doc = document(); doc['agents'][0]['name'] = '<img src=x onerror=alert(1)>'
        self.write(doc); snapshot = DisplayMetadata.load(self.path); self.path.unlink()
        self.assertEqual(snapshot.public()['agents'][0]['name'], doc['agents'][0]['name'])


class GatewayNamesTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = web_fixture.WebTests.asyncSetUp
    asyncTearDown = web_fixture.WebTests.asyncTearDown
    headers = web_fixture.WebTests.headers
    request = web_fixture.WebTests.request

    async def test_metadata_requires_basic_and_never_contacts_upstream(self):
        path = self.authdir / 'names.json'; path.write_text(json.dumps(document())); path.chmod(0o600)
        self.gateway._metadata = DisplayMetadata.load(path)
        with mock.patch('hostd.console_web.asyncio.open_unix_connection', side_effect=AssertionError('no upstream')):
            self.assertEqual((await self.request('/api/metadata', auth=False))[0], 401)
            self.assertEqual((await self.request('/api/metadata', headers=[('Origin', 'https://evil.invalid')]))[0], 403)
            self.assertEqual((await self.request('/api/metadata', 'POST'))[0], 400)
            status, head, raw = await self.request('/api/metadata')
            self.assertEqual(status, 200); self.assertEqual(json.loads(raw)['status'], 'available')
            self.assertIn(b'Cache-Control: no-store', head)
            for secret in (self.password, self.server._token): self.assertNotIn(secret.encode(), raw)

    async def test_missing_or_secret_snapshot_does_not_affect_graph(self):
        self.gateway._metadata = DisplayMetadata.load(self.root / 'missing')
        self.assertEqual(json.loads((await self.request('/api/metadata'))[2])['status'], 'unavailable')
        self.assertEqual((await self.request('/api/graph'))[0], 200)
        doc = document(); doc['agents'][0]['name'] = self.password
        self.gateway._metadata = DisplayMetadata(doc)
        raw = (await self.request('/api/metadata'))[2]
        self.assertNotIn(self.password.encode(), raw); self.assertEqual(json.loads(raw)['status'], 'unavailable')

    @unittest.skipUnless(Path('/opt/google/chrome/chrome').exists(), 'isolated Chrome required')
    async def test_real_browser_names_and_native_anchor_hrefs(self):
        from hostd import console_macos as browser_module
        with self.store.transaction():
            self.store.conn.execute("UPDATE binding SET channel_id=? WHERE binding_id='alpha'", (CHANNEL,))
        self.store.register_agent(PUB, owner_pubkey='b' * 64, app_id='cli_alpha', config_path='/synthetic/config', now=100)
        path = self.authdir / 'names.json'; path.write_text(json.dumps(document())); path.chmod(0o600)
        self.gateway._metadata = DisplayMetadata.load(path)
        origin = self.gateway.origin
        basic = 'Basic ' + base64.b64encode(('owner:' + self.password).encode()).decode()
        class TestBrowser(browser_module.Browser):
            async def event(own, event):
                if event['method'] != 'Fetch.requestPaused':
                    return await super().event(event)
                params = event['params']; request = params['request']; session = event.get('sessionId')
                allowed = [origin + p for p in ('/', '/api/graph', '/api/metadata', '/api/events')]
                if session not in own.sessions or request['method'] != 'GET' or request['url'] not in allowed:
                    await own.cdp.command('Fetch.failRequest', {'requestId': params['requestId'], 'errorReason': 'BlockedByClient'}, session)
                    return
                headers = [{'name': k, 'value': v} for k, v in request.get('headers', {}).items() if k.lower() != 'authorization']
                headers.append({'name': 'Authorization', 'value': basic})
                await own.cdp.command('Fetch.continueRequest', {'requestId': params['requestId'], 'headers': headers}, session)
        browser = TestBrowser('/opt/google/chrome/chrome', self.root / 'chrome-profile', origin, None,
                              extra=('--headless=new', '--disable-gpu'))
        try:
            await browser.start()
            session = browser.targets[browser.page]['session']
            expression = """(()=>{const svg=document.getElementById('graph');
              const groups=[...svg.querySelectorAll('g')];const g=groups.find(g=>g.querySelector('title')?.textContent.startsWith('实际测试群 ·'));
              if(!g||!svg.textContent.includes('local-agent'))return null;g.dispatchEvent(new MouseEvent('click',{bubbles:true}));
              const a=document.querySelector('#detail a');const channel=groups.find(g=>g.querySelector('title')?.textContent.startsWith('实际频道 ·'));
              if(!a||!channel)return null;const feishu=a.getAttribute('href');channel.dispatchEvent(new MouseEvent('click',{bubbles:true}));
              const b=document.querySelector('#detail a');return {feishu,buzz:b?.getAttribute('href'),rel:b?.rel,tag:b?.tagName,
              fullId:document.querySelector('#detail').textContent.includes('12345678-1234-1234-1234-123456789abc'),
              metadata:document.getElementById('metadata-status').textContent.includes('已核验快照')};})()"""
            value = None
            for _ in range(50):
                result = await browser.cdp.command('Runtime.evaluate', {'expression': expression, 'returnByValue': True}, session)
                value = result.get('result', {}).get('value')
                if value:break
                await asyncio.sleep(.05)
            self.assertEqual(value, {'feishu': 'https://applink.feishu.cn/client/chat/open?openChatId=oc_alpha',
                                    'buzz': 'buzz://channel/' + CHANNEL, 'rel': 'noopener noreferrer',
                                    'tag': 'A', 'fullId': True, 'metadata': True})
        finally:
            self.assertTrue(await browser.close())


class UiNamesTests(unittest.TestCase):
    def run_js(self, checks):
        node = shutil.which('node') or '/home/jchen/.nvm/versions/node/v20.20.1/bin/node'
        self.assertTrue(Path(node).is_file(), 'Node is required for actual UI contracts')
        html = (TESTS.parent / 'scripts/hostd/console_ui.html').read_text()
        script = re.search(r'<script>(.*?)</script>', html, re.S)[1]
        body = script[script.index("'use strict';"):script.index("for(const button of document.querySelectorAll")]
        setup = r'''
const assert=require('node:assert/strict');
class Element {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this._text='';}set textContent(value){this._text=value;this.children=[];}get textContent(){return this._text;}append(...values){this.children.push(...values);}replaceChildren(...values){this._text='';this.children=values;}setAttribute(k,v){this.attrs[k]=v;}}
const elements=new Map();const document={getElementById:id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);},createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)};
const navigator={},window={};
'''
        data = document(); data['status'] = 'available'
        fixture = r'''
metadata=DOC;
const r=metadata.bindings[0],a=metadata.agents[0];
const n=(id,kind)=>({id,kind,visibility:'local',status:'active',label:'old '+id});
const fixture={nodes:[n('binding:'+r.binding_id,'binding'),n('channel:'+r.channel_id,'channel'),n('group:'+r.chat_ref,'group'),n('agent:'+a.pubkey,'agent'),n('app:'+a.app_id,'app')],edges:[{source:'group:'+r.chat_ref,target:'binding:'+r.binding_id,kind:'binding'},{source:'binding:'+r.binding_id,target:'channel:'+r.channel_id,kind:'binding'},{source:'agent:'+a.pubkey,target:'app:'+a.app_id,kind:'own_bot'}]};
'''.replace('DOC', json.dumps(data))
        result = subprocess.run([node, '-e', setup + body + fixture + '\n(async()=>{' + checks + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_names_exact_links_tooltip_and_safe_dom(self):
        self.run_js(r'''
metadata.bindings[0].chat_name='<img src=x onerror=alert(1)>';
graph=namedGraph(fixture);renderGraph();
const group=graph.nodes.find(n=>n.kind==='group');assert.equal(group.display_name,metadata.bindings[0].chat_name);
const target=new Element('div');showLinks(target,group);assert.equal(target.children[0].href,'https://applink.feishu.cn/client/chat/open?openChatId=oc_alpha');assert.equal(target.children[0].rel,'noopener noreferrer');
const channel=graph.nodes.find(n=>n.kind==='channel');const links=new Element('div');showLinks(links,channel);assert.equal(links.children[0].href,'buzz://channel/'+r.channel_id);
const svg=elements.get('graph');assert(svg.children.some(g=>g.children.some(c=>c.tagName==='title'&&c.textContent.includes('<img src=x'))));
assert(!svg.children.some(g=>g.children.some(c=>c.tagName==='img')));
assert.equal(graph.nodes.find(n=>n.kind==='app').display_name,'local-agent');
''')

    def test_public_and_wrong_binding_edges_never_receive_private_names_or_links(self):
        self.run_js(r'''
for(const kind of ['binding','group','channel','agent','app']){
 const data=structuredClone(fixture);data.nodes.find(n=>n.kind===kind).visibility='public';const result=namedGraph(data);const foreign=result.nodes.find(n=>n.kind===kind);assert.equal(foreign.display_name,'');assert.deepEqual(foreign.links,[]);
 if(['binding','group','channel'].includes(kind))assert.deepEqual(result.nodes.find(n=>n.kind==='group').links,[]);
}
for(const index of [0,1]){const data=structuredClone(fixture);data.edges.splice(index,1);assert.equal(namedGraph(data).nodes.find(n=>n.kind==='group').display_name,'');}
const data=structuredClone(fixture);data.nodes.find(n=>n.kind==='group').id='group:'+r.chat_ref.slice(0,8)+'0'.repeat(56);assert.equal(namedGraph(data).nodes.find(n=>n.kind==='group').display_name,'');
''')

    def test_stale_names_remain_readable_unknown_names_and_links_fail_closed(self):
        self.run_js(r'''
metadata.observed_at=1;metadata.status='stale';apply(fixture);assert.equal(graph.nodes.find(n=>n.kind==='group').display_name,'实际测试群');assert.equal(graph.nodes.find(n=>n.kind==='group').links[0].id,'oc_alpha');assert.equal(graph.nodes[0].status,'active');assert(elements.get('metadata-status').textContent.includes('缓存已超过 24 小时'));
metadata={status:'unavailable',bindings:[],agents:[]};const result=namedGraph(fixture);assert.equal(result.nodes.find(n=>n.kind==='group').display_name,'');assert.deepEqual(result.nodes.find(n=>n.kind==='group').links,[]);assert(nameOf(result.nodes[0]).includes('名称未提供'));
const target=new Element('div');showLinks(target,{links:[{kind:'feishu',id:'oc_good&evil=x'},{kind:'buzz',id:'javascript:alert(1)'},{kind:'x',id:'https://evil.invalid'}]});assert.equal(target.children.length,0);
''')

    def test_sse_replaces_selected_node_and_clears_removed_selection(self):
        self.run_js(r'''
apply(fixture);selected=graph.nodes.find(n=>n.kind==='group');await select(selected);
assert(elements.get('detail').children.some(c=>c.tagName==='p'&&c.children.some(a=>a.href&&a.href.includes('openChatId'))));
const data=structuredClone(fixture);data.nodes.find(n=>n.kind==='group').visibility='public';apply(data);
assert.equal(selected.visibility,'public');assert(!elements.get('detail').children.some(c=>c.children.some(a=>a.href)));
apply({nodes:[],edges:[]});assert.equal(selected,null);assert.equal(elements.get('operations').children.length,0);
''')

    def test_late_metadata_cannot_clear_failed_graph_readback(self):
        self.run_js(r'''
const expected=metadata;metadata={status:'unavailable',bindings:[],agents:[]};apply(fixture);
let release;const delayed=new Promise(resolve=>{release=resolve;});
globalThis.fetch=async path=>({ok:path==='/api/metadata',json:async()=>path==='/api/metadata'?await delayed:{notice:{message:'Backend unavailable'}}});
const pending=refresh();await new Promise(resolve=>setTimeout(resolve,0));
assert(elements.get('access').children.some(c=>c.textContent==='Backend unavailable'));
release(expected);await pending;assert(elements.get('access').children.some(c=>c.textContent==='Backend unavailable'));
assert.equal(graph.nodes.find(n=>n.kind==='group').display_name,'实际测试群');
''')

    def test_late_metadata_repaints_existing_graph_without_remote_n_plus_one(self):
        self.run_js(r'''
const expected=metadata;metadata={status:'unavailable',bindings:[],agents:[]};apply(fixture);
let release;const delayed=new Promise(resolve=>{release=resolve;});const calls=[];
globalThis.fetch=async path=>{calls.push(path);return {ok:true,json:async()=>path==='/api/metadata'?await delayed:fixture};};
const pending=refresh();await new Promise(resolve=>setTimeout(resolve,0));assert.equal(graph.nodes.find(n=>n.kind==='group').display_name,'');
release(expected);await pending;assert.equal(graph.nodes.find(n=>n.kind==='group').display_name,'实际测试群');assert.deepEqual(calls.sort(),['/api/graph','/api/metadata']);
''')


if __name__ == '__main__':
    unittest.main()
