#!/usr/bin/env python3
"""Offline fixed-snapshot Go Echo / Java Spring route inventory. No network/auth."""
import argparse,json,re,pathlib

def clean(text):
    # Keep source offsets/newlines intact, and ignore comment markers in literals.
    tokens = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*'
    def mask(match):
        value = match.group(0)
        return re.sub(r'[^\r\n]', ' ', value) if value.startswith(('/*', '//')) else value
    return re.sub(tokens, mask, text, flags=re.S)

def block(text,start):
    depth=0
    for i in range(start,len(text)):
        if text[i]=='{':depth+=1
        elif text[i]=='}':
            depth-=1
            if depth==0:return text[start:i+1]
    return text[start:]

def entry(path,text,pos):return {'file':str(path),'line':text[:pos].count('\n')+1}

def go_routes(root):
    funcs={}
    for p in sorted(root.rglob('*.go')):
        if p.name.endswith('_test.go'):continue
        text=p.read_text()
        for m in re.finditer(r'func\s+\(h\s+\*Handler\)\s+(\w+)\([^\n]*\)\s*(?:error\s*)?\{',text):
            funcs[m[1]]=(p.relative_to(root),text,m.start(),block(text,text.index('{',m.start())))
    registry=root/'handler/handler.go'; text=registry.read_text(); groups={'v1':'/api'}; routes=[]
    for line in text.splitlines():
        if line.lstrip().startswith('//'):continue
        g=re.search(r'(\w+)\s*:=\s*(\w+)\.Group\("([^"]*)"',line)
        if g:groups[g[1]]=groups.get(g[2],'<unresolved>')+g[3]
        m=re.search(r'(\w+)\.(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\("([^"]*)",\s*h\.(\w+)',line)
        if not m:continue
        p,t,pos,body=funcs.get(m[4],(pathlib.Path('handler/handler.go'),text,0,''))
        roles=re.findall(r'verifyPermissions\([^\n]+',body)
        gates=[x.strip() for x in body.splitlines() if re.search(r'^\s*if .*?(?:Status|Suspended|Disabled|Permission|Role|Gray|gray|Step|NonCancelable|Apollo|admin)',x)]
        models=sorted(set(re.findall(r'(?:request|response|model)\.\w+',body)))
        read=m[2] in ['GET','HEAD'] and m[4] not in ['logout','GitLabOAuthCallback','GitLabOAuthLogin']
        routes.append({'method':m[2],'path':groups.get(m[1],'<unresolved>')+m[3],'handler':m[4],
          'source':entry(p,t,pos),'modelSymbols':models,'modelSources':[str(x.relative_to(root)) for x in root.rglob('*.go') if x.parent.name in ['request','response','model'] and any(re.search(r'\b'+s.split('.')[1]+r'\b',x.read_text()) for s in models)],
          'sideEffectCalls':sorted(set(re.findall(r'(?:h\.op|utils)\.\w+',body))), 'permissionEvidence':roles,'gateEvidence':gates,'effect':'read' if read else 'write-or-auth-transition',
          'evidence':'source-verified','deployment':'unknown','readback': '/api/task' if m[3].startswith('/task') or m[1] in ['task','tasks'] else '/api/apollo/gray/config' if m[1]=='apolloGray' else 'blocked: select source-confirmed resource read and reconcile actual side effect before execution'})
    return routes

def java_routes(root):
    routes=[]; unresolved=[]; model_index={}; constants={}
    production=root/'src/main/java'
    source_root=production if production.is_dir() else root
    for x in source_root.rglob('*.java'):
        for key,val in re.findall(r'(?:static\s+final|final\s+static)\s+String\s+(\w+)\s*=\s*"([^"]+)"',x.read_text()): constants[key]=val
    for x in source_root.rglob('*.java'):model_index.setdefault(x.stem,[]).append(str(x.relative_to(root)))
    for p in sorted(source_root.rglob('*.java')):
        original=p.read_text(); text=clean(original)
        if not re.search(r'@(?:RestController|Controller)\b',text):continue
        classpos=text.find('class '); prefix=text[:classpos]
        base=re.search(r'@RequestMapping\s*\((.*?)\)',prefix,re.S)
        bases=re.findall(r'"([^"]*)"',base[1]) if base else ['']
        if not bases:bases=[constants.get(base[1].strip(),'<dynamic-class-mapping>')]
        patterns=r'@(?P<kind>Get|Post|Put|Delete|Patch|Request)Mapping\s*(?:\((?P<args>.*?)\))?'
        for m in re.finditer(patterns,text[classpos:],re.S):
            pos=classpos+m.start(); tail=text[classpos+m.end():]
            sig=re.search(r'\bpublic\s+(?:static\s+)?([\w<>?, .\[\]]+)\s+(\w+)\s*\((.*?)\)\s*(?:throws[^\{]+)?\{',tail,re.S)
            if not sig:unresolved.append({'file':str(p.relative_to(root)),'mapping':m[0]});continue
            declaration_pos=classpos+m.end()+sig.start()
            args=m['args'] or ''; paths=re.findall(r'"([^"]*)"',args) or ['']; methods=[m['kind'].upper()] if m['kind']!='Request' else re.findall(r'RequestMethod\.(\w+)',args) or ['ANY']
            body=block(tail,tail.index('{',sig.start())); annotation=text[max(classpos,text.rfind('}',classpos,pos)+1):pos]+tail[:sig.start()]
            perms=re.findall(r'@(?:Client|Server)Accessible(?:\([^)]*\))?',prefix+'\n'+annotation,re.S)
            calls=sorted(set(re.findall(r'\b(\w+(?:Service|service))\.(\w+)\(',body)))
            query=bool(re.search(r'(?:query|list|page|search|get|find|count|stat|detail)',sig[2],re.I))
            mutation=bool(re.search(r'\.(?:save|insert|delete|update|remove|create|publish|release|send|sync|revoke|cancel|execute)\w*\(',body,re.I))
            # Query POSTs are candidates until transitive service effects are reviewed.
            effect='query-candidate-needs-service-review' if query and not mutation else 'mutation-or-unclassified'
            if methods==['GET'] and not mutation:effect='read-candidate-needs-service-review'
            models=sorted(set([name for name in re.findall(r'\b[A-Z]\w*\b',sig[1]+' '+sig[3]) if name in model_index]))
            for b in bases:
              for path in paths:
               for method in methods:
                routes.append({'method':method,'path':re.sub(r'/+','/','/'+(b+'/'+path).strip('/')),'handler':sig[2],'source':{'file':str(p.relative_to(root)),'symbol':sig[2],'line':original[:declaration_pos].count('\n')+1},'requestSignature':sig[3].strip(),'responseSignature':sig[1].strip(),'modelSymbols':models,'modelSources':[path for model in models for path in model_index.get(model,[])], 'serviceCalls':['.'.join(x) for x in calls], 'serviceSources':[v for typ,var in re.findall(r'private\s+([\w.]+)\s+(\w+)\s*;',text) if any(var==call[0] for call in calls) for v in model_index.get(typ.split('.')[-1],[])], 'permissionEvidence':perms,'gateEvidence':[x.strip() for x in body.splitlines() if re.search(r'^\s*if .*?(?:permission|Permission|role|Role|status|Status|USER_ID)',x)],'effect':effect,'evidence':'source-verified','deployment':'unknown','readback':'blocked: correlate service side effects and source-confirmed query for the same resource; no retry of ambiguous write'})
    return routes,unresolved

def scan(root,kind,frontend_root=None):
    manifest=json.loads((root/'manifest.json').read_text()); routes,unresolved=(go_routes(root),[]) if kind=='go' else java_routes(root)
    expected=[x for x in manifest['paths'] if x.endswith('.go') and x.startswith(('handler/','router/','request/','response/','model/'))] if kind=='go' else [x for x in manifest['paths'] if x.startswith('src/main/java/') and x.endswith('.java')]
    missing=[x for x in expected if not (root/x).is_file()]
    for route in routes:
        route['resource']=route['path'].rsplit('/',1)[0]
        route['executionGate']='owner authorization + current identity/role/resource + current state + side-effect/readback contract; deployment match pending'
    result={'schemaVersion':1,'source':{'projectId':manifest['id'],'commit':manifest['sha'],'deployedCommit':None,'snapshotRoot':str(root)},'coverage':'all static router registrations in fixed source snapshot; not runtime commands or deployment proof','snapshotCoverage':{'expectedSourceFiles':len(expected),'missingSourceFiles':missing},'routes':routes,'unresolvedMappings':unresolved}
    if frontend_root is not None:
        fm=json.loads((frontend_root/'manifest.json').read_text());result['frontendSource']={'projectId':fm['id'],'commit':fm['sha'],'snapshotRoot':str(frontend_root)}
        paths=sorted(set('/api'+url for p in (frontend_root/'src/api/cicd').rglob('*.ts') for url in re.findall(r"=\s*['\"](/[^'\"]+)['\"]",p.read_text())))
        known={r['path'] for r in routes}
        result['frontendBusinessPaths']=paths
        result['frontendPathsMissingBackend']=[p for p in paths if p not in known]
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=pathlib.Path,required=True);p.add_argument('--kind',choices=['go','java'],required=True);p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--frontend-root',type=pathlib.Path);a=p.parse_args();d=scan(a.root,a.kind,a.frontend_root);a.output.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'routes':len(d['routes']),'unresolved':len(d['unresolvedMappings'])}))
