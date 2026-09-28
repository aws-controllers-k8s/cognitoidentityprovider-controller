// Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License"). You may
// not use this file except in compliance with the License. A copy of the
// License is located at
//
//     http://aws.amazon.com/apache2.0/
//
// or in the "license" file accompanying this file. This file is distributed
// on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
// express or implied. See the License for the specific language governing
// permissions and limitations under the License.

package user_pool_client_secret

import (
	"errors"
	"fmt"
	"testing"

	ackv1alpha1 "github.com/aws-controllers-k8s/runtime/apis/core/v1alpha1"
	"github.com/aws/aws-sdk-go-v2/aws"
	svcsdktypes "github.com/aws/aws-sdk-go-v2/service/cognitoidentityprovider/types"
	smithy "github.com/aws/smithy-go"
	"github.com/stretchr/testify/assert"
	k8scorev1 "k8s.io/api/core/v1"

	svcapitypes "github.com/aws-controllers-k8s/cognitoidentityprovider-controller/apis/v1alpha1"
)

func newSecretSourceTestResource(spec svcapitypes.UserPoolClientSecretSpec) *resource {
	return &resource{
		ko: &svcapitypes.UserPoolClientSecret{
			Spec: spec,
		},
	}
}

func TestValidateSecretSourceInput(t *testing.T) {
	clientSecretRef := &ackv1alpha1.SecretKeyReference{
		SecretReference: k8scorev1.SecretReference{Name: "supplied"},
		Key:             "value",
	}
	exportToRef := &ackv1alpha1.SecretKeyReference{
		SecretReference: k8scorev1.SecretReference{Name: "destination"},
		Key:             "clientSecret",
	}

	tests := []struct {
		name    string
		spec    svcapitypes.UserPoolClientSecretSpec
		wantErr bool
	}{
		{
			name:    "neither clientSecret nor exportTo is rejected",
			spec:    svcapitypes.UserPoolClientSecretSpec{},
			wantErr: true,
		},
		{
			name: "clientSecret only is valid",
			spec: svcapitypes.UserPoolClientSecretSpec{
				ClientSecret: clientSecretRef,
			},
			wantErr: false,
		},
		{
			name: "exportTo only is valid",
			spec: svcapitypes.UserPoolClientSecretSpec{
				ExportTo: exportToRef,
			},
			wantErr: false,
		},
		{
			name: "both clientSecret and exportTo is rejected",
			spec: svcapitypes.UserPoolClientSecretSpec{
				ClientSecret: clientSecretRef,
				ExportTo:     exportToRef,
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateSecretSourceInput(newSecretSourceTestResource(tt.spec))
			if tt.wantErr {
				assert.Error(t, err)
			} else {
				assert.NoError(t, err)
			}
		})
	}
}

func TestFindClientSecretByID(t *testing.T) {
	secrets := []svcsdktypes.ClientSecretDescriptorType{
		{ClientSecretId: aws.String("initial-id")},
		{ClientSecretId: aws.String("rotated-id")},
	}

	t.Run("matches an existing id", func(t *testing.T) {
		got := findClientSecretByID(secrets, "rotated-id")
		if assert.NotNil(t, got) {
			assert.Equal(t, "rotated-id", *got.ClientSecretId)
		}
	})

	t.Run("returns nil when id is absent", func(t *testing.T) {
		got := findClientSecretByID(secrets, "missing-id")
		assert.Nil(t, got)
	})

	t.Run("returns nil for an empty list", func(t *testing.T) {
		got := findClientSecretByID(nil, "any-id")
		assert.Nil(t, got)
	})

	t.Run("skips descriptors with a nil id", func(t *testing.T) {
		withNil := []svcsdktypes.ClientSecretDescriptorType{
			{ClientSecretId: nil},
			{ClientSecretId: aws.String("only-id")},
		}
		got := findClientSecretByID(withNil, "only-id")
		if assert.NotNil(t, got) {
			assert.Equal(t, "only-id", *got.ClientSecretId)
		}
	})
}

// TestIsResourceNotFoundError verifies that customFind correctly recognizes
// the ResourceNotFoundException returned by ListUserPoolClientSecrets when
// the parent UserPool or UserPoolClient has already been deleted, so that
// deletion of the UserPoolClientSecret CR is not blocked (regression test).
func TestIsResourceNotFoundError(t *testing.T) {
	t.Run("ResourceNotFoundException is recognized", func(t *testing.T) {
		err := &smithy.GenericAPIError{Code: "ResourceNotFoundException", Message: "User pool eu-west-3_ZpNvEEP9f does not exist."}
		assert.True(t, isResourceNotFoundError(err))
	})

	t.Run("wrapped ResourceNotFoundException is recognized", func(t *testing.T) {
		err := fmt.Errorf("wrapped: %w", &smithy.GenericAPIError{Code: "ResourceNotFoundException"})
		assert.True(t, isResourceNotFoundError(err))
	})

	t.Run("other AWS API errors are not recognized", func(t *testing.T) {
		err := &smithy.GenericAPIError{Code: "InvalidParameterException"}
		assert.False(t, isResourceNotFoundError(err))
	})

	t.Run("non-API errors are not recognized", func(t *testing.T) {
		assert.False(t, isResourceNotFoundError(errors.New("boom")))
	})

	t.Run("nil error is not recognized", func(t *testing.T) {
		assert.False(t, isResourceNotFoundError(nil))
	})
}

// TestPopulateResourceFromAnnotation_Adoption verifies that the generated
// adoption path (used e.g. to bind a UserPoolClientSecret CR to the initial
// secret captured in UserPoolClient.status.initialClientSecretID) correctly
// populates status.id and the required spec identity fields from the
// adoption-fields annotation, without ever needing to call
// AddUserPoolClientSecret.
func TestPopulateResourceFromAnnotation_Adoption(t *testing.T) {
	r := &resource{ko: &svcapitypes.UserPoolClientSecret{}}

	err := r.PopulateResourceFromAnnotation(map[string]string{
		"id":               "initial-id",
		"userPoolClientID": "client-id",
		"userPoolID":       "pool-id",
	})

	assert.NoError(t, err)
	if assert.NotNil(t, r.ko.Status.ID) {
		assert.Equal(t, "initial-id", *r.ko.Status.ID)
	}
	if assert.NotNil(t, r.ko.Spec.UserPoolClientID) {
		assert.Equal(t, "client-id", *r.ko.Spec.UserPoolClientID)
	}
	if assert.NotNil(t, r.ko.Spec.UserPoolID) {
		assert.Equal(t, "pool-id", *r.ko.Spec.UserPoolID)
	}
}

func TestPopulateResourceFromAnnotation_MissingRequiredField(t *testing.T) {
	r := &resource{ko: &svcapitypes.UserPoolClientSecret{}}

	err := r.PopulateResourceFromAnnotation(map[string]string{
		"userPoolClientID": "client-id",
		"userPoolID":       "pool-id",
	})

	assert.Error(t, err)
}
