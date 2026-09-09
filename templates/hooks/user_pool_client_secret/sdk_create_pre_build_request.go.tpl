if desired.ko.Spec.ClientSecret != nil && desired.ko.Spec.ExportTo != nil {
	return nil, ackerr.NewTerminalError(fmt.Errorf("only one of spec.clientSecret or spec.exportTo may be specified"))
}
if desired.ko.Spec.ClientSecret == nil && desired.ko.Spec.ExportTo == nil {
	return nil, ackerr.NewTerminalError(fmt.Errorf("one of spec.clientSecret or spec.exportTo must be specified"))
}
if desired.ko.Spec.ExportTo != nil {
	if err := rm.validateExportTarget(ctx, desired.ko); err != nil {
		return nil, err
	}
}
