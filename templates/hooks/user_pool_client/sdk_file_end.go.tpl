// validateClientSecretInput enforces that at most one of spec.clientSecret
// or spec.generateSecret=true is used to establish the client's secret, and
// that spec.clientSecretExportTo is only used when Cognito is asked to
// generate the secret.
func validateClientSecretInput(r *resource) error {
	generateSecret := r.ko.Spec.GenerateSecret != nil && *r.ko.Spec.GenerateSecret
	if r.ko.Spec.ClientSecret != nil && generateSecret {
		return ackerr.NewTerminalError(fmt.Errorf("only one of spec.clientSecret or spec.generateSecret=true may be specified"))
	}
	if r.ko.Spec.ClientSecretExportTo != nil && !generateSecret {
		return ackerr.NewTerminalError(fmt.Errorf("spec.clientSecretExportTo requires spec.generateSecret=true"))
	}
	return nil
}
