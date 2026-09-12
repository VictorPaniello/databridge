"""SCIM-shaped payload building from a configured field mapping - see
provisioning.py's build_scim_payload docstring for the "true"/"false"
literal special-case and the full_name-split-on-first-space limitation,
both called out explicitly in the spec."""

from __future__ import annotations

import uuid

from tidybridge.models import ClientRecord
from tidybridge.provisioning import _load_mapping, build_scim_payload


def test_default_mapping_file_loads_the_documented_scim_shape():
    mapping = _load_mapping()
    assert mapping == {
        "userName": "email",
        "name.givenName": "full_name",
        "name.familyName": "full_name",
        "emails[0].value": "email",
        "active": "true",
    }


def test_build_scim_payload_maps_fields_into_the_documented_shape():
    record = ClientRecord(
        id=uuid.uuid4(),
        source_file="test.csv",
        full_name="Grace Hopper",
        email="grace@example.com",
    )
    mapping = _load_mapping()

    payload = build_scim_payload(record, mapping)

    assert payload == {
        "userName": "grace@example.com",
        "name": {"givenName": "Grace", "familyName": "Hopper"},
        "emails": [{"value": "grace@example.com"}],
        "active": True,
    }


def test_build_scim_payload_splits_a_single_word_name_on_both_parts():
    # "documented limitation" (spec) - a name with no space has nothing
    # to put in familyName, so it repeats into both.
    record = ClientRecord(id=uuid.uuid4(), source_file="test.csv", full_name="Cher", email="c@e.com")
    mapping = _load_mapping()

    payload = build_scim_payload(record, mapping)

    assert payload["name"] == {"givenName": "Cher", "familyName": "Cher"}
