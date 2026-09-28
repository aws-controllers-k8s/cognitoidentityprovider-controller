# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
# 	 http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""Integration tests for the Cognito ResourceServer resource."""

import logging
import time

import pytest
from acktest.k8s import resource as k8s
from acktest.k8s import condition
from acktest.resources import random_suffix_name
from e2e import load_cognitoidentityprovider_resource, service_marker
from e2e.replacement_values import REPLACEMENT_VALUES

from e2e.tests.helper import CognitoValidator, create_and_assert_resource, delete_and_assert_gone

RESOURCE_PLURAL = 'resourceservers'

CREATE_WAIT_AFTER_SECONDS = 10
UPDATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10

@pytest.fixture(scope='module')
def simple_userpool(cognitoidentityprovider_client):
    userpool_name = random_suffix_name("userpool", 16)
    replacements = REPLACEMENT_VALUES.copy()
    replacements['USERPOOL_NAME'] = userpool_name
    replacements['USERPOOL_DELETION_PROTECTION'] = 'INACTIVE'

    resource_data = load_cognitoidentityprovider_resource(
        'userpool_simple',
        additional_replacements=replacements
    )
    logging.debug(resource_data)

    ref, cr = create_and_assert_resource(
        'userpools', userpool_name, resource_data,
        wait_seconds=CREATE_WAIT_AFTER_SECONDS,
    )

    yield (ref, cr)

    delete_and_assert_gone(ref, wait_seconds=DELETE_WAIT_AFTER_SECONDS)


@pytest.fixture(scope='module')
def simple_resourceserver(cognitoidentityprovider_client, simple_userpool):
    _, userpool_cr = simple_userpool
    resourceserver_name = random_suffix_name("resourceserver", 24)
    identifier = f"https://{resourceserver_name}.example.com"

    replacements = REPLACEMENT_VALUES.copy()
    replacements['RESOURCE_SERVER_NAME'] = resourceserver_name
    replacements['RESOURCE_SERVER_IDENTIFIER'] = identifier
    replacements['USERPOOL_NAME'] = userpool_cr['metadata']['name']

    resource_data = load_cognitoidentityprovider_resource(
        'resourceserver_simple',
        additional_replacements=replacements,
    )
    logging.debug(resource_data)

    ref, cr = create_and_assert_resource(
        RESOURCE_PLURAL, resourceserver_name, resource_data,
        wait_seconds=CREATE_WAIT_AFTER_SECONDS,
    )

    yield (ref, cr, userpool_cr['status']['id'], identifier)

    delete_and_assert_gone(ref, wait_seconds=DELETE_WAIT_AFTER_SECONDS)


@service_marker
@pytest.mark.canary
class TestResourceServer():
    def test_create_update_delete_resourceserver(
        self, simple_resourceserver, cognitoidentityprovider_client
    ):
        (ref, cr, user_pool_id, identifier) = simple_resourceserver
        assert cr is not None
        assert 'spec' in cr
        assert 'identifier' in cr['spec']
        assert cr['spec']['identifier'] == identifier
        assert 'name' in cr['spec']

        assert k8s.wait_on_condition(ref, condition.CONDITION_TYPE_RESOURCE_SYNCED, "True", wait_periods=5)

        # Verify the resource exists in AWS
        validator = CognitoValidator(cognitoidentityprovider_client)
        assert validator.resource_server_exists(user_pool_id, identifier)

        # Verify scopes were set correctly
        aws_rs = validator.get_resource_server(user_pool_id, identifier)
        assert len(aws_rs['Scopes']) == 1
        assert aws_rs['Scopes'][0]['ScopeName'] == 'read'
        assert aws_rs['Scopes'][0]['ScopeDescription'] == 'Read access'

        # Update: add a second scope
        updates = {
            'spec': {
                'scopes': [
                    {
                        'scopeName': 'read',
                        'scopeDescription': 'Read access',
                    },
                    {
                        'scopeName': 'write',
                        'scopeDescription': 'Write access',
                    },
                ]
            }
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(ref, condition.CONDITION_TYPE_RESOURCE_SYNCED, "True", wait_periods=5)

        # Verify update in AWS
        aws_rs = validator.get_resource_server(user_pool_id, identifier)
        assert len(aws_rs['Scopes']) == 2
        scope_names = [s['ScopeName'] for s in aws_rs['Scopes']]
        assert 'read' in scope_names
        assert 'write' in scope_names

        # Delete
        _, deleted = k8s.delete_custom_resource(
            ref,
            DELETE_WAIT_AFTER_SECONDS,
        )
        assert deleted

        assert not validator.resource_server_exists(user_pool_id, identifier)
