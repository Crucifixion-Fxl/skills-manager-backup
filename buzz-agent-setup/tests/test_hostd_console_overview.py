"""Default overview stays readable without exposing public graph metadata."""
import unittest
import test_hostd_console_names as names


class OverviewTests(unittest.TestCase):
    run_js = names.UiNamesTests.run_js

    def test_one_relationship_row_agent_dedup_links_and_expansion_survive_snapshot(self):
        self.run_js(r'''
fixture.edges.push({source:'agent:'+a.pubkey,target:'channel:'+r.channel_id,kind:'member'});
fixture.edges.push({source:'agent:'+a.pubkey,target:'group:'+r.chat_ref,kind:'in_group'});
fixture.nodes.push(n('connection:socket:technical','connection'));
apply(fixture);
const overview=elements.get('overview');assert.equal(overview.children.length,1);
let row=overview.children[0],pair=row.children[0];
assert.equal(pair.children[0].children[1].textContent,r.chat_name);
assert.equal(pair.children[2].children[1].textContent,r.channel_name);
assert.equal(pair.children[0].children[2].children[0].href,'https://applink.feishu.cn/client/chat/open?openChatId='+r.chat_id);
assert.equal(pair.children[2].children[2].children[0].href,'buzz://channel/'+r.channel_id);
let detail=row.children[2];assert.equal(detail.open,false);assert.equal(detail.children[0].textContent,'1 个已登记 Agent');
assert.equal(detail.children[1].children.length,1);assert.equal(detail.children[1].children[0].textContent,a.name);
detail.open=true;detail.ontoggle();apply(fixture);assert.equal(overview.children[0].children[2].open,true);
apply({nodes:[],edges:[]});assert.equal(expandedBindings.size,0);
''')

    def test_attention_first_and_missing_public_group_has_no_private_link(self):
        self.run_js(r'''
const other={...r,binding_id:'other',chat_ref:'b'.repeat(64),chat_id:'oc_other',channel_id:'22222222-2222-4222-8222-222222222222',chat_name:'其他群',channel_name:'其他频道'};
metadata.bindings.push(other);
fixture.nodes.push(n('binding:'+other.binding_id,'binding'),n('group:'+other.chat_ref,'group'),n('channel:'+other.channel_id,'channel'));
fixture.nodes.find(n=>n.id==='binding:'+other.binding_id).status='degraded';
fixture.edges.push({source:'group:'+other.chat_ref,target:'binding:other',kind:'binding'},{source:'binding:other',target:'channel:'+other.channel_id,kind:'binding'});
apply(fixture);const overview=elements.get('overview');assert.equal(overview.children.length,2);
assert.equal(overview.children[0].attrs['aria-label'],'其他群 ↔ 其他频道');assert.equal(overview.children[0].children[1].children[0].textContent,'需要修复');
const publicFixture=structuredClone(fixture);publicFixture.nodes.find(n=>n.id==='group:'+other.chat_ref).visibility='public';apply(publicFixture);
const hidden=overview.children[0].children[0].children[0];assert.equal(hidden.children[1].textContent,'尚未登记');assert.equal(hidden.children.length,2);
assert.equal(elements.get('overview-summary').children[2].textContent,'1 组状态需关注');
''')


if __name__ == '__main__':
    unittest.main()
