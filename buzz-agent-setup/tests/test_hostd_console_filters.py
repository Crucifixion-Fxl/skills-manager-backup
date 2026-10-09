"""Filter graph relationships without expanding through the shared host/app."""
import unittest
import test_hostd_console_names as names


class FilterTests(unittest.TestCase):
    run_js = names.UiNamesTests.run_js

    def test_group_and_agent_filters_do_not_pull_unrelated_branches(self):
        self.run_js(r'''
const g='group:'+r.chat_ref,c='channel:'+r.channel_id,agent='agent:'+a.pubkey;
fixture.nodes.push(n('group:other','group'),n('binding:other','binding'),n('channel:other','channel'),n('agent:other','agent'),n('host:local','host'),n('connection:shared','connection'));
fixture.edges.push({source:agent,target:g,kind:'in_group'},{source:agent,target:c,kind:'member'},
{source:agent,target:'group:other',kind:'in_group'},
{source:'agent:other',target:'group:other',kind:'in_group'},
{source:'group:other',target:'binding:other',kind:'binding'},
{source:'binding:other',target:'channel:other',kind:'binding'},
{source:'host:local',target:'binding:other',kind:'holds'},
{source:'host:local',target:'binding:'+r.binding_id,kind:'holds'},
{source:'connection:shared',target:'app:'+a.app_id,kind:'connection'});
apply(fixture);groupFilter=g;renderGraph();
let ids=new Set(filteredNodes().map(n=>n.id));assert(ids.has(agent));assert(ids.has('connection:shared'));assert(!ids.has('group:other'));assert(!ids.has('agent:other'));assert(!ids.has('binding:other'));
agentFilter=agent;ids=new Set(filteredNodes().map(n=>n.id));assert(ids.has(g));assert(!ids.has('group:other'));
groupFilter='';ids=new Set(filteredNodes().map(n=>n.id));assert(ids.has(g));assert(ids.has('group:other'));assert(!ids.has('agent:other'));
groupFilter=g;agentFilter='agent:other';assert.equal(filteredNodes().length,0);renderGraph();assert.equal(elements.get('graph-empty').hidden,false);
''')

    def test_filter_and_tabs_persist_across_snapshots_missing_target_stays_empty(self):
        self.run_js(r'''
apply(fixture);groupFilter='group:'+r.chat_ref;setScreen('advanced');apply(fixture);
assert.equal(screen,'advanced');assert.equal(elements.get('advanced').hidden,false);assert.equal(elements.get('overview-panel').hidden,true);
assert.equal(elements.get('tab-advanced').attrs['aria-selected'],'true');assert.equal(elements.get('tab-overview').tabIndex,-1);
assert.equal(elements.get('filter-group').value,groupFilter);
apply({nodes:[],edges:[]});assert.equal(filteredNodes().length,0);assert.equal(elements.get('graph-empty').hidden,false);assert.equal(elements.get('filter-group').children.at(-1).disabled,true);
setScreen('overview');assert.equal(elements.get('advanced').hidden,true);assert.equal(elements.get('overview-panel').hidden,false);
''')

    def test_public_names_remain_public_and_filter_options_are_text(self):
        self.run_js(r'''
metadata.bindings[0].chat_name='<img src=x onerror=alert(1)>';
apply(fixture);const options=elements.get('filter-group').children;
assert(options[1].textContent.includes('<img'));assert.equal(options[1].children.length,0);
const data=structuredClone(fixture);data.nodes.find(n=>n.kind==='group').visibility='public';apply(data);
assert(!elements.get('filter-group').children[1].textContent.includes('<img'));
assert(elements.get('filter-group').children[1].textContent.includes('公开资料'));
''')


if __name__ == '__main__':
    unittest.main()
