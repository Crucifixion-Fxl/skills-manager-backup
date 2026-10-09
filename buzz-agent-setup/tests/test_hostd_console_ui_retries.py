"""Execute the shipped UI script against explicit offline DOM/HTTP seams."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

NODE=shutil.which('node')
UI=Path(__file__).resolve().parents[1]/'scripts/hostd/console_ui.html'

@unittest.skipUnless(NODE,'real JavaScript runtime required; dependency environment must execute')
class RetryUI(unittest.TestCase):
    def test_lost_response_keeps_original_key_and_unknown_uses_readback(self):
        script=re.search(r'<script>(.*?)</script>',UI.read_text(),re.S).group(1)
        script=script.replace('})();','globalThis.testOperate=operate;\n})();')
        harness=r'''
const vm=require('node:vm');
const posts=[],gets=[];
const element=()=>({children:[],textContent:'',append(...n){this.children.push(...n)},replaceChildren(...n){this.children=[...n]},setAttribute(){}});
const nodes=new Map();
const context={console,crypto:require('node:crypto').webcrypto,setTimeout,clearTimeout,
 document:{getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id)},createElement:element,createElementNS:element,querySelectorAll(){return []}},
 navigator:{},window:{addEventListener(){}},EventSource:class {addEventListener(){} close(){}},
 async fetch(path,options){
  if(options.method==='POST'){
   posts.push({path,headers:options.headers,body:options.body});
   if(posts.length===1)throw new Error('offline lost response');
   return {ok:true,async json(){return {id:'a'.repeat(64),status:'unknown',message:'pending',remedy:'inspect',copy_to_ai:'inspect'}}};
  }
  gets.push(path);return {ok:true,async json(){return path.startsWith('/api/operations/')?{id:'a'.repeat(64),status:'unknown',message:'pending'}:{nodes:[],edges:[],operations:[],events:[],requests:[]}}};
 }};
vm.createContext(context);
vm.runInContext(SOURCE,context);
(async()=>{
 const target={kind:'binding',id:'binding:alpha'};
 await context.testOperate(target,'pause');
 await context.testOperate(target,'pause');
 await context.testOperate(target,'pause');
 process.stdout.write(JSON.stringify({posts,gets}));
})().catch(()=>process.exit(2));
'''
        result=subprocess.run([NODE,'-e',harness.replace('SOURCE',json.dumps(script))],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,'offline UI harness failed')
        data=json.loads(result.stdout)
        self.assertEqual(len(data['posts']),2,'an unresolved known operation needs only GET readback')
        first,second=data['posts']
        key=first['headers'].get('Idempotency-Key')
        self.assertRegex(key or '',r'^[0-9a-f]{64}$')
        self.assertEqual(second['headers'].get('Idempotency-Key'),key)
        self.assertEqual(first['body'],'{}');self.assertEqual(second['body'],'{}')
        self.assertIn('/api/operations/'+'a'*64,data['gets'])
        self.assertNotIn('Authorization',first['headers'])

if __name__=='__main__':unittest.main()
