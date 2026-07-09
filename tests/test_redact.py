from csm.redact import REDACTED, redact


def test_api_key():
    assert "sk-ant" not in redact("key sk-ant-abc123DEF456ghi789")


def test_bearer():
    out = redact("Authorization: Bearer abcdef123456789012")
    assert "abcdef" not in out and REDACTED in out


def test_key_value():
    out = redact("password=hunter2secret token: 'abc' API_KEY: xyz123")
    assert "hunter2secret" not in out
    assert "xyz123" not in out


def test_sshpass():
    out = redact("sshpass -p SuperSecret ssh admin@10.0.0.1")
    assert "SuperSecret" not in out
    assert "ssh admin@10.0.0.1" in out


def test_private_key_block():
    out = redact("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----")
    assert "MIIE" not in out


def test_plain_text_untouched():
    assert redact("hello world /home/kelly/project") == "hello world /home/kelly/project"


def test_empty():
    assert redact("") == ""
