from smoke_sense.logutil import redact


def test_redact_masks_secret_keys():
    params = {"email": "me@example.com", "key": "SECRET", "state": "06"}
    out = redact(params, {"email", "key"})
    assert out == {"email": "***", "key": "***", "state": "06"}


def test_redact_does_not_mutate_input():
    params = {"key": "SECRET", "x": 1}
    redact(params, {"key"})
    assert params == {"key": "SECRET", "x": 1}


def test_redact_ignores_absent_keys():
    out = redact({"a": 1}, {"key"})
    assert out == {"a": 1}
