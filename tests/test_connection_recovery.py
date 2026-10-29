import unittest
from unittest.mock import Mock, patch

import requests

from curve_api import CurveAPI


def response(status, data=None):
    result = Mock(status_code=status)
    result.json.return_value = data
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(response=result)
    return result


class ConnectionRecoveryTest(unittest.TestCase):
    @patch('curve_api.requests.get')
    def test_rejected_cached_token_is_renewed_once(self, get):
        for status in (401, 403):
            with self.subTest(status=status):
                api = CurveAPI('id', 'secret', 'test')
                api._token = 'old'
                api._token_expires_at = 9999999999
                get.reset_mock()
                get.side_effect = [response(status), response(200, {'objects': []})]
                with patch('curve_api.requests.post', return_value=response(200, {
                    'access_token': 'new', 'expires_in': 3600
                })) as post:
                    self.assertEqual(api.get_clinics(), [])
                    post.assert_called_once()
                self.assertEqual(get.call_args_list[0].kwargs['headers']['Authorization'], 'Bearer old')
                self.assertEqual(get.call_args_list[1].kwargs['headers']['Authorization'], 'Bearer new')

    @patch('curve_api.requests.get')
    def test_persistent_denial_is_not_hidden(self, get):
        api = CurveAPI('id', 'secret', 'test')
        get.side_effect = [response(403), response(403)]
        with patch.object(api, '_get_token', return_value='token'):
            with self.assertRaises(requests.HTTPError):
                api.get_clinics()
        self.assertEqual(get.call_count, 2)

    @patch('curve_api.time.sleep')
    @patch('curve_api.requests.get')
    def test_gateway_failure_retries_same_read(self, get, sleep):
        api = CurveAPI('id', 'secret', 'test')
        get.side_effect = [response(503), response(200, {'objects': []})]
        with patch.object(api, '_get_token', return_value='token'):
            self.assertEqual(api.get_clinics(), [])
        self.assertEqual(get.call_args_list[0], get.call_args_list[1])
        sleep.assert_called_once_with(1)

    @patch('curve_api.requests.get')
    def test_other_errors_do_not_retry(self, get):
        api = CurveAPI('id', 'secret', 'test')
        get.return_value = response(404)
        with patch.object(api, '_get_token', return_value='token'):
            with self.assertRaises(requests.HTTPError):
                api.get_clinics()
        get.assert_called_once()


if __name__ == '__main__':
    unittest.main()
