from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest

from jinja2 import Environment, StrictUndefined
import yaml


class PrometheusInventoryTests(unittest.TestCase):
    def render(self, include_worker):
        path = (
            Path(__file__).resolve().parents[1]
            / "roles/prometheus_collector/templates/prometheus.yml.j2"
        )
        values = {
            "groups": {"zava_workloads": ["azurelinux-worker"] if include_worker else []},
            "hostvars": {"azurelinux-worker": {"ansible_host": "192.0.2.15"}},
            "zava_db_address": "192.0.2.11",
            "zava_api_address": "192.0.2.12",
            "zava_web_address": "192.0.2.13",
            "zava_worker_address": "192.0.2.14",
            "zava_prometheus_remote_write_url": "https://example.invalid/write",
        }
        template = Environment(undefined=StrictUndefined).from_string(path.read_text())
        return yaml.safe_load(template.render(values))

    def test_new_worker_address_comes_from_inventory_without_extra_variable(self):
        config = self.render(True)
        linux = next(job for job in config["scrape_configs"] if job["job_name"] == "zava-linux")
        self.assertEqual(len(linux["static_configs"]), 5)
        worker = linux["static_configs"][-1]
        self.assertEqual(worker["targets"], ["192.0.2.15:9100"])
        self.assertEqual(worker["labels"]["distro"], "azurelinux")

    def test_four_host_estate_does_not_require_the_optional_worker(self):
        config = self.render(False)
        linux = next(job for job in config["scrape_configs"] if job["job_name"] == "zava-linux")
        self.assertEqual(len(linux["static_configs"]), 4)

    @unittest.skipUnless(shutil.which("promtool"), "Requires the prepared Prometheus promtool")
    def test_live_contribution_excludes_absent_workers_but_preserves_configured_down_workers(self):
        dashboard = json.loads((
            Path(__file__).resolve().parents[1] / "dashboards/zava-shipment-flow.json"
        ).read_text())["dashboard"]
        panel = next(panel for panel in dashboard["panels"] if panel["id"] == 9)
        workers = [("A", "ciq-rocky-worker", 12), ("B", "azurelinux-worker", 0)]
        tests = []
        for optional_up in (None, 1, 0):
            inputs = [
                {
                    "series": f'zava:worker_completions_per_minute{{worker_host="{host}",worker_site="azure/westus2"}}',
                    "values": f"{value}+0x2",
                }
                for _, host, value in workers
            ]
            inputs.append({
                "series": 'up{job="zava-linux",tier="worker",host="ciq-rocky-worker"}',
                "values": "0+0x2",
            })
            if optional_up is not None:
                inputs.append({
                    "series": 'up{job="zava-linux",tier="worker",host="azurelinux-worker"}',
                    "values": f"{optional_up}+0x2",
                })
            assertions = []
            for ref, host, value in workers:
                target = next(target for target in panel["targets"] if target["refId"] == ref)
                assertions.append({
                    "expr": target["expr"], "eval_time": "1m",
                    "exp_samples": [] if ref == "B" and optional_up is None else [{
                        "labels": f'zava:worker_completions_per_minute{{worker_host="{host}",worker_site="azure/westus2"}}',
                        "value": value,
                    }],
                })
            tests.append({
                "interval": "1m", "input_series": inputs, "promql_expr_test": assertions,
            })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker-contribution.json"
            path.write_text(json.dumps({"evaluation_interval": "1m", "tests": tests}))
            result = subprocess.run(
                [shutil.which("promtool"), "test", "rules", str(path)],
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
