if desired.ko.Spec.ClientSecret != nil && input.ClientSecret == nil {
	return nil, ackerr.NewTerminalError(fmt.Errorf("spec.clientSecret must reference a non-empty Secret value"))
}
