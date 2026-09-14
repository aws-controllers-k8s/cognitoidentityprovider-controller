# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
#     http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""Integration tests for the Cognito UserPoolClientSecret resource."""

import base64
import hashlib
import hmac
import logging
import time

import pytest
from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from kubernetes import client

from e2e import CRD_GROUP, CRD_VERSION, load_cognitoidentityprovider_resource, service_marker
from e2e.replacement_values import REPLACEMENT_VALUES
from e2e.tests.helper import CognitoValidator

RESOURCE_PLURAL = 'userpoolclientsecrets'
CREATE_WAIT_AFTER_SECONDS = 10
DELETE_WAIT_AFTER_SECONDS = 10


@pytest.fixture
def user_pool_client_with_secret(cognitoidentityprovider_client):
    pool_name = random_suffix_name('pool-for-client-secret', 24)
    pool = cognitoidentityprovider_client.create_user_pool(PoolName=pool_name)
    user_pool_id = pool['UserPool']['Id']
    client_name = random_suffix_name('client-for-secret', 24)
    user_pool_client = cognitoidentityprovider_client.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=client_name,
        GenerateSecret=True,
        ExplicitAuthFlows=['ALLOW_USER_PASSWORD_AUTH'],
    )
    user_pool_client_id = user_pool_client['UserPoolClient']['ClientId']
    username = random_suffix_name('secret-user', 24)
    password = 'AckTestPassword123!'
    cognitoidentityprovider_client.admin_create_user(
        UserPoolId=user_pool_id,
        Username=username,
        MessageAction='SUPPRESS',
    )
    cognitoidentityprovider_client.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )

    yield user_pool_id, user_pool_client_id, username, password

    try:
        cognitoidentityprovider_client.delete_user_pool(UserPoolId=user_pool_id)
    except cognitoidentityprovider_client.exceptions.ResourceNotFoundException:
        pass

def create_user_pool_client_secret(name, resource_data):
    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL, name, namespace='default',
    )
    k8s.create_custom_resource(ref, resource_data)
    time.sleep(CREATE_WAIT_AFTER_SECONDS)
    cr = k8s.wait_resource_consumed_by_controller(ref)
    return ref, k8s.get_resource(ref)


def assert_client_secret_authenticates(
    cognitoidentityprovider_client, user_pool_client_id, username, password, client_secret,
):
    secret_hash = base64.b64encode(
        hmac.new(
            client_secret.encode('utf-8'),
            f'{username}{user_pool_client_id}'.encode('utf-8'),
            hashlib.sha256,
        ).digest(),
    ).decode('utf-8')
    response = cognitoidentityprovider_client.initiate_auth(
        ClientId=user_pool_client_id,
        AuthFlow='USER_PASSWORD_AUTH',
        AuthParameters={
            'USERNAME': username,
            'PASSWORD': password,
            'SECRET_HASH': secret_hash,
        },
    )
    assert response['AuthenticationResult']['AccessToken']

def client_secret_exists(validator, user_pool_id, user_pool_client_id, secret_id):
    secrets = validator.list_user_pool_client_secrets(user_pool_id, user_pool_client_id)
    return any(secret['ClientSecretId'] == secret_id for secret in secrets)

@service_marker
@pytest.mark.canary
class TestUserPoolClientSecretGenerated:
    def test_create_delete_generated_secret(
        self, cognitoidentityprovider_client, k8s_client, user_pool_client_with_secret,
    ):
        user_pool_id, user_pool_client_id, username, password = user_pool_client_with_secret
        resource_name = random_suffix_name('userpoolclientsecret', 24)
        target_secret_name = random_suffix_name('generated-client-secret', 32)
        k8s.create_opaque_secret(namespace='default', name=target_secret_name, key='unrelated', value='preserved')

        replacements = REPLACEMENT_VALUES.copy()
        replacements.update({
            'USERPOOLCLIENTSECRET_NAME': resource_name,
            'USERPOOL_ID': user_pool_id,
            'USERPOOLCLIENT_ID': user_pool_client_id,
            'TARGET_SECRET_NAME': target_secret_name,
        })
        resource_data = load_cognitoidentityprovider_resource(
            'userpoolclientsecret_generated', additional_replacements=replacements,
        )
        validator = CognitoValidator(cognitoidentityprovider_client)
        ref = None
        second_ref = None
        second_target_secret_name = random_suffix_name('generated-client-secret', 32)
        core_api = client.CoreV1Api(k8s_client)
        try:
            ref, cr = create_user_pool_client_secret(resource_name, resource_data)
            assert k8s.wait_on_condition(ref, 'ACK.ResourceSynced', 'True')

            assert cr is not None
            assert cr['status']['id']
            secret_id = cr['status']['id']
            assert 'clientSecret' not in cr['status']
            assert 'clientSecret' not in cr['spec']
            target = core_api.read_namespaced_secret(target_secret_name, 'default')
            generated_secret = base64.b64decode(target.data['clientSecret']).decode('utf-8')
            assert generated_secret
            assert base64.b64decode(target.data['unrelated']).decode('utf-8') == 'preserved'
            assert_client_secret_authenticates(
                cognitoidentityprovider_client,
                user_pool_client_id,
                username,
                password,
                generated_secret,
            )
            assert client_secret_exists(validator, user_pool_id, user_pool_client_id, secret_id)
            assert len(validator.list_user_pool_client_secrets(user_pool_id, user_pool_client_id)) == 2

            # Cognito generate when creating a new UserPoolClient a secret for the client,
            # and the controller will create a UserPoolClientSecret for that secret.
            # Creating a second UserPoolClientSecret for the same client should fail.
            second_resource_name = random_suffix_name('userpoolclientsecret', 24)
            k8s.create_opaque_secret(namespace='default', name=second_target_secret_name, key='unrelated', value='preserved')
            second_replacements = replacements.copy()
            second_replacements.update({
                'USERPOOLCLIENTSECRET_NAME': second_resource_name,
                'TARGET_SECRET_NAME': second_target_secret_name,
            })
            second_resource_data = load_cognitoidentityprovider_resource(
                'userpoolclientsecret_generated', additional_replacements=second_replacements,
            )
            second_ref, second_cr = create_user_pool_client_secret(second_resource_name, second_resource_data)
            assert k8s.wait_on_condition(second_ref, 'ACK.ResourceSynced', 'Unknown')

            assert second_cr is not None
        finally:
            k8s.delete_custom_resource(second_ref, DELETE_WAIT_AFTER_SECONDS)
            k8s.delete_secret(namespace='default', name=second_target_secret_name)

            k8s.delete_custom_resource(ref, DELETE_WAIT_AFTER_SECONDS)
            # Ensure client secret is not removed from Kubernetes secret when the UserPoolClientSecret is deleted
            # since the secret is managed by the controller and not the CRD.
            assert 'clientSecret' in core_api.read_namespaced_secret(target_secret_name, 'default').data
            k8s.delete_secret(namespace='default', name=target_secret_name)

            assert len(validator.list_user_pool_client_secrets(user_pool_id, user_pool_client_id)) == 1

    def test_create_with_existing_key_secret_should_fail(
            self, user_pool_client_with_secret,
    ):
        user_pool_id, user_pool_client_id, username, password = user_pool_client_with_secret
        resource_name = random_suffix_name('userpoolclientsecret', 24)
        target_secret_name = random_suffix_name('generated-client-secret', 32)
        # clientSecret key is the referenced key in the CRD, so creating a secret with that key should cause the controller to fail
        k8s.create_opaque_secret(namespace='default', name=target_secret_name, key='clientSecret', value='already_there')

        replacements = REPLACEMENT_VALUES.copy()
        replacements.update({
            'USERPOOLCLIENTSECRET_NAME': resource_name,
            'USERPOOL_ID': user_pool_id,
            'USERPOOLCLIENT_ID': user_pool_client_id,
            'TARGET_SECRET_NAME': target_secret_name,
        })
        resource_data = load_cognitoidentityprovider_resource(
            'userpoolclientsecret_generated', additional_replacements=replacements,
        )
        ref = None
        try:
            ref, cr = create_user_pool_client_secret(resource_name, resource_data)
            assert k8s.wait_on_condition(ref, 'ACK.ResourceSynced', 'False')
            assert k8s.assert_condition_state_message(ref, 'ACK.Terminal', 'True', f'target Secret "{target_secret_name}" key "clientSecret" already contains a value')
        finally:
            k8s.delete_custom_resource(ref, DELETE_WAIT_AFTER_SECONDS)
            k8s.delete_secret(namespace='default', name=target_secret_name)


@service_marker
@pytest.mark.canary
class TestUserPoolClientSecretSupplied:
    def test_create_delete_supplied_secret(
        self, cognitoidentityprovider_client, user_pool_client_with_secret,
    ):
        user_pool_id, user_pool_client_id, username, password = user_pool_client_with_secret
        resource_name = random_suffix_name('userpoolclientsecret', 24)
        source_secret_name = random_suffix_name('supplied-client-secret', 32)
        supplied_secret = 'a' * 32
        k8s.create_opaque_secret(namespace='default', name=source_secret_name, key='clientSecret', value=supplied_secret)

        replacements = REPLACEMENT_VALUES.copy()
        replacements.update({
            'USERPOOLCLIENTSECRET_NAME': resource_name,
            'USERPOOL_ID': user_pool_id,
            'USERPOOLCLIENT_ID': user_pool_client_id,
            'SOURCE_SECRET_NAME': source_secret_name,
        })
        resource_data = load_cognitoidentityprovider_resource(
            'userpoolclientsecret_supplied', additional_replacements=replacements,
        )
        ref = None
        try:
            ref, cr = create_user_pool_client_secret(resource_name, resource_data)
            assert k8s.wait_on_condition(ref, 'ACK.ResourceSynced', 'True')

            assert cr is not None
            assert cr['status']['id']
            secret_id = cr['status']['id']
            assert 'clientSecret' not in cr['status']
            assert_client_secret_authenticates(
                cognitoidentityprovider_client,
                user_pool_client_id,
                username,
                password,
                supplied_secret,
            )
            validator = CognitoValidator(cognitoidentityprovider_client)
            assert client_secret_exists(validator, user_pool_id, user_pool_client_id, secret_id)
        finally:
            k8s.delete_custom_resource(ref, DELETE_WAIT_AFTER_SECONDS)
            k8s.delete_secret(namespace='default', name=source_secret_name)
