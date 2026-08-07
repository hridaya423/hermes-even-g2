from hermes_g2_bridge.security import redact


def test_redacts_credentials_and_home_paths():
    value = redact({"command": "open /Users/alice/private/file.txt", "api_key": "top-secret"})
    assert "alice" not in value["command"]
    assert value["api_key"] == "<redacted>"

