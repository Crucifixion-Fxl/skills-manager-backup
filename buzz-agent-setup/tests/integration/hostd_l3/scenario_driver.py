"""Private MSG001/002 transport observations; native wake and L3 stay pending."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import time
import traceback
from types import MappingProxyType
from datetime import datetime, timezone
from . import prepare, agent_launcher
from hostd import delivery_mapping as mapping, bot_clients
import buzz_feishu_group_sync as gs
import recovery_authority as authority

NOTICE = ('场景证据尚未确认。怎么解决：核对本次运行的固定输入、完整消息读回与原话题；未知发送只能查询，不能重发。'
          '\n复制给 AI：检查 hostd MSG001/002 私有证据，不输出凭据、正文或个人信息；原生唤醒仍待关联证据。')
USER_APP = 'cli_a940faa4ec381bc4'
USER_ID = 'ou_755158d120e03b0c18dd4a9334bc3aad'
PROFILE = 'jchen-personal'


def projected_human_body(native, projected, *, human_pubkey, roles, profiles, union_ids, now):
    """Exact original route projection, never a role/identity authority DTO.

    Consumers obtain roles and pairings through original authenticated reads.
    Profile names are accepted only from exact, verified human-signed kind0.
    """
    try:
        if roles.get(human_pubkey) not in gs.HUMAN_ROLES or not isinstance(union_ids,dict):raise ValueError
        if not isinstance(profiles,list) or not 0<len(profiles)<257:raise ValueError
        for event in profiles:
            if (event.get('kind')!=0 or event.get('pubkey')!=human_pubkey
                or not gs._nip01_event_verified(event) or not 0<=event['created_at']<=now):raise ValueError
        latest=authority.latest(profiles);profile=json.loads(latest['content'])
        name=profile.get('display_name')
        if not isinstance(name,str) or not name.strip():raise ValueError
        paired,duplicates=gs.one_pubkey_per_id(union_ids)
        for key in ('message_id','chat_id','root_id','parent_id','msg_type','body','create_time'):
            if projected.get(key)!=native.get(key):raise ValueError
        sender=projected.get('sender') or {};original=native.get('sender') or {}
        if (native.get('deleted') or projected.get('deleted') or original.get('sender_type')!='user'
            or original.get('id_type')!='open_id' or not gs.OPEN_ID_RE.fullmatch(original.get('id',''))
            or sender.get('sender_type')!='user' or sender.get('id_type')!='union_id'
            or paired.get(sender.get('id'))!=human_pubkey):raise ValueError
        normalized=bot_clients.BotLarkCli._normalize_message(native)
        inbound=gs.route_feishu_message(normalized,open_id_to_pubkey=paired,bot_member_to_pubkey={},
            channel_members=set(roles),names={human_pubkey:name},now=datetime.fromtimestamp(now,timezone.utc),
            resolve_id=lambda ident:sender['id'] if ident==original['id'] else '')
        if not isinstance(inbound,gs.Inbound) or inbound.sender_pubkey!=human_pubkey or inbound.context_only:raise ValueError
        return inbound.text
    except Exception:raise ScenarioError() from None

class ScenarioError(ValueError):
    def __init__(self): super().__init__(NOTICE)


def _read(path):
    fd = prepare._open(path, private=True)
    try:
        if os.fstat(fd).st_nlink != 1: raise ValueError
        with os.fdopen(fd, 'rb', closefd=False) as stream: raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024: raise ValueError
        return raw
    finally: os.close(fd)


def _canonical(raw, *, mentions=None):
    """Exact post semantics; optional original API mention bindings only.

    Locale removal and observed empty styles are presentation defaults. A read
    row's selected placeholder/name resolves only through its own typed open_id
    entity. Image dimensions are display metadata; the image key and the later
    byte/hash readback remain exact. Unknown fields remain comparison input.
    """
    doc=prepare._json(raw)
    if isinstance(doc,dict) and set(doc)=={'zh_cn'}:doc=doc['zh_cn']
    if not isinstance(doc,dict) or set(doc) not in ({'title','content'},{'title','content','content_v2'}) or not isinstance(doc['title'],str):raise ValueError
    # Redundant native views must not hide contradictory identity/display data.
    if 'content_v2' in doc and doc['content_v2']!=doc['content']:raise ValueError
    entities=[]
    if mentions is not None:
        if not isinstance(mentions,list) or len(mentions)>1:raise ValueError
        for entity in mentions:
            if (not isinstance(entity,dict) or not {'id','id_type','key','name'}<=set(entity)
                or not set(entity)<={'id','id_type','key','name','tenant_key'}
                or entity['id_type']!='open_id' or not isinstance(entity['id'],str)
                or not gs.OPEN_ID_RE.fullmatch(entity['id'])
                or not isinstance(entity['key'],str) or not re.fullmatch(r'@_user_[1-9][0-9]*',entity['key'])
                or not isinstance(entity['name'],str) or not isinstance(entity.get('tenant_key',''),str)):raise ValueError
            entities.append(entity)
    used=[]
    def paragraphs(rows):
        if not isinstance(rows,list) or len(rows)>2048:raise ValueError
        result=[];count=0
        for row in rows:
            if not isinstance(row,list):raise ValueError
            cells=[]
            for cell in row:
                count+=1
                if count>16384 or not isinstance(cell,dict) or not isinstance(cell.get('tag'),str):raise ValueError
                value=dict(cell)
                if 'style' in value and (not isinstance(value['style'],list) or any(not isinstance(style,str) for style in value['style'])):raise ValueError
                if value['tag'] in ('text','a') and value.get('style')==[]:value.pop('style')
                if value['tag']=='at' and mentions is not None:
                    matches=[entity for entity in entities if value.get('user_id') in (entity['key'],entity['id'])]
                    if len(matches)>1:raise ValueError
                    if matches:
                        entity=matches[0]
                        if entity['key'] in used:raise ValueError
                        used.append(entity['key']);value['user_id']=entity['id']
                        if 'user_name' in value:
                            if value['user_name']!=entity['name']:raise ValueError
                            value.pop('user_name')
                        if value.get('style')==[]:value.pop('style')
                    elif str(value.get('user_id','')).startswith('@_'):raise ValueError
                if value['tag']=='img' and mentions is not None and ('width' in value or 'height' in value):
                    if any(type(value.get(key)) is not int or not 0<value[key]<=65535 for key in ('width','height')):raise ValueError
                    value.pop('width');value.pop('height')
                cells.append(value)
            result.append(cells)
        return result
    content=paragraphs(doc['content'])
    if mentions is not None and len(used)!=len(entities):raise ValueError
    return json.dumps({'title':doc['title'],'content':content},sort_keys=True,separators=(',',':'),ensure_ascii=False)


def _post_readback(row):
    # Use the production parser to refuse unsupported message representations.
    # Its rendered text is separately used by the original signed projection.
    bot_clients.BotLarkCli._normalize_message(row)
    return _canonical(row['body']['content'],mentions=row.get('mentions',[]))


def _failure(stage,error):
    """Safe stage/type/code location; never argv, stdout or exception messages."""
    return {'stage':stage,'failure_type':type(error).__name__,
            'failure_location':[{'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name}
                                for frame in traceback.extract_tb(error.__traceback__)]}


def _current_inputs(p):
    # PreparedRun.revalidate is a pre-start gate which also requires no outputs.
    # The same frozen inputs must remain current once the owned runtime exists.
    prepare._git_check(p.source_root,p.revision,p._command_runner)
    if prepare._runtime_files(p.source_root) != {f for f,_ in p._source_hashes if f.is_relative_to(p.source_root)}: raise ValueError
    for directory in p._directories: prepare._private_directory(directory)
    for path, expected in p._snapshots:
        if prepare._hash(_read(path)) != expected: raise ValueError
    for path, expected in p._source_hashes:
        if prepare._file_digest(path) != expected: raise ValueError


@dataclass(frozen=True)
class ScenarioPlan:
    prepared: prepare.PreparedRun = field(repr=False, compare=False)
    run_id: str
    _doc: str = field(repr=False)
    _pins: tuple = field(repr=False)
    _directories: tuple = field(repr=False)

    @classmethod
    def check(cls, prepared, manifest_path, manifest_sha256, *, human_identity_pin):
        try:
            if type(prepared) is not prepare.PreparedRun: raise ValueError
            _current_inputs(prepared)
            path = prepare._inside(manifest_path, prepared.run_dir)
            raw = _read(path)
            if prepare._hash(raw) != manifest_sha256: raise ValueError
            doc = prepare._json(raw)
            expected = {'version','run_id','revision','initial_binding','channel_id','chat_id','sync_app_id','agent_app_id',
                        'agent_pubkey','user_config_dir','user_data_dir','user_profile','human_env_file','human_pubkey',
                        'phase_dir','msg001_content_file','msg002_content_file','image_file','image_key','image_sha256','link_base'}
            if set(doc) != expected or type(doc['version']) is not int or doc['version'] != 1: raise ValueError
            if any(doc[k] != getattr(prepared,k) for k in ('run_id','revision','initial_binding')): raise ValueError
            rt = prepare._json(prepare._bytes(prepared.onboarding_config))
            cfg_path = Path(rt['binding_dir']) / prepared.initial_binding / 'config.json'
            cfg = prepare._json(prepare._bytes(cfg_path))
            if (doc['channel_id'] != cfg['channel_id'] or doc['chat_id'] != cfg['chat_id']
                or doc['sync_app_id'] != cfg['agents'][cfg['desk_pubkey']]['app_id']
                or doc['agent_pubkey'] not in cfg['agents'] or doc['agent_app_id'] != cfg['agents'][doc['agent_pubkey']]['app_id']
                or doc['agent_app_id'] == doc['sync_app_id'] or doc['user_profile'] != PROFILE
                or cfg['owner_app_id'] != USER_APP or doc['link_base'] != cfg['people_api']['base_url']): raise ValueError
            catalog = prepare._json(prepare._bytes(rt['catalog_path']))
            rows = [a for a in catalog['agents'] if gs._signer_pubkey(prepare.join.parse_env(_read(prepare._inside(a['env_file'],prepared.run_dir)).decode())['BUZZ_PRIVATE_KEY']) == doc['agent_pubkey'] and a['feishu']['app_id'] == doc['agent_app_id']]
            if len(rows) != 1: raise ValueError
            directories = []
            for key in ('phase_dir','user_config_dir','user_data_dir'):
                directory = prepare._inside(doc[key], prepared.run_dir)
                prepare._private_directory(directory); directories.append(directory)
            phase = Path(doc['phase_dir'])
            if path.parent != phase: raise ValueError
            pins = [(path, prepare._hash(raw))]
            for key in ('human_env_file','msg001_content_file','msg002_content_file','image_file'):
                file = prepare._inside(doc[key], prepared.run_dir)
                if key != 'human_env_file' and file.parent != phase: raise ValueError
                pins.append((file, prepare._hash(_read(file))))
            user_config = Path(doc['user_config_dir']) / 'config.json'
            apps = prepare._json(_read(user_config)).get('apps')
            if not isinstance(apps,list) or len(apps) != 1 or apps[0].get('appId') != USER_APP or apps[0].get('name') != PROFILE: raise ValueError
            pins.append((user_config,prepare._hash(_read(user_config))))
            env = prepare.join.parse_env(_read(Path(doc['human_env_file'])).decode())
            if set(env) != {'BUZZ_PRIVATE_KEY','BUZZ_RELAY_URL'} or env['BUZZ_RELAY_URL'] != rt['relay_url']: raise ValueError
            if gs._signer_pubkey(env['BUZZ_PRIVATE_KEY']) != human_identity_pin or doc['human_pubkey'] != human_identity_pin: raise ValueError
            if prepare._hash(_read(Path(doc['image_file']))) != doc['image_sha256']: raise ValueError
            gs.read_image(Path(doc['image_file']), allowed=frozenset({'png','jpeg','gif','webp'}))
            post = prepare._json(_read(Path(doc['msg001_content_file'])))
            cells = [c for row in post['zh_cn']['content'] for c in row]
            if not any(c.get('tag') == 'text' and c.get('text') for c in cells): raise ValueError
            if len([c for c in cells if c.get('tag') == 'at']) != 1: raise ValueError
            if [c.get('image_key') for c in cells if c.get('tag') == 'img'] != [doc['image_key']]: raise ValueError
            if not _read(Path(doc['msg002_content_file'])).decode().strip(): raise ValueError
            return cls(prepared, prepared.run_id, json.dumps(doc), tuple(pins), tuple(directories))
        except Exception: raise ScenarioError() from None

    def readback(self): return {'status':'prepared','live_verified':False}

    def revalidate(self):
        try:
            _current_inputs(self.prepared)
            for directory in self._directories:
                prepare._inside(directory,self.prepared.run_dir)
                prepare._private_directory(directory)
            if prepare._json(_read(self._pins[0][0])) != json.loads(self._doc): raise ValueError
            for path, expected in self._pins:
                if prepare._hash(_read(path)) != expected: raise ValueError
        except Exception: raise ScenarioError() from None


class ScenarioDriver:
    def __init__(self,plan,*,hostd,native_agent,runner=subprocess.run,clock=time.time,deadline_seconds=30):
        if (type(plan) is not ScenarioPlan or type(hostd) is not prepare.OwnedHostd
            or type(native_agent) is not agent_launcher.OwnedAgent or hostd.plan is not plan.prepared
            or native_agent.plan.prepared is not plan.prepared or not 0 < deadline_seconds <= 90): raise ScenarioError()
        self.plan=plan; self.hostd=hostd; self.native_agent=native_agent
        self.runner=runner; self.clock=clock; self.timeout=deadline_seconds; self._lock=asyncio.Lock()
        self.doc=MappingProxyType(json.loads(plan._doc))
        if (native_agent.plan.pubkey != self.doc['agent_pubkey'] or native_agent.plan.app_id != self.doc['agent_app_id']
            or native_agent.plan.channel != self.doc['channel_id']): raise ScenarioError()

    async def _io(self,fn,*args,**kwargs):
        task=asyncio.create_task(asyncio.to_thread(fn,*args,**kwargs))
        try: return await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            # Cleanup must still join this original dispatch if cancellation is
            # requested again. Never release the owner/phase lock while its
            # scoped subprocess thread can still be performing the mutation.
            while True:
                try:
                    await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    if task.done(): break
                except Exception:
                    break
            raise cancelled

    def _env(self,user=True):
        env=self.plan.prepared.environment();env.pop('PYTHONPATH',None)
        if user:
            env.update(LARKSUITE_CLI_CONFIG_DIR=self.doc['user_config_dir'],LARKSUITE_CLI_DATA_DIR=self.doc['user_data_dir'],LARKSUITE_CLI_NO_SKILLS_NOTIFIER='1')
        else:
            env.update(prepare.join.parse_env(_read(Path(self.doc['human_env_file'])).decode()))
        return env

    def _run(self,args,*,buzz=False,input=None,auth=False):
        argv=([str(self._cfg()['buzz_cli']),*args] if buzz else [prepare.NODE,prepare.CLI_ENTRY,*args,'--profile',PROFILE])
        if not buzz and not auth: argv += ['--as','user']
        result=self.runner(argv,input=input,capture_output=True,text=True,timeout=self.timeout,check=False,env=self._env(not buzz))
        if result.returncode != 0: raise ValueError
        if buzz and args[:2] == ['media','get']: return None
        value=json.loads(result.stdout)
        if not buzz and not auth:
            if not isinstance(value,dict) or value.get('ok') is not True or value.get('identity') != 'user': raise ValueError
            if not isinstance(value.get('data'),dict): raise ValueError
        return value

    def _cfg(self):
        p=self.plan.prepared;rt=prepare._json(_read(p.onboarding_config))
        return prepare._json(_read(Path(rt['binding_dir'])/p.initial_binding/'config.json'))

    def _project_root(self, original):
        cfg=self._cfg();p=self.plan.prepared;rt=prepare._json(_read(p.onboarding_config))
        now=datetime.fromtimestamp(self.clock(),timezone.utc)
        relay_url=gs.read_env_file(Path(cfg['mirror_env_file'])).get('BUZZ_RELAY_URL','')
        # PreparedRun v1 pins the websocket address and corresponding HTTPS origin.
        if relay_url.startswith('wss://'):relay_url='https://'+relay_url[6:]
        rows=gs._relay_query(cfg,relay_url,[{'kinds':[39002],'authors':[rt['relay_pubkey']],
            '#d':[self.doc['channel_id']],'limit':257}],gs._http_get,now)
        if not 0<len(rows)<257:raise ScenarioError()
        for row in rows:
            if row['kind']!=39002 or row['pubkey']!=rt['relay_pubkey'] or row['created_at']>self.clock():raise ScenarioError()
        roles=authority.membership(authority.latest(rows),self.doc['channel_id'])
        people=gs.fetch_people(cfg,roles,gs._http_get,now)
        profiles=gs._relay_query(cfg,relay_url,[{'kinds':[0],'authors':[self.doc['human_pubkey']],
            'limit':257}],gs._http_get,now)
        block=cfg['agents'][cfg['desk_pubkey']]
        bot=bot_clients.BotLarkCli(block['app_id'],block['lark_config_dir'],block['lark_data_dir'],
            base_env=p.environment(),runner=self.runner,chat_id=self.doc['chat_id'])
        if bot.identity()!=(self.doc['sync_app_id'],''):raise ScenarioError()
        native=bot.message_view(original['message_id'],'open_id')
        projected=bot.message_view(original['message_id'],'union_id')
        if not isinstance(native,dict) or native.get('chat_id')!=self.doc['chat_id']:raise ScenarioError()
        for key in ('message_id','chat_id','root_id','parent_id','msg_type','body','create_time'):
            if native.get(key)!=original.get(key):raise ScenarioError()
        return projected_human_body(native,projected,human_pubkey=self.doc['human_pubkey'],roles=roles,
            profiles=profiles,union_ids=people.union_ids,now=self.clock())

    def _call(self,args,**kw): return self._run(args,**kw)['data']

    def _rows(self):
        params={'container_id_type':'chat','container_id':self.doc['chat_id'],'page_size':50,'sort_type':'ByCreateTimeDesc','user_id_type':'open_id'}
        value=self._run(['api','GET','/open-apis/im/v1/messages','--params',json.dumps(params,separators=(',',':')),'--page-all','--page-limit','0'])
        data=value['data'];rows=data.get('items')
        # Native API --page-all terminates with has_more=False/page_token='';
        # unlike shortcuts it need not add meta.pagination.complete. Contradictory
        # or unknown metadata refuses rather than claiming complete pagination.
        if set(value) not in ({'ok','identity','data'},{'ok','identity','data','meta'}):raise ValueError
        if set(data)!={'items','has_more','page_token'} or data['has_more'] is not False or data['page_token']!='':raise ValueError
        if 'meta' in value:
            meta=value['meta']
            if (not isinstance(meta,dict) or set(meta)!={'pagination'} or not isinstance(meta['pagination'],dict)
                or set(meta['pagination'])!={'complete'} or meta['pagination']['complete'] is not True):raise ValueError
        # Same finite page budget as the production HTTP reader (1000*50).
        # A saturated result cannot become an exhaustive uniqueness proof.
        if not isinstance(rows,list) or len(rows)>=50*1000:raise ValueError
        if any(not isinstance(r,dict) or not isinstance(r.get('message_id'),str)
               or not 0<len(r['message_id'])<=128 for r in rows):raise ValueError
        if len({r['message_id'] for r in rows}) != len(rows): raise ValueError
        return rows

    def _identity(self):
        status=self._run(['auth','status','--verify','--json'],auth=True)
        if status.get('appId') != USER_APP or status.get('identities',{}).get('user',{}).get('openId') != USER_ID: raise ValueError
        if self._call(['api','GET','/open-apis/authen/v1/user_info']).get('open_id') != USER_ID: raise ValueError
        chat=self._call(['api','GET','/open-apis/im/v1/chats/'+self.doc['chat_id']])
        # Successful user GET is scoped to the exact requested chat; this API
        # may omit chat_id. A present contradictory echo still refuses.
        if 'chat_id' in chat and chat['chat_id'] != self.doc['chat_id']: raise ValueError
        roster={}
        for kind in ('user','bot'):
            data=self._call(['im','+chat-members-list','--chat-id',self.doc['chat_id'],'--member-id-type','open_id','--member-types',kind,'--page-all','--page-limit','0'])
            key='users' if kind=='user' else 'bots';rows=data.get(key)
            if not isinstance(rows,list) or data.get('has_more') is not False or data.get('truncations') != [] or data.get(kind+'_total') != len(rows): raise ValueError
            roster[key]=rows
        if USER_ID not in {r['member_id'] for r in roster['users']}: raise ValueError
        bots={r['app_id']:r['member_id'] for r in roster['bots']}
        if len(bots) != len(roster['bots']) or set(bots) != prepare.APPS: raise ValueError
        post=prepare._json(_read(Path(self.doc['msg001_content_file'])))
        mentions=[c['user_id'] for row in post['zh_cn']['content'] for c in row if c.get('tag')=='at']
        if mentions != [bots[self.doc['agent_app_id']]]: raise ValueError

    def _intent(self,phase,root=None):
        path=Path(self.doc['phase_dir'])/(phase+'.json')
        body=Path(self.doc[phase+'_content_file'])
        base={'version':1,'status':'unknown','phase':phase,'run_id':self.plan.run_id,'revision':self.plan.prepared.revision,
              'binding':self.doc['initial_binding'],'chat':self.doc['chat_id'],'channel':self.doc['channel_id'],
              'bodyhash':prepare._hash(_read(body)),'idempotency':phase+'-'+self.plan.run_id,'root':root}
        if os.path.lexists(path):
            doc=prepare._json(_read(path))
            if set(doc) != set(base)|{'started'} or any(doc[k] != v for k,v in base.items()) or type(doc['started']) not in (int,float) or not math.isfinite(doc['started']) or not 0 <= doc['started'] <= self.clock(): raise ScenarioError()
            return doc,False
        base['started']=self.clock()
        directory=prepare._open(path.parent,directory=True,private=True)
        try:
            try: fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory)
            except FileExistsError: return self._intent(phase,root)
            with os.fdopen(fd,'wb') as out:
                out.write(json.dumps(base,sort_keys=True).encode());out.flush();os.fsync(out.fileno())
            os.fsync(directory)
        finally: os.close(directory)
        return base,True

    def _events(self,since):
        # The native reader enforces complete paging and saturation rather than silently truncating.
        client=gs.BuzzCli(self._cfg()['buzz_cli'],self._env(False),runner=self.runner)
        return client.messages(self.doc['channel_id'],int(since),'9')

    def _root(self,intent,gates):
        post=_canonical(_read(Path(self.doc['msg001_content_file'])))
        rows=self._rows(); candidates=[]
        for row in rows:
            try:
                sender=row.get('sender')
                if (row.get('chat_id')==self.doc['chat_id'] and row.get('deleted',False) is False and row.get('msg_type')=='post'
                    and row.get('root_id',row.get('message_id'))==row.get('message_id')
                    and row.get('parent_id') in (None,'')
                    and isinstance(sender,dict) and set(sender)<={'sender_type','id_type','id','tenant_key'}
                    and all(sender.get(k)==v for k,v in {'sender_type':'user','id_type':'open_id','id':USER_ID}.items())
                    and isinstance(sender.get('tenant_key',''),str)
                    and isinstance(row.get('create_time'),str) and row['create_time'].isascii()
                    and row['create_time'].isdigit() and len(row['create_time'])<=20
                    and intent['started']*1000 <= int(row['create_time']) <= self.clock()*1000
                    and _post_readback(row)==post): candidates.append(row)
            except Exception: pass
        if len(candidates)!=1: return None,None
        mid=candidates[0]['message_id']
        exact=self._call(['api','GET','/open-apis/im/v1/messages/'+mid]).get('items')
        if exact != candidates: return None,None
        gates['feishu_readback']=True
        cfg=self._cfg();text=self._project_root(candidates[0])
        found=[]
        for event in self._events(intent['started']):
            m=mapping.buzz_mapping(event,self.doc['channel_id'],set(),{cfg['mirror_pubkey']})
            if m and m.message_id==mid and m.feishu_root==mid and m.buzz_root is None and event.get('content')==text and intent['started'] <= event.get('created_at',0) <= self.clock(): found.append((event,m))
        if len(found)!=1:return None,None
        event,m=found[0];gates['signed_mirror']=True
        gates['selected_mention']=len([t for t in event['tags'] if t[:2]==['p',self.doc['agent_pubkey']]])==1
        metas=[t for t in event['tags'] if t[:1]==['imeta']]
        if len(metas)==1:
            fields={x.split(' ',1)[0]:x.split(' ',1)[1] for x in metas[0][1:] if ' ' in x}
            image=Path(self.doc['image_file']);segment=self.doc['image_sha256']+image.suffix
            if fields.get('x')==self.doc['image_sha256'] and fields.get('url')==self.doc['link_base']+'/media/'+segment and fields.get('m')=='image/'+gs.read_image(image,allowed=frozenset({'png','jpeg','gif','webp'}))[1]:
                scratch=Path(self.doc['phase_dir'])/('media-'+os.urandom(12).hex()+'.png')
                fd=os.open(scratch,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
                try:
                    self._run(['media','get',segment,'-o',str(scratch)],buzz=True)
                    gates['image_readback']=_read(scratch)==_read(image)
                finally: scratch.unlink(missing_ok=True)
        return event,m

    async def _owners(self):
        self.plan.revalidate()
        if self.hostd.readback()['status']!='running': raise ScenarioError()
        try: value=await self.native_agent.refresh()
        except Exception: raise ScenarioError() from None
        if value['status']!='running' or not value['native_ready']: raise ScenarioError()

    async def run_msg001(self):
        async with self._lock:
            await self._owners()
            gates={k:False for k in ('feishu_readback','signed_mirror','selected_mention','image_readback','native_awake')}
            diagnostics=[];stage='identity'
            try:
                await self._io(self._identity)
                stage='intent';intent,fresh=self._intent('msg001')
                if fresh:
                    self.plan.revalidate()
                    stage='publish'
                    try: await self._io(self._run,['im','+messages-send','--chat-id',self.doc['chat_id'],'--content','-','--msg-type','post','--idempotency-key',intent['idempotency']],input=_read(Path(self.doc['msg001_content_file'])).decode())
                    except Exception as error:diagnostics.append(_failure(stage,error))
                stage='root_readback'
                root, unused = await self._io(self._root,intent,gates)
                if root is None:diagnostics.append({'stage':stage,'outcome':'incomplete_root_proof'})
                if root is not None and all(gates[key] for key in
                        ('feishu_readback','signed_mirror','selected_mention','image_readback')):
                    await self._owners()
                    stage='native_wake'
                    witness = await self.native_agent.correlate_wake(root,
                        selected_pubkey=self.doc['agent_pubkey'], image_sha256=self.doc['image_sha256'])
                    await self._owners()
                    self.plan.revalidate()
                    if (witness.get('status') == 'observed' and witness.get('live_verified') is False
                            and self.native_agent._initial is not None
                            and self.native_agent._current == self.native_agent._initial):
                        gates['native_awake'] = True
            except ScenarioError: raise
            except Exception as error:diagnostics.append(_failure(stage,error))
            observed = all(gates.values())
            return {'status':'observed' if observed else 'pending',
                    'reason':'transport_and_wake_observed' if observed else 'wake_witness_pending',
                    'gates':gates,'live_verified':False,'diagnostics':diagnostics}

    def _reply(self,intent,root,root_map,gates):
        text=_read(Path(self.doc['msg002_content_file'])).decode()
        events=[e for e in self._events(intent['started']) if mapping._verified(e,self.doc['channel_id'])
                and e.get('pubkey')==self.doc['human_pubkey'] and e.get('content')==text
                and gs.buzz_thread_root(e)==root['id'] and gs._buzz_parent(e)==root['id']
                and intent['started'] <= e.get('created_at',0) <= self.clock()]
        if len(events)!=1: return
        event=events[0];gates['signed_human']=True
        rows=self._rows();found=[]
        for row in rows:
            m=mapping.feishu_mapping(row,event,self.doc['channel_id'],self.doc['chat_id'],self.doc['link_base'],{self.doc['human_pubkey']},{},self.doc['sync_app_id'],root_mapping=root_map)
            if m:
                document=prepare._json(row['body']['content'])
                expected=gs.card_markdown(text).replace(gs.CARD_OPEN_TEXT,'Buzz 链接')
                if document.get('elements',[{}])[0].get('text') == {'tag':'lark_md','content':expected}: found.append(row)
        if len(found)!=1:return
        card=found[0];items=self._call(['api','GET','/open-apis/im/v1/messages/'+card['message_id']]).get('items')
        if items!=[card]:return
        gates['card_readback']=True
        dbpath=self.plan.prepared.run_dir/'runtime/hostd.sqlite3'
        fd=prepare._open(dbpath,private=True)
        try:
            db=sqlite3.connect('file:/proc/self/fd/'+str(fd)+'?mode=ro',uri=True)
            try:
                db.execute('PRAGMA query_only=ON')
                rows=db.execute('SELECT source_id,target_id,root_id,status,content_hash FROM delivery WHERE binding_id=? AND direction=? AND source_id=?',
                    (self.doc['initial_binding'],'b2f',event['id'])).fetchall()
                gates['ledger_acked']=rows==[(event['id'],card['message_id'],root_map.message_id,'acked',hashlib.sha256(text.encode()).hexdigest())]
            finally:db.close()
        finally:os.close(fd)

    async def run_msg002(self):
        async with self._lock:
            await self._owners()
            gates={k:False for k in ('signed_human','card_readback','ledger_acked')}
            try:
                await self._io(self._identity)
                root_intent,_=self._intent_existing('msg001')
                root_gates={k:False for k in ('feishu_readback','signed_mirror','selected_mention','image_readback')}
                root,root_map=await self._io(self._root,root_intent,root_gates)
                if not root or not all(root_gates.values()): raise ValueError
                who=await self._io(self._run,['users','get'],buzz=True)
                if who.get('pubkey')!=self.doc['human_pubkey']:raise ValueError
                intent,fresh=self._intent('msg002',root['id'])
                if fresh:
                    self.plan.revalidate()
                    try:await self._io(self._run,['messages','send','--channel',self.doc['channel_id'],'--reply-to',root['id'],'--content','-'],buzz=True,input=_read(Path(self.doc['msg002_content_file'])).decode())
                    except Exception:pass
                await self._io(self._reply,intent,root,root_map,gates)
            except ScenarioError:raise
            except Exception:pass
            return {'status':'observed' if all(gates.values()) else 'pending','gates':gates,'live_verified':False}

    def _intent_existing(self,phase):
        if not os.path.lexists(Path(self.doc['phase_dir'])/(phase+'.json')):raise ValueError
        return self._intent(phase)
