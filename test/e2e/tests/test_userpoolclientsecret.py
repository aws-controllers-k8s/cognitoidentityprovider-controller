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

import json
import base64

import pytest
from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name
from kubernetes import client

from e2e import load_cognitoidentityprovider_resource, service_marker
from e2e.replacement_values import REPLACEMENT_VALUES
from e2e.tests.helper import (
    CognitoValidator,
    create_and_wait_resource,
    create_boto3_user_pool,
    create_confirmed_user,
    delete_boto3_user_pool,
)

RESOURCE_PLURAL = 'userpoolclientsecrets'
CREATE_WAIT_AFTER_SECONDS = 5
DELETE_WAIT_AFTER_SECONDS = 5


@pytest.fixture
def user_pool_client_with_secret(cognitoidentityprovider_client):
    user_pool_id = create_boto3_user_pool(cognitoidentityprovider_client, 'pool-for-client-secret')
    client_name = random_suffix_name('client-for-secret', 24)
    user_pool_client = cognitoidentityprovider_client.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=client_name,
        GenerateSecret=True,
        ExplicitAuthFlows=['ALLOW_USER_PASSWORD_AUTH'],
    )
    user_pool_client_id = user_pool_client['UserPoolClient']['ClientId']
    username, password = create_confirmed_user(
        cognitoidentityprovider_client, user_pool_id, 'secret-user',
    )

    yield user_pool_id, user_pool_client_id, username, password

    delete_boto3_user_pool(cognitoidentityprovider_client, user_pool_id)

def create_user_pool_client_secret(name, resource_data):
    return create_and_wait_resource(
        RESOURCE_PLURAL, name, resource_data, wait_seconds=CREATE_WAIT_AFTER_SECONDS,
    )


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
            assert validator.get_access_token(user_pool_client_id, username, password, generated_secret)
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
            validator = CognitoValidator(cognitoidentityprovider_client)
            assert validator.get_access_token(
                user_pool_client_id,
                username,
                password,
                supplied_secret,
            )
            assert client_secret_exists(validator, user_pool_id, user_pool_client_id, secret_id)
        finally:
            k8s.delete_custom_resource(ref, DELETE_WAIT_AFTER_SECONDS)
            k8s.delete_secret(namespace='default', name=source_secret_name)

@service_marker
@pytest.mark.canary
class TestUserPoolClientSecretAdoption:
    def test_adopt_initial_secret_and_retire_it(
            self, cognitoidentityprovider_client, user_pool_client_with_secret,
    ):
        user_pool_id, user_pool_client_id, username, password = user_pool_client_with_secret
        validator = CognitoValidator(cognitoidentityprovider_client)

        second_secret_ref = None
        adopted_ref = None
        second_target_secret_name = random_suffix_name('upc-adopt-second-secret', 32)
        try:
            # Sanity check: exactly one secret exists in AWS before adoption/rotation.
            secret_ids = validator.list_user_pool_client_secrets(user_pool_id, user_pool_client_id)
            assert len(secret_ids) == 1
            initial_client_secret_id = secret_ids[0]['ClientSecretId']

            # Create a second secret via the UserPoolClientSecret CRD, bringing
            # AWS to its two-secret maximum.
            second_secret_name = random_suffix_name('userpoolclientsecret', 24)
            k8s.create_opaque_secret(namespace='default', name=second_target_secret_name, key='unrelated', value='preserved')
            second_secret_replacements = REPLACEMENT_VALUES.copy()
            second_secret_replacements.update({
                'USERPOOLCLIENTSECRET_NAME': second_secret_name,
                'USERPOOL_ID': user_pool_id,
                'USERPOOLCLIENT_ID': user_pool_client_id,
                'TARGET_SECRET_NAME': second_target_secret_name,
            })
            second_secret_resource_data = load_cognitoidentityprovider_resource(
                'userpoolclientsecret_generated', additional_replacements=second_secret_replacements,
            )
            second_secret_ref, second_secret_cr = create_user_pool_client_secret(
                second_secret_name, second_secret_resource_data,
            )
            assert k8s.wait_on_condition(second_secret_ref, 'ACK.ResourceSynced', 'True')
            assert len(validator.list_user_pool_client_secrets(user_pool_id, user_pool_client_id)) == 2

            # Adopt the initial secret into a UserPoolClientSecret via ACK's
            # standard annotation-based adoption flow.
            adopted_name = random_suffix_name('upc-adopted-secret', 24)
            adoption_fields = json.dumps({
                'id': initial_client_secret_id,
                'userPoolClientID': user_pool_client_id,
                'userPoolID': user_pool_id,
            })
            adoption_replacements = REPLACEMENT_VALUES.copy()
            adoption_replacements.update({
                'USERPOOLCLIENTSECRET_NAME': adopted_name,
                'ADOPTION_FIELDS': adoption_fields.replace('"', '\\"'),
            })
            adoption_resource_data = load_cognitoidentityprovider_resource(
                'userpoolclientsecret_adopt', additional_replacements=adoption_replacements,
            )
            adopted_ref, adopted_cr = create_user_pool_client_secret(adopted_name, adoption_resource_data)
            assert k8s.wait_on_condition(adopted_ref, 'ACK.ResourceSynced', 'True')
            assert adopted_cr['status']['id'] == initial_client_secret_id

            # Adoption must not have created a new Cognito secret.
            assert len(validator.list_user_pool_client_secrets(user_pool_id, user_pool_client_id)) == 2

            # Deleting the adopted CR retires the original secret.
            k8s.delete_custom_resource(adopted_ref, DELETE_WAIT_AFTER_SECONDS)
            adopted_ref = None
            remaining_secrets = validator.list_user_pool_client_secrets(user_pool_id, user_pool_client_id)
            assert len(remaining_secrets) == 1
            assert remaining_secrets[0]['ClientSecretId'] != initial_client_secret_id
        finally:
            if adopted_ref is not None:
                k8s.delete_custom_resource(adopted_ref, DELETE_WAIT_AFTER_SECONDS)
            k8s.delete_secret(namespace='default', name=second_target_secret_name)
            k8s.patch_custom_resource(second_secret_ref, {'metadata': {'finalizers': []}})
            k8s.delete_custom_resource(second_secret_ref, DELETE_WAIT_AFTER_SECONDS)
