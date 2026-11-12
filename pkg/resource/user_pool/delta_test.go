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

package user_pool

import (
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws"

	svcapitypes "github.com/aws-controllers-k8s/cognitoidentityprovider-controller/apis/v1alpha1"
)

func userPoolWithTags(tags map[string]*string) *resource {
	return &resource{
		ko: &svcapitypes.UserPool{
			Spec: svcapitypes.UserPoolSpec{
				Name:         aws.String("test-user-pool"),
				UserPoolTags: tags,
			},
		},
	}
}

func TestNewResourceDelta_UserPoolTags(t *testing.T) {
	tests := []struct {
		name     string
		desired  map[string]*string
		latest   map[string]*string
		wantDiff bool
	}{
		{
			name:    "controller-injected tags absent from spec produce no diff",
			desired: nil,
			latest: map[string]*string{
				"services.k8s.aws/controller-version": aws.String("cognitoidentityprovider-1.7.1"),
				"services.k8s.aws/namespace":          aws.String("ack-system"),
			},
			wantDiff: false,
		},
		{
			name:     "aws-managed tags absent from spec produce no diff",
			desired:  nil,
			latest:   map[string]*string{"aws:cloudformation:stack-name": aws.String("my-stack")},
			wantDiff: false,
		},
		{
			name:    "user tags matching with extra injected tags observed produce no diff",
			desired: map[string]*string{"env": aws.String("prod")},
			latest: map[string]*string{
				"env":                                 aws.String("prod"),
				"services.k8s.aws/controller-version": aws.String("cognitoidentityprovider-1.7.1"),
				"services.k8s.aws/namespace":          aws.String("ack-system"),
			},
			wantDiff: false,
		},
		{
			name:     "both sides empty produce no diff",
			desired:  map[string]*string{},
			latest:   nil,
			wantDiff: false,
		},
		{
			name:    "changed user tag value produces a diff",
			desired: map[string]*string{"env": aws.String("staging")},
			latest: map[string]*string{
				"env":                        aws.String("prod"),
				"services.k8s.aws/namespace": aws.String("ack-system"),
			},
			wantDiff: true,
		},
		{
			name:    "removed user tag produces a diff",
			desired: nil,
			latest: map[string]*string{
				"env":                        aws.String("prod"),
				"services.k8s.aws/namespace": aws.String("ack-system"),
			},
			wantDiff: true,
		},
		{
			name:    "added user tag produces a diff",
			desired: map[string]*string{"env": aws.String("prod")},
			latest: map[string]*string{
				"services.k8s.aws/namespace": aws.String("ack-system"),
			},
			wantDiff: true,
		},
		{
			name:     "renamed user tag key produces a diff",
			desired:  map[string]*string{"environment": aws.String("prod")},
			latest:   map[string]*string{"env": aws.String("prod")},
			wantDiff: true,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			a := userPoolWithTags(tc.desired)
			b := userPoolWithTags(tc.latest)

			delta := newResourceDelta(a, b)

			if got := delta.DifferentAt("Spec.UserPoolTags"); got != tc.wantDiff {
				t.Errorf("DifferentAt(Spec.UserPoolTags) = %v, want %v", got, tc.wantDiff)
			}
		})
	}
}

func TestCustomPreCompare_DoesNotMutateInputs(t *testing.T) {
	desired := map[string]*string{"env": aws.String("prod")}
	latest := map[string]*string{
		"env":                        aws.String("prod"),
		"services.k8s.aws/namespace": aws.String("ack-system"),
	}
	a := userPoolWithTags(desired)
	b := userPoolWithTags(latest)

	newResourceDelta(a, b)

	if len(a.ko.Spec.UserPoolTags) != 1 {
		t.Errorf("desired UserPoolTags mutated: got %d keys, want 1", len(a.ko.Spec.UserPoolTags))
	}
	if len(b.ko.Spec.UserPoolTags) != 2 {
		t.Errorf("latest UserPoolTags mutated: got %d keys, want 2", len(b.ko.Spec.UserPoolTags))
	}
	if _, ok := b.ko.Spec.UserPoolTags["services.k8s.aws/namespace"]; !ok {
		t.Error("latest UserPoolTags lost services.k8s.aws/namespace")
	}
}
