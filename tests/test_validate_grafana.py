import importlib.util
import json
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
