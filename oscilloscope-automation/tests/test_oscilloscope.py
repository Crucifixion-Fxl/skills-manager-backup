import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from adapters.rigol_mho_dho5000 import RigolMho5104Adapter, parse_tmc_block
from oscilloscope import (
    command_measure,
    command_query,
    command_restore,
    positive_int,
    require_adapter,
)


class FakeScope:
    def __init__(self, responses, raw=b""):
        self.responses = responses
        self.raw = raw
        self.writes = []
        self.read_termination = "\n"
        self.chunk_size = 20_480

    def query(self, command):
        return self.responses[command]

    def write(self, command):
        self.writes.append(command)

    def read_raw(self):
        return self.raw


def complete_settings():
    settings = {}
    for channel in range(1, 5):
        settings.update(
            {
                f"channel{channel}_display": "1",
                f"channel{channel}_probe": "10",
                f"channel{channel}_scale": "1",
                f"channel{channel}_offset": "0",
                f"channel{channel}_coupling": "DC",
            }
        )
    settings.update(
        {
            "dvm_enable": "0",
            "timebase_scale": "0.001",
            "timebase_offset": "0",
            "trigger_source": "CHANnel1",
            "trigger_slope": "POSitive",
            "trigger_level": "1.7",
        }
    )
    return settings


class AdapterTest(unittest.TestCase):
    def test_timeout_requires_positive_integer(self):
        self.assertEqual(positive_int("1"), 1)
        with self.assertRaises(argparse.ArgumentTypeError):
            positive_int("0")

    def test_tmc_block(self):
        self.assertEqual(parse_tmc_block(b"#210abcdefghij\n"), b"abcdefghij")

    def test_rejects_incomplete_tmc_block(self):
        with self.assertRaises(ValueError):
            parse_tmc_block(b"#210abc")

    def test_idn_and_verification(self):
        adapter = RigolMho5104Adapter()
        idn = "RIGOL TECHNOLOGIES,MHO5104,MHO5C28210307,00.02.00"
        self.assertTrue(adapter.matches(idn))
        self.assertEqual(adapter.verification(idn), "hardware_verified")
        self.assertFalse(adapter.matches("RIGOL,DHO5108,SN,00.02.00"))
        self.assertFalse(adapter.matches("SIGLENT,SDS2104X,SN,1.0"))

    def test_unverified_firmware_cannot_execute_adapter_commands(self):
        adapter = RigolMho5104Adapter()
        idn = "RIGOL TECHNOLOGIES,MHO5104,SN,00.03.00"
        self.assertTrue(adapter.matches(idn))
        with self.assertRaisesRegex(RuntimeError, "adapter execution is restricted"):
            require_adapter(idn, adapter)

    def test_duty_is_normalized_to_percent(self):
        adapter = RigolMho5104Adapter()
        scope = FakeScope({":MEASure:ITEM? PDUTy,CHANnel1": "0.5\n"})
        result = adapter.measure(scope, 1, ["pduty"])
        self.assertEqual(result[0]["value"], 50.0)
        self.assertEqual(result[0]["unit"], "%")

    def test_screenshot_validates_png_and_restores_transport(self):
        adapter = RigolMho5104Adapter()
        png = b"\x89PNG\r\n\x1a\n"
        scope = FakeScope({}, b"#18" + png)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "scope.png"
            self.assertEqual(adapter.screenshot(scope, output), len(png))
            self.assertEqual(output.read_bytes(), png)
        self.assertEqual(scope.read_termination, "\n")
        self.assertEqual(scope.chunk_size, 20_480)

    def test_waveform_restores_transfer_settings(self):
        adapter = RigolMho5104Adapter()
        scope = FakeScope(
            {
                ":WAVeform:SOURce?": "CHANnel2\n",
                ":WAVeform:MODE?": "NORMal\n",
                ":WAVeform:FORMat?": "BYTE\n",
                ":WAVeform:POINts?": "500\n",
                ":WAVeform:PREamble?": "0,0,3,1,1e-6,0,0,0.1,0,0\n",
            },
            b"#13\x01\x02\x03",
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "wave.csv"
            result = adapter.waveform(scope, 1, 3, output)
            self.assertEqual(result["points"], 3)
            self.assertEqual(len(output.read_text(encoding="utf-8").splitlines()), 4)
        self.assertEqual(
            scope.writes[-4:],
            [
                ":WAVeform:SOURce CHANnel2",
                ":WAVeform:MODE NORMal",
                ":WAVeform:FORMat BYTE",
                ":WAVeform:POINts 500",
            ],
        )

    def test_waveform_snapshot_failure_preserves_original_error(self):
        adapter = RigolMho5104Adapter()
        scope = FakeScope({})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "wave.csv"
            with self.assertRaises(KeyError) as raised:
                adapter.waveform(scope, 1, 3, output)
        self.assertEqual(raised.exception.args, (":WAVeform:SOURce?",))
        self.assertEqual(scope.writes, [])

    def test_waveform_attempts_all_restores_and_preserves_acquisition_cause(self):
        class RestoreFailureScope(FakeScope):
            def write(self, command):
                self.writes.append(command)
                if command == ":WAVeform:SOURce CHANnel2":
                    raise OSError("restore source failed")

            def read_raw(self):
                raise ValueError("waveform transfer failed")

        scope = RestoreFailureScope(
            {
                ":WAVeform:SOURce?": "CHANnel2\n",
                ":WAVeform:MODE?": "NORMal\n",
                ":WAVeform:FORMat?": "BYTE\n",
                ":WAVeform:POINts?": "500\n",
                ":WAVeform:PREamble?": "0,0,3,1,1e-6,0,0,0.1,0,0\n",
            }
        )
        adapter = RigolMho5104Adapter()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "wave.csv"
            with self.assertRaisesRegex(
                RuntimeError, "restore was incomplete"
            ) as raised:
                adapter.waveform(scope, 1, 3, output)
        self.assertIsInstance(raised.exception.__cause__, ValueError)
        self.assertEqual(
            scope.writes[-4:],
            [
                ":WAVeform:SOURce CHANnel2",
                ":WAVeform:MODE NORMal",
                ":WAVeform:FORMat BYTE",
                ":WAVeform:POINts 500",
            ],
        )

    def test_restore_rejects_scpi_command_injection(self):
        adapter = RigolMho5104Adapter()
        scope = FakeScope({})
        settings = complete_settings()
        settings["channel1_scale"] = "1;:SYSTem:PRESet"
        with self.assertRaises(ValueError):
            adapter.restore(scope, settings)
        self.assertEqual(scope.writes, [])

    def test_restore_rejects_incomplete_settings_before_writing(self):
        adapter = RigolMho5104Adapter()
        scope = FakeScope({})
        with self.assertRaisesRegex(ValueError, "schema mismatch"):
            adapter.restore(scope, {"channel1_scale": "1"})
        self.assertEqual(scope.writes, [])

    def test_restore_accepts_complete_versioned_settings(self):
        adapter = RigolMho5104Adapter()
        scope = FakeScope({})
        adapter.restore(scope, complete_settings())
        self.assertEqual(len(scope.writes), 26)
        self.assertEqual(scope.writes[0], ":CHANnel1:DISPlay 1")
        self.assertEqual(scope.writes[-1], ":TRIGger:EDGE:LEVel 1.7")

    def test_query_rejects_scpi_command_separator(self):
        scope = FakeScope({})
        args = SimpleNamespace(scpi="*IDN?;:RUN")
        with self.assertRaises(ValueError):
            command_query(scope, args, {})
        self.assertEqual(scope.writes, [])

    def test_measure_rejects_empty_items(self):
        adapter = RigolMho5104Adapter()
        args = SimpleNamespace(items=" , ", channel=1)
        with self.assertRaises(ValueError):
            command_measure(FakeScope({}), adapter, args, {})

    def test_restore_rejects_snapshot_identity_mismatch(self):
        adapter = RigolMho5104Adapter()
        scope = FakeScope({})
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.json"
            snapshot.write_text(
                json.dumps({"idn": "another-scope", "resource": "USB::other"}),
                encoding="utf-8",
            )
            args = SimpleNamespace(input=str(snapshot), confirm_write=True)
            with self.assertRaises(ValueError):
                command_restore(
                    scope,
                    adapter,
                    args,
                    {"idn": "MHO5104", "resource": "USB::expected"},
                )
        self.assertEqual(scope.writes, [])

    def test_restore_rejects_empty_snapshot_settings(self):
        adapter = RigolMho5104Adapter()
        scope = FakeScope({":SYSTem:ERRor:NEXT?": '0,"No error"'})
        result = {
            "idn": "RIGOL TECHNOLOGIES,MHO5104,SN,00.02.00",
            "resource": "USB::expected",
        }
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "snapshot.json"
            snapshot.write_text(
                json.dumps(
                    {
                        **result,
                        "snapshot_schema": adapter.snapshot_schema,
                        "settings": {},
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(input=str(snapshot), confirm_write=True)
            with self.assertRaisesRegex(ValueError, "non-empty object"):
                command_restore(scope, adapter, args, result)
        self.assertEqual(scope.writes, [])


if __name__ == "__main__":
    unittest.main()
