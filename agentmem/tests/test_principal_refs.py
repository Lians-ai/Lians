"""Canonical principal references must be collision-resistant across auth realms."""

from uuid import uuid4

from lians.authz import api_key_principal_ref, oidc_principal_ref


def test_same_oidc_subject_cannot_collapse_different_issuer_bindings():
    binding_a = uuid4()
    binding_b = uuid4()
    provider_a = uuid4()
    provider_b = uuid4()
    assert oidc_principal_ref(provider_a, binding_a) != oidc_principal_ref(
        provider_b, binding_b
    )


def test_oidc_reference_is_stable_for_the_same_binding():
    provider_id = uuid4()
    binding_id = uuid4()
    expected = oidc_principal_ref(provider_id, binding_id)
    assert expected == oidc_principal_ref(str(provider_id), str(binding_id))
    assert expected.startswith("lians:principal:v1:oidc:")


def test_api_key_reference_uses_credential_uuid_not_a_display_label():
    credential_id = uuid4()
    reference = api_key_principal_ref(credential_id)
    assert reference == api_key_principal_ref(str(credential_id))
    assert reference.endswith(str(credential_id))
    assert "duplicate-label" not in reference
