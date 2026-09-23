#!/usr/bin/env python3
"""stdin: {"seckey":hex, "kind":int, "tags":[[..]], "content":str, "created_at":int?}
stdout: 完整签名事件 JSON（NIP-01 id + BIP-340 sig）"""
import sys, json, hashlib, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nostrkit as nk

req = json.load(sys.stdin)
sk = bytes.fromhex(req['seckey'])
pub = nk.pubkey_xonly(sk).hex()
ev = {'pubkey': pub,
      'created_at': req.get('created_at') or int(time.time()),
      'kind': req['kind'],
      'tags': req.get('tags', []),
      'content': req.get('content', '')}
ser = json.dumps([0, ev['pubkey'], ev['created_at'], ev['kind'], ev['tags'], ev['content']],
                 separators=(',', ':'), ensure_ascii=False).encode()
ev['id'] = hashlib.sha256(ser).hexdigest()
ev['sig'] = nk.schnorr_sign(bytes.fromhex(ev['id']), sk, os.urandom(32)).hex()
# 签完自校验：签错了不如不签。用 raise 而非 assert —— python3 -O 会剥掉 assert
if not nk.schnorr_verify(bytes.fromhex(ev['id']), bytes.fromhex(pub), bytes.fromhex(ev['sig'])):
    raise SystemExit('自校验失败：签名验不过，拒绝输出事件')
json.dump(ev, sys.stdout, separators=(',', ':'), ensure_ascii=False)
