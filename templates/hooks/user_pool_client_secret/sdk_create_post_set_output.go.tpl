if desired.ko.Spec.ExportTo != nil {
	if resp.ClientSecretDescriptor.ClientSecretValue == nil {
		return &resource{ko}, ackerr.NewTerminalError(fmt.Errorf("Cognito did not return a generated client secret"))
	}
	if err = rm.exportGeneratedSecret(ctx, desired.ko, *resp.ClientSecretDescriptor.ClientSecretValue); err != nil {
		return &resource{ko}, err
	}
}
