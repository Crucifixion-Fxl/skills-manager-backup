#!/usr/bin/env python3
"""铸一个 buzz agent 身份：生成密钥 + owner 的 NIP-OA 背书签名。

用法:  python3 mint-agent.py <agent-name> [<agent-name> ...]
       python3 mint-agent.py -h | --help        只打印用法：不读 owner 密钥，不生成任何密钥
读:    ~/.config/buzz/env 里的 owner BUZZ_PRIVATE_KEY
出:    每个 agent 的 nsec / npub / pubkey_hex / BUZZ_AUTH_TAG（JSON 到 stdout）

名字规则：[A-Za-z0-9][A-Za-z0-9._-]{0,62}。以 `-` 开头的参数、不符合规则的名字一律先于读密钥被拒绝：
stderr 报错、退出码 2、stdout 没有任何密钥材料。想看参数请用 --help，别为了试参数而铸密钥。

⚠️ stdout 含私钥：重定向落盘务必先收紧 umask，别写进 /tmp 或 shell history：
     (umask 077; python3 mint-agent.py nh-desk > keys.json)   # 生成 600 文件
   用完 `shred -u keys.json`。

背书原理：owner 用 BIP-340 对 sha256("nostr:agent-auth:<agent_pub_hex>:") 签名，
得到 ["auth", <owner_pub>, "", <sig>] 作为 agent 的 BUZZ_AUTH_TAG。
带上它 relay 就放行该 agent（relay >= 0.2.1），不需要 buzz-admin add-member。
"""
import hashlib, secrets, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nostrkit as nk

# 用法文字里不出现任何密钥字段名：--help 的输出必须能原样贴进聊天／日志而不含密钥材料。
USAGE = """用法: python3 mint-agent.py <agent-name> [<agent-name> ...]
      python3 mint-agent.py -h | --help

铸 buzz agent 身份：为每个名字生成一对密钥，并用 owner 密钥（~/.config/buzz/env 里的
BUZZ_PRIVATE_KEY）签一份 NIP-OA 背书；JSON 输出到 stdout，其中含私钥。

名字规则: [A-Za-z0-9][A-Za-z0-9._-]{0,62}
以 - 开头的参数、不符合规则的名字：stderr 报错，退出码 2，不读 owner 密钥，stdout 没有任何输出。
-h / --help：只打印这段用法，退出码 0；不读 owner 密钥，不生成任何密钥。

想看参数请用 --help，别为了试参数而铸密钥。
输出落盘务必先 (umask 077; python3 mint-agent.py nh-desk > keys.json)，用完 shred -u keys.json。
"""
NAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,62}')

args = sys.argv[1:]
if any(a in ('-h', '--help') for a in args):
    sys.stdout.write(USAGE)
    sys.exit(0)
if not args:
    sys.stderr.write(USAGE)
    sys.exit(2)
# 全部参数先校验、再读 owner 密钥、再铸：任何一个名字不合规就整批拒绝，不会铸出一半。
# 用 fullmatch：re.match + `$` 会放过结尾的换行（"abc\n"）。以 - 开头的参数本来就不符合规则。
bad = [a for a in args if not NAME_RE.fullmatch(a)]
if bad:
    # 回显只截前 24 个字符：误把密钥当名字粘进来时，错误信息也不会把它完整回显进日志。
    shown = ', '.join(repr(a[:24]) + ('…' if len(a) > 24 else '') for a in bad)
    sys.stderr.write('mint-agent.py: 不合法的 agent 名字 %s（规则 [A-Za-z0-9][A-Za-z0-9._-]{0,62}；'
                     '想看用法请用 --help）。未读 owner 密钥，未生成任何密钥。\n' % shown)
    sys.exit(2)
names = args

env = dict(l.strip().split('=', 1) for l in open(os.path.expanduser('~/.config/buzz/env'))
           if '=' in l and not l.startswith('#'))
v = env['BUZZ_PRIVATE_KEY'].strip().strip('\'"')   # env 文件里的值可能带引号
owner_sk = nk.bech32_decode(v, 'nsec') if v.startswith('nsec') else bytes.fromhex(v)
owner_pub = nk.pubkey_xonly(owner_sk).hex()

out = {}
for name in names:
    while True:
        sk = secrets.token_bytes(32)
        if 1 <= int.from_bytes(sk, 'big') <= nk.n - 1:
            break
    pub = nk.pubkey_xonly(sk).hex()
    msg = hashlib.sha256(f"nostr:agent-auth:{pub}:".encode()).digest()
    sig = nk.schnorr_sign(msg, owner_sk, secrets.token_bytes(32)).hex()
    # 自校验：签错了不如不签。用 raise 而非 assert —— python3 -O 会剥掉 assert
    if not nk.schnorr_verify(msg, bytes.fromhex(owner_pub), bytes.fromhex(sig)):
        raise SystemExit('自校验失败：owner 背书签名验不过')
    nsec = nk.bech32_encode('nsec', sk)
    if nk.bech32_decode(nsec) != sk:
        raise SystemExit('自校验失败：nsec bech32 往返不一致')
    out[name] = {'pubkey_hex': pub, 'npub': nk.bech32_encode('npub', bytes.fromhex(pub)),
                 'nsec': nsec,
                 'auth_tag': json.dumps(["auth", owner_pub, "", sig], separators=(',', ':'))}
json.dump(out, sys.stdout, indent=2)
print()
