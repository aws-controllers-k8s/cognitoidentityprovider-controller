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
