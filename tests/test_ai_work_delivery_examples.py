"""Explicit associations and real check criteria must survive example tooling."""
import contextlib
import io
import json
import unittest
from unittest import mock

from hormuz.work_client import WorkClientError
from tools import ai_work_delivery_examples as delivery


class DeliveryExamplesTests(unittest.TestCase):
    def test_preview_never_loads_gateway_credentials(self):
        with mock.patch("hormuz.work_client.WorkClient.from_environment") as load, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(delivery.main(["report", "--work-id", "work-example", "--result", "success", "--reference", "github-run/123/1"]), 0)
        load.assert_not_called()

    def test_intermediate_pass_does_not_submit_completed_observation(self):
        args = delivery.parser().parse_args(["report", "--work-id", "work-example", "--result", "success", "--reference", "run/1"])
        client = mock.Mock()
        receipt = delivery.perform(args, client)
        client.job.return_value.observe.assert_not_called()
        client.job.return_value.state.assert_called_once()
        self.assertFalse(receipt["observation_submitted"])
        args.complete_on_pass = True
        delivery.perform(args, client)
        client.job.return_value.observe.assert_called_once_with("completed", source="workflow", reference="run/1")

    def test_failed_and_inconclusive_checks_do_not_manufacture_completion(self):
        self.assertEqual(delivery.observation("failure", False), "corrected")
        self.assertEqual(delivery.observation("cancelled", False), "unknown")
        self.assertEqual(delivery.observation("cancelled", True), "unknown")
        self.assertEqual(delivery.observation("skipped", True), "unknown")
        self.assertEqual(delivery.observation("unknown", True), "unknown")

    def test_github_and_linear_bind_to_declared_conditions_not_arbitrary_success(self):
        args = delivery.parser().parse_args(["bind", "--provider", "github", "--connector-id", "github-primary", "--container-id", "456", "--object-id", "1001", "--repository", "fixture/repo"])
        client = mock.Mock()
        client.create_job.return_value = {"work_id": "work-example"}
        client.job.return_value.bindings.return_value = {"binding_id": "binding-example"}
        delivery.perform(args, client)
        self.assertEqual(client.create_job.call_args.kwargs["completion_condition"], "github.pull_request.merged.v1")
        self.assertEqual(client.job.return_value.bindings.call_args.kwargs["object_id"], "1001")
        self.assertEqual(delivery.CONDITIONS["linear"], "linear.issue.completed.v1")
        with self.assertRaises(ValueError):
            delivery.validate_binding("github", "company/repo", "#17")
        with self.assertRaises(ValueError):
            delivery.validate_binding("linear", "team-name", "ABC-17")

    def test_reference_header_injection_is_rejected_before_delivery(self):
        args = delivery.parser().parse_args(["report", "--work-id", "work-example", "--result", "failure", "--reference", "bad\nAuthorization: secret"])
        client = mock.Mock()
        with self.assertRaises(ValueError):
            delivery.perform(args, client)
        client.job.assert_not_called()

    def test_canceled_check_cannot_cancel_work(self):
        args = delivery.parser().parse_args(["report", "--work-id", "work-example", "--result", "cancelled", "--reference", "run/1", "--complete-on-pass"])
        client = mock.Mock()
        receipt = delivery.perform(args, client)
        client.job.return_value.observe.assert_called_once_with("unknown", source="workflow", reference="run/1")
        self.assertEqual(receipt["observation_status"], "unknown")

    def test_created_work_id_survives_failed_binding_and_is_reusable(self):
        argv = ["bind", "--provider", "github", "--connector-id", "github-primary", "--container-id", "456", "--object-id", "1001", "--repository", "fixture/repo", "--execute"]
        client = mock.Mock()
        client.create_job.return_value = {"work_id": "work-example"}
        client.job.return_value.bindings.side_effect = WorkClientError("binding_connector_not_qualified", 403)
        error = io.StringIO()
        with mock.patch("hormuz.work_client.WorkClient.from_environment", return_value=client), contextlib.redirect_stderr(error):
            self.assertEqual(delivery.main(argv), 1)
        receipt = json.loads(error.getvalue())
        self.assertEqual(receipt["work_id"], "work-example")
        self.assertTrue(receipt["work_created"])
        self.assertTrue(receipt["retry_with_existing_work"])
        self.assertEqual(receipt["binding_status"], "unknown")
        self.assertEqual(receipt["reason"], "binding_connector_not_qualified")
        self.assertEqual(receipt["failure_type"], "WorkClientError")

        client.reset_mock()
        client.job.return_value.bindings.side_effect = None
        client.job.return_value.bindings.return_value = {"binding_id": "binding-example"}
        with mock.patch("hormuz.work_client.WorkClient.from_environment", return_value=client), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(delivery.main([*argv, "--work-id", receipt["work_id"]]), 0)
        client.create_job.assert_not_called()
        client.job.assert_called_once_with("work-example")

    def test_failed_binding_receipt_does_not_echo_raw_error_or_invalid_id(self):
        argv = ["bind", "--provider", "github", "--connector-id", "github-primary", "--container-id", "456", "--object-id", "1001", "--repository", "fixture/repo", "--execute"]
        client = mock.Mock()
        client.create_job.return_value = {"work_id": "private\ninvalid-id"}
        client.job.return_value.bindings.side_effect = RuntimeError("private customer payload or secret")
        error = io.StringIO()
        with mock.patch("hormuz.work_client.WorkClient.from_environment", return_value=client), contextlib.redirect_stderr(error):
            self.assertEqual(delivery.main(argv), 1)
        self.assertNotIn("private", error.getvalue())
        receipt = json.loads(error.getvalue())
        self.assertNotIn("work_id", receipt)
        self.assertEqual(receipt["reason"], "delivery_example_unavailable")

    def test_arbitrary_exception_text_is_not_a_public_reason(self):
        self.assertEqual(delivery.failure_reason(ValueError("PrivateCustomerSecret")), "delivery_example_unavailable")
        self.assertEqual(delivery.failure_reason(ValueError("invalid_workflow_reference")), "invalid_workflow_reference")

    def test_malformed_binding_response_still_preserves_created_id(self):
        args = delivery.parser().parse_args(["bind", "--provider", "github", "--connector-id", "github-primary", "--container-id", "456", "--object-id", "1001", "--repository", "fixture/repo"])
        client = mock.Mock()
        client.create_job.return_value = {"work_id": "work-example"}
        client.job.return_value.bindings.return_value = {}
        with self.assertRaises(delivery.BindingFailure) as raised:
            delivery.perform(args, client)
        self.assertEqual(raised.exception.work_id, "work-example")
        self.assertEqual(raised.exception.reason, "delivery_example_unavailable")
