import os

from conftest import load_module

cs = load_module("check_secrets_under_test", "scripts/check_secrets.py")


def test_generic_pattern_flags_hardcoded_tokens_but_not_names_or_redaction_fixtures():
    sample = "AbCd" * 8                                                # built at runtime: no secret-shaped literal in the source
    assert cs.GENERIC.search(f'api_key = "{sample}"')
    assert cs.GENERIC.search("FMP_API_KEY=" + "a" * 32)
    assert not cs.GENERIC.search("FMP_API_KEY=")                       # .env.example lists names only
    assert not cs.GENERIC.search('os.environ.get("FRED_API_KEY")')


def test_both_keychain_and_environment_values_are_checked(monkeypatch):
    """Regression: a dict keyed by name let the environment value overwrite the Keychain value."""
    monkeypatch.setattr(cs.mcp_config, "keychain_get", lambda n: "keychain-value-AAAA1111" if n == "BLS_API_KEY" else None)
    monkeypatch.setitem(os.environ, "BLS_API_KEY", "environment-value-BBBB2222")
    found = {v for n, v in cs.key_values() if n == "BLS_API_KEY"}
    assert {"keychain-value-AAAA1111", "environment-value-BBBB2222"} <= found
