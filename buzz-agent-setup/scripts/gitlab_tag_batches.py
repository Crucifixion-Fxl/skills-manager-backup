"""Deterministic tag snapshots; scoped durable recovery never resends unknown work."""
from __future__ import annotations
import hashlib
import json
import re
from types import SimpleNamespace
from urllib.parse import urlsplit, unquote

COMMIT = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
MARKER = re.compile(r'\[gitlab-tag-batch:v1\]\[commit:((?:[0-9a-f]{40}|[0-9a-f]{64}))\]')
ESCAPES = '\\`_*[]'


def valid_ref(ref):
    return (isinstance(ref, str) and 0 < len(ref) <= 1024 and ref != '@'
            and not re.search(r'[\x00-\x20\x7f~^:?*\[\\]', ref)
            and not any(x in ref for x in ('..', '@{', '//'))
            and not ref.startswith('/') and not ref.endswith(('/', '.'))
            and all(not p.startswith('.') and not p.endswith('.lock') for p in ref.split('/')))


def ref_label(ref):
    return ''.join('\\' + c if c in ESCAPES else c for c in ref)


def _unlabel(value):
    return re.sub(r'\\([\\`_*\[\]])', r'\1', value)


def ref_url(ref, url):
    if not valid_ref(ref) or not isinstance(url, str) or re.search(r'[\s<>\\()\[\]`]', url):
        return False
    try:
        parsed = urlsplit(url)
        decoded = unquote(parsed.path.split('/-/tags/', 1)[1], errors='strict')
        # Producer neutralizes mentions before displaying a ref, but URL keeps
        # its original GitLab bytes. The mapping remains display-only.
        return (parsed.scheme in ('http', 'https') and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and not parsed.query and not parsed.fragment
                and decoded.replace('@', '＠').replace('nostr:', 'nostr：') == ref.replace('@', '＠').replace('nostr:', 'nostr：'))
    except (ValueError, IndexError, UnicodeError):
        return False


def display_tag(body, fact):
    """Strict old/new producer contract; malformed prose is never rewritten."""
    lines = body.split('\n')
    from gitlab_buzz_sync import parse_header
    if any(MARKER.fullmatch(line) for line in lines):
        batch = parse_snapshot(body, parse_header)
        if batch is None:
            return body
        return '\n'.join(lines[:-2])
    phrase = {'tag_created': '新建 tag', 'tag_deleted': '删除 tag', 'pushed': '移动 tag'}.get(fact.get('event'))
    if phrase is None or set(fact) != {'object', 'event', 'project', 'events'}:
        return body
    edges = [i for i in (0, len(lines)-1) if parse_header(lines[i])]
    if len(set(edges)) != 1:
        return body
    readable = [line for i,line in enumerate(lines) if i not in edges]
    refs = [(i,line[5:]) for i,line in enumerate(readable) if line.startswith('ref: ')]
    if len(refs) != 1 or len(readable) < 3 or refs[0][0] != 2:
        return body
    ref, url = refs[0][1], readable[1]
    if not ref_url(ref, url):
        return body
    headline = readable[0]
    old_phrase = '🔔 **通知**' if fact['event']=='pushed' else f'🏷 **{phrase}**'
    if headline.startswith(old_phrase + ' · '):
        title = headline[len(old_phrase + ' · '):]
        details = ['commit: ' + title] if title and title != ref_label(ref) else []
    elif headline == f'{ref_label(ref)} · 🏷 **{phrase}**':
        details = []
    else:
        return body
    return '\n'.join([f'[{ref_label(ref)}]({url})', f'🏷 **{phrase}**', *details, *readable[3:]])


def render_snapshot(snapshot):
    refs = snapshot['refs']
    lines = [f"🏷 **新建 tag · {len(refs)} 个版本**"]
    lines += [f"- [{ref_label(item['ref'])}]({item['url']})" for item in refs]
    lines += ['commit: ' + ref_label(title) for title in snapshot['titles']]
    lines += ['by: ' + ref_label(actor) for actor in snapshot['actors']]
    lines += [f"[gitlab-tag-batch:v1][commit:{snapshot['sha']}]",
              f"[gitlab-notify:v1][object:tag][event:tag_created][project:{snapshot['project']}]"
              f"[events:{','.join(snapshot['source_keys'])}][rev:{snapshot['revision']}]"]
    return '\n'.join(lines)


def parse_snapshot(content, parse_header):
    """Recover complete facts from canonical visible rows and signed metadata."""
    try:
        lines=content.split('\n');fact=parse_header(content);marker=MARKER.fullmatch(lines[-2])
        if (not marker or not fact or set(fact)!={'object','event','project','events','rev'}
                or fact['object']!='tag' or fact['event']!='tag_created'):
            return None
        refs=[];titles=[];actors=[];section=0
        for line in lines[1:-2]:
            link=re.fullmatch(r'- \[((?:\\.|[^\]])*)\]\(([^\s]+)\)',line)
            if link and section==0:
                ref,url=_unlabel(link[1]),link[2]
                if not ref_url(ref,url):return None
                refs.append({'ref':ref,'url':url})
            elif line.startswith('commit: ') and section<=1:
                section=1;titles.append(_unlabel(line[8:]))
            elif line.startswith('by: '):
                section=2;actors.append(_unlabel(line[4:]))
            else:return None
        answer={'sha':marker[1],'project':fact['project'],'revision':fact['rev'],
                'source_keys':fact['events'],'refs':refs,'titles':titles,'actors':actors}
        if (not refs or len(refs)>256 or len(fact['events'])>2048 or len(content.encode())>48000
                or refs!=sorted(refs,key=lambda r:r['ref']) or len({r['ref'] for r in refs})!=len(refs)
                or any(values!=sorted(set(values)) for values in (titles,actors,fact['events']))
                or not 1<=fact['rev']<=999999999 or set(marker[1])=={'0'}
                or render_snapshot(answer)!=content):return None
        return answer
    except (ValueError,TypeError,KeyError,IndexError,AttributeError):
        return None


class TagBatches:
    """One root per full commit scope, with immutable ordinary outbox actions."""
    def __init__(self, syncer, api):
        self.s=syncer;self.a=SimpleNamespace(**api)

    def _need(self, condition):
        if not condition:raise self.a.SyncError('tag batch identity, history or delivery proof is incomplete')

    def eligible(self, record):
        return (record.get('object')=='tag' and record.get('event')=='tag_created'
                and isinstance(record.get('sha'),str) and COMMIT.fullmatch(record['sha']) is not None
                and set(record['sha'])!={'0'} and ref_url(record.get('ref'),record.get('url')))

    def _scope(self, project, sha):
        self._need(project in self.s.config['gitlab']['projects'] and COMMIT.fullmatch(sha) is not None and set(sha)!={'0'})
        return {'channel':self.s.channel,'publisher':self.s.publisher,'project':project,'sha':sha}

    def _path(self, scope):
        key=hashlib.sha256(json.dumps(scope,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return self.s.state_dir/('gitlab-tag-batch-'+key+'.json')

    def _disk(self, scope):
        try:disk=json.loads(self._path(scope).read_text())
        except FileNotFoundError:return None
        except (OSError,ValueError):raise self.a.SyncError('tag batch state is unreadable') from None
        self._need(isinstance(disk,dict) and set(disk)=={'version','scope','root_id','event_id','created_at','snapshot','legacy_keys'}
                   and disk['version']==1 and disk['scope']==scope
                   and isinstance(disk['root_id'],str) and self.a.HEX64_RE.fullmatch(disk['root_id'])
                   and isinstance(disk['event_id'],str) and self.a.HEX64_RE.fullmatch(disk['event_id'])
                   and isinstance(disk['legacy_keys'],list)
                   and all(isinstance(k,str) and self.a.EVENT_KEY_RE.fullmatch(k) for k in disk['legacy_keys']))
        return disk

    def _history(self, scopes=None):
        # Established aggregates use fresh exact roots plus explicitly paged
        # edits. Only first discovery / lost ledger needs complete root history.
        saved=[self._disk(scope) for scope in scopes] if scopes else []
        if saved and all(saved):
            rows=[]
            for disk in saved:
                found=[r for r in self.s.buzz.thread(disk['root_id']) if r.get('id')==disk['root_id']]
                self._need(len(found)==1);rows.extend(found)
        else:
            rows=self.s.buzz.channel_messages(0)
        roots=[]
        for event in rows:
            if event.get('pubkey')!=self.s.publisher:continue
            fact=self.a.parse_header(event.get('content'))
            marked='[gitlab-tag-batch:v1]' in str(event.get('content',''))
            if not fact and not marked:continue
            self.a.verify_nostr_event_signature(event,label='tag batch history')
            self._need(event.get('kind')==9 and self.a._tag_values(event,'h')==[['h',self.s.channel]])
            if self.a._tag_values(event,'e'):continue
            if marked:self._need(parse_snapshot(event['content'],self.a.parse_header) is not None
                                 and not self.a._tag_values(event,'p'))
            roots.append(event)
        batches=[r for r in roots if parse_snapshot(r['content'],self.a.parse_header)]
        edits=self.s.buzz.compact_edits(batches) if batches else []
        for edit in edits:
            self.a.verify_nostr_event_signature(edit,label='tag batch edit')
            self._need(edit.get('pubkey')==self.s.publisher and edit.get('kind')==40003
                       and self.a._tag_values(edit,'h')==[['h',self.s.channel]]
                       and not self.a._tag_values(edit,'p'))
        return roots,edits

    def _contains(self, later, earlier):
        return (set(earlier['source_keys'])<=set(later['source_keys'])
                and all(item in later['refs'] for item in earlier['refs'])
                and set(earlier['titles'])<=set(later['titles'])
                and set(earlier['actors'])<=set(later['actors']))

    def _recover(self, scope, history):
        roots,edits=history
        disk=self._disk(scope)
        legacy_keys=self.a.posted_keys([r for r in roots if not parse_snapshot(r['content'],self.a.parse_header)
            and (self.a.parse_header(r['content']) or {}).get('project')==scope['project']],self.s.publisher)
        if disk:legacy_keys.update(disk['legacy_keys'])
        candidates=[]
        for root in roots:
            snap=parse_snapshot(root['content'],self.a.parse_header)
            if snap and (snap['project'],snap['sha'])==(scope['project'],scope['sha']):candidates.append((root,snap))
        self._need(len(candidates)<=1)
        state=None
        if candidates:
            root,snap=candidates[0];self._need(snap['revision']==1)
            current=root;seen={1:(root['id'],snap)}
            revisions=[]
            for edit in edits:
                if self.a.edit_target(edit)!=root['id']:continue
                replacement=parse_snapshot(edit['content'],self.a.parse_header)
                self._need(replacement is not None and (replacement['project'],replacement['sha'])==(scope['project'],scope['sha']))
                revisions.append((replacement['revision'],edit,replacement))
            for rev,edit,replacement in sorted(revisions,key=lambda row:(row[0],row[1]['id'])):
                self._need(rev==snap['revision']+1 and edit['created_at']>current['created_at'] and self._contains(replacement,snap))
                current,snap=edit,replacement;seen[rev]=(edit['id'],replacement)
            state={'version':1,'scope':scope,'root_id':root['id'],'event_id':current['id'],
                   'created_at':current['created_at'],'snapshot':snap,'legacy_keys':sorted(legacy_keys)}
        if disk is not None:
            self._need(state is not None and disk['root_id']==state['root_id'])
            old=disk['snapshot'];proof=seen.get(old.get('revision')) if isinstance(old,dict) else None
            self._need(proof is not None and proof==(disk['event_id'],old) and self._contains(state['snapshot'],old))
        return state

    def _confirm(self, payload, result_id=None, history=None):
        meta=payload['tag_batch'];scope=meta['scope'];snapshot=meta['snapshot'];root_id=meta['root_id']
        self._need(scope==self._scope(snapshot['project'],snapshot['sha'])
                   and render_snapshot(snapshot)==payload['content']
                   and parse_snapshot(payload['content'],self.a.parse_header)==snapshot)
        if history is None:
            history=self._history([scope])
        elif result_id is not None:
            # Exact successful-write readback, not another channel-wide scan.
            self._need(isinstance(result_id,str) and self.a.HEX64_RE.fullmatch(result_id))
            found=[r for r in self.s.buzz.thread(result_id) if r.get('id')==result_id]
            self._need(len(found)==1)
            event=found[0];self.a.verify_nostr_event_signature(event,label='tag batch write')
            self._need(event.get('pubkey')==self.s.publisher and event.get('content')==payload['content']
                       and self.a._tag_values(event,'h')==[['h',self.s.channel]] and not self.a._tag_values(event,'p')
                       and (event.get('kind')==9 and root_id is None and not self.a._tag_values(event,'e')
                            or event.get('kind')==40003 and self.a.edit_target(event)==root_id))
            destination=history[0] if event['kind']==9 else history[1]
            if event['id'] not in {r['id'] for r in destination}:destination.append(event)
        state=self._recover(scope,history)
        self._need(state is not None)
        candidates=[]
        for event in [*history[0],*history[1]]:
            if (event['content']==payload['content'] and (result_id is None or event['id']==result_id)
                    and (event['kind']==9 and root_id is None and not self.a._tag_values(event,'e')
                         or event['kind']==40003 and root_id==state['root_id'] and self.a.edit_target(event)==root_id)):
                candidates.append(event)
        self._need(len(candidates)==1 and self._contains(state['snapshot'],snapshot))
        # Proof and complete facts become durable BEFORE dropping the outbox
        # action. A crash here is reconciled using the same frozen operation.
        self.a.atomic_write_json(self._path(scope),state)
        return candidates[0]['id']

    def reconcile(self, item):
        payload=item.get('payload');meta=payload.get('tag_batch') if isinstance(payload,dict) else None
        self._need(isinstance(meta,dict) and set(meta)=={'scope','snapshot','root_id'})
        kind=item.get('kind');root=meta['root_id']
        self._need(kind==('buzz_edit' if root else 'buzz_message')
                   and item.get('change_id')==self.a.delivery_change_id(self.s.channel,kind,payload))
        expected={'content','project_id','tag_batch','event_id'} if root else {'content','project_id','tag_batch','reply_to','mentions'}
        self._need(set(payload)==expected and payload['project_id']==meta['scope']['project']
                   and (payload.get('event_id')==root if root else payload.get('reply_to') is None and payload.get('mentions')==[]))
        # Validate scope/shape before an unattempted operation can dispatch.
        snap=meta['snapshot'];self._need(meta['scope']==self._scope(snap['project'],snap['sha'])
                                        and parse_snapshot(payload['content'],self.a.parse_header)==snap)
        self._prior_pending([meta['scope']],check_only=True)
        history=None
        if item.get('attempted') is False:
            history=self._history([meta['scope']]);current=self._recover(meta['scope'],history)
            if current and current['snapshot']==snap:
                result=self._confirm(payload,history=history)
                self.s._ack_delivery(item['change_id'],recovered=True,result=result)
                return
            self._need((root is None and current is None and snap['revision']==1)
                       or (current is not None and root==current['root_id']
                           and snap['revision']==current['snapshot']['revision']+1
                           and self._contains(snap,current['snapshot'])
                           and int(self.a.dt.datetime.now(self.a.dt.timezone.utc).timestamp())>current['created_at']))
        try:
            result=self.s._execute_queued_delivery(item) if item.get('attempted') is False else None
        except self.a.BuzzSendRejected:
            self.s._discard_rejected_delivery(item['change_id'])
            raise
        result=self._confirm(payload,result,history)
        self.s._ack_delivery(item['change_id'],recovered=True,result=result)

    def _prior_pending(self, scopes, *, dry_run=False, check_only=False):
        # The generic outbox filename includes the whole configured project
        # list. That list may change while this stable group remains UNKNOWN.
        # Recover only this group's exact immutable operation in its original
        # ledger; never migrate it into a freshly named, empty outbox.
        matches=[]
        current_path=self.s.outbox_path()
        for path in sorted(self.s.state_dir.glob('gitlab-buzz-sync-*.outbox.json')):
            if path==current_path:continue
            try:ledger=json.loads(path.read_text())
            except (OSError,ValueError):raise self.a.SyncError('tag batch prior outbox is unreadable') from None
            self._need(isinstance(ledger,dict) and ledger.get('version')==1
                       and isinstance(ledger.get('pending'),list) and isinstance(ledger.get('acked'),list))
            for item in ledger['pending']:
                self._need(isinstance(item,dict))
                payload=item.get('payload')
                if not isinstance(payload,dict) or 'tag_batch' not in payload:continue
                meta=payload['tag_batch']
                self._need(isinstance(meta,dict) and set(meta)=={'scope','snapshot','root_id'}
                           and isinstance(meta.get('scope'),dict)
                           and set(meta['scope'])=={'channel','publisher','project','sha'})
                if meta['scope'] in scopes:matches.append((path,item))
        # Two queued operations for one group cannot establish which was first.
        for scope in scopes:
            self._need(sum(item['payload']['tag_batch']['scope']==scope for _,item in matches)<=1)
        if dry_run or check_only:self._need(not matches);return
        existed='outbox_path' in self.s.__dict__;previous=self.s.__dict__.get('outbox_path')
        try:
            for path,item in matches:
                self.s.outbox_path=lambda path=path:path
                self.reconcile(item)
        finally:
            if existed:self.s.outbox_path=previous
            else:self.s.__dict__.pop('outbox_path',None)

    def sync(self, records, summary, dry_run):
        grouped={}
        for record in records:
            if self.eligible(record):grouped.setdefault((record['project'],record['sha']),[]).append(record)
        if not grouped:return set()
        scopes=[self._scope(project,sha) for project,sha in grouped]
        self._prior_pending(scopes,dry_run=dry_run)
        history=self._history(scopes)
        # Only legacy, individually ACKed notices are excluded. An aggregate's
        # edit source keys are recovered through its scoped full snapshot.
        handled=set()
        for (project,sha),group in sorted(grouped.items()):
            scope=self._scope(project,sha);state=self._recover(scope,history)
            legacy_keys=set(state['legacy_keys']) if state else self.a.posted_keys(
                [r for r in history[0] if not parse_snapshot(r['content'],self.a.parse_header)
                 and (self.a.parse_header(r['content']) or {}).get('project')==project],self.s.publisher)
            old=state['snapshot'] if state else {'refs':[],'source_keys':[],'titles':[],'actors':[]}
            refs={r['ref']:r['url'] for r in old['refs']};keys=set(old['source_keys']);titles=set(old['titles']);actors=set(old['actors'])
            for record in group:
                handled.add(record['key'])
                if record['key'] in legacy_keys or record['key'] in keys:continue
                ref,url=record['ref'],record['url'];self._need(ref not in refs or refs[ref]==url)
                refs[ref]=url;keys.add(record['key'])
                if record.get('title'):titles.add(record['title'])
                if record.get('actor') not in (None,'','-','?'):actors.add(record['actor'])
            if keys==set(old['source_keys']):
                if state and not dry_run:self.a.atomic_write_json(self._path(scope),state)
                continue
            snapshot={'sha':sha,'project':project,'revision':old.get('revision',0)+1,
                      'source_keys':sorted(keys),'refs':[{'ref':r,'url':u} for r,u in sorted(refs.items())],
                      'titles':sorted(titles),'actors':sorted(actors)}
            content=render_snapshot(snapshot);self._need(parse_snapshot(content,self.a.parse_header)==snapshot)
            if dry_run:summary['notified']['instant']+=1;continue
            # Native edit selection orders by event timestamp. Never publish
            # two aggregate revisions in one second and risk choosing the old.
            if state:self._need(int(self.a.dt.datetime.now(self.a.dt.timezone.utc).timestamp())>state['created_at'])
            root=state['root_id'] if state else None
            meta={'scope':scope,'snapshot':snapshot,'root_id':root}
            payload={'content':content,'project_id':project,'tag_batch':meta}
            if root:payload['event_id']=root;kind='buzz_edit'
            else:payload.update(reply_to=None,mentions=[]);kind='buzz_message'
            def action():
                result=self.s.buzz.edit(root,content) if root else self.s.buzz.send(content,reply_to=None,mentions=[])
                self._confirm(payload,result,history)
                return result
            self.s._deliver(kind,payload,action)
            summary['notified']['instant']+=1
        return handled
