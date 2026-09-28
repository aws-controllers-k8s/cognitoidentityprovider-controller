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

package user_pool_client

import (
	"testing"

	ackv1alpha1 "github.com/aws-controllers-k8s/runtime/apis/core/v1alpha1"
	"github.com/stretchr/testify/assert"
	k8scorev1 "k8s.io/api/core/v1"

	svcapitypes "github.com/aws-controllers-k8s/cognitoidentityprovider-controller/apis/v1alpha1"
)

func newClientSecretTestResource(spec svcapitypes.UserPoolClientSpec) *resource {
	return &resource{
		ko: &svcapitypes.UserPoolClient{
			Spec: spec,
		},
	}
}

func TestValidateClientSecretInput(t *testing.T) {
	trueVal := true
	falseVal := false
	suppliedSecretRef := &ackv1alpha1.SecretKeyReference{
		SecretReference: k8scorev1.SecretReference{Name: "creds"},
		Key:             "clientSecret",
	}
	exportRef := &ackv1alpha1.SecretKeyReference{
		SecretReference: k8scorev1.SecretReference{Name: "creds"},
		Key:             "exportedSecret",
	}

	tests := []struct {
		name    string
		spec    svcapitypes.UserPoolClientSpec
		wantErr bool
	}{
		{
			name:    "no secret fields set",
			spec:    svcapitypes.UserPoolClientSpec{},
			wantErr: false,
		},
		{
			name: "generateSecret alone is valid",
			spec: svcapitypes.UserPoolClientSpec{
				GenerateSecret: &trueVal,
			},
			wantErr: false,
		},
		{
			name: "clientSecret alone is valid",
			spec: svcapitypes.UserPoolClientSpec{
				ClientSecret: suppliedSecretRef,
			},
			wantErr: false,
		},
		{
			name: "generateSecret=true and exportTo is valid",
			spec: svcapitypes.UserPoolClientSpec{
				GenerateSecret:       &trueVal,
				ClientSecretExportTo: exportRef,
			},
			wantErr: false,
		},
		{
			name: "clientSecret and generateSecret=true is rejected",
			spec: svcapitypes.UserPoolClientSpec{
				ClientSecret:   suppliedSecretRef,
				GenerateSecret: &trueVal,
			},
			wantErr: true,
		},
		{
			name: "clientSecret and generateSecret=false is allowed",
			spec: svcapitypes.UserPoolClientSpec{
				ClientSecret:   suppliedSecretRef,
				GenerateSecret: &falseVal,
			},
			wantErr: false,
		},
		{
			name: "exportTo without generateSecret is rejected",
			spec: svcapitypes.UserPoolClientSpec{
				ClientSecretExportTo: exportRef,
			},
			wantErr: true,
		},
		{
			name: "exportTo with generateSecret=false is rejected",
			spec: svcapitypes.UserPoolClientSpec{
				ClientSecretExportTo: exportRef,
				GenerateSecret:       &falseVal,
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateClientSecretInput(newClientSecretTestResource(tt.spec))
			if tt.wantErr {
				assert.Error(t, err)
			} else {
				assert.NoError(t, err)
			}
		})
	}
}
