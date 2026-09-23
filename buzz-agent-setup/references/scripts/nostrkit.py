"""Pure-python BIP-340 schnorr + bech32 (nsec/npub) — no external deps."""
import hashlib, secrets

p = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)

def tagged_hash(tag, msg):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + msg).digest()

def point_add(P1, P2):
    if P1 is None: return P2
    if P2 is None: return P1
    if P1[0] == P2[0] and P1[1] != P2[1]: return None
    if P1 == P2:
        lam = (3 * P1[0] * P1[0] * pow(2 * P1[1], p - 2, p)) % p
    else:
        lam = ((P2[1] - P1[1]) * pow(P2[0] - P1[0], p - 2, p)) % p
    x3 = (lam * lam - P1[0] - P2[0]) % p
    return (x3, (lam * (P1[0] - x3) - P1[1]) % p)

def point_mul(P, k):
    R = None
    for i in range(256):
        if (k >> i) & 1: R = point_add(R, P)
        P = point_add(P, P)
    return R

def bytes_from_int(x): return x.to_bytes(32, 'big')
def has_even_y(P): return P[1] % 2 == 0
def lift_x(x):
    if x >= p: return None
    y_sq = (pow(x, 3, p) + 7) % p
    y = pow(y_sq, (p + 1) // 4, p)
    if pow(y, 2, p) != y_sq: return None
    return (x, y if y % 2 == 0 else p - y)

def pubkey_xonly(seckey: bytes) -> bytes:
    d0 = int.from_bytes(seckey, 'big')
    if not 1 <= d0 <= n - 1:
        raise ValueError('seckey 不在 [1, n-1] 内')
    return bytes_from_int(point_mul(G, d0)[0])

def schnorr_sign(msg32: bytes, seckey: bytes, aux: bytes = None) -> bytes:
    if aux is None: aux = b'\x00' * 32
    d0 = int.from_bytes(seckey, 'big')
    if not 1 <= d0 <= n - 1:
        raise ValueError('seckey 不在 [1, n-1] 内')
    P = point_mul(G, d0)
    d = d0 if has_even_y(P) else n - d0
    t = bytes(a ^ b for a, b in zip(bytes_from_int(d), tagged_hash("BIP0340/aux", aux)))
    k0 = int.from_bytes(tagged_hash("BIP0340/nonce", t + bytes_from_int(P[0]) + msg32), 'big') % n
    if k0 == 0:
        raise ValueError('nonce k0 为 0，无法签名')
    R = point_mul(G, k0)
    k = k0 if has_even_y(R) else n - k0
    e = int.from_bytes(tagged_hash("BIP0340/challenge",
        bytes_from_int(R[0]) + bytes_from_int(P[0]) + msg32), 'big') % n
    return bytes_from_int(R[0]) + bytes_from_int((k + e * d) % n)

def schnorr_verify(msg32: bytes, pubkey32: bytes, sig: bytes) -> bool:
    P = lift_x(int.from_bytes(pubkey32, 'big'))
    if P is None: return False
    r = int.from_bytes(sig[:32], 'big'); s = int.from_bytes(sig[32:], 'big')
    if r >= p or s >= n: return False
    e = int.from_bytes(tagged_hash("BIP0340/challenge", sig[:32] + pubkey32 + msg32), 'big') % n
    R = point_add(point_mul(G, s), point_mul(P, n - e))
    return R is not None and has_even_y(R) and R[0] == r

CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
def _polymod(values):
    GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk
def _hrp_expand(hrp): return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]
def _convertbits(data, frombits, tobits, pad=True):
    acc = 0; bits = 0; ret = []; maxv = (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value; bits += frombits
        while bits >= tobits:
            bits -= tobits; ret.append((acc >> bits) & maxv)
    if pad and bits: ret.append((acc << (tobits - bits)) & maxv)
    return ret
def bech32_encode(hrp, data32: bytes) -> str:
    data = _convertbits(data32, 8, 5)
    chk = _polymod(_hrp_expand(hrp) + data + [0, 0, 0, 0, 0, 0]) ^ 1
    return hrp + '1' + ''.join([CHARSET[d] for d in data + [(chk >> 5 * (5 - i)) & 31 for i in range(6)]])
def bech32_decode(s: str, expected_hrp: str = None) -> bytes:
    """解 bech32。校验和与 hrp 都要验——抄错/贴错一个字符必须报错，
    不能静默解出另一把密钥（那会铸出错误身份、签出验不过的名，且事后极难定位）。"""
    hrp, sep, data = s.rpartition('1')
    if not sep or not hrp or len(data) < 6:
        raise ValueError(f'malformed bech32: {s[:12]}…')
    if expected_hrp is not None and hrp != expected_hrp:
        raise ValueError(f'bech32 hrp mismatch: expected {expected_hrp!r}, got {hrp!r}')
    try:
        d = [CHARSET.index(c) for c in data]
    except ValueError:
        raise ValueError(f'invalid bech32 character in {s[:12]}…') from None
    if _polymod(_hrp_expand(hrp) + d) != 1:
        raise ValueError(f'bech32 checksum failed for {hrp}1…（抄错或贴错了？）')
    return bytes(_convertbits(d[:-6], 5, 8, False))


if __name__ == '__main__':
    # 自检：BIP-340 官方测试向量 + bech32 往返。改动本文件后先跑 `python3 nostrkit.py`
    sk = bytes.fromhex('00' * 31 + '03')
    if pubkey_xonly(sk).hex().upper() != \
            'F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9':
        raise SystemExit('self-test FAILED: pubkey vector')
    sig = schnorr_sign(bytes(32), sk, bytes(32))
    if sig.hex().upper() != (
            'E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA8215'
            '25F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0'):
        raise SystemExit('self-test FAILED: sig vector')
    if not schnorr_verify(bytes(32), pubkey_xonly(sk), sig):
        raise SystemExit('self-test FAILED: verify')
    if schnorr_verify(b'\x01' * 32, pubkey_xonly(sk), sig):
        raise SystemExit('self-test FAILED: verify must reject wrong msg')
    import secrets as _s
    k = _s.token_bytes(32)
    if bech32_decode(bech32_encode('nsec', k)) != k:
        raise SystemExit('self-test FAILED: bech32 roundtrip')
    print('nostrkit self-test OK')
