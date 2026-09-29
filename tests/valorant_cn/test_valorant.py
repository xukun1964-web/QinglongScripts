"""Offline protocol, security and failure-path tests. All credentials are synthetic."""
import contextlib
import io
import json
import os
import subprocess
import unittest
import urllib.error
import urllib.parse
from unittest.mock import Mock, patch

from valorant_cn.core import Http, NoRedirect, SafeError, safe_value, trusted_url
from valorant_cn.store import exchange_qq, image_url, load_auth, parse_store, push, query_store, render
from valorant_cn.qq_login import QQLogin, body_url, callback, gh_call, parameters, qr_token
from valorant_cn.__main__ import main

AUTH = {"version": 1, "userId": "fake-user", "tid": "fake-ticket", "uin": "0",
        "openid": "fake-openid", "access_token": "fake-access"}


def store_response():
    return {"result": 0, "data": [
        {"key": "bundle", "list": [{"goods_name": "不是每日商品"}]},
        {"key": "dailystore", "list": [
            {"goods_name": f"测试皮肤{i}", "rmb_price": "129.00", "goods_pic": "https://game.gtimg.cn/test.png"}
            for i in range(4)]}]}


class StoreTests(unittest.TestCase):
    def test_daily_selection_and_price(self):
        items = parse_store(store_response())
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]["price"], "129.00")
        self.assertIn("¥129.00", render(items)[1])

    def test_object_compatibility(self):
        data = store_response()["data"][1]
        for obj in (data, {"list": data["list"]}):
            self.assertEqual(len(parse_store({"result": "0", "data": obj})), 4)

    def test_reject_missing_empty_wrong_section_and_wrong_count(self):
        for data in (None, [], {}, [{"key": "bundle", "list": [1, 2, 3, 4]}],
                     {"key": "dailystore", "list": []}, {"list": [1, 2, 3]}):
            with self.subTest(data=data), self.assertRaises(SafeError):
                parse_store({"result": 0, "data": data})

    def test_auth_detection_has_no_server_message(self):
        for code in (1001, "1003", 999999):
            with self.assertRaises(SafeError) as error:
                parse_store({"result": code, "msg": "DO-NOT-PRINT-TOKEN"})
            self.assertEqual(error.exception.code, "auth")
            self.assertNotIn("DO-NOT-PRINT", str(error.exception))

    def test_missing_price_is_not_free(self):
        response = store_response()
        del response["data"][1]["list"][0]["rmb_price"]
        with self.assertRaises(SafeError):
            parse_store(response)

    def test_blank_price_and_malformed_login_response(self):
        response = store_response()
        response['data'][1]['list'][0]['rmb_price'] = ''
        with self.assertRaises(SafeError):
            parse_store(response)
        http = Mock()
        for data in (None, [], {'login_info': 'bad'}):
            http.json.return_value = {'result': 0, 'data': data}
            with self.assertRaises(SafeError) as error:
                exchange_qq(AUTH, http)
            self.assertEqual(error.exception.code, 'protocol')

    def test_html_escaping(self):
        items = [{"name": '<script>bad</script>', "price": '1<img>', "image": ''}]
        output = render(items)[1]
        self.assertNotIn("<script>", output)
        self.assertNotIn("1<img>", output)

    def test_image_urls_drop_untrusted_or_signed(self):
        for url in ('https://evil.test/a', 'javascript:alert(1)',
                    'https://game.gtimg.cn/a?token=secret', 'https://gtimg.com.evil.test/a',
                    'https://user:pass@gtimg.com/a', 'https://gtimg.com:123/a'):
            self.assertEqual(image_url(url), '')
        self.assertEqual(image_url('http://game.gtimg.cn/a.png'), 'https://game.gtimg.cn/a.png')

    def test_reexchange_on_expiry_and_verify_identity(self):
        http = Mock()
        http.json.side_effect = [SafeError("auth"), {"result": 0, "data": {"login_info": {
            "user_id": AUTH["userId"], "wt": "new-ticket", "uin": 0}}}, store_response()]
        self.assertEqual(len(query_store(AUTH, http)), 4)
        self.assertIn('tid=new-ticket', http.json.call_args.kwargs['headers']['Cookie'])
        self.assertEqual(AUTH['tid'], 'fake-ticket')

    def test_reexchange_must_not_switch_account(self):
        http = Mock()
        http.json.side_effect = [SafeError("auth"), {"result": 0, "data": {"login_info": {
            "user_id": "wrong-user", "wt": "new-ticket"}}}]
        with self.assertRaises(SafeError):
            query_store(AUTH, http)
        self.assertEqual(http.json.call_count, 2)

    def test_no_reexchange_on_transient_error(self):
        http = Mock()
        http.json.side_effect = SafeError("network")
        with self.assertRaises(SafeError):
            query_store(AUTH, http)
        self.assertEqual(http.json.call_count, 1)

    def test_secret_validation_and_header_injection(self):
        for raw in ('{}', '[]', 'broken-json', json.dumps({**AUTH, 'tid': 'abc;other=1'}),
                    json.dumps({**AUTH, 'userId': 'bad\r\nX: a'})):
            with self.assertRaises(SafeError):
                load_auth(raw)
        self.assertEqual(load_auth(json.dumps(AUTH)), AUTH)

    def test_push_checks_business_code_without_retry(self):
        for result in ({'code': 400, 'msg': 'fake-private-token'}, {'code': 500}, {}):
            http = Mock()
            http.json.return_value = result
            with self.assertRaises(SafeError) as error:
                push('fake-token', 'test', 'body', http)
            self.assertEqual(error.exception.code, 'push')
            self.assertEqual(http.json.call_count, 1)
        http.json.return_value = {'code': 200, 'data': 'fake-message-id'}
        self.assertEqual(push('fake-token', 'test', 'body', http), 'fake-message-id')


class LoginTests(unittest.TestCase):
    def test_token_known_vector(self):
        self.assertEqual(qr_token('abc'), 108966)

    def test_callback_and_nested_fragment(self):
        code, url = callback("ptuiCB('0','0','https://connect.qq.com/#openid=fake&access_token=stub','0','ok','name');")
        self.assertEqual(code, '0')
        nested = 'https://connect.qq.com/?u1=' + urllib.parse.quote(url, safe='')
        self.assertEqual(parameters(nested)['access_token'], 'stub')
        self.assertEqual(parameters(urllib.parse.quote(url, safe=''))['openid'], 'fake')

    def test_callback_body_no_eval(self):
        value = 'auth://tauth.qq.com/#openid=fake&access_token=stub'
        self.assertEqual(body_url('_Callback(' + json.dumps({'url': value}) + ');'), value)
        self.assertEqual(body_url('console.log("no url")'), '')

    def test_redirect_domain_allowlist(self):
        for url in ('https://qq.com.evil.test/a', 'http://openmobile.qq.com/a',
                    'https://user:pass@openmobile.qq.com/a', 'https://openmobile.qq.com:90/a'):
            with self.assertRaises(SafeError):
                trusted_url(url, {'openmobile.qq.com'})
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))

    def test_resolve_check_sig_and_redirect_key(self):
        login = QQLogin()
        login.http = Mock()
        login.http.cookie.return_value = ''
        login.http.request.side_effect = [
            (302, {'Location': 'https://imgcache.qq.com/a?redirect_uri_key=fake-key'}, b''),
            (200, {}, b'_Callback({"url":"auth://tauth.qq.com/#openid=fake&access_token=stub"});')]
        self.assertEqual(login.resolve('https://ptlogin4.openmobile.qq.com/check_sig?x=fake'),
                         {'openid': 'fake', 'access_token': 'stub'})

    def test_qr_expired(self):
        login = QQLogin()
        login.token, login.login_sig, login.jsver = 123, 'fake-sig', 'fake-jsver'
        login.http = Mock()
        login.http.cookie.return_value = ''
        login.http.request.return_value = (200, {}, b"ptuiCB('65','0','','0','expired');")
        with self.assertRaises(SafeError) as error:
            login.poll(1)
        self.assertEqual(error.exception.code, 'expired')

    def test_gh_secret_uses_stdin(self):
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as run:
            gh_call('gh', ['secret', 'set', 'VALORANT_AUTH_JSON'], b'fake-sensitive-payload')
        self.assertNotIn('fake-sensitive-payload', str(run.call_args.args))
        self.assertEqual(run.call_args.kwargs['input'], b'fake-sensitive-payload')


class LogSafetyTests(unittest.TestCase):
    def capture(self, args, query_error):
        buf = io.StringIO()
        with patch.dict(os.environ, {'VALORANT_AUTH_JSON': json.dumps(AUTH), 'PUSH_PLUS_TOKEN': 'fake-push'}, clear=True), \
                patch('valorant_cn.__main__.query_store', side_effect=query_error), \
                patch('valorant_cn.__main__.push') as sender, contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            status = main(args)
        self.assertNotIn('fake-private-secret', buf.getvalue())
        self.assertNotIn('Traceback', buf.getvalue())
        return status, sender

    def test_unexpected_error_no_traceback(self):
        status, _ = self.capture([], RuntimeError('fake-private-secret'))
        self.assertEqual(status, 1)

    def test_check_only_has_no_push(self):
        status, sender = self.capture(['--check-only'], SafeError('auth'))
        self.assertEqual(status, 1)
        sender.assert_not_called()

    def test_failure_notifies_without_credentials(self):
        status, sender = self.capture([], SafeError('auth'))
        self.assertEqual(status, 1)
        self.assertEqual(sender.call_count, 1)
        self.assertNotIn('fake-ticket', str(sender.call_args))

    def test_http_error_message_sanitized(self):
        client = Http({'app.mval.qq.com'})
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.URLError('fake-private-secret')
        with self.assertRaises(SafeError) as error:
            client.json('https://app.mval.qq.com/test')
        self.assertNotIn('fake-private-secret', str(error.exception))

    def test_network_retries_are_bounded(self):
        client = Http({'app.mval.qq.com'})
        client.opener = Mock()
        client.opener.open.side_effect = urllib.error.URLError('fake-private-secret')
        with patch('valorant_cn.core.time.sleep') as sleep, self.assertRaises(SafeError):
            client.json('https://app.mval.qq.com/test', retries=3)
        self.assertEqual(client.opener.open.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2])

    def test_no_secret_output_on_success(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'VALORANT_AUTH_JSON': json.dumps(AUTH), 'PUSH_PLUS_TOKEN': 'fake-push'}, clear=True), \
                patch('valorant_cn.__main__.query_store', return_value=parse_store(store_response())), \
                patch('valorant_cn.__main__.push'), contextlib.redirect_stdout(output):
            self.assertEqual(main([]), 0)
        for private in ('fake-user', 'fake-ticket', 'fake-access', 'fake-openid', '测试皮肤'):
            self.assertNotIn(private, output.getvalue())


if __name__ == '__main__':
    unittest.main()
