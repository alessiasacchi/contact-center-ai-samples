import time

import dialogflow_sample as ds
import google.api_core.exceptions
import google.auth
import sample_base as sb
from google.cloud.dialogflowcx import (
    BatchDeleteTestCasesRequest,
    GetTestCaseRequest,
    ListTestCasesRequest,
    RunTestCaseRequest,
    TestCase,
    TestCasesClient,
    TestConfig,
    TestResult,
)


class DialogflowTestCaseFailure(Exception):
    """Exception to raise when a test case fails"""


class TestCaseDelegator(sb.ClientDelegator):

    _CLIENT_CLASS = TestCasesClient

    def __init__(self, controller: ds.DialogflowSample, **kwargs) -> None:
        self._is_webhook_enabled = kwargs.pop("is_webhook_enabled", False)
        self._conversation_turns = kwargs.pop("conversation_turns")
        self.expected_exception = kwargs.pop("expected_exception", None)
        self._test_case = None
        super().__init__(controller, **kwargs)

    @property
    def test_case(self):
        if not self._test_case:
            raise RuntimeError("Test Case not yet created")
        return self._test_case

    def initialize(self):
        try:
            self._test_case = self.client.create_test_case(
                parent=self.controller.agent_delegator.agent.name,
                test_case=TestCase(
                    display_name=self.display_name,
                    test_case_conversation_turns=[
                        t.get_conversation_turn(self._is_webhook_enabled)
                        for t in self._conversation_turns
                    ],
                    test_config=TestConfig(flow=self.controller.start_flow),
                ),
            )
        except google.api_core.exceptions.AlreadyExists:
            request = ListTestCasesRequest(parent=self.parent)
            for curr_test_case in self.client.list_test_cases(request=request):
                if curr_test_case.display_name == self.display_name:
                    request = GetTestCaseRequest(
                        name=curr_test_case.name,
                    )
                    self._test_case = self.client.get_test_case(request=request)
                    return

    def tear_down(self):
        request = BatchDeleteTestCasesRequest(
            parent=self.parent,
            names=[self.test_case.name],
        )
        try:
            self.client.batch_delete_test_cases(request=request)
            self._test_case = None
        except google.api_core.exceptions.NotFound:
            pass

    def run_test_case(self, wait=10, max_retries=3):
        retry_count = 0
        result = None
        while retry_count < max_retries:
            time.sleep(wait)
            lro = self.client.run_test_case(
                request=RunTestCaseRequest(name=self.test_case.name)
            )
            while lro.running():
                try:
                    result = lro.result().result
                    agent_response_differences = [
                        conversation_turn.virtual_agent_output.differences
                        for conversation_turn in result.conversation_turns
                    ]
                    test_case_fail = result.test_result != TestResult.PASSED
                    if any(agent_response_differences) or test_case_fail:
                        raise DialogflowTestCaseFailure(
                            f'Test "{self.test_case.display_name}" failed'
                        )
                    return
                except google.api_core.exceptions.NotFound as e:
                    if str(e) == (
                        "404 com.google.apps.framework.request.NotFoundException: "
                        "NLU model for flow '00000000-0000-0000-0000-000000000000' does not exist. "
                        "Please try again after retraining the flow."
                    ):
                        retry_count += 1
        raise RuntimeError(f"Retry count exceeded: {retry_count}")
