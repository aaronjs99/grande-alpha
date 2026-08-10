import keyring
import pytest

from grande_alpha.broker import oauth


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        assert len(value) <= oauth.CHUNK_CHARACTERS
        self.values[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self.values[(service, username)]
        except KeyError as exc:
            raise keyring.errors.PasswordDeleteError(service) from exc


@pytest.fixture
def memory_keyring(monkeypatch) -> MemoryKeyring:
    vault = MemoryKeyring()
    monkeypatch.setattr(oauth.keyring, "get_password", vault.get_password)
    monkeypatch.setattr(oauth.keyring, "set_password", vault.set_password)
    monkeypatch.setattr(oauth.keyring, "delete_password", vault.delete_password)
    return vault


def test_large_credential_is_chunked_verified_and_cleared(memory_keyring: MemoryKeyring) -> None:
    storage = oauth.CredentialTokenStorage("test")
    value = "token-payload-" * 600

    storage._set("tokens", value)

    assert storage._get("tokens") == value
    manifest = memory_keyring.values[(oauth.KEYRING_SERVICE, "test:tokens")]
    assert manifest.startswith(oauth.CHUNKED_CREDENTIAL_PREFIX)
    assert len(memory_keyring.values) > 2

    storage.clear()
    assert not memory_keyring.values


def test_chunk_integrity_failure_is_detected(memory_keyring: MemoryKeyring) -> None:
    storage = oauth.CredentialTokenStorage("test")
    storage._set("tokens", "sensitive-token-contents" * 200)
    chunk_key = next(key for key in memory_keyring.values if ":tokens:" in key[1])
    memory_keyring.values[chunk_key] = "X" + memory_keyring.values[chunk_key][1:]

    with pytest.raises(RuntimeError, match="integrity check"):
        storage._get("tokens")


def test_token_rotation_removes_superseded_chunks(memory_keyring: MemoryKeyring) -> None:
    storage = oauth.CredentialTokenStorage("test")
    storage._set("tokens", "old-token" * 600)
    old_chunk_keys = {key for key in memory_keyring.values if ":tokens:" in key[1]}

    replacement = "replacement-token" * 500
    storage._set("tokens", replacement)

    assert storage._get("tokens") == replacement
    assert old_chunk_keys.isdisjoint(memory_keyring.values)


def test_direct_legacy_value_migrates_to_chunked_primary_service(memory_keyring: MemoryKeyring) -> None:
    legacy_key = (oauth.LEGACY_KEYRING_SERVICE, "test:tokens")
    memory_keyring.values[legacy_key] = "legacy-token-json"
    storage = oauth.CredentialTokenStorage("test")

    assert storage._get("tokens") == "legacy-token-json"
    assert storage._get("tokens") == "legacy-token-json"
    assert memory_keyring.values[(oauth.KEYRING_SERVICE, "test:tokens")].startswith(
        oauth.CHUNKED_CREDENTIAL_PREFIX
    )
