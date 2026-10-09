"""Dynamic onboarding proof from current daemon tasks and durable request identity."""
import hashlib
import json
import time
from .join_effects import RegistrationProof, gs

NOTICE = ('动态绑定尚未确认运行，申请继续等待。怎么解决：核对已批准的申请、固定绑定配置、同步 bot 连接、Worker 与 agent 自己的发送器读回。'
          '\n复制给 AI：帮我检查 hostd 动态绑定注册和真实连接证据；不要将写入配置或旧的连接记录当作已接入。')


class RuntimeRegistrar:
    def __init__(self, daemon, *, clock=time.time):
        self.daemon, self.clock = daemon, clock

    def _snapshot(self, row, plan):
        try:
            h = self.daemon
            if h.runtime_store is None or h.onboarding is None:
                raise ValueError
            current = h.runtime_store.join_request(row['request_id'])
            saved = h.runtime_store.effect_plan(row['request_id'])
            if (current is None or saved is None
                    or dict(saved) != dict(plan)
                    or any(current[key] != row[key] for key in ('request_id', 'agent_id', 'owner_pubkey', 'callback_app_id', 'chat_id', 'kind', 'binding_id'))):
                raise ValueError
            record = h.onboarding.records.get(current['callback_app_id'])
            if (record is None or record.pubkey != current['agent_id']
                    or record.owner_pubkey != current['owner_pubkey'] or record.app_id != current['callback_app_id']):
                raise ValueError
            return current, saved
        except Exception:
            raise ValueError(NOTICE) from None

    def _request(self, row, plan):
        current,saved=self._snapshot(row,plan)
        if current['status'] not in {'approved','applied'}:
            raise ValueError(NOTICE)
        return current,saved

    def _restore_done_request(self,row,plan):
        """Restore done only from an active durable grant, including degraded tasks.

        Degraded is a recoverable runtime observation, not a revoked grant.
        Dispatch marks restored active/degraded tasks degraded until the new
        worker proves readiness, so a first-round interruption stays recoverable.
        Paused, conflicting, pending and retired bindings remain excluded.
        """
        current,saved=self._snapshot(row,plan)
        if current['status']!='done':raise ValueError(NOTICE)
        db=self.daemon.runtime_store
        chat_ref=gs.chat_ref(current['chat_id'])
        grant=db.conn.execute("""SELECT b.sync_app_id,b.mirror_pubkey FROM agent a
         JOIN agent_chat ac ON ac.agent_id=a.pubkey
         JOIN binding b ON b.binding_id=ac.binding_id
         WHERE a.pubkey=? AND a.owner_pubkey=? AND a.app_id=? AND a.status='active'
         AND ac.chat_id=? AND ac.binding_id=? AND ac.status='active' AND ac.chat_ref=?
         AND b.status IN ('active','degraded') AND b.channel_id=? AND b.chat_id=? AND b.config_path=?
         AND b.chat_ref IN ('',?)""",
         (current['agent_id'],current['owner_pubkey'],current['callback_app_id'],current['chat_id'],
          saved['binding_id'],chat_ref,saved['channel_id'],current['chat_id'],saved['config_path'],chat_ref)).fetchone()
        if (grant is None or (current['kind']=='channel' and current['binding_id']!=saved['binding_id'])
                or (current['kind']=='new_binding' and (grant['sync_app_id']!=current['callback_app_id']
                                                       or grant['mirror_pubkey']!=saved['mirror_pubkey']))):
            raise ValueError(NOTICE)
        return current,saved

    def check_own_profile(self,record):
        self.daemon.check_own_profile(record)

    async def own_admitted(self,record):
        self.daemon.register_own_app(record)

    async def restore(self,*,request_id=None):
        """Rebuild authorized local tasks only; counts are dispatch, not readiness.

        Never invoke onboarding effects, cards or process activation. A failed
        row stays durable and pending for a later explicit restoration attempt.
        Normal register/readback permissions continue to exclude done rows.
        """
        result={'registered':0,'pending':0,'notice':''}
        try:
            if self.daemon.runtime_store is None or self.daemon.onboarding is None:raise ValueError
            if request_id is None:rows=self.daemon.runtime_store.join_requests()
            else:
                row=self.daemon.runtime_store.join_request(request_id)
                rows=[row] if row is not None else []
        except Exception:
            result['notice']=NOTICE
            return result
        for row in rows:
            if row['status'] not in {'approved','applied','done'}:continue
            try:
                plan=self.daemon.runtime_store.effect_plan(row['request_id'])
                if plan is None:raise ValueError
                if row['status']=='done':
                    current,saved=self._restore_done_request(row,plan)
                    await self._dispatch(current,saved,restore_done=True)
                else:
                    await self.register(row,plan)
                result['registered']+=1
            except Exception:
                result['pending']+=1
                result['notice']=NOTICE
        return result

    async def prepare_members(self, row, plan):
        row, plan = self._request(row, plan)
        return await self.daemon.onboarding.prepare_members(row, plan)

    def _bound(self, row, plan):
        binding = self.daemon.reg.bindings.get(plan['binding_id'])
        if (binding is None or row['binding_id'] != plan['binding_id']
                or binding.channel_id != plan['channel_id'] or binding.chat_id != row['chat_id']
                or str(binding.config) != plan['config_path']):
            raise ValueError(NOTICE)
        return binding

    async def register(self, row, plan):
        row, plan = self._request(row, plan)
        await self._dispatch(row,plan)

    async def _dispatch(self,row,plan,*,restore_done=False):
        options={'restore_done':True} if restore_done else {}
        if row['kind'] == 'channel':
            self._bound(row, plan)
            await self.daemon.register_bound_outlet(row, plan, **options)
            return
        await self.daemon.register_runtime_binding(row, plan, **options)

    async def readback(self, row, plan):
        row, plan = self._request(row, plan)
        h, name, app, agent = self.daemon, plan['binding_id'], row['callback_app_id'], row['agent_id']
        reader_app = self._bound(row, plan).sync_app_id if row['kind'] == 'channel' else app
        alive = lambda task: task is not None and not task.done() and not task.cancelling()
        tasks = h.binding_tasks.get(name, {})
        status = h.status['bindings'].get(name, {})
        worker = (alive(tasks.get('worker')) and name in h.workers and status.get('runs', 0) > 0
                  and h.workers[name].binding_id == name and str(h.workers[name].config) == plan['config_path']
                  and h._binding_state(name) == 'active')
        reader = all(alive(h.app_tasks.get(identity)) and h.status['apps'].get(identity, {}).get('feishu') == 'connected'
                     for identity in {app, reader_app})
        relay = alive(tasks.get('relay')) and status.get('relay') == 'connected'
        report = getattr(h.workers.get(name), 'last', {})
        result = (report.get('hostd') or {}).get('outlet_results', {}).get(agent, {})
        observe = getattr(h.workers.get(name), 'outlet_readiness', None)
        if callable(observe):
            result = observe(agent, h.runtime_store)
        checked = result.get('checked_at')
        outlet = (alive(h.outlet_tasks.get((name, agent))) and h.outlet_status.get((name, agent)) == 'connected'
                  and result.get('status') in {'verified', 'acked'} and result.get('app_id') == app
                  and type(result.get('pending')) is int and result['pending'] == 0
                  and type(checked) is int and 0 <= int(self.clock()) - checked <= 300)
        values = [row['request_id'], name, plan['channel_id'], row['chat_id'], app, worker, reader, relay, outlet]
        digest = hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()
        return RegistrationProof(*values, int(self.clock()), digest)
