import os
import sys
import subprocess
import textwrap
import unittest

class RtcMissingTests(unittest.TestCase):
    def test_app_import_without_rtc(self):
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        script = textwrap.dedent(
            f"""
            import os, sys, types
            os.environ['FLASK_SECRET_KEY'] = 'test'
            sys.path.insert(0, r'{root}')
            sys.modules['lgpio'] = types.SimpleNamespace(
                gpiochip_open=lambda *a, **k: 1,
                gpio_claim_output=lambda *a, **k: None,
                gpio_write=lambda *a, **k: None,
                gpio_free=lambda *a, **k: None,
                error=Exception,
            )
            sys.modules['pygame'] = types.SimpleNamespace(
                mixer=types.SimpleNamespace(
                    init=lambda *a, **k: None,
                    music=types.SimpleNamespace(set_volume=lambda *a, **k: None),
                )
            )
            sys.modules['pydub'] = types.SimpleNamespace(AudioSegment=types.SimpleNamespace())
            sys.modules['schedule'] = types.SimpleNamespace(
                every=lambda *a, **k: types.SimpleNamespace(do=lambda *a, **k: None),
                run_pending=lambda *a, **k: None,
                clear=lambda *a, **k: None,
            )
            def raise_fnf(*a, **k):
                raise FileNotFoundError('missing bus')
            sys.modules['smbus'] = types.SimpleNamespace(SMBus=raise_fnf)
            started = []
            class DummyThread:
                def __init__(self, *a, **k):
                    self.started = False
                def start(self):
                    self.started = True
                    started.append(True)
            import threading
            threading.Thread = lambda *a, **k: DummyThread()
            import app
            print('started', bool(started))
            print('bus_is_none', app.bus is None)
            """
        )
        env = os.environ.copy()
        env.pop("TESTING", None)
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('started True', result.stdout)
        self.assertIn('bus_is_none True', result.stdout)

if __name__ == "__main__":
    unittest.main()
