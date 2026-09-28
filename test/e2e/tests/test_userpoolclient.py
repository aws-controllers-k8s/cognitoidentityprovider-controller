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

"""Integration tests for the Cognito UserPoolClient resource."""

import logging
import time
import base64

import pytest
from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from kubernetes import client
from e2e import load_cognitoidentityprovider_resource, service_marker
from e2e.replacement_values import REPLACEMENT_VALUES

from e2e.tests.helper import (
    CognitoValidator,
    create_and_assert_resource,
    create_and_wait_resource,
    create_boto3_user_pool,
    create_confirmed_user,
    delete_and_assert_gone,
    delete_boto3_user_pool,
)

RESOURCE_PLURAL = 'userpoolclients'

CREATE_WAIT_AFTER_SECONDS = 5
UPDATE_WAIT_AFTER_SECONDS = 5
DELETE_WAIT_AFTER_SECONDS = 5

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
def user_pool_for_client(cognitoidentityprovider_client):
    """Create a UserPool via boto3 to serve as the parent for UserPoolClient tests."""
    user_pool_id = create_boto3_user_pool(cognitoidentityprovider_client, "pool-for-client")
    logging.info(f"Created UserPool {user_pool_id} for UserPoolClient tests")
    yield user_pool_id
    delete_boto3_user_pool(cognitoidentityprovider_client, user_pool_id)
    logging.info(f"Deleted UserPool {user_pool_id}")

@pytest.fixture(scope='module')
def simple_userpoolclient(cognitoidentityprovider_client, user_pool_for_client):
    userpoolclient_name = random_suffix_name("userpoolclient", 24)
    user_pool_id = user_pool_for_client

    replacements = REPLACEMENT_VALUES.copy()
    replacements['USERPOOLCLIENT_NAME'] = userpoolclient_name
    replacements['USERPOOL_ID'] = user_pool_id

    resource_data = load_cognitoidentityprovider_resource(
        'userpoolclient_simple',
        additional_replacements=replacements,
    )

    ref, cr = create_and_assert_resource(
        RESOURCE_PLURAL, userpoolclient_name, resource_data,
        wait_seconds=CREATE_WAIT_AFTER_SECONDS,
    )

    yield (ref, cr, user_pool_id)

    delete_and_assert_gone(ref, wait_seconds=DELETE_WAIT_AFTER_SECONDS)

@pytest.fixture(scope='module')
def simple_userpoolclient_fromref(cognitoidentityprovider_client, simple_userpool):
    userpoolclient_name = random_suffix_name("userpoolclient", 24)
    _, userpool_cr = simple_userpool
    replacements = REPLACEMENT_VALUES.copy()
    replacements['USERPOOLCLIENT_NAME'] = userpoolclient_name
    replacements['USERPOOL_NAME'] = userpool_cr['metadata']['name']

    resource_data = load_cognitoidentityprovider_resource(
        'userpoolclient_from_ref',
        additional_replacements=replacements,
    )

    ref, cr = create_and_assert_resource(
        RESOURCE_PLURAL, userpoolclient_name, resource_data,
        wait_seconds=CREATE_WAIT_AFTER_SECONDS,
    )

    yield (ref, cr, userpool_cr['status']['id'])

    delete_and_assert_gone(ref, wait_seconds=DELETE_WAIT_AFTER_SECONDS)

@pytest.fixture
def user_pool(cognitoidentityprovider_client):
    """A bare UserPool, created via boto3, for tests that don't need to
    verify authentication (validation-error and adoption tests)."""
    user_pool_id = create_boto3_user_pool(cognitoidentityprovider_client, 'pool-for-upc-secret-fields')

    yield user_pool_id

    delete_boto3_user_pool(cognitoidentityprovider_client, user_pool_id)


@pytest.fixture
def user_pool_with_admin_user(cognitoidentityprovider_client):
    """A UserPool plus a confirmed admin-created user, for tests that need to
    verify a client secret actually authenticates against Cognito."""
    user_pool_id = create_boto3_user_pool(cognitoidentityprovider_client, 'pool-for-upc-secret-fields')
    username, password = create_confirmed_user(
        cognitoidentityprovider_client, user_pool_id, 'secret-fields-user',
    )

    yield user_pool_id, username, password

    delete_boto3_user_pool(cognitoidentityprovider_client, user_pool_id)


def create_user_pool_client(name, resource_data):
    return create_and_wait_resource(
        RESOURCE_PLURAL, name, resource_data, wait_seconds=CREATE_WAIT_AFTER_SECONDS,
    )

@service_marker
@pytest.mark.canary
class TestUserPoolClient():
    def test_create_delete_simple_userpoolclient(
        self, simple_userpoolclient, cognitoidentityprovider_client
    ):
        (ref, cr, user_pool_id) = simple_userpoolclient
        assert cr is not None
        assert 'spec' in cr
        assert 'name' in cr['spec']
        assert 'userPoolID' in cr['spec']
        assert cr['spec']['userPoolID'] == user_pool_id

        assert 'status' in cr
        assert 'id' in cr['status']
        client_id = cr['status']['id']

        # Verify the resource exists in AWS
        validator = CognitoValidator(cognitoidentityprovider_client)
        assert validator.user_pool_client_exists(user_pool_id, client_id)

        # Verify explicit auth flows were set correctly
        aws_client = validator.get_user_pool_client(user_pool_id, client_id)
        assert 'ALLOW_USER_SRP_AUTH' in aws_client['ExplicitAuthFlows']
        assert 'ALLOW_REFRESH_TOKEN_AUTH' in aws_client['ExplicitAuthFlows']

        # Update: add callback URLs
        updates = {
            'spec': {
                'callbackURLs': [
                    'https://example.com/callback',
                ],
                'allowedOAuthFlowsUserPoolClient': True,
                'allowedOAuthFlows': ['code'],
                'allowedOAuthScopes': ['openid'],
            }
        }
        k8s.patch_custom_resource(ref, updates)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        # Verify update in AWS
        aws_client = validator.get_user_pool_client(user_pool_id, client_id)
        assert 'https://example.com/callback' in aws_client['CallbackURLs']
        assert aws_client['AllowedOAuthFlowsUserPoolClient'] is True
        assert 'code' in aws_client['AllowedOAuthFlows']
        assert 'openid' in aws_client['AllowedOAuthScopes']

        # Delete
        _, deleted = k8s.delete_custom_resource(
            ref,
            DELETE_WAIT_AFTER_SECONDS,
        )
        assert deleted

        assert not validator.user_pool_client_exists(user_pool_id, client_id)

    def test_create_delete_simple_userpoolclient_fromref(
        self, simple_userpoolclient_fromref, cognitoidentityprovider_client
    ):
        (ref, cr, user_pool_id) = simple_userpoolclient_fromref
        assert cr is not None
        assert 'spec' in cr
        assert 'name' in cr['spec']
        assert 'userPoolRef' in cr['spec']
        assert cr['spec']['userPoolRef']['from']['name'] is not None

        assert 'status' in cr
        assert 'id' in cr['status']
        client_id = cr['status']['id']

        # Verify the resource exists in AWS
        validator = CognitoValidator(cognitoidentityprovider_client)
        assert validator.user_pool_client_exists(user_pool_id, client_id)

        # Delete
        _, deleted = k8s.delete_custom_resource(
            ref,
            DELETE_WAIT_AFTER_SECONDS,
        )
        assert deleted

        assert not validator.user_pool_client_exists(user_pool_id, client_id)


@service_marker
@pytest.mark.canary
class TestUserPoolClientSecretExport:
    def test_export_generated_secret(
            self, cognitoidentityprovider_client, k8s_client, user_pool_with_admin_user,
    ):
        user_pool_id, username, password = user_pool_with_admin_user
        userpoolclient_name = random_suffix_name('upc-export', 24)
        target_secret_name = random_suffix_name('upc-export-secret', 32)
        k8s.create_opaque_secret(namespace='default', name=target_secret_name, key='unrelated', value='preserved')

        replacements = REPLACEMENT_VALUES.copy()
        replacements.update({
            'USERPOOLCLIENT_NAME': userpoolclient_name,
            'USERPOOL_ID': user_pool_id,
            'TARGET_SECRET_NAME': target_secret_name,
        })
        resource_data = load_cognitoidentityprovider_resource(
            'userpoolclient_generated_secret_export', additional_replacements=replacements,
        )
        validator = CognitoValidator(cognitoidentityprovider_client)
        core_api = client.CoreV1Api(k8s_client)
        ref = None
        exported_secret = None
        try:
            ref, cr = create_user_pool_client(userpoolclient_name, resource_data)
            assert k8s.wait_on_condition(ref, 'ACK.ResourceSynced', 'True')

            assert cr is not None
            assert 'status' in cr
            assert 'id' in cr['status']
            client_id = cr['status']['id']
            assert 'initialClientSecretID' in cr['status']
            assert 'clientSecret' not in cr['status']
            assert 'clientSecret' not in cr['spec']

            target = core_api.read_namespaced_secret(target_secret_name, 'default')
            exported_secret = base64.b64decode(target.data['clientSecret']).decode('utf-8')
            assert exported_secret
            assert base64.b64decode(target.data['unrelated']).decode('utf-8') == 'preserved'

            aws_client = validator.get_user_pool_client(user_pool_id, client_id)
            assert aws_client['ClientSecret'] == exported_secret

            assert validator.get_access_token(client_id, username, password, exported_secret)
        finally:
            k8s.delete_custom_resource(ref, DELETE_WAIT_AFTER_SECONDS)
            if exported_secret is not None:
                # The destination Secret is user-owned; deleting the
                # UserPoolClient must not clear or touch it.
                assert base64.b64decode(
                    core_api.read_namespaced_secret(target_secret_name, 'default').data['clientSecret'],
                ).decode('utf-8') == exported_secret
            k8s.delete_secret(namespace='default', name=target_secret_name)

    def test_export_without_generate_secret_fails(self, user_pool):
        userpoolclient_name = random_suffix_name('upc-noexport', 24)
        target_secret_name = random_suffix_name('upc-noexport-secret', 32)
        k8s.create_opaque_secret(namespace='default', name=target_secret_name, key='unrelated', value='preserved')

        replacements = REPLACEMENT_VALUES.copy()
        replacements.update({
            'USERPOOLCLIENT_NAME': userpoolclient_name,
            'USERPOOL_ID': user_pool,
            'TARGET_SECRET_NAME': target_secret_name,
        })
        resource_data = load_cognitoidentityprovider_resource(
            'userpoolclient_export_without_generate', additional_replacements=replacements,
        )
        ref = None
        try:
            ref, cr = create_user_pool_client(userpoolclient_name, resource_data)
            assert k8s.wait_on_condition(ref, 'ACK.ResourceSynced', 'False')
            assert k8s.assert_condition_state_message(
                ref, 'ACK.Terminal', 'True',
                'spec.clientSecretExportTo requires spec.generateSecret=true',
            )
        finally:
            k8s.delete_custom_resource(ref, DELETE_WAIT_AFTER_SECONDS)
            k8s.delete_secret(namespace='default', name=target_secret_name)


@service_marker
@pytest.mark.canary
class TestUserPoolClientSecretSupply:
    def test_supply_client_secret(
            self, cognitoidentityprovider_client, user_pool_with_admin_user,
    ):
        user_pool_id, username, password = user_pool_with_admin_user
        userpoolclient_name = random_suffix_name('upc-supply', 24)
        source_secret_name = random_suffix_name('upc-supply-secret', 32)
        supplied_secret = 'b' * 32
        k8s.create_opaque_secret(namespace='default', name=source_secret_name, key='clientSecret', value=supplied_secret)

        replacements = REPLACEMENT_VALUES.copy()
        replacements.update({
            'USERPOOLCLIENT_NAME': userpoolclient_name,
            'USERPOOL_ID': user_pool_id,
            'SOURCE_SECRET_NAME': source_secret_name,
        })
        resource_data = load_cognitoidentityprovider_resource(
            'userpoolclient_supplied_secret', additional_replacements=replacements,
        )
        validator = CognitoValidator(cognitoidentityprovider_client)
        ref = None
        try:
            ref, cr = create_user_pool_client(userpoolclient_name, resource_data)
            assert k8s.wait_on_condition(ref, 'ACK.ResourceSynced', 'True')

            assert cr is not None
            assert 'status' in cr
            client_id = cr['status']['id']
            assert 'clientSecret' not in cr['status']

            client_secret_id = cr['status'].get('initialClientSecretID')
            assert client_secret_id in [s['ClientSecretId'] for s in validator.list_user_pool_client_secrets(user_pool_id, client_id)]

            assert validator.get_access_token(client_id, username, password, supplied_secret)
        finally:
            k8s.delete_custom_resource(ref, DELETE_WAIT_AFTER_SECONDS)
            k8s.delete_secret(namespace='default', name=source_secret_name)

    def test_conflicting_generate_and_supplied_secret_fails(self, user_pool):
        userpoolclient_name = random_suffix_name('upc-conflict', 24)
        source_secret_name = random_suffix_name('upc-conflict-secret', 32)
        k8s.create_opaque_secret(namespace='default', name=source_secret_name, key='clientSecret', value='c' * 32)

        replacements = REPLACEMENT_VALUES.copy()
        replacements.update({
            'USERPOOLCLIENT_NAME': userpoolclient_name,
            'USERPOOL_ID': user_pool,
            'SOURCE_SECRET_NAME': source_secret_name,
        })
        resource_data = load_cognitoidentityprovider_resource(
            'userpoolclient_secret_conflict', additional_replacements=replacements,
        )
        ref = None
        try:
            ref, cr = create_user_pool_client(userpoolclient_name, resource_data)
            assert k8s.wait_on_condition(ref, 'ACK.ResourceSynced', 'False')
            assert k8s.assert_condition_state_message(
                ref, 'ACK.Terminal', 'True',
                'only one of spec.clientSecret or spec.generateSecret=true may be specified',
            )
        finally:
            k8s.delete_custom_resource(ref, DELETE_WAIT_AFTER_SECONDS)
            k8s.delete_secret(namespace='default', name=source_secret_name)
