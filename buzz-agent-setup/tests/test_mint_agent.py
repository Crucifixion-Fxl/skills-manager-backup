"""Contract for references/scripts/mint-agent.py: `--help` and bad names must never mint key material.

Real incident: the script had no `--help`, took `--help` as an agent name, minted a key pair, printed the nsec to
stdout and signed a NIP-OA endorsement with the owner's key. These tests run the shipped script as a subprocess with
HOME pointed at a throwaway directory that holds a fixed TEST owner key; they never touch the real HOME.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "references" / "scripts"
MINT = SCRIPTS / "mint-agent.py"
sys.path.insert(0, str(SCRIPTS))
import nostrkit as nk  # noqa: E402

# 64 x "1": an obviously fake but valid secp256k1 scalar. Test-only; never a real owner key.
TEST_OWNER_HEX = "1" * 64
TEST_OWNER_PUB = nk.pubkey_xonly(bytes.fromhex(TEST_OWNER_HEX)).hex()
NAME_RULE = r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}"
# Anything that looks like key material: a bech32 secret/public key or a 64-hex string.
KEY_MATERIAL = re.compile(r"nsec1|npub1|[0-9a-f]{64}")


class MintAgentBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name).resolve()
        (self.home / ".config" / "buzz").mkdir(parents=True)
        self.env_file = self.home / ".config" / "buzz" / "env"
        self.write_env(f"BUZZ_PRIVATE_KEY={TEST_OWNER_HEX}\n")
        self.env = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "PYTHONDONTWRITEBYTECODE": "1"}
        # The whole point of the sandbox: prove the child resolves ~ to the throwaway directory.
        probe = subprocess.run([sys.executable, "-c", "import os;print(os.path.expanduser('~'))"], env=self.env,
                               capture_output=True, text=True, check=True)
        self.assertEqual(probe.stdout.strip(), str(self.home))
        real_home = os.path.expanduser("~")
        self.assertNotEqual(str(self.home), real_home)

    def write_env(self, text: str) -> None:
        self.env_file.write_text(text, encoding="utf-8")
        os.chmod(self.env_file, 0o600)

    def run_mint(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(MINT), *args], env=self.env, capture_output=True, text=True,
                              timeout=60)

    def assert_no_key_material(self, proc: subprocess.CompletedProcess, label: str = "") -> None:
        self.assertEqual(proc.stdout, "", f"{label}: stdout must be empty, got {proc.stdout[:80]!r}")
        self.assertIsNone(KEY_MATERIAL.search(proc.stdout + proc.stderr), f"{label}: key material in output")
        self.assertNotIn("nsec", proc.stdout)
        self.assertNotIn("pubkey_hex", proc.stdout)


class HelpTest(MintAgentBase):
    def test_help_flags_print_usage_and_exit_zero_without_minting(self):
        """L1-MINT-001 `-h`／`--help`：退出 0，stdout 是用法（点名脚本、名字规则），没有 nsec／pubkey_hex／任何密钥材料。"""
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                proc = self.run_mint(flag)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("mint-agent.py", proc.stdout)
                self.assertIn(NAME_RULE, proc.stdout)
                self.assertNotIn("nsec", proc.stdout)
                self.assertNotIn("pubkey_hex", proc.stdout)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertIsNone(KEY_MATERIAL.search(proc.stdout + proc.stderr))
                with self.assertRaises(ValueError):  # the usage is text, not the JSON the minting path prints
                    json.loads(proc.stdout)

    def test_help_does_not_read_the_owner_key(self):
        """L1-MINT-002 `--help` 不读 owner 密钥：临时 HOME 里没有 env 文件（读了会 FileNotFoundError）也照样退出 0。"""
        self.env_file.unlink()
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                proc = self.run_mint(flag)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("mint-agent.py", proc.stdout)
                self.assertNotIn("Traceback", proc.stderr)

    def test_help_mixed_with_a_valid_name_still_mints_nothing(self):
        """L1-MINT-003 `--help` 与合法名字混用（前后位置都试）：只打印用法，不铸。"""
        for args in (("--help", "nh-desk"), ("nh-desk", "--help"), ("nh-desk", "-h", "nh-dev"), ("-x", "--help")):
            with self.subTest(args=args):
                self.env_file.unlink(missing_ok=True)
                proc = self.run_mint(*args)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertNotIn("nsec", proc.stdout)
                self.assertNotIn("pubkey_hex", proc.stdout)
                self.assertIsNone(KEY_MATERIAL.search(proc.stdout + proc.stderr))
                self.assertIn(NAME_RULE, proc.stdout)


class RejectTest(MintAgentBase):
    LONG = "a" * 64  # the rule allows 63

    def rejected_names(self) -> list[tuple[str, str]]:
        return [
            ("-x", "-x"), ("--foo", "--foo"), ("dash", "-"), ("double dash", "--"), ("empty", ""),
            ("space", "my agent"), ("slash", "a/b"), ("path traversal", "../x"), ("semicolon", "a;b"),
            ("newline", "abc\n"), ("embedded newline", "a\nb"), ("dollar", "$(id)"), ("backtick", "`id`"),
            ("too long", self.LONG), ("dot first", ".hidden"), ("underscore first", "_a"),
            ("non-ascii", "agént"), ("tab", "a\tb"), ("negative-looking", "-1"),
        ]

    def test_bad_names_exit_two_with_no_key_material_and_never_read_the_owner_key(self):
        """L1-MINT-004 以 `-` 开头的参数、不符合 `[A-Za-z0-9][A-Za-z0-9._-]{0,62}` 的名字：退出码 2，stdout 无任何密钥材料；
        临时 HOME 里没有 env 文件也是干净的退出码 2（不是读 env 抛 FileNotFoundError 的 1），证明先校验、后读密钥。"""
        self.env_file.unlink()
        for label, name in self.rejected_names():
            with self.subTest(name=label):
                proc = self.run_mint(name)
                self.assertEqual(proc.returncode, 2, (proc.stdout, proc.stderr))
                self.assert_no_key_material(proc, label)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertNotEqual(proc.stderr.strip(), "", "an error must say why on stderr")

    def test_bad_names_are_rejected_the_same_when_the_owner_key_exists(self):
        """L1-MINT-005 owner 密钥文件存在时结果一样：退出码 2、stdout 为空（不会因为读得到密钥就先铸再报错）。"""
        for label, name in self.rejected_names():
            with self.subTest(name=label):
                proc = self.run_mint(name)
                self.assertEqual(proc.returncode, 2, (proc.stdout, proc.stderr))
                self.assert_no_key_material(proc, label)

    def test_one_bad_name_among_valid_ones_mints_nothing(self):
        """L1-MINT-006 合法名字里夹一个坏名字（任意位置）：整批拒绝、退出码 2、stdout 为空——不是铸完前面的再报错。"""
        for args in (("nh-desk", "-x"), ("-x", "nh-desk"), ("nh-desk", "bad name", "nh-dev"), ("nh-desk", "--foo")):
            with self.subTest(args=args):
                proc = self.run_mint(*args)
                self.assertEqual(proc.returncode, 2, (proc.stdout, proc.stderr))
                self.assert_no_key_material(proc, repr(args))

    def test_no_arguments_is_a_usage_error_without_key_material(self):
        """L1-MINT-007 没有参数：用法写到 stderr，退出码 2，stdout 为空。"""
        self.env_file.unlink()
        proc = self.run_mint()
        self.assertEqual(proc.returncode, 2, (proc.stdout, proc.stderr))
        self.assertEqual(proc.stdout, "")
        self.assertIn("mint-agent.py", proc.stderr)


class MintTest(MintAgentBase):
    def check_entry(self, entry: dict, owner_pub: str = TEST_OWNER_PUB) -> None:
        self.assertEqual(set(entry), {"pubkey_hex", "npub", "nsec", "auth_tag"})
        secret = nk.bech32_decode(entry["nsec"], "nsec")
        self.assertEqual(nk.pubkey_xonly(secret).hex(), entry["pubkey_hex"])
        self.assertEqual(nk.bech32_decode(entry["npub"], "npub").hex(), entry["pubkey_hex"])
        tag = json.loads(entry["auth_tag"])
        self.assertEqual(tag[:3], ["auth", owner_pub, ""])
        message = hashlib.sha256(f"nostr:agent-auth:{entry['pubkey_hex']}:".encode()).digest()
        self.assertTrue(nk.schnorr_verify(message, bytes.fromhex(owner_pub), bytes.fromhex(tag[3])),
                        "the NIP-OA endorsement must verify against the owner public key")

    def test_valid_names_still_mint_with_a_verifiable_endorsement(self):
        """L1-MINT-008 特征化（旧代码本来就绿）：合法名字退出 0，stdout 是 JSON，含 nsec／auth_tag，auth_tag 用 nostrkit.schnorr_verify 验得过；多名字各自独立。"""
        proc = self.run_mint("jchen-ubuntu-todo", "nh-desk")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(list(out), ["jchen-ubuntu-todo", "nh-desk"])
        for entry in out.values():
            self.check_entry(entry)
        self.assertNotEqual(out["jchen-ubuntu-todo"]["nsec"], out["nh-desk"]["nsec"])

    def test_boundary_and_punctuated_names_are_accepted(self):
        """L1-MINT-009 特征化：规则边界内的名字都放行——单字符、数字开头、含 . _ -、正好 63 字符。"""
        names = ["a", "0", "9lives", "a.b_c-d", "A", "x" * 63, "nh-desk"]
        proc = self.run_mint(*names)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(list(out), names)
        for entry in out.values():
            self.check_entry(entry)

    def test_owner_key_formats_in_the_env_file_still_work(self):
        """L1-MINT-010 特征化：env 文件里 owner 密钥带引号、带注释行、或写成 nsec1… 都照旧可用，背书对应同一个 owner 公钥。"""
        nsec = nk.bech32_encode("nsec", bytes.fromhex(TEST_OWNER_HEX))
        for text in (f'# comment\nBUZZ_PRIVATE_KEY="{TEST_OWNER_HEX}"\n', f"BUZZ_PRIVATE_KEY='{TEST_OWNER_HEX}'\n",
                     f"OTHER=1\nBUZZ_PRIVATE_KEY={nsec}\n"):
            with self.subTest(text=text.split("=")[0]):
                self.write_env(text)
                proc = self.run_mint("nh-dev")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.check_entry(json.loads(proc.stdout)["nh-dev"])


if __name__ == "__main__":
    unittest.main()
