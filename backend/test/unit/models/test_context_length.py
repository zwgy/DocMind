import pytest

from yuxi.models.context_length import resolve_context_length


def test_resolve_context_length_prefers_persisted_value_and_source():
    resolved = resolve_context_length(
        configured_value=65536,
        configured_source="models_api",
        profile_value=131072,
        default_value=32768,
    )

    assert resolved.value == 65536
    assert resolved.source == "models_api"


def test_resolve_context_length_treats_legacy_config_as_manual():
    resolved = resolve_context_length(
        configured_value="32768",
        configured_source=None,
        profile_value=None,
        default_value=16384,
    )

    assert resolved.value == 32768
    assert resolved.source == "manual"


def test_resolve_context_length_uses_profile_then_default():
    profile = resolve_context_length(
        configured_value=None,
        configured_source=None,
        profile_value=131072,
        default_value=32768,
    )
    fallback = resolve_context_length(
        configured_value=None,
        configured_source=None,
        profile_value=None,
        default_value=32768,
    )

    assert (profile.value, profile.source) == (131072, "langchain_profile")
    assert (fallback.value, fallback.source) == (32768, "default")


def test_resolve_context_length_rejects_invalid_default():
    with pytest.raises(ValueError, match="default_context_window"):
        resolve_context_length(
            configured_value=None,
            configured_source=None,
            profile_value=None,
            default_value=0,
        )
