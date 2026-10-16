import json
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class MetricsBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        template = (
            Path(__file__).resolve().parents[1]
            / "roles/inspektor_gadget/templates/zava-ig-export.py.j2"
        ).read_text()
        self.bridge = self.root / "bridge.py"
        self.bridge.write_text(
            template.replace("{% raw %}", "").replace("{% endraw %}", "")
        )
        self.metrics = self.root / "metrics.prom"

    def start_bridge(self, child, evidence="tcp"):
        process = subprocess.Popen(
            [
                sys.executable, str(self.bridge),
                "--mode", "continuous", "--evidence", evidence,
                "--tcp-destination", "192.168.90.11", "--tcp-port", "1433",
                "--metrics-file", str(self.metrics),
                "--", sys.executable, "-c", child,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        def stop():
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)

        self.addCleanup(stop)
        return process

    def wait_for_metric(self, process, name, predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(f"Metrics bridge exited: {process.stderr.read()}")
            if self.metrics.is_file():
                match = re.search(
                    rf"^{name}(?:\{{[^\n]*\}})? ([^\n]+)$",
                    self.metrics.read_text(),
                    re.MULTILINE,
                )
                if match:
                    value = float(match.group(1))
                    if predicate(value):
                        return value
            time.sleep(0.05)
        self.fail(f"{name} did not reach the required value within {timeout}s")

    def test_partial_line_does_not_stall_heartbeat(self):
        process = self.start_bridge(
            "import sys,time; sys.stdout.write('partial'); "
            "sys.stdout.flush(); time.sleep(60)"
        )
        name = "zava_ig_collector_heartbeat_timestamp_seconds"
        initial = self.wait_for_metric(process, name, lambda value: value > 0, 5)
        self.wait_for_metric(process, name, lambda value: value > initial + 5, 15)

    def test_fragmented_utf8_event_is_counted(self):
        event = json.dumps(
            {
                "type": "connect",
                "dst": {"addr": "192.168.90.11", "port": 1433},
                "proc": {"comm": "caf\u00e9"},
            },
            ensure_ascii=False,
        ).encode() + b"\n"
        split = event.index(b"\xc3") + 1
        process = self.start_bridge(
            f"import sys,time; data={event!r}; "
            f"sys.stdout.buffer.write(data[:{split}]); sys.stdout.buffer.flush(); "
            f"time.sleep(0.2); sys.stdout.buffer.write(data[{split}:]); "
            "sys.stdout.buffer.flush(); time.sleep(60)"
        )
        self.wait_for_metric(
            process, "zava_ig_tcp_connections_total", lambda value: value == 1, 5
        )

    def test_blockio_profile_retains_operation_count_and_p95(self):
        output = (
            "latency\n"
            "0 -> 7 : 9 |*********|\n"
            "8 -> 15 : 1 |*|\n"
            "ZAVA_IG_PROFILE_END\n"
        )
        process = self.start_bridge(
            f"import sys,time; sys.stdout.write({output!r}); "
            "sys.stdout.flush(); time.sleep(60)",
            evidence="blockio",
        )
        for name, expected in [
            ("zava_ig_blockio_profiles_total", 1),
            ("zava_ig_blockio_operations_profiled", 10),
            ("zava_ig_blockio_latency_p95_seconds", 0.000015),
        ]:
            self.wait_for_metric(process, name, lambda value: value == expected, 5)


if __name__ == "__main__":
    unittest.main()
