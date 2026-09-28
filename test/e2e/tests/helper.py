# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
#	 http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.

"""Helper functions for CognitoIdentityProvider e2e tests
"""

import hmac
import base64
import hashlib
import time

from acktest.k8s import resource as k8s
from acktest.resources import random_suffix_name

from e2e import CRD_GROUP, CRD_VERSION


def create_and_wait_resource(crd_plural, name, resource_data, namespace='default', wait_seconds=5):
    """Create a k8s custom resource and wait for it to be consumed by the
    ACK controller. Returns (ref, cr). Does not assert the resource
    reconciled successfully -- use create_and_assert_resource for that, or
    call this directly when the test itself needs to assert on a specific
    (possibly failed) condition."""
    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, crd_plural, name, namespace=namespace,
    )
    k8s.create_custom_resource(ref, resource_data)
    time.sleep(wait_seconds)
    cr = k8s.wait_resource_consumed_by_controller(ref)
    return ref, cr


def create_and_assert_resource(crd_plural, name, resource_data, namespace='default', wait_seconds=5):
    """Create a k8s custom resource, wait for it to be consumed by the ACK
    controller, and assert it was successfully consumed and exists in
    Kubernetes. Returns (ref, cr)."""
    ref, cr = create_and_wait_resource(
        crd_plural, name, resource_data, namespace=namespace, wait_seconds=wait_seconds,
    )
    assert cr is not None
    assert k8s.get_resource_exists(ref)
    return ref, cr


def delete_and_assert_gone(ref, wait_seconds=5):
    """Delete a k8s custom resource (if it still exists) and assert it's
    gone afterward. Safe to call with ref=None."""
    if ref is None:
        return
    if k8s.get_resource_exists(ref):
        _, deleted = k8s.delete_custom_resource(ref, wait_seconds)
        assert deleted
    assert not k8s.get_resource_exists(ref)


DEFAULT_TEST_PASSWORD = 'AckTestPassword123!'


def create_boto3_user_pool(cognitoidentityprovider_client, name_prefix, name_length=24):
    """Create a bare UserPool directly via boto3 (bypassing the ACK
    controller), for use as a pre-existing parent resource in tests. Returns
    the UserPool's id."""
    pool_name = random_suffix_name(name_prefix, name_length)
    response = cognitoidentityprovider_client.create_user_pool(PoolName=pool_name)
    return response['UserPool']['Id']


def delete_boto3_user_pool(cognitoidentityprovider_client, user_pool_id):
    """Delete a UserPool created with create_boto3_user_pool, ignoring the
    case where it has already been removed."""
    try:
        cognitoidentityprovider_client.delete_user_pool(UserPoolId=user_pool_id)
    except cognitoidentityprovider_client.exceptions.ResourceNotFoundException:
        pass


def create_confirmed_user(
        cognitoidentityprovider_client, user_pool_id, name_prefix,
        password=DEFAULT_TEST_PASSWORD, name_length=24,
):
    """Create a confirmed (password-set, non-temporary) user in the given
    UserPool via boto3, for tests that need to verify a client secret
    actually authenticates against Cognito. Returns (username, password)."""
    username = random_suffix_name(name_prefix, name_length)
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
    return username, password


class CognitoValidator:
    def __init__(self, cognitoidentityprovider_client):
        self.cognitoidentityprovider_client = cognitoidentityprovider_client
    
    def get_user_pool(self, user_pool_id):
        try:
            response = self.cognitoidentityprovider_client.describe_user_pool(UserPoolId=user_pool_id)
            return response['UserPool']
        except self.cognitoidentityprovider_client.exceptions.ResourceNotFoundException:
            return None
        
    def user_pool_exists(self, user_pool_id):
        response = self.get_user_pool(user_pool_id)
        return response is not None

    def user_pool_list_tags(self, user_pool_arn):
        try:
            response = self.cognitoidentityprovider_client.list_tags_for_resource(ResourceArn=user_pool_arn)
            return response['Tags']
        except self.cognitoidentityprovider_client.exceptions.ResourceNotFoundException:
            return []

    def get_user_pool_client(self, user_pool_id, client_id):
        try:
            response = self.cognitoidentityprovider_client.describe_user_pool_client(
                UserPoolId=user_pool_id,
                ClientId=client_id,
            )
            return response['UserPoolClient']
        except self.cognitoidentityprovider_client.exceptions.ResourceNotFoundException:
            return None

    def user_pool_client_exists(self, user_pool_id, client_id):
        response = self.get_user_pool_client(user_pool_id, client_id)
        return response is not None

    def list_user_pool_client_secrets(self, user_pool_id, client_id):
        try:
            response = self.cognitoidentityprovider_client.list_user_pool_client_secrets(
                UserPoolId=user_pool_id,
                ClientId=client_id,
            )
            return response['ClientSecrets']
        except self.cognitoidentityprovider_client.exceptions.ResourceNotFoundException:
            return []

    def get_resource_server(self, user_pool_id, identifier):
        try:
            response = self.cognitoidentityprovider_client.describe_resource_server(
                UserPoolId=user_pool_id,
                Identifier=identifier,
            )
            return response['ResourceServer']
        except self.cognitoidentityprovider_client.exceptions.ResourceNotFoundException:
            return None

    def resource_server_exists(self, user_pool_id, identifier):
        return self.get_resource_server(user_pool_id, identifier) is not None

    def get_access_token(
            self, user_pool_client_id, username, password, client_secret,
    ):
        secret_hash = base64.b64encode(
            hmac.new(
                client_secret.encode('utf-8'),
                f'{username}{user_pool_client_id}'.encode('utf-8'),
                hashlib.sha256,
            ).digest(),
        ).decode('utf-8')
        response = self.cognitoidentityprovider_client.initiate_auth(
            ClientId=user_pool_client_id,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': username,
                'PASSWORD': password,
                'SECRET_HASH': secret_hash,
            },
        )
        return response['AuthenticationResult']['AccessToken']
