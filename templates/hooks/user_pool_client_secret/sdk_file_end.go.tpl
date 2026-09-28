// findClientSecretByID returns the descriptor within secrets whose
// ClientSecretId matches id, or nil if no descriptor matches. This is a pure
// helper extracted from customFind for unit testability.
func findClientSecretByID(secrets []svcsdktypes.ClientSecretDescriptorType, id string) *svcsdktypes.ClientSecretDescriptorType {
	for _, descriptor := range secrets {
		if descriptor.ClientSecretId != nil && *descriptor.ClientSecretId == id {
			d := descriptor
			return &d
		}
	}
	return nil
}

// isResourceNotFoundError returns true if err is an AWS API error with code
// ResourceNotFoundException. Cognito returns this error from
// ListUserPoolClientSecrets when the parent UserPool or UserPoolClient no
// longer exists (as opposed to an empty secrets list). This is a pure
// helper extracted from customFind for unit testability.
func isResourceNotFoundError(err error) bool {
	var awsErr smithy.APIError
	return errors.As(err, &awsErr) && awsErr.ErrorCode() == "ResourceNotFoundException"
}

// validateSecretSourceInput enforces that exactly one of spec.clientSecret
// or spec.exportTo is specified. Extracted from sdk_create_pre_build_request
// for unit testability.
func validateSecretSourceInput(r *resource) error {
	if r.ko.Spec.ClientSecret != nil && r.ko.Spec.ExportTo != nil {
		return ackerr.NewTerminalError(fmt.Errorf("only one of spec.clientSecret or spec.exportTo may be specified"))
	}
	if r.ko.Spec.ClientSecret == nil && r.ko.Spec.ExportTo == nil {
		return ackerr.NewTerminalError(fmt.Errorf("one of spec.clientSecret or spec.exportTo must be specified"))
	}
	return nil
}

func (rm *resourceManager) customFind(
	ctx context.Context,
	r *resource,
) (*resource, error) {
	if r.ko.Spec.UserPoolID == nil || r.ko.Spec.UserPoolClientID == nil || r.ko.Status.ID == nil {
		return nil, ackerr.NotFound
	}

	resp, err := rm.sdkapi.ListUserPoolClientSecrets(ctx, &svcsdk.ListUserPoolClientSecretsInput{
		ClientId:   r.ko.Spec.UserPoolClientID,
		UserPoolId: r.ko.Spec.UserPoolID,
	})
	rm.metrics.RecordAPICall("READ_ONE", "ListUserPoolClientSecrets", err)
	if err != nil {
		// If the parent UserPool or UserPoolClient has already been deleted,
		// Cognito returns ResourceNotFoundException here instead of an empty
		// list. Treat that the same as "secret not found" so that deletion of
		// this CR can still proceed instead of getting stuck reconciling.
		if isResourceNotFoundError(err) {
			return nil, ackerr.NotFound
		}
		return nil, err
	}

	descriptor := findClientSecretByID(resp.ClientSecrets, *r.ko.Status.ID)
	if descriptor == nil {
		return nil, ackerr.NotFound
	}
	ko := r.ko.DeepCopy()
	if descriptor.ClientSecretCreateDate != nil {
		ko.Status.ClientSecretCreateDate = &metav1.Time{Time: *descriptor.ClientSecretCreateDate}
	}
	rm.setStatusDefaults(ko)
	return &resource{ko}, nil
}

func (rm *resourceManager) exportGeneratedSecret(
	ctx context.Context,
	ko *svcapitypes.UserPoolClientSecret,
	value string,
) error {
	ref := ko.Spec.ExportTo
	namespace := ref.Namespace
	if namespace == "" {
		namespace = ko.Namespace
	}
	return rm.rr.WriteToSecret(ctx, value, namespace, ref.Name, ref.Key)
}

func (rm *resourceManager) validateExportTarget(
	ctx context.Context,
	ko *svcapitypes.UserPoolClientSecret,
) error {
	ref := ko.Spec.ExportTo
	existing, err := rm.rr.SecretValueFromReference(ctx, ref)
	if err == nil && existing != "" {
		return ackerr.NewTerminalError(fmt.Errorf("target Secret %q key %q already contains a value", ref.Name, ref.Key))
	}
	if err != nil && !errors.Is(err, ackerr.SecretNotFound) {
		return err
	}
	if errors.Is(err, ackerr.SecretNotFound) {
		// WriteToSecret cannot distinguish a missing key from a missing Secret.
		// It will perform the definitive existence check before writing.
		return nil
	}
	return nil
}
