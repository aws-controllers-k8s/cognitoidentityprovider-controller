if err := validateSecretSourceInput(desired); err != nil {
	return nil, err
}
if desired.ko.Spec.ExportTo != nil {
	if err := rm.validateExportTarget(ctx, desired.ko); err != nil {
		return nil, err
	}
}
