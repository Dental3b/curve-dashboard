import unittest
from unittest.mock import Mock, patch

import requests

from curve_api import CurveAPI, IncompletePagination, _TREATMENT_PLAN_PAGE_LIMIT


def response(status, data=None):
    result = Mock(status_code=status, ok=status < 400)
    result.json.return_value = data
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(response=result)
    else:
        result.raise_for_status.return_value = None
    return result


def page(ids, nxt=None):
    body = {"objects": [{"id": str(item)} for item in ids]}
    if nxt is not None:
        body["meta"] = {"next": nxt}
    return response(200, body)


def api():
    client = CurveAPI("id", "secret", "test")
    client._token = "token"
    client._token_expires_at = 9999999999
    return client


class TreatmentPlanPaginationTest(unittest.TestCase):
    def test_safety_limit_is_well_past_the_old_15_page_cap(self):
        self.assertGreater(_TREATMENT_PLAN_PAGE_LIMIT, 15)

    @patch("curve_api.requests.get")
    def test_every_page_is_loaded_past_the_old_cap(self, get):
        # 16 pages used to stop at 15 and drop the last page, which is the newest.
        pages = []
        for number in range(1, 17):
            nxt = None if number == 16 else f"/api/v2/treatment_plan?page={number + 1}"
            pages.append(page([number], nxt))
        get.side_effect = pages
        plans = api().get_treatment_plans_v2()
        self.assertEqual([item["id"] for item in plans], [str(n) for n in range(1, 17)])
        self.assertEqual(get.call_count, 16)
        self.assertEqual(
            get.call_args_list[15].args[0],
            "https://test.curvehero.com/api/v2/treatment_plan?page=16",
        )

    @patch("curve_api.requests.get")
    def test_relative_next_stays_under_api_v2(self, get):
        get.side_effect = [
            page(["1"], "treatment_plan?state=2"),
            page(["2"]),
        ]
        self.assertEqual(api().get_treatment_plans_v2(), [{"id": "1"}, {"id": "2"}])
        self.assertEqual(
            get.call_args_list[1].args[0],
            "https://test.curvehero.com/api/v2/treatment_plan?state=2",
        )

    @patch("curve_api.requests.get")
    def test_safety_stop_raises_instead_of_returning_a_short_list(self, get):
        get.side_effect = [
            page(["1"], "/api/v2/treatment_plan?page=2"),
            page(["2"], "/api/v2/treatment_plan?page=3"),
            page(["3"]),
        ]
        with self.assertRaises(IncompletePagination) as caught:
            api().get_treatment_plans_v2(max_pages=2)
        self.assertIn("incomplete", str(caught.exception))
        self.assertEqual(get.call_count, 2)

    @patch("curve_api.requests.get")
    def test_foreign_or_repeated_next_is_not_followed(self, get):
        start = "https://test.curvehero.com/api/v2/treatment_plan"
        cases = [
            "https://elsewhere.test/api/v2/treatment_plan",
            start,
            "http://test.curvehero.com/api/v2/treatment_plan",
        ]
        for nxt in cases:
            with self.subTest(nxt=nxt):
                get.reset_mock()
                get.side_effect = [page(["1"], nxt)]
                with self.assertRaises(IncompletePagination):
                    api().get_treatment_plans_v2()
                self.assertEqual(get.call_count, 1)
                self.assertEqual(get.call_args.args[0], start)

    @patch("curve_api.requests.get")
    def test_invalid_page_does_not_return_earlier_pages(self, get):
        get.side_effect = [
            page(["1"], "/api/v2/treatment_plan?page=2"),
            response(500, {}),
        ]
        with self.assertRaises(requests.HTTPError):
            api().get_treatment_plans_v2()
        self.assertEqual(get.call_count, 2)

    @patch("curve_api.requests.get")
    def test_rejected_token_is_renewed_once(self, get):
        client = api()
        client._token = "old"
        get.side_effect = [response(401), page(["1"])]
        with patch("curve_api.requests.post", return_value=response(200, {
            "access_token": "new", "expires_in": 3600
        })) as post:
            self.assertEqual(client.get_treatment_plans_v2(), [{"id": "1"}])
            post.assert_called_once()
        self.assertEqual(get.call_args_list[0].kwargs["headers"]["Authorization"], "Bearer old")
        self.assertEqual(get.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer new")

    @patch("curve_api.time.sleep")
    @patch("curve_api.requests.get")
    def test_gateway_failure_retries_the_same_page(self, get, sleep):
        get.side_effect = [response(503), page(["1"])]
        self.assertEqual(api().get_treatment_plans_v2(), [{"id": "1"}])
        self.assertEqual(get.call_args_list[0].args[0], get.call_args_list[1].args[0])
        sleep.assert_called_once_with(1)

    @patch("curve_api.requests.get")
    def test_max_pages_must_be_positive(self, get):
        with self.assertRaises(ValueError):
            api().get_treatment_plans_v2(max_pages=0)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
