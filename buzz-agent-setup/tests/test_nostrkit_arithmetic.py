"""Real BIP340 and legacy affine arithmetic controls; no external dependencies."""
from pathlib import Path
import importlib.util
import unittest

_SUBJECT = Path(__file__).resolve().parents[1] / "references/scripts/nostrkit.py"
_spec = importlib.util.spec_from_file_location("_nostrkit_arithmetic_subject", _SUBJECT)
nostrkit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nostrkit)

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def affine_add(left, right):
    """Independent slope oracle with the original malformed-point behavior.

    Prime-field modular inversion equals the original Fermat exponent; preserve
    its zero denominator result explicitly. No subject arithmetic is called.
    """
    if left is None:
        return right
    if right is None:
        return left
    if left[0] == right[0] and left[1] != right[1]:
        return None
    if left == right:
        numerator, denominator = 3 * left[0] * left[0], 2 * left[1]
    else:
        numerator, denominator = right[1] - left[1], right[0] - left[0]
    inverse = pow(denominator, -1, P) if denominator % P else 0
    slope = numerator * inverse % P
    x = (slope * slope - left[0] - right[0]) % P
    return x, (slope * (left[0] - x) - left[1]) % P


def affine_mul(point, scalar):
    """Legacy 256-bit little-endian double/add, including Python negatives."""
    result = None
    for bit in range(256):
        if (scalar >> bit) & 1:
            result = affine_add(result, point)
        point = affine_add(point, point)
    return result


def outcome(operation, point, scalar):
    try:
        return "value", operation(point, scalar)
    except Exception as error:
        return "exception", type(error)


# Official BIP340 test vectors 0 and 1, including nonzero auxiliary randomness.
VECTOR0=dict(sk=bytes.fromhex('00'*31+'03'),msg=bytes(32),aux=bytes(32),
    pub=bytes.fromhex('F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9'),
    sig=bytes.fromhex('E907831F80848D1069A5371B402410364BDF1C5F8307B0084C55F1CE2DCA821525F66A4A85EA8B71E482A74F382D2CE5EBEEE8FDB2172F477DF4900D310536C0'))
VECTOR1=dict(sk=bytes.fromhex('B7E151628AED2A6ABF7158809CF4F3C762E7160F38B4DA56A784D9045190CFEF'),
    msg=bytes.fromhex('243F6A8885A308D313198A2E03707344A4093822299F31D0082EFA98EC4E6C89'),aux=bytes.fromhex('00'*31+'01'),
    pub=bytes.fromhex('DFF1D77F2A671C5F36183726DB2341BE58FEAE1DA2DECED843240F7B502BA659'),
    sig=bytes.fromhex('6896BD60EEAE296DB48A229FF71DFE071BDE413E6D43F917DC8DCF8C78DE33418906D11AC976ABCCB20B091292BFF4EA897EFCB639EA871CFA95F6DE339E4B0A'))

class NostrkitArithmetic(unittest.TestCase):
    def test_repeated_public_signature_proofs_are_bounded_and_content_bound(self):
        from unittest import mock
        v = VECTOR1
        if hasattr(nostrkit, "_verified_public_signature"):
            nostrkit._verified_public_signature.cache_clear()
        with mock.patch.object(nostrkit, "point_mul", wraps=nostrkit.point_mul) as multiply:
            self.assertTrue(nostrkit.schnorr_verify(v["msg"], v["pub"], v["sig"]))
            first = multiply.call_count
            for _ in range(20):
                self.assertTrue(nostrkit.schnorr_verify(v["msg"], v["pub"], v["sig"]))
            self.assertEqual(multiply.call_count, first)
            self.assertFalse(nostrkit.schnorr_verify(b"x" * 32, v["pub"], v["sig"]))
            self.assertFalse(nostrkit.schnorr_verify(v["msg"], VECTOR0["pub"], v["sig"]))
            self.assertFalse(nostrkit.schnorr_verify(v["msg"], v["pub"], bytes(64)))
        self.assertEqual(nostrkit._verified_public_signature.cache_info().maxsize, 4096)

    def test_public_proof_cache_eviction_and_legacy_mutable_inputs(self):
        from unittest import mock
        v = VECTOR0
        nostrkit._verified_public_signature.cache_clear()
        self.assertTrue(nostrkit.schnorr_verify(v["msg"], v["pub"], v["sig"]))
        # Out-of-field keys reject cheaply, exercising the actual bounded cache.
        for i in range(4097):
            self.assertFalse(nostrkit.schnorr_verify(i.to_bytes(32, "big"),
                             P.to_bytes(32, "big"), v["sig"]))
        self.assertEqual(nostrkit._verified_public_signature.cache_info().currsize, 4096)
        with mock.patch.object(nostrkit, "point_mul", wraps=nostrkit.point_mul) as multiply:
            self.assertTrue(nostrkit.schnorr_verify(v["msg"], v["pub"], v["sig"]))
            self.assertEqual(multiply.call_count, 2)
        class BytesSubclass(bytes):
            pass
        for wrap in (bytearray, memoryview, BytesSubclass):
            for index in range(3):
                args = [v["msg"], v["pub"], v["sig"]]
                args[index] = wrap(args[index])
                def result(function):
                    try:
                        return ("value", function(*args))
                    except Exception as exc:
                        return ("error", type(exc))
                before = nostrkit._verified_public_signature.cache_info()
                self.assertEqual(result(nostrkit.schnorr_verify), result(nostrkit._schnorr_verify))
                self.assertEqual(before, nostrkit._verified_public_signature.cache_info())

    def test_official_vectors_zero_and_one(self):
        self.assertEqual((nostrkit.p, nostrkit.n, nostrkit.G), (P, N, G))
        for vector in (VECTOR0, VECTOR1):
            with self.subTest(public_key=vector["pub"].hex()):
                self.assertEqual(nostrkit.pubkey_xonly(vector["sk"]), vector["pub"])
                self.assertEqual(nostrkit.schnorr_sign(vector["msg"], vector["sk"],
                                                      vector["aux"]), vector["sig"])
                self.assertTrue(nostrkit.schnorr_verify(vector["msg"], vector["pub"],
                                                       vector["sig"]))

    def test_wrong_message_key_signature_and_ranges_rejected(self):
        vector = VECTOR0
        wrong_key = G[0].to_bytes(32, "big")
        cases = [(b"\x01" * 32, vector["pub"], vector["sig"]),
                 (vector["msg"], wrong_key, vector["sig"]),
                 (vector["msg"], vector["pub"], vector["sig"][:-1] +
                  bytes([vector["sig"][-1] ^ 1])),
                 (vector["msg"], P.to_bytes(32, "big"), vector["sig"]),
                 (vector["msg"], vector["pub"], bytes(64))]
        cases += [(vector["msg"], vector["pub"], r.to_bytes(32, "big") +
                   vector["sig"][32:]) for r in (P, P + 1)]
        cases += [(vector["msg"], vector["pub"], vector["sig"][:32] +
                   scalar.to_bytes(32, "big")) for scalar in (N, N + 1)]
        for index, (message, key, signature) in enumerate(cases):
            with self.subTest(control=index):
                self.assertFalse(nostrkit.schnorr_verify(message, key, signature))

    def test_zero_infinity_and_infinite_verification_rejected(self):
        negative = G[0], -G[1] % P
        self.assertIsNone(nostrkit.point_mul(G, 0))
        self.assertIsNone(nostrkit.point_add(G, negative))
        self.assertEqual(nostrkit.point_add(None, G), G)
        self.assertEqual(nostrkit.point_add(G, None), G)
        self.assertIsNone(nostrkit.point_add(None, None))
        for left, right in (((1, 0), (1, 0)), ((1, 1), (1 + P, 2))):
            self.assertEqual(nostrkit.point_add(left, right), affine_add(left, right))
        public_key, message, r = G[0].to_bytes(32, "big"), bytes(32), bytes(32)
        challenge = int.from_bytes(nostrkit.tagged_hash(
            "BIP0340/challenge", r + public_key + message), "big") % N
        signature = r + challenge.to_bytes(32, "big")
        self.assertIsNone(nostrkit.point_add(nostrkit.point_mul(G, challenge),
                                            nostrkit.point_mul(G, N - challenge)))
        self.assertFalse(nostrkit.schnorr_verify(message, public_key, signature))

    def test_point_add_and_field_inverse_match_affine_oracle(self):
        points = [None] + [affine_mul(G, k) for k in (1, 2, 3, 7, 16)]
        for left in points:
            for right in points:
                self.assertEqual(nostrkit.point_add(left, right), affine_add(left, right))
        for denominator in (0, 1, 2, -1, P - 1, P, P + 1, G[0], G[1]):
            inverse = pow(denominator, -1, P) if denominator % P else 0
            self.assertEqual(inverse, pow(denominator, P - 2, P))

    def test_canonical_points_and_scalar_boundaries_match_affine_oracle(self):
        points = [None, G, (G[0], -G[1] % P)]
        points += [affine_mul(G, k) for k in (2, 3, 7, 16, 257)]
        scalars = (0, 1, 2, N - 1, N, N + 1, -1, -2, -N - 1,
                   (1 << 256) - 1, (1 << 256) + 1, (1 << 300) + 17, True, False)
        for index, point in enumerate(points):
            for scalar in scalars:
                with self.subTest(point=index, scalar=scalar):
                    self.assertEqual(nostrkit.point_mul(point, scalar),
                                     affine_mul(point, scalar))

    def test_legacy_point_and_scalar_fallback_preserves_exact_outcomes(self):
        points = [list(G), (1, 0), (1, 1), (G[0] + P, G[1]),
                  (G[0], G[1] + P), (1,), ("x", 1)]
        for index, point in enumerate(points):
            for scalar in (0, 1, 2, -1, 1 << 256):
                with self.subTest(point=index, scalar=scalar):
                    self.assertEqual(outcome(nostrkit.point_mul, point, scalar),
                                     outcome(affine_mul, point, scalar))
        for point in (None, G):
            for scalar in (None, 1.5, "1"):
                with self.subTest(scalar_type=type(scalar).__name__):
                    self.assertEqual(outcome(nostrkit.point_mul, point, scalar),
                                     outcome(affine_mul, point, scalar))


if __name__ == "__main__":
    unittest.main()
