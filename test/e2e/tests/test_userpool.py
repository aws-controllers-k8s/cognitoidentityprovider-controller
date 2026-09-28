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

"""Integration tests for the Cognito UserPool resource."""

import logging
import time

import pytest
from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from e2e import load_cognitoidentityprovider_resource, service_marker
from e2e.replacement_values import REPLACEMENT_VALUES

from e2e.tests.helper import CognitoValidator, create_and_assert_resource, delete_and_assert_gone

RESOURCE_PLURAL = 'userpools'

CREATE_WAIT_AFTER_SECONDS = 10
UPDATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10

@pytest.fixture(scope='module')
def simple_userpool(cognitoidentityprovider_client):
    userpool_name = random_suffix_name("userpool", 16)
    replacements = REPLACEMENT_VALUES.copy()
    replacements['USERPOOL_NAME'] = userpool_name

    resource_data = load_cognitoidentityprovider_resource(
        'userpool_simple',
        additional_replacements=replacements
    )
    logging.debug(resource_data)

    ref, cr = create_and_assert_resource(
        RESOURCE_PLURAL, userpool_name, resource_data,
        wait_seconds=CREATE_WAIT_AFTER_SECONDS,
    )

    yield (ref, cr)

    delete_and_assert_gone(ref, wait_seconds=DELETE_WAIT_AFTER_SECONDS)

@service_marker
@pytest.mark.canary
class TestUserPool():
    def test_create_delete_simple_userpool(self, simple_userpool, cognitoidentityprovider_client):
        (ref, cr) = simple_userpool
        assert cr is not None
        assert 'spec' in cr
        assert 'deletionProtection' in cr['spec']
        assert cr['spec']['deletionProtection'] == "ACTIVE"
        assert 'status' in cr
        assert 'id' in cr['status']
        assert 'tags' in cr['spec']
        assert len(cr['spec']['tags']) == 1
        assert 'key' in cr['spec']['tags']
        assert 'val' == cr['spec']['tags']['key']
        id = cr['status']['id']
        arn = cr['status']['ackResourceMetadata']['arn']
        deletion_protection = 'INACTIVE'
        validator = CognitoValidator(cognitoidentityprovider_client)

        assert validator.user_pool_exists(id)

        updates = {
            'spec': {
                'deletionProtection': deletion_protection,
                'tags': {
                    'anotherKey': 'val',
                    'key': None
                }
            }
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        tags = validator.user_pool_list_tags(arn)

        # User pool is going to have some tags attached
        # `"services.k8s.aws/controller-version": "cognitoidentityprovider-unknown"`
        # `"services.k8s.aws/namespace": "default"`
        # these come from controller. only looking for custom tags
        assert len(tags) == 3
        assert 'anotherKey' in tags
        assert tags['anotherKey'] == 'val'

        user_pool = validator.get_user_pool(id)
        assert user_pool['DeletionProtection'] == deletion_protection

        _, deleted = k8s.delete_custom_resource(
            ref,
            DELETE_WAIT_AFTER_SECONDS,
        )
        assert deleted

        assert not validator.user_pool_exists(id)