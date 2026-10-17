from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
