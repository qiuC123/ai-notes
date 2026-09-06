import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mobile_registration", ROOT / "experiments/project-chemist/mobile-register.py")
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)


@unittest.skipUnless(os.name == "nt", "Windows DPAPI integration")
class CredentialTests(unittest.TestCase):
    def test_save_decrypt_and_no_plaintext_with_inherited_powershell7_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "credential.xml"
            secret = "fixture-only-<>&-中文"
            with patch.dict(os.environ, {"PSModulePath": r"C:\Program Files\PowerShell\7\Modules"}):
                registration.save_credentials({"client_id": "cli_fixture", "client_secret": secret}, target)
            self.assertNotIn(secret, target.read_text(encoding="utf-16"))
            command = ("$ErrorActionPreference='Stop'; $p=Import-Clixml -LiteralPath $env:CHEMIST_CREDENTIAL_TARGET; "
                       "$expected=[Console]::In.ReadToEnd(); "
                       "if ($p.UserName -ne 'cli_fixture' -or $p.GetNetworkCredential().Password -ne $expected) {exit 2}")
            env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
            env["CHEMIST_CREDENTIAL_TARGET"] = str(target)
            # Use ASCII input for comparison to avoid console-codepage dependence.
            command = command.replace("$expected=[Console]::In.ReadToEnd()", "$expected=([Console]::In.ReadToEnd() | ConvertFrom-Json).secret")
            import json
            result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                    input=json.dumps({"secret": secret}), text=True, capture_output=True, env=env, timeout=30)
            self.assertEqual(result.returncode, 0, "Encrypted credentials must round-trip without exposing the secret")
