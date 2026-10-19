import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_grafana", ROOT / "tools/validate_grafana.py"
)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def response(values):
    return {"results": {"A": {
        "status": 200,
        "frames": [{
            "schema": {"fields": [{"type": "time"}, {"type": "number"}]},
            "data": {"values": [[1000] * len(values), values]},
        }],
    }}}


class GrafanaAcceptanceTests(unittest.TestCase):
    def test_variable_binding_is_rejected_even_if_its_saved_selection_is_correct(self):
        dashboard = {
            "panels": [{
                "id": 1, "datasource": {"uid": "${DS_PROMETHEUS}"},
                "targets": [{"refId": "A"}],
            }],
            "templating": {"list": [{
                "name": "DS_PROMETHEUS", "current": {"value": "new-workspace"},
            }]},
        }
        with self.assertRaisesRegex(ValueError, "not explicitly bound"):
            VALIDATOR.check_datasource(dashboard, "new-workspace")

    def test_explicit_binding_does_not_depend_on_a_browser_variable(self):
        dashboard = {"panels": [{
            "id": 1, "datasource": {"uid": "new-workspace"},
            "targets": [{"refId": "A"}],
        }]}
        VALIDATOR.check_datasource(dashboard, "new-workspace")

    def test_target_cannot_override_the_configured_workspace(self):
        dashboard = {"panels": [{
            "id": 1, "datasource": {"uid": "new-workspace"},
            "targets": [{"refId": "A", "datasource": {"uid": "old-workspace"}}],
        }]}
        with self.assertRaisesRegex(ValueError, "different datasource"):
            VALIDATOR.check_datasource(dashboard, "new-workspace")

    def test_checks_all_four_panels_and_preserves_real_query_modes(self):
        dashboard = json.loads(
            (ROOT / "dashboards/zava-shipment-flow.json").read_text()
        )["dashboard"]
        queries = VALIDATOR.build_queries(dashboard, "selected-workspace")
        self.assertEqual(
            {query["refId"] for query in queries},
            {"1-A", "4-A", "7-A", "7-B", "7-C", "10-A"},
        )
        for query in queries:
            self.assertEqual(query["datasource"]["uid"], "selected-workspace")
            self.assertEqual(query["range"], query["refId"].startswith("7-"))
            self.assertNotEqual(query["range"], query["instant"])

    def test_missing_panel_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "four required"):
            VALIDATOR.build_queries({"panels": []}, "workspace")

    def test_capacity_acceptance_includes_both_workers_and_all_five_cpu_queries(self):
        dashboard = json.loads(
            (ROOT / "dashboards/zava-shipment-flow.json").read_text()
        )["dashboard"]
        queries = VALIDATOR.build_queries(
            dashboard, "selected-workspace", VALIDATOR.PANEL_IDS | {9, 11}
        )
        refs = {query["refId"] for query in queries}
        self.assertTrue({"9-A", "9-B", "11-A", "11-B", "11-C", "11-D", "11-E"} <= refs)

    def test_empty_targets_are_rejected(self):
        panels = [{"id": panel_id, "targets": []} for panel_id in VALIDATOR.PANEL_IDS]
        with self.assertRaisesRegex(ValueError, "no query targets"):
            VALIDATOR.build_queries({"panels": panels}, "workspace")

    def test_zero_is_real_data(self):
        self.assertEqual(
            VALIDATOR.check_results([{"refId": "A"}], response([0]), True),
            [{"ref": "A", "samples": 1}],
        )

    def test_null_nan_boolean_and_empty_data_do_not_pass_stage(self):
        for values in ([], [None], [float("nan")], [False]):
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "no numeric samples"):
                    VALIDATOR.check_results([{"refId": "A"}], response(values), True)

    def test_prepare_reports_empty_data_without_claiming_stage_acceptance(self):
        self.assertEqual(
            VALIDATOR.check_results([{"refId": "A"}], response([]), False),
            [{"ref": "A", "samples": 0}],
        )

    def test_backend_timeout_is_rejected_in_both_modes(self):
        payload = {"results": {"A": {
            "status": 500, "error": "downstream HTTP 504 Gateway Timeout",
        }}}
        for require_data in (True, False):
            with self.subTest(require_data=require_data):
                with self.assertRaisesRegex(ValueError, "504"):
                    VALIDATOR.check_results([{"refId": "A"}], payload, require_data)

    def test_missing_query_result_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "result missing"):
            VALIDATOR.check_results([{"refId": "A"}], {"results": {}}, False)


class GrafanaPhaseSelectionTests(unittest.TestCase):
    def test_coordinates_are_loaded_for_both_documented_phase_selectors(self):
        for phase, selector in [
            ("prepare", "--skip-tags"),
            ("stage", "--tags"),
        ]:
            with self.subTest(phase=phase):
                result = subprocess.run(
                    [
                        sys.executable, "-m", "ansible.cli.playbook",
                        "-i", str(ROOT / "inventory/example/hosts.yml"),
                        str(ROOT / "playbooks/control-plane.yml"),
                        "--list-tasks", selector, "observability_deploy",
                        "-e", f"zava_observability_phase={phase}",
                    ],
                    env={
                        **os.environ,
                        "ANSIBLE_CONFIG": str(ROOT / "ansible.cfg"),
                        "ANSIBLE_ROLES_PATH": str(ROOT / "roles"),
                    },
                    capture_output=True, text=True, check=True, timeout=30,
                )
                self.assertIn("Load prepared Azure metrics coordinates", result.stdout)
                if phase == "prepare":
                    self.assertIn("Check the real business query path", result.stdout)
                    self.assertNotIn("Publish Zava operations dashboard", result.stdout)
                else:
                    self.assertIn("Publish Zava operations dashboard", result.stdout)


if __name__ == "__main__":
    unittest.main()
