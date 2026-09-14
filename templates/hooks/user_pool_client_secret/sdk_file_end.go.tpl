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
		return nil, err
	}

	for _, descriptor := range resp.ClientSecrets {
		if descriptor.ClientSecretId == nil || *descriptor.ClientSecretId != *r.ko.Status.ID {
			continue
		}
		ko := r.ko.DeepCopy()
		if descriptor.ClientSecretCreateDate != nil {
			ko.Status.ClientSecretCreateDate = &metav1.Time{Time: *descriptor.ClientSecretCreateDate}
		}
		rm.setStatusDefaults(ko)
		return &resource{ko}, nil
	}

	return nil, ackerr.NotFound
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
