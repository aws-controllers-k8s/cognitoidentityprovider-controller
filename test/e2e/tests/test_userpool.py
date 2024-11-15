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

"""Integration tests for the ELB TargetGroups.
"""

import logging
import time

import pytest
from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from e2e import CRD_GROUP, CRD_VERSION, load_cognitoidentityprovider_resource, service_marker
from e2e.bootstrap_resources import get_bootstrap_resources
from e2e.replacement_values import REPLACEMENT_VALUES

from e2e.tests.helper import CognitoValidator

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

    # Create k8s resource
    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
        userpool_name, namespace="default")
    k8s.create_custom_resource(ref, resource_data)

    time.sleep(CREATE_WAIT_AFTER_SECONDS)
    cr = k8s.wait_resource_consumed_by_controller(ref)

    assert cr is not None
    assert k8s.get_resource_exists(ref)

    yield (ref, cr)

    # Delete k8s resource
    if k8s.get_resource_exists(ref):
        _, deleted = k8s.delete_custom_resource(
            ref,
            DELETE_WAIT_AFTER_SECONDS,
        )
        assert deleted
    assert not k8s.get_resource_exists(ref)

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
        id = cr['status']['id']
        deletion_protection = 'INACTIVE'
        validator = CognitoValidator(cognitoidentityprovider_client)

        assert validator.user_pool_exists(id)

        updates = {
            'spec': {
                'deletionProtection': deletion_protection
            }
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        user_pool = validator.get_user_pool(id)
        assert user_pool['DeletionProtection'] == deletion_protection

        _, deleted = k8s.delete_custom_resource(
            ref,
            DELETE_WAIT_AFTER_SECONDS,
        )
        assert deleted

        assert not validator.user_pool_exists(id)